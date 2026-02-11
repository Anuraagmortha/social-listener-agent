"""
Streamlit UI for Social Listening Opportunity Agent
Run: streamlit run ui.py
"""

import glob
import io
import os
import sys
from collections import Counter
from contextlib import redirect_stdout
from datetime import datetime

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

from outreach import draft_outreach, save_outreach_csv
from scorer import generate_refined_keywords, score_opportunities
from scrapers.reddit_scraper import scrape_reddit
from scrapers.web_scraper import scrape_web

load_dotenv()

st.set_page_config(page_title="Galvanize Social Listener", layout="wide")

# ── Load config from disk as defaults ────────────────────────────
@st.cache_data
def load_default_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)

defaults = load_default_config()

# ── Sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    st.header("Configuration")

    keywords_text = st.text_area(
        "Keywords (one per line)",
        value="\n".join(defaults["keywords"]),
        height=130,
    )
    subreddits_text = st.text_area(
        "Subreddits (one per line)",
        value="\n".join(defaults["subreddits"]),
        height=150,
    )

    col1, col2 = st.columns(2)
    with col1:
        use_reddit = st.checkbox("Reddit", value="reddit" in defaults["sources"])
    with col2:
        use_web = st.checkbox("Web", value="web" in defaults["sources"])

    min_score = st.slider("Min intent score", 0, 100, defaults["min_intent_score"])
    max_results = st.number_input("Max results", 1, 100, defaults["max_results"])
    max_iterations = st.number_input("Max search iterations", 1, 5, defaults.get("max_refinement_iterations", 3))

    st.divider()
    run_btn = st.button("Run Agent", type="primary", use_container_width=True)

    # Past runs
    st.divider()
    st.subheader("Past Runs")
    past_files = sorted(glob.glob("output/opportunities_*.csv"), reverse=True)
    past_selection = None
    if past_files:
        labels = [os.path.basename(f).replace("opportunities_", "").replace(".csv", "") for f in past_files]
        past_selection = st.selectbox("Load a previous run", ["(none)"] + labels)
    else:
        st.caption("No past runs found in output/")


# ── Helper: build config dict from sidebar values ────────────────
def build_config():
    sources = []
    if use_reddit:
        sources.append("reddit")
    if use_web:
        sources.append("web")
    return {
        "keywords": [k.strip() for k in keywords_text.strip().splitlines() if k.strip()],
        "subreddits": [s.strip() for s in subreddits_text.strip().splitlines() if s.strip()],
        "sources": sources,
        "min_intent_score": min_score,
        "max_results": max_results,
        "max_refinement_iterations": max_iterations,
        "openai_model": defaults["openai_model"],
    }


# ── Helper: save CSVs to disk and return bytes for download ──────
def opportunities_to_csv_bytes(posts):
    df = pd.DataFrame([
        {
            "source_platform": p.get("source", ""),
            "url": p.get("url", ""),
            "title_snippet": (p.get("title", "") + " " + (p.get("snippet", "") or "")[:100]).strip(),
            "topic_label": p.get("topic_label", ""),
            "intent_score": p.get("intent_score", 0),
            "recommended_action": p.get("recommended_action", ""),
            "suggested_response": p.get("suggested_response", ""),
            "why_this_matters": p.get("why_this_matters", ""),
        }
        for p in posts
    ])
    return df, df.to_csv(index=False).encode("utf-8")


def drafts_to_csv_bytes(drafts):
    df = pd.DataFrame(drafts)
    return df, df.to_csv(index=False).encode("utf-8")


# ── Helper: display results ──────────────────────────────────────
def show_results(opp_df, drafts_df, stats, logs):
    tab1, tab2, tab3 = st.tabs(["Opportunities", "Outreach Drafts", "Stats"])

    with tab1:
        if opp_df is not None and not opp_df.empty:
            st.dataframe(
                opp_df,
                use_container_width=True,
                column_config={
                    "url": st.column_config.LinkColumn("URL"),
                    "intent_score": st.column_config.ProgressColumn(
                        "Intent Score", min_value=0, max_value=100, format="%d"
                    ),
                },
                hide_index=True,
            )
        else:
            st.info("No opportunities found.")

    with tab2:
        if drafts_df is not None and not drafts_df.empty:
            st.dataframe(
                drafts_df,
                use_container_width=True,
                column_config={"url": st.column_config.LinkColumn("URL")},
                hide_index=True,
            )
        else:
            st.info("No outreach drafts generated.")

    with tab3:
        if stats:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Scraped", stats["total_raw"])
            c2.metric("After Dedup", stats["after_dedup"])
            c3.metric("Above Threshold", stats["qualified_count"])
            c4.metric("Avg Intent Score", f"{stats['avg_score']:.1f}" if stats["avg_score"] else "N/A")

            if stats.get("topics"):
                st.subheader("Topic Distribution")
                st.bar_chart(pd.Series(stats["topics"]))
            if stats.get("actions"):
                st.subheader("Action Distribution")
                st.bar_chart(pd.Series(stats["actions"]))
        else:
            st.info("Run the agent to see stats.")

    if logs:
        with st.expander("Agent Logs"):
            st.code(logs, language="text")


