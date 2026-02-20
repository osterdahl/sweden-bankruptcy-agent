# Codebase Audit - Swedish Bankruptcy Monitor

**Date:** 2026-02-18
**Auditor:** automated audit agent
**Scope:** Full codebase review + integration point analysis for three new modules

---

## 1. Project Overview

A single-file Python application that scrapes Swedish bankruptcy filings from TIC.io, optionally scores them with AI, and emails an HTML/plain-text report. Currently runs as a GitHub Actions cron job (1st of each month at 06:00 UTC) or manually via `workflow_dispatch`.

### File Inventory

| File | Purpose |
|------|---------|
| `bankruptcy_monitor.py` (953 lines) | Entire application: scraping, filtering, scoring, email formatting, sending |
| `email_template.html` (301 lines) | HTML template for the email report (loaded via `string.Template`) |
| `requirements.txt` | Two deps: `playwright>=1.40.0`, `anthropic>=0.18.0` |
| `.env.example` | Documents all env vars |
| `.env` | Actual secrets (gitignored) |
| `.github/workflows/monthly-report.yml` | GitHub Actions scheduling + manual dispatch |
| `CLAUDE.md` | Project philosophy and coding conventions |
| `README.md` | User-facing documentation |
| `.gitignore` | Standard Python + env + playwright ignores |

**No tests exist.** No database. No ORM. No web framework. No config files beyond `.env`.

---

## 2. Entry Points

| Entry Point | How It Runs |
|-------------|-------------|
| `python bankruptcy_monitor.py` | CLI invocation; calls `main()` |
| GitHub Actions cron | Monthly on the 1st; runs the same CLI command |
| GitHub Actions `workflow_dispatch` | Manual trigger with year/month/AI/skip-email inputs |

There is exactly one entry point: `main()` at line 884.

---

## 3. Data Flow

```
TIC.io website
    |
    v
scrape_tic_bankruptcies(year, month) --> List[BankruptcyRecord]
    |
    v
lookup_trustee_emails(records)  --> enriches records with trustee_email (Brave Search API)
    |
    v
filter_records(records) --> filtered List[BankruptcyRecord]
    |
    v
score_bankruptcies(filtered) --> scored List[BankruptcyRecord] (rule-based + optional Claude API)
    |
    v
format_email_html(scored, year, month) --> HTML string
format_email_plain(scored, year, month) --> plain text string
    |
    v
send_email(subject, html, plain) --> Gmail SMTP  (or print to stdout if NO_EMAIL=true)
```

### Data Model: `BankruptcyRecord` (line 40)

17 fields total:
- 13 scraped fields (company_name, org_number, initiated_date, court, sni_code, industry_name, trustee, trustee_firm, trustee_address, employees, net_sales, total_assets, region)
- 4 computed/enriched fields (ai_score, ai_reason, priority, trustee_email)

**Data is never persisted.** Records exist only in memory for the duration of a single run. There is no database, no file export, no JSON dump. The only output is the email (or stdout).

---

## 4. Module Breakdown

### 4a. Scraper (lines 66-217)

- Uses Playwright (headless Chromium) to load TIC.io pages
- Paginates up to `max_pages=10` with `pageSize=100`
- Stops when it encounters dates before the target month
- Date format from TIC.io: `MM/DD/YYYY`
- All CSS selectors are TIC.io-specific (`.bankruptcy-card`, `.bankruptcy-card__name a`, etc.)

### 4b. Trustee Email Lookup (lines 224-332)

- Gated by `LOOKUP_TRUSTEE_EMAIL=true` and `BRAVE_API_KEY`
- Uses Brave Search API to find trustee email addresses from search snippets
- Deduplicates by (trustee_name, firm_name) pair -- each unique pair looked up once
- Tries 4 query variations per pair, stops early on personal (non-generic) email match
- Filters out generic emails (info@, kontakt@, etc.)

### 4c. Filtering (lines 339-383)

- Region filter: comma-separated list, case-insensitive substring match
- Keyword filter: matches against company_name + industry_name
- Employee filter: default minimum 5 (records with N/A pass through)
- Revenue filter: default minimum 1M SEK; parses TSEK format

### 4d. AI Scoring (lines 390-537)

