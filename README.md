# Nordic Bankruptcy Monitor

**Automated bankruptcy monitoring across the Nordic region: Sweden, Norway, Denmark, and Finland.**

Scrapes official sources per country, scores filings for data-asset acquisition relevance, stores results in SQLite, and surfaces a Streamlit dashboard with an outreach queue and AI-powered asset search.

## Features

- **Multi-country**: Sweden, Norway, Denmark, Finland — run one or all via `COUNTRIES` env var
- **Free sources**: TIC.io (SE), brreg.no (NO), Statstidende + CVR API (DK), PRH YTJ API (FI)
- **Persistent SQLite DB**: Delta scraping — only new records are processed each run
- **AI scoring**: Rule-based + optional LLM hybrid, prioritises HIGH/MEDIUM/LOW
- **Streamlit dashboard**: Browse all records, approve/reject outreach emails, AI asset search
- **Outreach queue**: Stage emails for trustees, review & edit before sending via Mailgun
- **AI Asset Search**: Natural-language queries converted to SQL against the full record set

## Quick Start

```bash
pip install -r requirements.txt
```

### Run the scraper

```bash
# Sweden only (default)
python bankruptcy_monitor.py

# All Nordic countries
COUNTRIES=se,no,dk,fi python bankruptcy_monitor.py

# Norway only
COUNTRIES=no python bankruptcy_monitor.py

# Dry run — print report, no email
NO_EMAIL=true python bankruptcy_monitor.py

# Specific month
YEAR=2025 MONTH=11 python bankruptcy_monitor.py
```

### Run the dashboard

```bash
streamlit run dashboard.py
```

## All Parameters

### Country selection

| Variable | Default | Description |
|----------|---------|-------------|
| `COUNTRIES` | `se` | Comma-separated ISO codes: `se`, `no`, `dk`, `fi` |

### Month override

| Variable | Description |
|----------|-------------|
| `YEAR` | Override year (e.g. `2025`) |
| `MONTH` | Override month number (e.g. `11`) |

Auto-detect logic: on days 1–7 of a month, defaults to the previous month. Otherwise uses the current month.

### Email report

| Variable | Required | Description |
|----------|----------|-------------|
| `SENDER_EMAIL` | Yes | Gmail address to send from |
| `SENDER_PASSWORD` | Yes | Gmail app password |
| `RECIPIENT_EMAILS` | Yes | Comma-separated recipient addresses |
| `NO_EMAIL` | No | Set `true` to skip sending and print/save instead |

### Outreach (Mailgun)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAILGUN_OUTREACH_ENABLED` | `false` | Enable outreach email staging |
| `MAILGUN_DOMAIN` | — | Your Mailgun domain |
| `MAILGUN_API_KEY` | — | Mailgun API key |
| `MAILGUN_FROM_EMAIL` | — | Sender address for outreach |
| `MAILGUN_LIVE` | `false` | Actually send emails (default: dry-run) |

### Filtering (email report)

| Variable | Default | Description |
|----------|---------|-------------|
| `FILTER_REGIONS` | — | Comma-separated regions to include (e.g. `Stockholm,Göteborg`) |
| `FILTER_INCLUDE_KEYWORDS` | — | Match company name or industry (e.g. `IT,konsult`) |
| `FILTER_MIN_EMPLOYEES` | `5` | Minimum employees (set `0` to disable; skipped if data unavailable) |
| `FILTER_MIN_REVENUE` | `1000000` | Minimum revenue in local currency (set `0` to disable; skipped if data unavailable) |

### AI scoring

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_SCORING_ENABLED` | `false` | Enable LLM scoring on top of rule-based scoring |
| `AI_PROVIDER` | — | `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `AI_MODEL` | — | Model override (e.g. `claude-haiku-4-5-20251001`, `gpt-4o-mini`) |
| `AI_RATE_DELAY` | `0.5` | Seconds between scoring API calls |

### Trustee email lookup

| Variable | Description |
|----------|-------------|
| `BRAVE_API_KEY` | Brave Search API key — fallback for trustee email discovery |

## Architecture

```
bankruptcy_monitor.py   Entry point and orchestration
core/
  pipeline.py           run_all() / run_country() — scrape→dedup→score→lookup→email
  database.py           SQLite schema and helpers
  models.py             BankruptcyRecord dataclass
  scoring.py            Rule-based + LLM scoring
  email_lookup.py       Trustee email lookup (Brave Search fallback)
  reporting.py          HTML + plain-text email formatting
countries/
  __init__.py           Plugin registry (COUNTRY_REGISTRY, get_active_countries)
  protocol.py           CountryPlugin protocol definition
  sweden.py             TIC.io scraper, Advokatsamfundet lookup
  norway.py             brreg.no Enhetsregisteret API
  denmark.py            Statstidende scraper + CVR API enrichment
  finland.py            PRH YTJ Open Data API v3
scheduler.py            SQLite dedup, backfill helpers
dashboard.py            Streamlit UI — overview, outreach queue, AI asset search
outreach.py             Mailgun outreach — staging, approval, sending
```

