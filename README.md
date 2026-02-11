# Social Listener Agent

An AI-powered social listening tool that scrapes Reddit and the web for high-intent posts, scores them using GPT, and drafts personalized outreach messages. Built for marketing and growth teams who want to find and engage with people actively looking for help.

## What It Does

1. **Scrapes** Reddit subreddits and the web (Twitter/X, Quora, forums via DuckDuckGo) for posts matching your keywords
2. **Scores** each post's intent (0-100) using GPT-4o-mini to determine how likely the person needs your services
3. **Refines** search keywords automatically — the agent analyzes what's working and generates better keywords across multiple iterations
4. **Drafts** personalized outreach messages (DMs, comments, or content ideas) for high-scoring posts
5. **Exports** everything as CSV and JSON files ready for your team

The agent **never auto-sends anything**. All outreach drafts are a ready-to-act queue for human review.

## Use Cases

- **EdTech / Education companies** — Find students asking about test prep, admissions, study abroad, scholarships, and visas
- **SaaS & B2B** — Monitor forums and communities for people describing problems your product solves
- **Agencies & Freelancers** — Spot potential clients asking for help in your niche
- **Community managers** — Track relevant discussions across platforms

The default configuration targets the education/study-abroad space (GRE, TOEFL, scholarships, student visas), but you can customize keywords, subreddits, and scoring prompts for any domain.

## Architecture

```
agent.py          → CLI entry point — runs the full pipeline
ui.py             → Streamlit web dashboard — same pipeline with a UI
scorer.py         → GPT-based intent scoring + keyword refinement
outreach.py       → Outreach decision logic + GPT draft generation
scrapers/
  reddit_scraper.py  → Reddit API scraper (PRAW)
  web_scraper.py     → DuckDuckGo web scraper (ddgs)
config.yaml       → Keywords, subreddits, thresholds, model config
output/           → Generated CSV/JSON files (gitignored)
```

### Agent Flow

```
Search (Reddit + Web)
  → Deduplicate
  → Score intent with GPT
  → Enough results? If not → Refine keywords → Search again (up to N iterations)
  → Rank by intent score
  → Decide outreach action (DM / Comment / Content idea / Skip)
  → Draft personalized messages with GPT
  → Save CSV + JSON
```

## Prerequisites

- Python 3.10+
- Reddit API credentials — [create a Reddit app here](https://www.reddit.com/prefs/apps)
- OpenAI API key — [get one here](https://platform.openai.com/api-keys)

## Setup

1. **Clone the repo:**
   ```bash
   git clone https://github.com/Anuraagmortha/social-listener-agent.git
   cd social-listener-agent
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate        # Linux/Mac
   venv\Scripts\activate           # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables:**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` with your actual credentials:
   ```
   REDDIT_CLIENT_ID=your_reddit_client_id
   REDDIT_CLIENT_SECRET=your_reddit_client_secret
   REDDIT_USERNAME=your_reddit_username
   REDDIT_PASSWORD=your_reddit_password
   OPENAI_API_KEY=your_openai_api_key
   ```

5. **Customize config (optional):**

   Edit `config.yaml` to change keywords, subreddits, scoring thresholds, etc.

## Usage

### CLI Mode

```bash
python agent.py
```

Runs the full pipeline in the terminal and saves results to `output/`.

### Web Dashboard (Streamlit)

```bash
streamlit run ui.py
```

Opens an interactive dashboard where you can:
- Edit keywords and subreddits from the sidebar
- Toggle Reddit/Web sources on or off
- Adjust intent score thresholds and max results
- View results in tabbed tables (Opportunities, Outreach Drafts, Stats)
- Download CSVs directly from the browser
- Load and review past runs

## Configuration

All settings live in `config.yaml`:

| Key | Description | Default |
|---|---|---|
| `keywords` | Search terms to find relevant posts | GRE, TOEFL, study abroad, etc. |
| `subreddits` | Reddit communities to scrape | indianstudents, GRE, studyAbroad, etc. |
| `sources` | Enabled scrapers (`reddit`, `web`) | Both enabled |
| `min_intent_score` | Minimum score (0-100) to include in results | `25` |
| `max_results` | Number of top opportunities to keep | `20` |
| `max_refinement_iterations` | How many search-refine loops to run | `3` |
| `openai_model` | OpenAI model for scoring and drafting | `gpt-4o-mini` |

## Output

Results are saved to `output/` with timestamps:

- `opportunities_YYYYMMDD_HHMMSS.csv` — Scored opportunities
- `opportunities_YYYYMMDD_HHMMSS.json` — Full data with all fields
- `outreach_drafts_YYYYMMDD_HHMMSS.csv` — Generated outreach messages

### Opportunity CSV Columns

| Column | Description |
|---|---|
| `source_platform` | reddit, twitter, quora, or web |
| `url` | Link to the original post |
| `title_snippet` | Post title + first 100 chars of content |
| `topic_label` | GRE, TOEFL, study_abroad, scholarships, visa, loans, admits, general_education |
| `intent_score` | 0-100 (higher = stronger buying/help-seeking intent) |
| `recommended_action` | comment, DM, or content |
| `suggested_response` | AI-drafted response for engagement |
| `why_this_matters` | One-line explanation of the opportunity |

### Outreach Decision Logic

| Condition | Action |
|---|---|
| Intent >= 80 & recommended "DM" | Draft a personalized DM |
| Intent >= 70 & recommended "comment" | Draft a helpful comment |
| Intent >= 60 & recommended "content" | Note as a content idea |
| Below thresholds | No outreach drafted |

## Intent Scoring Guide

| Score Range | Meaning |
|---|---|
| 80-100 | Actively seeking help, asking specific questions, expressing frustration |
| 50-79 | Discussing the topic, sharing experience, might benefit from guidance |
| 25-49 | Tangentially related, general discussion |
| 0-24 | Not relevant, spam, or already resolved |
