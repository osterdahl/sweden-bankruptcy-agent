# Swedish Bankruptcy Monitor

**Automated bankruptcy monitoring for Swedish companies using TIC.io open data.**

## Philosophy

**Radical simplicity.** One Python file, one dependency, complete data, zero CAPTCHA, zero complexity.

## Features

- 📊 **Complete data**: Company, trustee contact, SNI code, financials - everything
- 🆓 **100% free**: Scrapes TIC.io public open data
- 🚫 **No CAPTCHA**: No bot detection, no API limits
- 📧 **Beautiful emails**: HTML reports with alternating row colors
- 🎯 **Smart filtering**: By region, keywords, employee count
- 🤖 **AI scoring** (optional): Hybrid prioritization with HIGH/MEDIUM/LOW sections
- ⚡ **Fast & reliable**: Single source, no enrichment delays
- 🤖 **GitHub Actions**: Automated monthly runs

## AI Scoring (Optional)

**Intelligent bankruptcy prioritization for data acquisition.**

### How It Works

1. **Rule-Based Scoring** (FREE, fast)
   - SNI code analysis (tech/consulting/R&D = HIGH value)
   - Company size metrics (employees, revenue, assets)
   - Keyword detection in company name

2. **AI Validation** (Optional, only for HIGH candidates)
   - Claude API analyzes top ~10-15% of bankruptcies
   - Validates industry fit + data infrastructure likelihood
   - Provides reasoning for each HIGH-priority case
   - Cost: ~$0.60-0.90/month for typical usage

### Email Format

When enabled, emails split into 3 sections:
- ⭐ **HIGH PRIORITY** - Data-rich companies (tech, SaaS, consulting)
- ⚠️ **MEDIUM PRIORITY** - Moderate acquisition potential
- ℹ️ **LOW PRIORITY** - Limited data assets expected

### Configuration

```bash
# Enable AI scoring (default: disabled)
AI_SCORING_ENABLED=true

# Optional: Claude API for HIGH candidate validation
ANTHROPIC_API_KEY=sk-ant-your-api-key-here
```

**Without API key**: Rule-based scoring only (still very effective!)
**With API key**: Hybrid scoring with AI reasoning for top candidates

### Example Output

January 2026: 837 bankruptcies → 74 HIGH | 623 MEDIUM | 140 LOW

HIGH examples:
- Computer programming/consultancy (SNI 62)
- Scientific R&D (SNI 72)
- Professional/technical services (SNI 749)

## Data Coverage

**11/11 fields (100% when available)**:

| Field | Coverage | Source |
|-------|----------|--------|
| Company Name | 100% | TIC.io |
| Org Number | 100% | TIC.io |
| Initiated Date | 100% | TIC.io |
| Court | 95% | TIC.io |
| Region | 100% | TIC.io |
| SNI Code | 95% | TIC.io |
| Industry Name | 95% | TIC.io |
| **Trustee Name** | **100%** | **TIC.io** |
| **Trustee Firm** | **100%** | **TIC.io** |
| **Trustee Address** | **100%** | **TIC.io** |
| Employees | 70% | TIC.io |
| Net Sales | 70% | TIC.io |
| Total Assets | 70% | TIC.io |

**Previous version**: 56% coverage (5/9 fields) from Konkurslistan.se
**Current version**: **100% coverage** (11/11 fields) from TIC.io

No more missing lawyer contact information!

## Quick Start

### Prerequisites

```bash
# Install dependencies
pip install playwright
playwright install chromium
```

### Configuration

Create `.env` file:

```bash
# Required for email
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=your-app-password
RECIPIENT_EMAILS=recipient@example.com

# Optional filters
FILTER_REGIONS=Stockholm,Göteborg
FILTER_INCLUDE_KEYWORDS=IT,restaurang
FILTER_MIN_EMPLOYEES=10
```

### Run

```bash
# Send email report for last month
python bankruptcy_monitor.py

# Dry run (print to console)
NO_EMAIL=true python bankruptcy_monitor.py

# Specific month
YEAR=2025 MONTH=12 python bankruptcy_monitor.py
```

## How It Works