**Database**: `data/bankruptcies.db` (SQLite)
- `bankruptcy_records` — all scraped filings (deduped by country + org number + date)
- `outreach_log` — per-email send/approve/reject state
- `opt_out` — unsubscribe list

## Country Data Sources

| Country | Source | Auth | Trustee info | Financial data |
|---------|--------|------|--------------|----------------|
| Sweden (SE) | [TIC.io](https://tic.io/en/oppna-data/konkurser) | None | Yes (scraped) | Yes (~70%) |
| Norway (NO) | [brreg.no](https://data.brreg.no/enhetsregisteret/api) | None | No | Employees only |
| Denmark (DK) | [Statstidende](https://www.telestatstidende.dk) + [cvrapi.dk](https://cvrapi.dk) | None | Partial | Employees (midpoint of range, via CVR API) |
| Finland (FI) | [PRH YTJ API](https://avoindata.prh.fi/opendata-ytj-api/v3) | None | No | No |

**Note on Denmark**: The Statstidende scraper HTML selectors have not been validated against the live site and are expected to return zero results until fixed. No CVR API fallback is implemented yet — if Statstidende scraping fails, Denmark returns an empty list. See `countries/denmark.py` TODOs for the planned fallback.

**Note on Finland**: The PRH YTJ API is queried by company registration date, not bankruptcy date. Companies that registered before the target month but entered bankruptcy during it will not appear. `YEAR`/`MONTH` filtering has unreliable semantics for Finland as a result. Trustee information is not available from PRH.

## AI Scoring

### How It Works

1. **Rule-based** (always runs, free): industry code → company size → keywords → score 1–10
2. **LLM scoring** (optional): all records scored via Claude or OpenAI when `AI_SCORING_ENABLED=true`
3. **Priority assignment**: HIGH (≥8), MEDIUM (5–7), LOW (1–4)

### High-Value Industry Codes (NACE / SNI / NACE / TOL 2008)

All four countries use NACE Rev. 2 at the 2-digit level, so the same maps apply.

| Code | Industry | Asset types |
|------|----------|-------------|
| 58 | Publishing | media |
| 59 | Film/video/sound | media |
| 60 | Broadcasting | media |
| 62 | Computer programming | code |
| 63 | Information services | database |
| 72 | Scientific R&D | database, sensor |
| 742 | Photography | media |
| 90 | Creative arts | media |
| 91 | Libraries/archives/museums | media, database |
| 26 | Computer/electronic mfg | code |
| 71 | Architectural/engineering | cad |
| 73 | Advertising/market research | media, database |
| 85 | Education | media |

## AI Asset Search (Dashboard)

The **Asset Search** tab converts a natural-language query to SQL and runs it against all records.

Example queries:
- `"source code"` — software companies, SaaS, tech consulting
- `"sensor data or robotics"` — R&D, manufacturing, engineering
- `"CAD drawings and technical specs"` — architectural, engineering firms
- `"companies with above 1 million assets and 10 or more employees"` — size filter
- `"media rights and image libraries"` — photography, publishing, film

The generated SQL is shown below the results. Requires `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

## Outreach Queue

The **Outreach Queue** tab shows pending emails staged for HIGH/MEDIUM records. Each email can be reviewed and edited, then approved or rejected. Approved emails are sent via Mailgun.

Templates are in `templates/outreach_se.md`, `outreach_no.md`, `outreach_dk.md`, `outreach_fi.md`. Supports `{{company_name}}` and `{{trustee_name}}` placeholders.

## GitHub Actions

Automated monthly runs via `.github/workflows/` — runs on the 1st of each month at 06:00 UTC.

Required secrets: `SENDER_EMAIL`, `SENDER_PASSWORD`, `RECIPIENT_EMAILS`
Optional: `COUNTRIES`, `AI_SCORING_ENABLED`, `ANTHROPIC_API_KEY`, `BRAVE_API_KEY`, `FILTER_*`

## Troubleshooting

**No records for Sweden**: Check if TIC.io HTML structure has changed; try `YEAR`/`MONTH` override.

**No records for Norway**: brreg.no returns all entities currently flagged `konkurs=true` (~3000 records). Date-based filtering is not possible because brreg.no does not expose the konkurs date — `YEAR`/`MONTH` overrides have no effect for Norway. Dedup filters to genuinely new entries only; on the first run expect a large initial import.

**No records for Denmark**: Statstidende scraping has not been fully validated. See `countries/denmark.py` TODOs.

**AI search returns nothing**: Confirm `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set.

**Email not sending**: Use a Gmail app password, not your account password.

**Debug logging**: Change `level=logging.INFO` to `level=logging.DEBUG` in `bankruptcy_monitor.py`.

## License

MIT