- Two-phase hybrid approach:
  1. **Rule-based** (all records): SNI code lookup + employee count boost + keyword bonus
  2. **AI validation** (HIGH priority only): Claude API call using claude-3-haiku
- Priority tiers: HIGH (>=8), MEDIUM (5-7), LOW (1-4)
- Gated by `AI_SCORING_ENABLED=true`

### 4e. Email Formatting (lines 544-841)

- **HTML** (lines 544-744): Builds card-based HTML using f-string interpolation, then loads `email_template.html` via `string.Template.substitute()`
- **Plain text** (lines 747-841): Structured text with sections by priority
- Both split records into HIGH/MEDIUM/LOW/no-score groups

### 4f. Email Sending (lines 844-877)

- Gmail SMTP over SSL (port 465)
- MIME multipart/alternative (HTML + plain text)
- Single function, ~30 lines

### 4g. Main Orchestration (lines 884-952)

- Date logic: uses env vars or auto-detects; previous month if day <= 7
- Sequential pipeline: scrape -> email lookup -> filter -> score -> format -> send

---

## 5. Existing Scheduling / Job Mechanisms

| Mechanism | Details |
|-----------|---------|
| GitHub Actions cron | `0 6 1 * *` -- 1st of month at 06:00 UTC |
| GitHub Actions manual | `workflow_dispatch` with year/month/AI/skip-email inputs |
| In-code date logic | Auto-selects previous month if `datetime.now().day <= 7` |

**There is no application-level scheduler** (no APScheduler, no Celery, no crontab). Scheduling is entirely delegated to GitHub Actions.

---

## 6. Integration Point Analysis

### 6a. Mailgun Email Outreach (templated emails to scraped contacts)

**What it needs:** Access to the enriched `List[BankruptcyRecord]` after scoring, specifically the `trustee_email` field.

**Cleanest integration point:** After `score_bankruptcies()` returns in `main()` (line 926). At this point, records have all enrichment (emails, scores, priorities). Insert a call to a new outreach function here, before the report email is generated.

```
scored = score_bankruptcies(filtered)
# --> NEW: send_outreach_emails(scored)  # Mailgun templated emails to trustees
```

**Key considerations:**
- The existing `send_email()` is tightly coupled to Gmail SMTP. Mailgun outreach should be a separate function/module, not a modification of the report email sender.
- `trustee_email` is only populated when `LOOKUP_TRUSTEE_EMAIL=true`. Outreach depends on this being enabled.
- The current `BankruptcyRecord` dataclass would need no changes -- `trustee_email` field already exists.
- Outreach should be gated by its own env var (e.g., `MAILGUN_OUTREACH_ENABLED=true`).
- **Deduplication concern:** Since the app is stateless, there is nothing preventing the same trustee from being emailed on every run. This module needs dedup (see 6b).

**Estimated scope:** New file `outreach.py` or a new section in the main file. Needs Mailgun API integration, HTML template for outreach emails, and an env var gate.

### 6b. Scheduling + Deduplication (APScheduler or Celery, dedup on company+date+email)

**Current state:** No persistence at all. No database. No record of what was previously sent.

**Cleanest integration points:**

