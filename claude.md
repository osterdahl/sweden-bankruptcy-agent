# Swedish Bankruptcy Monitor

## Philosophy
**Radical simplicity.** Minimal files, minimal dependencies, complete data, zero CAPTCHA, zero complexity.
**strict code reviews** once you are finished, always summon a panel of experts to review code you have written and take action to fix bugs, simplify and delete dead code before considering a task to be finished.

## Architecture

| File | Lines | Purpose |
|------|-------|---------|
| `bankruptcy_monitor.py` | ~1310 | Scraper, parsers, scoring, email report |
| `scheduler.py` | ~400 | SQLite schema, dedup, backfill score/email helpers |
| `dashboard.py` | ~695 | Streamlit UI — overview table, outreach queue, AI asset search |
| `outreach.py` | ~420 | Mailgun outreach staging, approval workflow, sending |

**Database**: `data/bankruptcies.db` (SQLite, persistent across runs)
- `bankruptcy_records` — all filings, deduped by (org_number, initiated_date, trustee_email)
- `outreach_log` — per-email send/approve/reject state
- `opt_out` — unsubscribe list

**Dependencies**: `requests`, `beautifulsoup4`, `streamlit`, `pandas`, `anthropic`, `openai`, `python-dotenv`, `apscheduler`

## Workflow
1. Scrape TIC.io open data for monthly bankruptcies
2. Deduplicate against SQLite (delta only — cached records stop pagination)
3. Score all new records (rule-based always; LLM when `AI_SCORING_ENABLED=true`)
4. Stage outreach emails for HIGH/MEDIUM records
5. Email HTML report with plain text fallback
6. Dashboard: browse records, approve outreach emails, run AI asset search

## Code Structure — bankruptcy_monitor.py

```
Lines 1-45:    Imports, logging, financial parsers (_parse_sek, _parse_headcount)
Lines 46-104:  Data model (BankruptcyRecord dataclass — employees/net_sales/total_assets are Optional[int])
Lines 106-265: Scraper (HTTP + BeautifulSoup, delta pagination, _parse_card)
Lines 269-600: Trustee email lookup (Advokatsamfundet + Brave Search API)
Lines 601-638: Filtering (region, keywords, size — all integer comparisons)
Lines 641-880: AI Scoring (rule-based SNI → size → keywords; LLM validation)
Lines 882-1185: Email (HTML template + plain text, 3 priority sections)
Lines 1186-1220: SMTP email sending
Lines 1222-end: Main (orchestration, month logic, dedup, score, outreach, email)
```

## Key Patterns

### TIC.io Data Structure
- Bankruptcy cards: `.bankruptcy-card`
- Company name: `.bankruptcy-card__name a`
- Org number: `.bankruptcy-card__org-number`
- SNI code: `.bankruptcy-card__sni-code`
- Industry: `.bankruptcy-card__sni-name`
- Trustee: `.bankruptcy-card__trustee-name`
- Firm: `.bankruptcy-card__trustee-company`
- Address: `.bankruptcy-card__trustee-address`
- Financials: `.bankruptcy-card__financial-item`

### Financial Fields
`employees`, `net_sales`, `total_assets` are stored as `Optional[int]` (INTEGER in SQLite).
- `net_sales` and `total_assets` are in SEK (e.g. "134 TSEK" → 134 000 as integer).
- Use `is not None` to check presence, never `!= 'N/A'`.
- Parsers: `_parse_sek(val)` and `_parse_headcount(val)` — safe, never raise.

### Common Tasks

**Modify email HTML styling**: CSS is inline in `format_email_html()` and loaded from `email_template.html`

**Adjust TIC.io selectors** (`_parse_card`, lines ~110–178): update CSS selectors if TIC.io changes HTML

**Add filtering logic** (`filter_records`, lines ~605–638): env-var driven, uses integer comparisons

## AI Scoring System

### High-Value SNI Codes
- 58/59/60: Publishing, film, broadcasting — **media**
- 62: Computer programming/consultancy — **code**
- 63: Information services — **database**
- 72: Scientific R&D — **database, sensor**
- 26: Computer/electronic mfg — **code**
- 71: Architectural/engineering — **cad**
- 742: Photography — **media**

### Scoring Flow
1. **Rule-based** (all records): SNI code → company size → keywords → score 1–10
2. **LLM scoring** (when `AI_SCORING_ENABLED=true`): all records scored via Claude/OpenAI
3. **Priority**: HIGH (≥8), MEDIUM (5–7), LOW (1–4)

### AI Asset Search (dashboard)
- **One API call**: AI converts the natural-language query to a SQL WHERE clause
- Executes the WHERE clause directly against `bankruptcy_records`, returns up to 200 results
- Generated SQL is shown below the results for transparency / debugging
- Results sorted by `ai_score DESC`, includes all financial columns

## Configuration

### Required (email report)
- `SENDER_EMAIL`, `SENDER_PASSWORD`, `RECIPIENT_EMAILS`

### Optional — Outreach
- `MAILGUN_OUTREACH_ENABLED=true`, `MAILGUN_DOMAIN`, `MAILGUN_API_KEY`, `MAILGUN_FROM_EMAIL`
- `MAILGUN_LIVE=true` — actually send emails (default: dry-run)

### Optional — Filtering
- `FILTER_REGIONS` - Comma-separated (e.g., "Stockholm,Göteborg")
- `FILTER_INCLUDE_KEYWORDS` - Match company name or industry
- `FILTER_MIN_EMPLOYEES` - Default: 5 (set to 0 to disable)
- `FILTER_MIN_REVENUE` - Default: 1000000 SEK (set to 0 to disable)
- `YEAR`, `MONTH` - Override auto-detection
- `NO_EMAIL=true` - Dry run

### Optional — AI
- `AI_SCORING_ENABLED=true` - Enable LLM scoring (default: false)
- `AI_PROVIDER=anthropic` or `openai`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
- `AI_MODEL` - Model override
- `AI_RATE_DELAY=0.5` - Seconds between scoring calls

### Optional — Email Lookup
- `BRAVE_API_KEY` - Trustee email lookup via Brave Search + Advokatsamfundet

## Known Limitations
1. Court data sometimes missing (95% coverage)
2. Gmail dependency for email report sending
3. Financial data present in ~70% of records

## Coding Principles
1. No over-engineering — don't add features "just in case"
2. No abstractions for one-time operations
3. Delete unused code completely (don't comment out)
4. Trust internal data, validate only at boundaries
5. Update README.md and claude.md after changes

## Before Adding Features
Ask:
1. Does this solve a real problem?
2. Can it be done with less code?
3. Assemble a panel of experts and ask for honest opinions

**Rule**: If a feature adds >50 lines, think hard before adding it.