1. **Scrapes TIC.io** open data portal (https://tic.io/en/oppna-data/konkurser)
2. **Extracts all fields** including trustee name, firm, address
3. **Filters** by your criteria (region, keywords, size)
4. **Sends email** with beautiful HTML table

## Email Report

The email includes:

**Main Table**:
- Company name & org number
- Bankruptcy initiated date
- Region & court
- SNI code & industry description
- POIT link

**Expandable Details** (per company):
- Trustee name, law firm, address
- Number of employees
- Net sales & total assets

## Architecture

**Before (Konkurslistan.se)**:
- 515 lines
- 5/9 fields (56%)
- Missing all lawyer contact info
- Complex parsing, variable quality

**After (TIC.io)**:
- 524 lines
- 11/11 fields (100%)
- Complete lawyer contact info
- Clean data, consistent structure

## Data Source

**TIC.io Open Data**:
- Free, public bankruptcy data
- Updated daily
- Data from Bolagsverket (Swedish Companies Registration Office)
- No authentication required
- No CAPTCHA
- No API rate limits

## Filtering

Control what bankruptcies you receive:

```bash
# Only Stockholm & Göteborg
FILTER_REGIONS=Stockholm,Göteborg

# Only IT & restaurant companies
FILTER_INCLUDE_KEYWORDS=IT,restaurang,konsult

# Only companies with 10+ employees
FILTER_MIN_EMPLOYEES=10
```

## GitHub Actions

Automated monthly reports:

- Runs 1st of each month at 6 AM UTC
- Can be triggered manually with optional AI scoring toggle
- Uses repository secrets for email credentials and API keys

### Required Secrets

Set these in GitHub repository settings → Secrets and variables → Actions:

```
SENDER_EMAIL           # Gmail address
SENDER_PASSWORD        # Gmail app password
RECIPIENT_EMAILS       # Comma-separated emails
```

### Optional Secrets

```
AI_SCORING_ENABLED     # Set to 'true' to enable AI scoring
ANTHROPIC_API_KEY      # Claude API key (only needed if AI scoring enabled)
FILTER_REGIONS         # Comma-separated regions
FILTER_INCLUDE_KEYWORDS # Comma-separated keywords
FILTER_MIN_EMPLOYEES   # Minimum employee count
```

### Manual Trigger

Go to Actions → Monthly Bankruptcy Report → Run workflow:
- **Enable AI scoring**: Override secret setting for this run
- **Year/Month**: Process specific period
- **Skip email**: Dry run mode

## Configuration

### Required Environment Variables

```bash
SENDER_EMAIL           # Gmail address
SENDER_PASSWORD        # Gmail app password
RECIPIENT_EMAILS       # Comma-separated emails
```

### Optional Environment Variables

```bash
FILTER_REGIONS         # Comma-separated regions
FILTER_INCLUDE_KEYWORDS # Comma-separated keywords
FILTER_MIN_EMPLOYEES   # Minimum employee count
YEAR                   # Override year
MONTH                  # Override month
NO_EMAIL              # Set to 'true' for dry run
```

### Gmail Setup

1. Enable 2-factor authentication
2. Generate app password: https://myaccount.google.com/apppasswords
3. Use app password as `SENDER_PASSWORD`

## File Structure

```
bankruptcy_monitor.py  # Single file - entire application (524 lines)
.env                  # Configuration (gitignored)
.env.example          # Example configuration
README.md             # This file
claude.md             # AI coding context
requirements.txt      # Python dependencies (just playwright)
```

## Development

```bash
# Test with dry run
NO_EMAIL=true YEAR=2026 MONTH=1 python bankruptcy_monitor.py

# With filtering
FILTER_REGIONS=Stockholm FILTER_MIN_EMPLOYEES=5 NO_EMAIL=true python bankruptcy_monitor.py
```

## Comparison: Before vs After

### Data Completeness

| Metric | Konkurslistan | TIC.io |
|--------|---------------|--------|
| Fields available | 5/9 (56%) | 11/11 (100%) |
| Lawyer name | ❌ 60% | ✅ 100% |
| Lawyer firm | ❌ 0% | ✅ 100% |
| Lawyer address | ❌ 0% | ✅ 100% |
| SNI code | ❌ 60% | ✅ 95% |
| Employees | ❌ 30% | ✅ 70% |
| Financials | ❌ 30% | ✅ 70% |

### Reliability

| Metric | Konkurslistan | TIC.io |
|--------|---------------|--------|
| CAPTCHA | No | No |
| Parsing complexity | High | Low |
| Data consistency | Variable | Excellent |
| Update frequency | Unknown | Daily |

### Code Simplicity

| Metric | Konkurslistan | TIC.io |
|--------|---------------|--------|
| Lines of code | 515 | 524 |
| Data sources | 1 | 1 |
| Enrichment steps | 1 | 0 |
| Dependencies | playwright | playwright |

## Why TIC.io?

1. **Complete data**: All fields we need in one place
2. **Free access**: No API key, no payment
3. **No CAPTCHA**: Public data portal
4. **Trustee contact**: Name, firm, address - everything for lawyer lookup
5. **Better SNI coverage**: 95% vs 60%
6. **Better financials**: 70% vs 30%
7. **Daily updates**: Fresh data
8. **Structured**: Clean HTML, easy parsing

## Limitations

1. **Stateless**: Doesn't track previously sent bankruptcies
2. **Single source**: Only TIC.io (but it has everything)
3. **Email dependency**: Gmail for sending
4. **Court data**: Sometimes missing (95% coverage)

## Troubleshooting

### No bankruptcies found

- TIC.io may not have data for that month yet
- Try previous month
- Check if filters are too restrictive

### Email not sending

- Use Gmail app password, not account password
- Check SMTP access enabled
- Verify `SENDER_EMAIL` and `SENDER_PASSWORD`

### Parsing errors

- Enable debug: Change `level=logging.INFO` to `level=logging.DEBUG`
- Check if TIC.io site structure changed
- Report issue on GitHub

## License

MIT

## Credits

Data source: [TIC.io Open Data](https://tic.io/en/oppna-data/konkurser) - The Intelligence Company (Jens Nylander)

Original bankruptcy data: [Bolagsverket](https://bolagsverket.se/) (Swedish Companies Registration Office)