1. **Deduplication store:** Introduce a lightweight persistence layer. Options:
   - SQLite file (`data/bankruptcies.db` -- already in `.gitignore`)
   - JSON file (simplest, but doesn't scale)
   - PostgreSQL/Redis (overkill for current scale)

   **Recommendation:** SQLite, stored at `data/bankruptcies.db`. The `.gitignore` already has an entry for this path, suggesting it was previously considered.

2. **Dedup check point:** Two places:
   - After `scrape_tic_bankruptcies()` returns -- dedup scraped records against previously seen (company org_number + initiated_date)
   - Before outreach email send -- dedup on (trustee_email + org_number) to avoid re-emailing the same trustee about the same bankruptcy

3. **Scheduler:** The GitHub Actions cron currently handles scheduling. If moving to an always-on process:
   - APScheduler (lighter, in-process) would replace GitHub Actions cron
   - Celery (heavier, requires Redis/RabbitMQ) is overkill unless there are multiple workers
   - **Recommendation:** APScheduler with `BackgroundScheduler` if moving to a long-running process; otherwise, keep GitHub Actions and just add the dedup store

**Key considerations:**
- Adding a database is the biggest architectural change -- it breaks the "stateless" philosophy
- The `BankruptcyRecord` dataclass could serve as the schema basis (org_number as natural key, initiated_date as part of composite key)
- Dedup table schema suggestion: `(org_number, initiated_date, trustee_email, sent_at, outreach_sent_at)`

### 6c. Streamlit Dashboard (read-only reporting)

**What it needs:** Access to bankruptcy data for display. Two options:
1. Read from the dedup database (if 6b is implemented first)
2. Scrape live on each dashboard load (slow, ~30s per run)

**Cleanest integration point:** Refactor `scrape_tic_bankruptcies()`, `filter_records()`, and `score_bankruptcies()` to be importable functions (they already are -- the file has `if __name__ == '__main__'` guard). A Streamlit app can simply:

```python
from bankruptcy_monitor import scrape_tic_bankruptcies, filter_records, score_bankruptcies
```

**Better with database (6b):** If the dedup database exists, the dashboard reads from SQLite directly -- no scraping needed, instant load, historical data available.

**Key considerations:**
- Streamlit needs its own entry point (`streamlit run dashboard.py`)
- The dashboard should be a separate file (`dashboard.py`)
- If reading from database, it's fully decoupled from the scraper
- If reading live, it needs Playwright installed (heavy dependency for a web dashboard)
- The existing `BankruptcyRecord` dataclass can be reused as-is

---

## 7. Observations and Risks

### Code Quality
- **No tests.** Zero test files exist. Any refactoring carries regression risk.
- **CLAUDE.md line counts are stale.** It says 524 lines; the file is actually 953 lines. The "Code Structure" section line ranges are all wrong.
- **Date logic bug (line 898):** When `month == 1` and we subtract 1, `month` becomes 0 (not 12). The conditional `month == 12` check on line 898 tests the *already decremented* value, so if the original month was 1, after `month = month - 1` we get `month = 0`, then `year = year - 1 if month == 12 else year` does NOT decrement the year because `month` is 0, not 12. This means January runs would produce month=0, year=current -- broken.

  ```python
  # Line 897-898 (BUGGY):
  month = month - 1 if month > 1 else 12
  year = year - 1 if month == 12 else year
  ```
  When original `month=1`: first line sets `month=12`, second line sees `month==12` and decrements year. This is actually correct for January.
  When original `month=2`: first line sets `month=1`, second line sees `month!=12`, year stays. Correct.
  **Revised: The logic is correct**, but confusingly written because it relies on the sequential mutation of `month`.

- **Bare except in card processing (line 201):** Catches all exceptions silently per card. Could hide real bugs.
- **No retry logic:** Network failures during scraping or email sending are not retried.
- **Gmail dependency:** SMTP is hardcoded to `smtp.gmail.com:465`. Switching to Mailgun for reports would require modifying `send_email()`.

### Architecture Tension
- The CLAUDE.md philosophy says "radical simplicity" and "single file," but the project is already 953 lines with an external HTML template. Adding three new modules (outreach, scheduling/dedup, dashboard) will fundamentally change the architecture. The team should explicitly acknowledge this transition.

### Security
- `.env` is properly gitignored
- Gmail app passwords are used (good -- not regular passwords)
- Brave API key is stored in env vars (appropriate)
- No user input beyond env vars, so injection risk is minimal

---

## 8. Recommended Implementation Order

1. **Deduplication + persistence (6b)** -- Foundation for everything else. Add SQLite store. Without this, outreach will spam trustees on every run and the dashboard has no historical data.
2. **Mailgun outreach (6a)** -- Depends on dedup to avoid re-sending. Can use the new database to track outreach status.
3. **Streamlit dashboard (6c)** -- Reads from the database populated by steps 1-2. Purely additive, no risk to existing functionality.
4. **Scheduling** -- Evaluate whether GitHub Actions cron is sufficient or if APScheduler is needed. Only add APScheduler if moving to an always-on deployment.

---

## 9. Summary

The codebase is a clean, well-structured single-file scraper with a clear data pipeline. The main integration seam is after `score_bankruptcies()` in `main()`. The biggest gap is the complete lack of persistence -- adding a SQLite database is the prerequisite for both outreach deduplication and the dashboard. No tests exist, so any structural changes should be accompanied by at least basic regression tests for the scraper output format and filtering logic.
