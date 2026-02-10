# Swedish Bankruptcy Monitor - Status

## Current State (v2 - TIC.io)

**Working and deployed.** Single file, scrapes TIC.io open data, sends HTML email reports.

### What's Done
- **Data source**: TIC.io web scraping (free, public, no CAPTCHA, no API key needed)
- **Data completeness**: 11/11 fields including full trustee contact info
- **Filtering**: Region, keywords, min employees (default 5), min revenue (default 1M SEK)
- **AI scoring**: Hybrid rule-based (SNI codes) + Claude API validation for HIGH priority
- **Email**: HTML card-based report with priority sections + plain text fallback
- **POIT**: Linked in emails for reference only (not scraped)

### Fields Captured
1. Company name
2. Org number
3. Initiated date
4. Court
5. SNI code
6. Industry name
7. Trustee name
8. Trustee firm
9. Trustee address
10. Employees
11. Net sales / Total assets

### Architecture
- `bankruptcy_monitor.py` (~1137 lines)
- Single dependency: `playwright`
- Optional: `anthropic` SDK (only if AI scoring enabled)
- Stateless, no database

## Previous Blockers (Resolved)
- ~~POIT CAPTCHA~~ — No longer relevant, switched to TIC.io
- ~~Missing administrator/trustee contact~~ — TIC.io provides all contact data
- ~~5/9 field coverage~~ — Now 11/11

## Potential Future Work
- Schedule automation (cron / GitHub Actions)
- Track previously sent records to avoid duplicates
- Multiple data source fallback if TIC.io changes structure
