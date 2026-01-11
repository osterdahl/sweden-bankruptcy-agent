# Swedish Bankruptcy Monitor

## Philosophy
**Radical simplicity.** One Python file, one dependency, zero complexity. Every line must justify its existence.

## Architecture
- **Single file**: `bankruptcy_monitor.py` (~500 lines)
- **One dependency**: `playwright` for web scraping
- **Stateless**: No database, no history tracking
- **Single source**: Konkurslistan.se only

## Workflow
1. Scrape Konkurslistan.se for monthly bankruptcies
2. Filter by region/keywords/size (optional)
3. Email HTML report with plain text fallback

## What We Removed (82% reduction)
- Multiple data sources → Konkurslistan only
- SQLite database → Stateless
- POIT enrichment → Manual lookup links
- Complex filtering → Simple keyword matching
- Separate files → Single file

## Code Structure

```
Lines 30-50:   Data models (BankruptcyRecord class)
Lines 52-100:  Utilities (date parsing, org number normalization)
Lines 102-370: Scraper (Konkurslistan parsing, detail enrichment)
Lines 374-387: Filtering (region, keywords, size)
Lines 389-680: Email (HTML + plain text generation, SMTP)
Lines 682-end: Main (orchestration, date logic)
```

## Key Patterns

### Swedish Text Parsing
- Company names end with: AB, HB, KB, "Ek. för."
- Courts: "[City] tingsrätt" (e.g., "Stockholms tingsrätt")
- Business type: "Verksamhet (SNI) [5-digit] [Description]"
- Dates: "Datum: YYYY-MM-DD"
- Employees: "Anställda: [number]"
- Revenue: "Omsättning: [number] tkr"

### Common Tasks

**Add court pattern** (lines 334-359):
```python
r'\b(Nya tingsrätt)\b',
```

**Modify HTML styling** (lines 446-553):
CSS is inline in `format_email_html()`

**Adjust parsing** (lines 240-300):
Look for Swedish keywords: "Datum", "Verksamhet", "Anställda"

## Configuration

### Required
- `SENDER_EMAIL`, `SENDER_PASSWORD`, `RECIPIENT_EMAILS`

### Optional
- `FILTER_REGIONS` - Comma-separated (e.g., "Stockholm,Göteborg")
- `FILTER_INCLUDE_KEYWORDS` - Match company name or business type
- `FILTER_MIN_EMPLOYEES`, `FILTER_MIN_REVENUE`
- `YEAR`, `MONTH` - Override auto-detection
- `NO_EMAIL=true` - Dry run

## Known Limitations
1. Administrator contact info requires manual POIT lookup (CAPTCHA-protected)
2. Single source only (Konkurslistan.se)
3. No historical tracking (stateless)
4. No HTML email templates (inline generation)

## Coding Principles
1. No over-engineering - don't add features "just in case"
2. No abstractions for one-time operations
3. Delete unused code completely (don't comment out)
4. Inline over import - keep in single file
5. Trust internal data, validate only at boundaries

## Before Adding Features
Ask:
1. Does this solve a real problem?
2. Can it be done with less code?
3. Does it maintain single-file philosophy?
4. Asemble a panel of experts and ask for honest opinions

**Rule**: If a feature adds >50 lines, it probably belongs elsewhere.
