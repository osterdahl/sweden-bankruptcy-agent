# Nordic Bankruptcy Monitor

**Automated bankruptcy monitoring across Sweden, Norway, Denmark, and Finland.**

Scrapes official data sources monthly, scores companies by acquisition potential, and sends HTML email reports with trustee contact details.

## Features

- **Multi-country**: Sweden (TIC.io), Norway (brreg.no), Denmark (Statstidende + CVR), Finland (PRH)
- **Complete data**: Company, trustee contact, industry code, financials — all fields
- **Free data sources**: All sources are public open data — no API keys required for scraping
- **Smart filtering**: By region, keywords, employee count, revenue
- **AI scoring** (optional): Hybrid HIGH/MEDIUM/LOW prioritisation via Claude API
- **Trustee email lookup**: Country-specific lawyer directories + Brave Search fallback
- **GitHub Actions**: Automated monthly runs, no infrastructure required
- **Deduplication**: SQLite cache prevents re-reporting the same bankruptcies

## Quick Start

### Install

```bash
pip install -r requirements.txt
```

### Configure

Create `.env`:

```bash
# Required — email delivery
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password   # Gmail app password, not account password
RECIPIENT_EMAILS=recipient@example.com

# Optional — which countries to monitor (default: se)
COUNTRIES=se

# Optional — filters (defaults shown)
FILTER_MIN_EMPLOYEES=5
FILTER_MIN_REVENUE=1000000         # In local currency
FILTER_REGIONS=Stockholm,Göteborg  # Leave blank for all regions
FILTER_INCLUDE_KEYWORDS=           # Leave blank for all industries
```

### Run

```bash
# Sweden only, auto-detect last month, send email
python run.py

# Multiple countries
python run.py --countries se,no,dk,fi

# Dry run — print report, skip email
python run.py --no-email

# Specific period
python run.py --year 2026 --month 1

# With AI scoring (requires ANTHROPIC_API_KEY)
python run.py --ai

# All options
python run.py --help
```

## CLI Reference

```
python run.py [OPTIONS]

  --countries CODES     Comma-separated country codes: se,no,dk,fi
                        Default: COUNTRIES env var, or 'se'
  --year YYYY           Year to process. Default: auto-detect
  --month 1-12          Month to process. Default: auto-detect
  --no-email            Dry run — print report, save HTML to /tmp/
  --ai                  Enable AI scoring via Claude API

  --filter-regions      Comma-separated regions (e.g. Stockholm,Oslo)
  --filter-keywords     Comma-separated keywords (e.g. IT,tech,consulting)
  --min-employees N     Minimum employee count (default: 5)
  --min-revenue N       Minimum revenue in local currency (default: 1000000)
```

All options can also be set via environment variables (CLI args take precedence).

## AI Scoring (Optional)

Prioritises bankruptcies by data acquisition potential.

### How It Works

1. **Rule-based** (free, fast) — NACE industry code analysis, employee/revenue thresholds, keyword detection
2. **AI validation** (optional) — Claude API refines the top ~10–15% of candidates; provides reasoning per company

Cost with AI enabled: ~$0.60–0.90/month for typical usage.

### Email Sections

- **HIGH PRIORITY** — Data-rich companies (tech, SaaS, consulting, R&D)
- **MEDIUM PRIORITY** — Moderate acquisition potential
- **LOW PRIORITY** — Limited data assets expected

```bash
AI_SCORING_ENABLED=true
ANTHROPIC_API_KEY=sk-ant-...
```

## Data Sources

