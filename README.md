# 🇸🇪 Sweden Bankruptcy Monitoring Agent

An automated system that monitors Swedish corporate bankruptcies from multiple aggregator sites, filters them by your criteria, and sends monthly email reports.

## Features

- 🔍 **Multi-Source Scraping**: Pulls data from Allabolag.se, Konkurslistan.se, Bolagsfakta.se
- 📊 **Smart Filtering**: Filter by employees, revenue, business type, region
- 📧 **Email Reports**: Beautiful HTML reports delivered monthly  
- 💾 **Data Storage**: SQLite database with full history
- 📤 **Export Options**: JSON and CSV exports
- 🐳 **Docker Ready**: Deploy anywhere with Docker
- ⚡ **GitHub Actions**: Free automated monthly runs

## Quick Start

### Option 1: GitHub Actions (Free, Recommended)

1. Fork this repository
2. Go to **Settings → Secrets and variables → Actions**
3. Add these secrets:

| Secret | Example Value |
|--------|---------------|
| `SENDER_EMAIL` | your-email@gmail.com |
| `SENDER_PASSWORD` | your-gmail-app-password |
| `RECIPIENT_EMAILS` | you@company.com |
| `FILTER_MIN_EMPLOYEES` | 10 |
| `FILTER_MIN_REVENUE` | 5000000 |

4. Go to **Actions** tab and enable workflows
5. Reports run automatically on the 1st of each month!

### Option 2: Local/Server

```bash
# Clone
git clone <repo>
cd sweden-bankruptcy-agent

# Configure
cp .env.example .env
# Edit .env with your settings

# Install
pip install -r requirements.txt
playwright install chromium

# Run
python main.py --month 12 --year 2025
```

### Option 3: Docker

```bash
docker-compose up -d
```

## Configuration

### Filter Criteria

```bash
# .env file
FILTER_MIN_EMPLOYEES=10        # Only companies with 10+ employees
FILTER_MIN_REVENUE=5000000     # Only companies with 5M+ SEK revenue
FILTER_BUSINESS_TYPES=Bygg,IT  # Business types (comma-separated)
FILTER_REGIONS=Stockholms län  # Regions (comma-separated)
```

### Email Setup (Gmail)

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. Generate an App Password
3. Use it as `SENDER_PASSWORD`

## Data Sources

The agent scrapes these public aggregator sites:

| Source | Data Available |
|--------|----------------|
| **Allabolag.se** | Company info, financials |
| **Konkurslistan.se** | Bankruptcy listings |
| **Bolagsfakta.se** | Company details |

All sources compile official Swedish court decisions.

## Usage

```bash
# Current month with email
python main.py

# Specific month, no email
python main.py --month 11 --year 2024 --no-email

# Test with mock data
python main.py --mock

# Verbose logging
python main.py -v
```

## Project Structure

```
sweden-bankruptcy-agent/
├── src/
│   ├── aggregator_scraper.py  # Multi-source web scraper
│   ├── models.py              # Data models
│   ├── filter.py              # Filtering logic
│   ├── database.py            # SQLite storage
│   ├── email_notifier.py      # Email reports
│   └── agent.py               # Main orchestrator
├── .github/workflows/
│   └── monthly-report.yml     # GitHub Actions workflow
├── main.py                    # CLI entry point
├── Dockerfile
└── docker-compose.yml
```

## License

MIT
