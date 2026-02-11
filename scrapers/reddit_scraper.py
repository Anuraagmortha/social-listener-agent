import os
import time
from datetime import datetime, timezone

import praw
from prawcore.exceptions import (
    Forbidden,
    NotFound,
    Redirect,
    ServerError,
    TooManyRequests,
)


def _has_reddit_credentials():
    required = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD"]
    return all(
        os.environ.get(k) and not os.environ[k].startswith("your_")
        for k in required
    )


def _make_reddit_client():
    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        username=os.environ["REDDIT_USERNAME"],
        password=os.environ["REDDIT_PASSWORD"],
        user_agent="social-listening-agent/1.0",
    )


def _is_within_days(created_utc, days=7):
    now = datetime.now(timezone.utc)
    post_time = datetime.fromtimestamp(created_utc, tz=timezone.utc)
    return (now - post_time).days <= days


def _submission_to_dict(submission):
    return {
        "source": "reddit",
        "url": f"https://reddit.com{submission.permalink}",
        "title": submission.title,
        "snippet": (submission.selftext or "")[:500],
        "subreddit": submission.subreddit.display_name,
        "score": submission.score,
        "num_comments": submission.num_comments,
        "created_utc": submission.created_utc,
        "author": str(submission.author),
    }


def scrape_reddit(keywords: list, subreddits: list) -> list[dict]:
    if not _has_reddit_credentials():
        print("  Reddit credentials not configured — skipping. Add them to .env when ready.")
        return []

    reddit = _make_reddit_client()
    seen_urls = set()
    results = []

    for sub_name in subreddits:
        print(f"  Scraping r/{sub_name}...")
        try:
            subreddit = reddit.subreddit(sub_name)

            # Search by keywords
            for kw in keywords:
                try:
                    for submission in subreddit.search(kw, sort="new", time_filter="week", limit=5):
                        if not _is_within_days(submission.created_utc):
                            continue
                        post = _submission_to_dict(submission)
                        if post["url"] not in seen_urls:
                            seen_urls.add(post["url"])
                            results.append(post)
                except Exception as e:
                    print(f"    Warning: search '{kw}' in r/{sub_name} failed: {e}")
                time.sleep(1)

            # Hot posts
            try:
                for submission in subreddit.hot(limit=10):
                    if not _is_within_days(submission.created_utc):
                        continue
                    post = _submission_to_dict(submission)
                    if post["url"] not in seen_urls:
                        seen_urls.add(post["url"])
                        results.append(post)
            except Exception as e:
                print(f"    Warning: hot posts in r/{sub_name} failed: {e}")
            time.sleep(1)

            # New posts
            try:
                for submission in subreddit.new(limit=10):
                    if not _is_within_days(submission.created_utc):
                        continue
                    post = _submission_to_dict(submission)
                    if post["url"] not in seen_urls:
                        seen_urls.add(post["url"])
                        results.append(post)
            except Exception as e:
                print(f"    Warning: new posts in r/{sub_name} failed: {e}")
            time.sleep(1)

        except (NotFound, Forbidden, Redirect) as e:
            print(f"    Skipping r/{sub_name}: {e}")
        except TooManyRequests:
            print(f"    Rate limited on r/{sub_name}, waiting 10s...")
            time.sleep(10)
        except ServerError as e:
            print(f"    Reddit server error on r/{sub_name}: {e}")
        except Exception as e:
            print(f"    Unexpected error on r/{sub_name}: {e}")

    print(f"  Reddit: collected {len(results)} posts")
    return results