# ── Main: Run Agent ──────────────────────────────────────────────
if run_btn:
    config = build_config()
    if not config["keywords"]:
        st.error("Add at least one keyword.")
        st.stop()
    if not config["sources"]:
        st.error("Enable at least one source (Reddit or Web).")
        st.stop()

    log_buffer = io.StringIO()
    model = config["openai_model"]
    all_seen_urls = set()
    all_qualified = []
    total_raw = 0
    current_keywords = list(config["keywords"])

    with st.status("Running agent...", expanded=True) as status:

        # ── Iterative search-refine loop ──
        for iteration in range(1, config["max_refinement_iterations"] + 1):
            st.write(f"**Iteration {iteration}/{config['max_refinement_iterations']}** — {len(all_qualified)}/{config['max_results']} qualified so far")
            iteration_posts = []

            # Scrape Reddit
            if "reddit" in config["sources"]:
                st.write(f"Scraping Reddit ({len(config['subreddits'])} subreddits)...")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    try:
                        iteration_posts.extend(scrape_reddit(current_keywords, config["subreddits"]))
                    except Exception as e:
                        st.warning(f"Reddit scraping failed: {e}")
                log_buffer.write(buf.getvalue())

            # Scrape Web
            if "web" in config["sources"]:
                st.write("Scraping Web (DuckDuckGo)...")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    try:
                        iteration_posts.extend(scrape_web(current_keywords))
                    except Exception as e:
                        st.warning(f"Web scraping failed: {e}")
                log_buffer.write(buf.getvalue())

            total_raw += len(iteration_posts)
            new_posts = [p for p in iteration_posts if p["url"] not in all_seen_urls]
            for p in new_posts:
                all_seen_urls.add(p["url"])

            st.write(f"Found {len(new_posts)} new unique posts (from {len(iteration_posts)} raw)")

            if not new_posts:
                st.write("No new posts. Stopping.")
                break

            # Score
            st.write(f"Scoring {len(new_posts)} posts with GPT...")
            buf = io.StringIO()
            with redirect_stdout(buf):
                scored = score_opportunities(new_posts, model)
            log_buffer.write(buf.getvalue())

            new_qualified = [p for p in scored if p.get("intent_score", 0) >= config["min_intent_score"]]
            all_qualified.extend(new_qualified)
            st.write(f"**{len(new_qualified)} qualified** this iteration ({len(all_qualified)} total)")

            if len(all_qualified) >= config["max_results"]:
                st.write("Target reached!")
                break

            # Refine keywords
            if iteration < config["max_refinement_iterations"]:
                st.write("Refining keywords...")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    high = sorted(all_qualified, key=lambda p: p["intent_score"], reverse=True)
                    new_kw = generate_refined_keywords(config["keywords"], high, model)
                log_buffer.write(buf.getvalue())
                if new_kw:
                    st.write(f"New keywords: {new_kw}")
                    current_keywords = new_kw
                else:
                    st.write("No new keywords. Stopping.")
                    break

        # Sort & trim
        all_qualified.sort(key=lambda p: p["intent_score"], reverse=True)
        top = all_qualified[: config["max_results"]]

        # Outreach
        drafts = []
        if top:
            st.write("Drafting outreach messages...")
            buf = io.StringIO()
            with redirect_stdout(buf):
                drafts = draft_outreach(top, model)
            log_buffer.write(buf.getvalue())

        # Save to disk
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs("output", exist_ok=True)
        if top:
            from agent import save_csv, save_json

            csv_path = f"output/opportunities_{ts}.csv"
            json_path = f"output/opportunities_{ts}.json"
            save_csv(top, csv_path)
            save_json(top, json_path)
            if drafts:
                save_outreach_csv(drafts, f"output/outreach_drafts_{ts}.csv")

        status.update(label=f"Done — {len(top)} opportunities, {len(drafts)} outreach drafts", state="complete")

    # ── Build stats ──
    stats = {
        "total_raw": total_raw,
        "after_dedup": len(all_seen_urls),
        "qualified_count": len([p for p in all_qualified if p.get("intent_score", 0) >= config["min_intent_score"]]),
        "avg_score": (sum(p["intent_score"] for p in top) / len(top)) if top else 0,
        "topics": dict(Counter(p["topic_label"] for p in top)) if top else {},
        "actions": dict(Counter(p["recommended_action"] for p in top)) if top else {},
    }

    # ── Build dataframes ──
    opp_df = drafts_df = None
    opp_csv = drafts_csv = None
    if top:
        opp_df, opp_csv = opportunities_to_csv_bytes(top)
    if drafts:
        drafts_df, drafts_csv = drafts_to_csv_bytes(drafts)

    show_results(opp_df, drafts_df, stats, log_buffer.getvalue())

    # Download buttons
    dl1, dl2 = st.columns(2)
    if opp_csv:
        dl1.download_button("Download Opportunities CSV", opp_csv, f"opportunities_{ts}.csv", "text/csv")
    if drafts_csv:
        dl2.download_button("Download Outreach Drafts CSV", drafts_csv, f"outreach_drafts_{ts}.csv", "text/csv")


