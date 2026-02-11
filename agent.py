"""
Social Listening Opportunity Agent for Galvanize
Usage: python agent.py
"""

import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime

import yaml
from dotenv import load_dotenv

from outreach import draft_outreach, save_outreach_csv
from scorer import generate_refined_keywords, score_opportunities
from scrapers.reddit_scraper import scrape_reddit
from scrapers.web_scraper import scrape_web


def load_config(path="config.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def print_banner(config):
    print("\n" + "=" * 60)
    print("  Social Listening Agent for Galvanize")
    print("=" * 60)
    print(f"  Keywords:       {len(config['keywords'])}")
    print(f"  Subreddits:     {len(config['subreddits'])}")
    print(f"  Sources:        {', '.join(config['sources'])}")
    print(f"  Min score:      {config['min_intent_score']}")
    print(f"  Max iterations: {config.get('max_refinement_iterations', 3)}")
    print("=" * 60 + "\n")


def print_stats(total_raw, after_dedup, qualified, top):
    print("\n" + "-" * 50)
    print("  Evaluation Summary")
    print("-" * 50)
    print(f"  Total posts scraped:       {total_raw}")
    print(f"  After dedup:               {after_dedup}")
    print(f"  Above intent threshold:    {len(qualified)}")

    if top:
        avg = sum(p["intent_score"] for p in top) / len(top)
        print(f"  Top {len(top)} avg intent score: {avg:.1f}")

        topics = Counter(p["topic_label"] for p in top)
        print(f"  Topics: {dict(topics)}")

        actions = Counter(p["recommended_action"] for p in top)
        print(f"  Actions: {dict(actions)}")
    print("-" * 50)


def print_top5(top):
    if not top:
        print("  No opportunities found.")
        return
    print("\n  Top 5 Opportunities:")
    print("  " + "-" * 70)
    for i, p in enumerate(top[:5], 1):
        title = (p.get("title") or "")[:50]
        print(f"  {i}. [{p['intent_score']}] {title}")
        print(f"     Source: {p['source']} | Topic: {p['topic_label']} | Action: {p['recommended_action']}")
        print(f"     {p['url']}")
        if p.get("why_this_matters"):
            print(f"     Why: {p['why_this_matters']}")
        print()


def save_csv(posts, filepath):
    fieldnames = [
        "source_platform",
        "url",
        "title_snippet",
        "topic_label",
        "intent_score",
        "recommended_action",
        "suggested_response",
        "why_this_matters",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in posts:
            title_snippet = (p.get("title", "") + " " + (p.get("snippet", "") or "")[:100]).strip()
            writer.writerow({
                "source_platform": p.get("source", ""),
                "url": p.get("url", ""),
                "title_snippet": title_snippet,
                "topic_label": p.get("topic_label", ""),
                "intent_score": p.get("intent_score", 0),
                "recommended_action": p.get("recommended_action", ""),
                "suggested_response": p.get("suggested_response", ""),
                "why_this_matters": p.get("why_this_matters", ""),
            })


def save_json(posts, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=2, default=str)


def main():
    load_dotenv()
    config = load_config()
    print_banner(config)

    max_iterations = config.get("max_refinement_iterations", 3)
    max_results = config["max_results"]
    min_score = config["min_intent_score"]
    model = config["openai_model"]

    all_seen_urls = set()
    all_qualified = []
    total_raw = 0
    current_keywords = list(config["keywords"])

    # ===== ITERATIVE SEARCH-REFINE LOOP =====
    for iteration in range(1, max_iterations + 1):
        print(f"\n{'=' * 50}")
        print(f"  Search Iteration {iteration}/{max_iterations}")
        print(f"  Keywords: {current_keywords}")
        print(f"  Qualified so far: {len(all_qualified)}/{max_results}")
        print(f"{'=' * 50}\n")

        iteration_posts = []

        # Scrape
        if "reddit" in config["sources"]:
            print(f"  [{iteration}.1] Scraping Reddit...")
            try:
                iteration_posts.extend(scrape_reddit(current_keywords, config["subreddits"]))
            except Exception as e:
                print(f"    Reddit scraping failed: {e}")

        if "web" in config["sources"]:
            print(f"\n  [{iteration}.2] Scraping Web...")
            try:
                iteration_posts.extend(scrape_web(current_keywords))
            except Exception as e:
                print(f"    Web scraping failed: {e}")

        total_raw += len(iteration_posts)

        # Dedup against all previously seen URLs
        new_posts = [p for p in iteration_posts if p["url"] not in all_seen_urls]
        for p in new_posts:
            all_seen_urls.add(p["url"])

        print(f"\n  Iteration {iteration}: {len(iteration_posts)} raw -> {len(new_posts)} new unique posts")

        if not new_posts:
            print("  No new posts found. Stopping refinement.")
            break

        # Score only new posts
        print(f"\n  [{iteration}.3] Scoring {len(new_posts)} posts with GPT...")
        scored = score_opportunities(new_posts, model)

        new_qualified = [p for p in scored if p.get("intent_score", 0) >= min_score]
        all_qualified.extend(new_qualified)
        print(f"  Found {len(new_qualified)} qualified ({len(all_qualified)} total)")

        # Enough?
        if len(all_qualified) >= max_results:
            print(f"\n  Reached target of {max_results} qualified posts.")
            break

        # Refine keywords for next iteration
        if iteration < max_iterations:
            print(f"\n  [{iteration}.4] Generating refined keywords...")
            high_scorers = sorted(all_qualified, key=lambda p: p["intent_score"], reverse=True)
            new_keywords = generate_refined_keywords(config["keywords"], high_scorers, model)
            if new_keywords:
                print(f"  Refined keywords: {new_keywords}")
                current_keywords = new_keywords
            else:
                print("  No new keywords generated. Stopping refinement.")
                break
    else:
        print(f"\n  Completed all {max_iterations} iterations.")

    # ===== SORT & TRIM =====
    all_qualified.sort(key=lambda p: p["intent_score"], reverse=True)
    top = all_qualified[:max_results]

    print_stats(total_raw, len(all_seen_urls), all_qualified, top)

    if not top:
        print("  No opportunities found above threshold.")
        sys.exit(0)

    # ===== OUTREACH DRAFTING =====
    print("\n" + "=" * 50)
    print("  Outreach Decision & Drafting")
    print("=" * 50 + "\n")
    drafts = draft_outreach(top, model)

    # ===== SAVE =====
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("output", exist_ok=True)

    csv_path = f"output/opportunities_{ts}.csv"
    json_path = f"output/opportunities_{ts}.json"
    outreach_path = f"output/outreach_drafts_{ts}.csv"

    save_csv(top, csv_path)
    save_json(top, json_path)

    print(f"\n  Saved {len(top)} opportunities:")
    print(f"    {csv_path}")
    print(f"    {json_path}")

    if drafts:
        save_outreach_csv(drafts, outreach_path)
        dm_count = sum(1 for d in drafts if d["action_type"] == "dm")
        comment_count = sum(1 for d in drafts if d["action_type"] == "comment")
        content_count = sum(1 for d in drafts if d["action_type"] == "content_idea")
        print(f"  Saved {len(drafts)} outreach drafts ({dm_count} DMs, {comment_count} comments, {content_count} content ideas):")
        print(f"    {outreach_path}")
    else:
        print("  No posts qualified for outreach drafting.")

    print_top5(top)
    print("Done!\n")


if __name__ == "__main__":
    main()
