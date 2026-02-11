import json
import time

from openai import OpenAI

KEYWORD_REFINEMENT_PROMPT = """You are a search keyword optimizer for Galvanize, an education company.

Given the original search keywords and a sample of posts that scored well (high intent), generate 3-5 NEW search keywords that:
1. Are variations or expansions of what worked
2. Target similar intent signals we might be missing
3. Are specific enough to find high-intent posts
4. Do NOT duplicate the original keywords

Return ONLY a JSON array of strings. No markdown, no backticks."""

SYSTEM_PROMPT = """You are an intent scoring agent for Galvanize, an education company that helps students with GRE preparation, TOEFL preparation, study abroad counseling, university admissions, scholarships, and student visa guidance.

Analyze each social media post and determine:
1. topic_label: One of [GRE, TOEFL, study_abroad, scholarships, visa, loans, admits, general_education]
2. intent_score: 0-100 (how likely this person needs Galvanize's services)
   - 80-100: Actively seeking help, asking specific questions, expressing frustration with prep
   - 50-79: Discussing topic, sharing experience, might benefit from guidance
   - 25-49: Tangentially related, general discussion
   - 0-24: Not relevant, spam, or already resolved
3. recommended_action: One of [comment, DM, content]
   - comment: Post a helpful reply in the thread
   - DM: Reach out directly (high intent, personal question)
   - content: Create content addressing this topic/pain point
4. suggested_response: A helpful, non-salesy 2-3 line response that Galvanize could post
5. why_this_matters: 1 line explaining the opportunity

Return ONLY valid JSON array. No markdown, no backticks."""


def _build_user_message(batch: list[dict]) -> str:
    items = []
    for i, post in enumerate(batch):
        items.append({
            "id": i,
            "source": post.get("source", ""),
            "title": post.get("title", ""),
            "snippet": post.get("snippet", "")[:300],
        })
    return f"Analyze these posts:\n{json.dumps(items)}"


def _parse_response(text: str) -> list[dict]:
    """Parse JSON from the model response, handling common issues."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    return json.loads(text)


def score_opportunities(posts: list[dict], model: str) -> list[dict]:
    if not posts:
        return []

    client = OpenAI()
    batch_size = 5
    scored = []

    for start in range(0, len(posts), batch_size):
        batch = posts[start : start + batch_size]
        batch_num = start // batch_size + 1
        total_batches = (len(posts) + batch_size - 1) // batch_size
        print(f"  Scoring batch {batch_num}/{total_batches}...")

        user_msg = _build_user_message(batch)
        result = None

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.3,
                    timeout=60,
                )
                raw = response.choices[0].message.content
                result = _parse_response(raw)
                break
            except json.JSONDecodeError:
                print(f"    Retry {attempt + 1}: malformed JSON response")
                time.sleep(2)
            except Exception as e:
                print(f"    Retry {attempt + 1}: API error: {e}")
                time.sleep(3)

        if result is None:
            # Assign default low scores on failure
            for post in batch:
                post.update({
                    "topic_label": "general_education",
                    "intent_score": 0,
                    "recommended_action": "content",
                    "suggested_response": "",
                    "why_this_matters": "Scoring failed",
                })
                scored.append(post)
            continue

        # Merge scores back into posts (by id if present, else by position)
        has_ids = all("id" in item for item in result)
        if has_ids:
            score_map = {item["id"]: item for item in result}
        else:
            score_map = {i: item for i, item in enumerate(result)}
        for i, post in enumerate(batch):
            info = score_map.get(i, {})
            post.update({
                "topic_label": info.get("topic_label", "general_education"),
                "intent_score": info.get("intent_score", 0),
                "recommended_action": info.get("recommended_action", "content"),
                "suggested_response": info.get("suggested_response", ""),
                "why_this_matters": info.get("why_this_matters", ""),
            })
            scored.append(post)

        time.sleep(1)

    return scored


def generate_refined_keywords(
    original_keywords: list[str],
    high_scoring_posts: list[dict],
    model: str,
) -> list[str]:
    """Use GPT to generate refined search keywords based on what scored well."""
    client = OpenAI()

    post_summaries = [
        {
            "title": p.get("title", "")[:100],
            "topic": p.get("topic_label", ""),
            "score": p.get("intent_score", 0),
        }
        for p in high_scoring_posts[:10]
    ]

    user_msg = json.dumps({
        "original_keywords": original_keywords,
        "high_scoring_posts": post_summaries,
    })

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": KEYWORD_REFINEMENT_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            timeout=30,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw = "\n".join(lines)
        new_keywords = json.loads(raw)
        original_lower = {k.lower() for k in original_keywords}
        return [k for k in new_keywords if k.lower() not in original_lower]
    except Exception as e:
        print(f"  Keyword refinement failed: {e}")
        return []