# ── Load past run ────────────────────────────────────────────────
elif past_selection and past_selection != "(none)":
    opp_path = None
    for f in past_files:
        if past_selection in f:
            opp_path = f
            break

    if opp_path:
        st.subheader(f"Past Run: {past_selection}")
        opp_df = pd.read_csv(opp_path)

        drafts_path = opp_path.replace("opportunities_", "outreach_drafts_")
        drafts_df = pd.read_csv(drafts_path) if os.path.exists(drafts_path) else None

        stats = None
        if not opp_df.empty:
            stats = {
                "total_raw": "—",
                "after_dedup": "—",
                "qualified_count": len(opp_df),
                "avg_score": opp_df["intent_score"].mean() if "intent_score" in opp_df.columns else 0,
                "topics": dict(opp_df["topic_label"].value_counts()) if "topic_label" in opp_df.columns else {},
                "actions": dict(opp_df["recommended_action"].value_counts()) if "recommended_action" in opp_df.columns else {},
            }

        show_results(opp_df, drafts_df, stats, "")


# ── Default: Usage Guide ─────────────────────────────────────────
else:
    st.title("Social Listening Agent for Galvanize")
    st.caption("AI-powered opportunity finder — scrapes Reddit & web, scores intent with GPT, drafts outreach messages.")

    st.divider()

    st.subheader("How to Use")
    st.markdown("""
1. **Set up credentials** — Create a `.env` file in the project root with:
   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   REDDIT_USERNAME=...
   REDDIT_PASSWORD=...
   OPENAI_API_KEY=...
   ```
2. **Configure** — Use the sidebar to adjust keywords, subreddits, sources, and thresholds.
3. **Run** — Click **Run Agent** in the sidebar. The agent will:
   - Scrape Reddit and web for posts matching your keywords
   - Score each post's intent using GPT-4o-mini
   - Automatically refine keywords and re-search if needed
   - Draft personalized outreach messages for high-intent posts
4. **Review** — Browse results in the **Opportunities** and **Outreach Drafts** tabs.
5. **Download** — Export CSVs for your marketing team.
""")

    st.subheader("Agent Flow")
    st.code(
        "Search -> Evaluate -> Refine keywords -> Score -> Decide outreach -> Draft messages -> Save\n"
        "  ^                         |                                                          |\n"
        "  |_____ loop until 20 good results __________________________________________________|",
        language="text",
    )

    st.subheader("Outreach Decision Logic")
    st.markdown("""
| Condition | Action |
|---|---|
| Intent >= 80 + recommended "DM" | Draft personalized DM |
| Intent >= 70 + recommended "comment" | Draft helpful comment |
| Intent >= 60 + recommended "content" | Note as content idea |
| Below 60 | No outreach |
""")

    st.info("The agent **never auto-sends** anything. Outreach drafts are a ready-to-act queue for your team.")
