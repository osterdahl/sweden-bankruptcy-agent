# Swedish Bankruptcy Monitor

**Automated bankruptcy monitoring for Swedish companies using TIC.io open data.**

Scrapes TIC.io, scores filings for data-asset acquisition relevance, stores results in SQLite, and surfaces a Streamlit dashboard with an outreach queue and AI-powered asset search.

## Features

- 📊 **Complete data**: Company, trustee contact, SNI code, financials — all from one source
- 🆓 **100% free source**: Scrapes TIC.io public open data, no API key required
- 🗄️ **Persistent SQLite DB**: Delta scraping — only new records are processed each run
- 🎯 **AI scoring**: Rule-based + optional LLM hybrid, prioritises HIGH/MEDIUM/LOW
- 📊 **Streamlit dashboard**: Browse all records, approve/reject outreach emails, AI asset search
- 📧 **Outreach queue**: Stage emails for trustees, review & edit before sending via Mailgun
- 🔍 **AI Asset Search**: Natural-language queries against the full record set ("source code", "companies with >1M SEK assets")
- ⚡ **Incremental scraping**: Stops pagination as soon as all cached records are found

## Quick Start

### Prerequisites

```bash
pip install -r requirements.txt
```

### Configuration

Create a `.env` file (see `.env.example`):

```bash
# Required for the email report
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-gmail-app-password
RECIPIENT_EMAILS=recipient@example.com

# Required for Streamlit outreach (optional feature)
MAILGUN_OUTREACH_ENABLED=true
MAILGUN_DOMAIN=mg.yourdomain.com
MAILGUN_API_KEY=key-...
MAILGUN_FROM_EMAIL=you@yourdomain.com

# Optional: AI scoring
AI_SCORING_ENABLED=true
AI_PROVIDER=anthropic          # or openai
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
# AI_MODEL=claude-haiku-4-5-20251001  # or gpt-4o-mini

# Optional: trustee email lookup
BRAVE_API_KEY=BSA...

# Optional: filters for the email report
FILTER_REGIONS=Stockholm,Göteborg
FILTER_MIN_EMPLOYEES=5      # default: 5
FILTER_MIN_REVENUE=1000000  # default: 1 000 000 SEK
```

### Run the scraper

```bash
# Scrape current month, send email report
python bankruptcy_monitor.py

# Dry run — print report, no email
NO_EMAIL=true python bankruptcy_monitor.py

# Specific month
YEAR=2025 MONTH=12 python bankruptcy_monitor.py
```

### Run the dashboard

```bash
streamlit run dashboard.py
```

## Architecture

**Four Python files + SQLite:**

| File | Purpose |
|------|---------|
| `bankruptcy_monitor.py` | Scraper, scoring, email report |
| `scheduler.py` | SQLite schema, dedup, backfill helpers |
| `dashboard.py` | Streamlit UI — overview, outreach queue, asset search |
| `outreach.py` | Mailgun outreach — staging, approval, sending |

**Database**: `data/bankruptcies.db` (SQLite)
- `bankruptcy_records` — all scraped filings (deduped by org + date)
- `outreach_log` — per-email send/approve/reject state
- `opt_out` — unsubscribe list

## AI Scoring

### How It Works

1. **Rule-based** (always runs, free): SNI code → company size → keyword → score 1–10
2. **AI validation** (optional): LLM scores all records when `AI_SCORING_ENABLED=true`
3. **Priority assignment**: HIGH (≥8), MEDIUM (5–7), LOW (1–4)

### SNI codes scored HIGH

| SNI | Industry | Asset types |
|-----|----------|-------------|
| 58 | Publishing | media |
| 59 | Film/video/sound | media |
| 62 | Computer programming | code |
| 63 | Information services | database |
| 72 | Scientific R&D | database, sensor |
| 26 | Computer/electronic mfg | code |
| 71 | Architectural/engineering | cad |

### Configuration

```bash
AI_SCORING_ENABLED=true          # Enable hybrid scoring (default: false)
AI_PROVIDER=anthropic            # or openai
ANTHROPIC_API_KEY=sk-ant-...
AI_RATE_DELAY=0.5                # Seconds between API calls (default: 0.5)
```

## AI Asset Search (Dashboard)

The **🔍 Asset Search** tab in the dashboard sends natural-language queries against all ~1000+ records using batched LLM calls.

Example queries:
- `"source code"` — software companies, gaming, SaaS, tech consulting
- `"sensor data or robotics"` — R&D, manufacturing, engineering
- `"CAD drawings and technical specs"` — architectural, engineering firms
- `"companies with above 1 million SEK assets and 10 or more employees"` — size filter
- `"media rights and image libraries"` — photography, publishing, film

Results are scored 0–10 for relevance; anything ≥ 4 is shown, sorted by score. Companies can be staged for outreach directly from search results.

Requires `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

## Outreach Queue

The **Outreach Queue** tab shows pending outreach emails staged by the scraper or from the asset search. Each email can be reviewed, the subject/body edited, then approved or rejected. Approved emails are sent in batch via Mailgun.

Template is in `outreach_template.md` — edit the subject/body there. Supports `{{company_name}}` and `{{trustee_name}}` placeholders.

## Data Coverage

| Field | Coverage | Stored as |
|-------|----------|-----------|
| Company Name | 100% | TEXT |
| Org Number | 100% | TEXT |
| Initiated Date | 100% | TEXT |
| Court | 95% | TEXT |
| Region | 100% | TEXT |
| SNI Code | 95% | TEXT |
| Industry Name | 95% | TEXT |
| Trustee Name | 100% | TEXT |
| Trustee Firm | 100% | TEXT |
| Trustee Address | 100% | TEXT |
| Employees | ~70% | INTEGER |
| Net Sales | ~70% | INTEGER (SEK) |
| Total Assets | ~70% | INTEGER (SEK) |

## Filtering (email report)

```bash
FILTER_REGIONS=Stockholm,Göteborg      # only these regions
FILTER_INCLUDE_KEYWORDS=IT,konsult     # match company name or industry
FILTER_MIN_EMPLOYEES=5                  # default: 5 (set 0 to disable)
FILTER_MIN_REVENUE=1000000              # default: 1M SEK (set 0 to disable)
```

## GitHub Actions

Automated monthly runs via `.github/workflows/` — runs on the 1st of each month at 06:00 UTC. Can be triggered manually.

Required secrets: `SENDER_EMAIL`, `SENDER_PASSWORD`, `RECIPIENT_EMAILS`
Optional: `AI_SCORING_ENABLED`, `ANTHROPIC_API_KEY`, `BRAVE_API_KEY`, `FILTER_*`

## Troubleshooting

**No records found**: Check TIC.io site structure hasn't changed; try `YEAR`/`MONTH` override.

**AI search returns nothing**: Confirm `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set.

**Email not sending**: Use Gmail app password, not account password. Enable SMTP access.

**Enable debug logging**: Change `level=logging.INFO` to `level=logging.DEBUG` in `bankruptcy_monitor.py`.

## Data Source

[TIC.io Open Data](https://tic.io/en/oppna-data/konkurser) — The Intelligence Company. Free, public, updated daily. Original data from [Bolagsverket](https://bolagsverket.se/).

## License

MIT
