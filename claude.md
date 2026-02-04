# Swedish Bankruptcy Monitor

## Philosophy
**Radical simplicity.** One Python file, one dependency, complete data, zero CAPTCHA, zero complexity.

## Architecture
- **Single file**: `bankruptcy_monitor.py` (524 lines)
- **One dependency**: `playwright` for web scraping
- **Stateless**: No database, no history tracking
- **Single source**: TIC.io open data only
- **Data completeness**: 100% (11/11 fields when available)

## Workflow
1. Scrape TIC.io open data for monthly bankruptcies
2. Extract ALL fields including trustee contact info
3. Filter by region/keywords/size (optional)
4. Email HTML report with plain text fallback

## Evolution
- **v1 (Konkurslistan)**: 515 lines, 5/9 fields (56%), missing all lawyer contact
- **v2 (TIC.io)**: 524 lines, 11/11 fields (100%), complete lawyer contact info

## Code Structure

```
Lines 1-30:    Imports and logging setup
Lines 31-54:   Data model (@dataclass BankruptcyRecord with AI fields)
Lines 56-200:  Scraper (TIC.io page scraping, data extraction)
Lines 202-235: Filtering (region, keywords, employee count)
Lines 237-413: AI Scoring (rule-based + Claude API hybrid)
Lines 415-765: Email (HTML + plain text, priority sections)
Lines 767-810: SMTP email sending
Lines 812-end: Main (orchestration, date logic, AI scoring integration)
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

### Common Tasks

**Modify HTML styling** (lines 280-410):
CSS is inline in `format_email_html()`

**Adjust TIC.io selectors** (lines 60-180):
If TIC.io changes their HTML structure, update CSS selectors

**Add filtering logic** (lines 202-235):
Simple environment variable-based filters

## AI Scoring System

### High-Value SNI Codes (lines 260-272)
SNI codes that score 8-10 (HIGH priority):
- 26: Computer/electronic manufacturing
- 62: Computer programming/consultancy
- 72: Scientific R&D
- 71: Architectural/engineering
- 749: Professional/scientific/technical

### Scoring Flow
1. **Rule-based** (all records): SNI code → company size → keywords → score 1-10
2. **AI validation** (HIGH only): Claude API validates + adjusts score
3. **Priority assignment**: HIGH (≥8), MEDIUM (5-7), LOW (1-4)

### Email Sections
- HTML: 3 color-coded sections with priority badges
- Plain text: Separate HIGH/MEDIUM/LOW sections with AI reasoning

## Configuration

### Required
- `SENDER_EMAIL`, `SENDER_PASSWORD`, `RECIPIENT_EMAILS`

### Optional - Filtering
- `FILTER_REGIONS` - Comma-separated (e.g., "Stockholm,Göteborg")
- `FILTER_INCLUDE_KEYWORDS` - Match company name or business type
- `FILTER_MIN_EMPLOYEES`, `FILTER_MIN_REVENUE`
- `YEAR`, `MONTH` - Override auto-detection
- `NO_EMAIL=true` - Dry run

### Optional - AI Scoring
- `AI_SCORING_ENABLED=true` - Enable hybrid scoring (default: false)
- `ANTHROPIC_API_KEY` - Claude API key for HIGH validation

## Known Limitations
1. Stateless - doesn't track previously sent bankruptcies
2. Single source only (TIC.io, but it has everything)
3. Court data sometimes missing (95% coverage)
4. Gmail dependency for email sending

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
