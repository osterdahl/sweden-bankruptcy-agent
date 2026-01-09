# Swedish Bankruptcy Monitor

**Ultra-simplified bankruptcy monitoring system** - one Python file, one dependency, zero complexity.

Scrapes [Konkurslistan.se](https://www.konkurslistan.se) for monthly bankruptcy announcements and sends a plain text email report.

## Features

- ✅ Automated monthly scraping via GitHub Actions
- ✅ Filter by region, keywords, employees, revenue
- ✅ Plain text email reports
- ✅ No database (stateless)
- ✅ Single file (~400 lines)
- ✅ One dependency (Playwright)

## Data Collected

From Konkurslistan.se:
- Company name and organization number
- Bankruptcy date
- Location and region
- Court
- Administrator name (when available)
- Business type
- Employees and revenue (when available)

**Note:** Email and phone contact information for administrators is not automatically collected (POIT source is CAPTCHA-protected). Manual lookup via POIT links provided in reports.

## Quick Start

### Local Setup

```bash
# Install
pip install playwright
playwright install chromium

# Configure
cp .env.example .env
# Edit .env with your email credentials

# Run
python bankruptcy_monitor.py
```

### GitHub Actions Setup

1. Fork this repository
2. Add GitHub Secrets (Settings → Secrets and variables → Actions):
   - `SENDER_EMAIL`: Your Gmail address
   - `SENDER_PASSWORD`: Gmail app password
   - `RECIPIENT_EMAILS`: Comma-separated recipient emails
   - `FILTER_REGIONS`: (Optional) e.g., "Stockholm,Göteborg"
   - `FILTER_INCLUDE_KEYWORDS`: (Optional) e.g., "bygg,IT"

3. The workflow runs automatically on the 1st of each month at 6 AM UTC

## Configuration

Set these environment variables in `.env` or GitHub Secrets:

### Required
- `SENDER_EMAIL`: Gmail address for sending emails
- `SENDER_PASSWORD`: Gmail app password ([create one](https://support.google.com/accounts/answer/185833))
- `RECIPIENT_EMAILS`: Comma-separated list of recipients

### Optional Filtering
- `FILTER_REGIONS`: Comma-separated regions (e.g., "Stockholm,Göteborg,Skåne")
- `FILTER_INCLUDE_KEYWORDS`: Match keywords in company name or business type
- `FILTER_MIN_EMPLOYEES`: Minimum number of employees
- `FILTER_MIN_REVENUE`: Minimum revenue in SEK

### Optional Overrides
- `YEAR`: Override target year (defaults to current/previous)
- `MONTH`: Override target month (defaults to previous month)
- `NO_EMAIL`: Set to `true` to skip email sending (prints to console only)

## Example Output

```
SWEDISH BANKRUPTCY REPORT - January 2026
============================================================

Total bankruptcies found: 3

1. Example Company AB (123456-7890)
   Date: 2026-01-15
   Location: Stockholm, Stockholms län
   Court: Stockholms tingsrätt
   Administrator: John Doe, Example Law Firm
   Business: IT consulting services
   Employees: 12
   Revenue: 5,000,000 SEK
   POIT: https://poit.bolagsverket.se/poit-app/sok?orgnr=1234567890

...
```

## Architecture

**Before:** 2,800 lines across 12 files, 8 dependencies, complex database layer
**After:** 400 lines in 1 file, 1 dependency, stateless

### What Was Removed
- ❌ Multiple data sources (Allabolag, Bolagsfakta) - kept only Konkurslistan
- ❌ SQLite database - stateless operation
- ❌ HTML email templates - plain text only
- ❌ POIT enrichment - blocked by CAPTCHA anyway
- ❌ Lawyer contact enrichment - low success rate
- ❌ Mock scrapers and test scaffolding
- ❌ Complex filtering system - simple keyword matching
- ❌ SNI code lookup - unused
- ❌ scheduler.py - use system cron or GitHub Actions

### What Was Kept
- ✅ Core bankruptcy data (company, date, location, court, administrator)
- ✅ Konkurslistan scraping (most complete source)
- ✅ Basic filtering (region, keywords, employees, revenue)
- ✅ Email notifications
- ✅ GitHub Actions deployment

## Limitations

1. **Administrator Contact Info:** Email and phone numbers require manual lookup via POIT (CAPTCHA-protected)
2. **Single Source:** Only scrapes Konkurslistan.se (trade-off for simplicity)
3. **No History:** Stateless system, doesn't store historical data
4. **Plain Text Emails:** No HTML styling (intentional simplicity)

## Manual Administrator Lookup

Each bankruptcy in the email report includes a POIT link:
```
POIT: https://poit.bolagsverket.se/poit-app/sok?orgnr=1234567890
```

Click this link to manually view the official bankruptcy announcement with full administrator contact details.

## Development

### Run Manually
```bash
# Run for specific month
YEAR=2025 MONTH=12 python bankruptcy_monitor.py

# Run with filtering
FILTER_REGIONS=Stockholm FILTER_MIN_EMPLOYEES=10 python bankruptcy_monitor.py

# Dry run (no email)
NO_EMAIL=true python bankruptcy_monitor.py
```

### Project Structure
```
bankruptcy_monitor.py        # Single file (400 lines)
├─ Data models              # BankruptcyRecord class
├─ Utilities                # Date parsing, org number normalization
├─ Scraper                  # Konkurslistan scraping & parsing
├─ Filtering                # Simple keyword/region matching
├─ Email                    # Plain text formatting & SMTP
└─ Main                     # Orchestration

requirements.txt             # playwright only
.github/workflows/monthly.yml # GitHub Actions automation
```

## Troubleshooting

### Email not sending
- Use Gmail app password, not account password: https://support.google.com/accounts/answer/185833
- Check SMTP access is enabled in Gmail settings
- Verify `SENDER_EMAIL` and `SENDER_PASSWORD` are set correctly

### No bankruptcies found
- Verify the target month has bankruptcy data
- Check if Konkurslistan.se site structure changed (CSS selectors may need updating)
- Run with verbose logging: `python bankruptcy_monitor.py` (INFO level by default)

### Filtering not working
- Ensure keywords are lowercase in `FILTER_INCLUDE_KEYWORDS`
- Region names must match format from Konkurslistan (e.g., "Stockholms län")
- Employee/revenue data is not always available - filter will skip records without data

## Migration from Complex Version

The previous version is preserved in git history. Key changes:

1. **Deleted files:**
   - `src/scraper.py`, `src/poit_enricher.py`, `src/lawyer_enrichment.py`
   - `src/enrichment.py`, `src/filter.py`, `src/database.py`
   - `scheduler.py`

2. **Simplified:**
   - `src/aggregator_scraper.py` → extracted Konkurslistan logic only
   - `src/email_notifier.py` → plain text template
   - `config/settings.py` → environment variables directly

3. **Data continuity:**
   - No database to migrate (new system is stateless)
   - Old SQLite database in `data/bankruptcies.db` can be exported to JSON if needed

## License

MIT

## Contributing

This is an intentionally minimal system. Before adding complexity:
1. Is it solving a real problem?
2. Can it be done with less code?
3. Does it maintain the "single file" philosophy?

**Rule of thumb:** If a feature adds >50 lines, it probably belongs in a separate tool.
