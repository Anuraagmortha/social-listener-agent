import csv
import json
import time

from openai import OpenAI

DM_MIN_SCORE = 80
COMMENT_MIN_SCORE = 70
CONTENT_NOTE_MIN_SCORE = 60

OUTREACH_SYSTEM_PROMPT = """You are an outreach copywriter for Galvanize, an education company helping students with GRE/TOEFL prep, study abroad counseling, university admissions, scholarships, and visa guidance.

Write a personalized outreach message based on the post details provided. Rules:
- Be genuinely helpful, not salesy
- Reference specific details from the post to show you read it
- Keep it concise (2-4 sentences for DMs, 1-3 sentences for comments)
- For Reddit: casual, community-friendly tone
- For Twitter: concise, conversational, within character spirit
- For Quora: authoritative, detailed, answer-style
- End with a soft CTA (e.g., "happy to share more if helpful" or "feel free to reach out")
- Do NOT use corporate jargon or hard sells

Return ONLY a JSON object with these keys:
- "draft_message": The outreach message text
- "reason_for_outreach": One sentence explaining why this post is worth reaching out to

No markdown, no backticks."""


def classify_outreach_action(post: dict) -> str | None:
    score = post.get("intent_score", 0)
    action = post.get("recommended_action", "")

    if score >= DM_MIN_SCORE and action == "DM":
        return "dm"
    if score >= COMMENT_MIN_SCORE and action == "comment":
        return "comment"
    if score >= CONTENT_NOTE_MIN_SCORE and action == "content":
        return "content_idea"
    return None


def _detect_platform(post: dict) -> str:
    source = post.get("source", "")
    url = post.get("url", "")
    if source == "reddit" or "reddit.com" in url:
        return "reddit"
    if source == "twitter" or "twitter.com" in url or "x.com" in url:
        return "twitter"
    if source == "quora" or "quora.com" in url:
        return "quora"
    return "web"


def _print_decision(post: dict, action_type: str, platform: str):
    title = (post.get("title") or "")[:50]
    score = post.get("intent_score", 0)
    why = post.get("why_this_matters", "")
    labels = {
        "dm": "drafting personalized DM",
        "comment": "drafting helpful comment",
        "content_idea": "noting as content idea (no outreach)",
    }
    print(f"    [{score}] {title}")
    print(f"         -> {labels.get(action_type, action_type)} on {platform}")
    if why:
        print(f"         Reason: {why}")


def draft_outreach(posts: list[dict], model: str) -> list[dict]:
    client = OpenAI()
    drafts = []
    posts_to_draft = []

    for post in posts:
        action_type = classify_outreach_action(post)
        platform = _detect_platform(post)

        if action_type is None:
            continue

        if action_type == "content_idea":
            drafts.append({
                "platform": platform,
                "url": post.get("url", ""),
                "post_title": post.get("title", ""),
                "action_type": "content_idea",
                "draft_message": "",
                "intent_score": post.get("intent_score", 0),
                "reason_for_outreach": f"Content idea: {post.get('why_this_matters', '')}",
            })
            _print_decision(post, "content_idea", platform)
            continue

        posts_to_draft.append((post, action_type, platform))
        _print_decision(post, action_type, platform)

    # Generate GPT drafts for dm/comment posts
    for post, action_type, platform in posts_to_draft:
        user_msg = json.dumps({
            "action_type": action_type,
            "platform": platform,
            "post_title": post.get("title", ""),
            "post_snippet": post.get("snippet", "")[:300],
            "topic": post.get("topic_label", ""),
            "intent_score": post.get("intent_score", 0),
            "why_this_matters": post.get("why_this_matters", ""),
        })

        parsed = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": OUTREACH_SYSTEM_PROMPT},
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
                parsed = json.loads(raw)
                break
            except (json.JSONDecodeError, Exception) as e:
                print(f"      Retry {attempt + 1} for outreach draft: {e}")
                time.sleep(2)

        drafts.append({
            "platform": platform,
            "url": post.get("url", ""),
            "post_title": post.get("title", ""),
            "action_type": action_type,
            "draft_message": parsed.get("draft_message", "") if parsed else "[Draft generation failed]",
            "intent_score": post.get("intent_score", 0),
            "reason_for_outreach": parsed.get("reason_for_outreach", "") if parsed else "Draft generation failed",
        })
        time.sleep(1)

    return drafts


def save_outreach_csv(drafts: list[dict], filepath: str):
    fieldnames = [
        "platform", "url", "post_title", "action_type",
        "draft_message", "intent_score", "reason_for_outreach",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for d in drafts:
            writer.writerow(d)
