import time
import random

from ddgs import DDGS


def _classify_source(url: str) -> str:
    if "twitter.com" in url or "x.com" in url:
        return "twitter"
    if "quora.com" in url:
        return "quora"
    if "reddit.com" in url:
        return "reddit"
    return "web"


def _search(query: str, ddgs: DDGS) -> list[dict]:
    """Search DuckDuckGo via the API. Returns list of {url, title, snippet}."""
    for attempt in range(2):
        try:
            results = ddgs.text(query, max_results=10)
            return [
                {
                    "url": r["href"],
                    "title": r["title"],
                    "snippet": r.get("body", ""),
                }
                for r in results
            ]
        except Exception as e:
            print(f"    Search failed (attempt {attempt + 1}): {e}")
            time.sleep(3)
    return []


def scrape_web(keywords: list) -> list[dict]:
    ddgs = DDGS()
    seen_urls = set()
    results = []

    queries_per_keyword = [
        '{kw} site:twitter.com OR site:x.com',
        '{kw} site:quora.com',
        '{kw} forum OR discussion',
    ]

    for kw in keywords:
        for query_template in queries_per_keyword:
            query = query_template.format(kw=kw)
            print(f"  Searching: {query[:60]}...")
            raw = _search(query, ddgs)
            print(f"    -> {len(raw)} results")

            for item in raw:
                if item["url"] in seen_urls:
                    continue
                seen_urls.add(item["url"])
                results.append({
                    "source": _classify_source(item["url"]),
                    "url": item["url"],
                    "title": item["title"],
                    "snippet": item["snippet"][:500],
                    "subreddit": "",
                    "score": 0,
                    "num_comments": 0,
                    "created_utc": None,
                    "author": "",
                })

            time.sleep(random.uniform(2, 4))

    print(f"  Web: collected {len(results)} posts")
    return results