| Country | Source | Method | Trustee info |
|---------|--------|--------|--------------|
| Sweden | [TIC.io](https://tic.io/en/oppna-data/konkurser) | HTTP + BeautifulSoup | Direct (100%) |
| Norway | [brreg.no](https://data.brreg.no) | REST API (JSON) | Via court announcements |
| Denmark | [Statstidende](https://statstidende.dk) + [CVR](https://virk.dk/cvr) | Scraping + API | Via Statstidende notices |
| Finland | [PRH](https://avoindata.prh.fi) | REST API (JSON) | Via PRH announcements |

All sources are public open data — no authentication required.

## Swedish Data Coverage (TIC.io)

| Field | Coverage |
|-------|----------|
| Company name, org number, date | 100% |
| Region, court | 95–100% |
| Industry code (SNI/NACE) | 95% |
| Trustee name, firm, address | 100% |
| Employees, net sales, total assets | 70% |

## GitHub Actions

Automated monthly runs — no server required.

### Schedule

Runs automatically on the 1st of each month at 6 AM UTC.

### Required Secrets

Set in GitHub repository → Settings → Secrets and variables → Actions:

```
SENDER_EMAIL           Gmail address
SENDER_PASSWORD        Gmail app password
RECIPIENT_EMAILS       Comma-separated recipient emails
```

### Optional Secrets

```
COUNTRIES              Comma-separated country codes (default: se)
AI_SCORING_ENABLED     Set to 'true' to enable AI scoring
ANTHROPIC_API_KEY      Required if AI scoring enabled
LOOKUP_TRUSTEE_EMAIL   Set to 'true' to enable trustee email lookup
BRAVE_API_KEY          Required if trustee email lookup enabled
FILTER_REGIONS         Comma-separated regions
FILTER_INCLUDE_KEYWORDS Comma-separated keywords
FILTER_MIN_EMPLOYEES   Minimum employee count
FILTER_MIN_REVENUE     Minimum revenue in local currency
```

### Manual Trigger

Actions → Monthly Bankruptcy Report → Run workflow:

- **Countries**: Which countries to process (e.g. `se,no`)
- **Year / Month**: Process a specific period
- **Enable AI scoring**: Override secret setting for this run
- **Skip email**: Dry run mode

## Filtering

```bash
# Only specific regions
FILTER_REGIONS=Stockholm,Göteborg,Oslo

# Only specific industries
FILTER_INCLUDE_KEYWORDS=IT,tech,consulting,data

# Size thresholds
FILTER_MIN_EMPLOYEES=10
FILTER_MIN_REVENUE=5000000   # 5M in local currency

# Disable size filters
FILTER_MIN_EMPLOYEES=0
FILTER_MIN_REVENUE=0
```

## Architecture

```
run.py                          # Entry point (CLI args + plugin registration)
core/
  pipeline.py                   # Orchestrator: scrape → dedup → score → email
  models.py                     # BankruptcyRecord dataclass
  scoring.py                    # Rule-based + AI scoring engine
  email_lookup.py               # Brave Search + regex email helpers
  reporting.py                  # HTML/plain email formatting
  database.py                   # SQLite layer (multi-country aware)
countries/
  protocol.py                   # CountryPlugin Protocol definition
  __init__.py                   # Country registry
  sweden.py                     # SE: TIC.io scraper
  norway.py                     # NO: brreg.no API
  denmark.py                    # DK: Statstidende + CVR
  finland.py                    # FI: PRH open data API
templates/
  outreach_se.md                # Swedish outreach template
  outreach_no.md / dk / fi      # English outreach templates
scheduler.py                    # APScheduler entry point (local use)
dashboard.py                    # Streamlit dashboard (multi-country)
outreach.py                     # Mailgun outreach staging
bankruptcy_monitor.py           # Legacy Sweden-only entry point (still works)
```

Each country is a self-contained plugin module. Adding a new country means adding one file to `countries/` and one import in `run.py`.

## Gmail Setup

1. Enable 2-factor authentication on your Google account
2. Generate an app password: <https://myaccount.google.com/apppasswords>
3. Use the app password as `SENDER_PASSWORD` (not your account password)

## Troubleshooting

**No bankruptcies found**
- The data source may not have data for that month yet — try the previous month
- Filters may be too restrictive — try `--no-email` with `--min-employees 0 --min-revenue 0`

**Email not sending**
- Use a Gmail app password, not your account password
- Verify SMTP access is not blocked by your Google account settings

**Country not processing**
- Ensure the country code is in `COUNTRIES` (e.g. `COUNTRIES=se,no`)
- Check logs for scraping errors from that country's data source

## License

MIT

## Credits

- Sweden: [TIC.io Open Data](https://tic.io/en/oppna-data/konkurser) — The Intelligence Company (Jens Nylander) / [Bolagsverket](https://bolagsverket.se/)
- Norway: [Brønnøysundregistrene](https://data.brreg.no)
- Denmark: [Statstidende](https://statstidende.dk) / [Erhvervsstyrelsen CVR](https://virk.dk/cvr)
- Finland: [PRH — Patent and Registration Office](https://avoindata.prh.fi)
