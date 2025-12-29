# 🤖 Autonomous GitHub Actions Workflow

## Overview

The bankruptcy monitoring agent is **fully autonomous** and requires **zero manual intervention**. It runs automatically every month on GitHub Actions.

## How It Works

### 🗓️ Automatic Monthly Execution

The workflow runs automatically:
- **When:** 1st of every month at 6 AM UTC
- **What:** Scrapes bankruptcies, enriches data, filters, and sends reports
- **No input needed:** Intelligently determines which month to process

### 🧠 Smart Date Detection

The workflow automatically decides which month to process based on the current date:

```
Day 1-3 of month  → Process PREVIOUS month
Day 4-31 of month → Process CURRENT month
```

**Why this logic?**
- Early in the month (day 1-3): Data for previous month is more complete
- Later in the month (day 4+): Can start processing current month data

### Example Timeline

```
Jan 1, 2025 → Processes December 2024
Jan 4, 2025 → Processes January 2025
Feb 1, 2025 → Processes January 2025
Feb 5, 2025 → Processes February 2025
```

## Manual Triggers (Optional)

You can still trigger the workflow manually for testing or re-runs:

1. Go to **Actions** tab in GitHub
2. Click **Monthly Bankruptcy Report**
3. Click **Run workflow**
4. **Leave all inputs blank** for automatic date detection
5. Or specify year/month to override

### Manual Trigger Options

| Input | Description | Default |
|-------|-------------|---------|
| Year | Year to process | Auto-detect |
| Month | Month (1-12) | Auto-detect |
| Skip email | Don't send email | false |

**Pro tip:** Just click "Run workflow" without filling anything - it will automatically detect the right period!

## What Happens Automatically

### 1. Data Collection
```
→ Scrapes Allabolag.se, Konkurslistan.se, Bolagsfakta.se
→ Finds bankruptcies for the target month
→ Enriches with company details
→ Enriches with lawyer contact information (NEW!)
```

### 2. Filtering
```
→ Applies your configured filters:
  - Minimum employees
  - Minimum revenue
  - Business types
  - Regions
  - Keywords
```

### 3. Storage & Export
```
→ Saves to SQLite database
→ Exports to JSON and CSV
→ Uploads as GitHub artifacts (90-day retention)
```

### 4. Notification
```
→ Sends HTML email report to configured recipients
→ Includes summary and top matching companies
→ If job fails, sends failure notification
```

## Configuration

### Required Secrets

Set these in **Settings → Secrets and variables → Actions**:

#### Email Configuration (Required for notifications)
- `SENDER_EMAIL` - Your Gmail address
- `SENDER_PASSWORD` - Gmail App Password ([How to generate](https://support.google.com/accounts/answer/185833))
- `RECIPIENT_EMAILS` - Comma-separated list of recipients

#### SMTP Settings (Optional - defaults work for Gmail)
- `SMTP_SERVER` - Default: `smtp.gmail.com`
- `SMTP_PORT` - Default: `587`

#### Filter Criteria (Optional)
- `FILTER_MIN_EMPLOYEES` - Minimum number of employees (e.g., `10`)
- `FILTER_MIN_REVENUE` - Minimum revenue in SEK (e.g., `5000000`)
- `FILTER_BUSINESS_TYPES` - Comma-separated (e.g., `Bygg,IT,Transport`)
- `FILTER_REGIONS` - Comma-separated Swedish län (e.g., `Stockholms län,Skåne län`)
- `FILTER_EXCLUDE_KEYWORDS` - Keywords to exclude (e.g., `enskild firma`)
- `FILTER_INCLUDE_KEYWORDS` - Keywords to include (e.g., `tech,digital`)

### Example Secret Configuration

```
SENDER_EMAIL=your-email@gmail.com
SENDER_PASSWORD=abcd efgh ijkl mnop
RECIPIENT_EMAILS=person1@company.com,person2@company.com
FILTER_MIN_EMPLOYEES=10
FILTER_MIN_REVENUE=5000000
FILTER_BUSINESS_TYPES=Bygg,IT,Transport,Restaurang
FILTER_REGIONS=Stockholms län,Västra Götalands län
```

## Monitoring

### View Execution History

1. Go to **Actions** tab
2. Click **Monthly Bankruptcy Report**
3. See all past runs with status

### Check Latest Results

Each run provides:

**Artifacts (downloadable files):**
- `bankruptcy-report-YYYY-MM.zip` - JSON/CSV exports + logs (90 days)
- `database-YYYY-MM.zip` - Complete SQLite database (365 days)

**Summary:**
- Click on any run to see the summary with:
  - Period processed
  - Execution status
  - Records found/exported
  - Whether it was automatic or manual

### Logs

Detailed logs available for each run:
- Click any workflow run
- Expand step to see detailed logs
- Download `bankruptcy_agent.log` from artifacts

## Customizing the Schedule

Want to change when it runs?

Edit `.github/workflows/monthly-report.yml`:

```yaml
schedule:
  - cron: '0 6 1 * *'  # Current: 6 AM UTC on 1st of month
```

### Common Schedules

```yaml
# Run on 1st at 9 AM UTC
- cron: '0 9 1 * *'

# Run on 1st and 15th at 6 AM UTC
- cron: '0 6 1,15 * *'

# Run every Monday at 6 AM UTC
- cron: '0 6 * * 1'

# Run weekly on Sunday at midnight UTC
- cron: '0 0 * * 0'
```

[Learn more about cron syntax](https://crontab.guru/)

## Troubleshooting

### Workflow isn't running automatically

**Check:**
1. Go to **Actions** tab → Ensure workflows are enabled
2. Check if repository is archived or disabled
3. Verify the workflow file is on the default branch (main/master)
4. GitHub requires at least one commit in the last 60 days for scheduled workflows

**Fix:** Make a small commit to keep the repo active

### Email not being sent

**Check:**
1. Secrets are configured correctly (Settings → Secrets)
2. Gmail App Password is valid (not your regular password!)
3. Check workflow logs for error messages
4. Verify SMTP settings if using non-Gmail

**Fix:** Test email configuration locally first:
```bash
python -c "
from config import Settings
settings = Settings.from_env()
print(f'SMTP: {settings.email_config.smtp_server}:{settings.email_config.smtp_port}')
print(f'Sender: {settings.email_config.sender_email}')
print(f'Recipients: {settings.email_config.recipient_emails}')
"
```

### No bankruptcies found

**Possible reasons:**
1. Filters are too strict (adjust `FILTER_MIN_EMPLOYEES`, `FILTER_MIN_REVENUE`)
2. Selected month has no bankruptcies matching criteria
3. Scraping failed (check logs)

**Fix:** Run with fewer filters or check if websites are accessible

### Workflow fails

**What happens:**
1. Failure notification email is sent automatically
2. Artifacts are still uploaded (if available)
3. Can re-run manually from Actions tab

**Debug:**
1. Check the specific step that failed in GitHub Actions
2. Download logs artifact for details
3. Re-run with manual trigger for testing

## Cost & Limits

### GitHub Actions Free Tier

- **Public repos:** Unlimited minutes ✅
- **Private repos:** 2,000 minutes/month (should be enough for 1x/month run)

### Typical Resource Usage

- **Duration:** 5-15 minutes per run
- **Storage:** ~10 MB per month (artifacts retained for 90 days)
- **Network:** Minimal (scraping 3-4 sites)

**Estimated:** Well within free tier limits! 🎉

**Note:** Artifacts are retained for 90 days by default. You can download and archive them locally for longer retention.

## Security Best Practices

### Protecting Your Secrets

✅ **DO:**
- Use GitHub Secrets for sensitive data
- Generate Gmail App Passwords (never use main password)
- Use least-privileged email account
- Regularly rotate passwords

❌ **DON'T:**
- Hardcode credentials in workflow file
- Commit `.env` file with real credentials
- Share App Passwords
- Use admin email accounts

### Repository Settings

Recommended:
- Enable **Require approval for first-time contributors**
- Enable **branch protection** on main branch
- Review **Actions permissions** (Settings → Actions)

## Advanced: Multi-Environment Setup

You can set up different configurations for testing:

1. Create a separate workflow file: `.github/workflows/test-report.yml`
2. Use different secrets (e.g., `TEST_SENDER_EMAIL`)
3. Run on different schedule or manually
4. Add `--no-email` flag for testing without sending

Example test workflow:
```yaml
name: Test Bankruptcy Report

on:
  workflow_dispatch:  # Manual only

jobs:
  test-report:
    runs-on: ubuntu-latest
    steps:
      # ... same as monthly-report.yml
      - name: Run bankruptcy monitor
        run: |
          python main.py --no-email -v  # Don't send email
```

## Support

### Common Questions

**Q: How do I know it's working?**
A: Check the Actions tab - you'll see green checkmarks for successful runs

**Q: Can I test without waiting for the 1st?**
A: Yes! Use manual trigger (leave inputs blank for auto-detection)

**Q: What if I want different filters each month?**
A: Update the secrets in Settings → Secrets → Edit the secret

**Q: Can I process multiple months at once?**
A: Run manually multiple times with different month inputs

**Q: How long is data retained?**
A: Both exports and database artifacts: 90 days (repository limit). Download and archive locally for longer retention.

### Getting Help

1. Check workflow logs in Actions tab
2. Review `bankruptcy_agent.log` in artifacts
3. Test locally first: `python main.py --mock --no-email`
4. Check [GitHub Actions documentation](https://docs.github.com/en/actions)

## Summary

✅ **Fully autonomous** - runs every month automatically
✅ **Smart date detection** - always processes the right period
✅ **Zero manual input** - set up once, forget about it
✅ **Failure notifications** - you'll know if something breaks
✅ **Artifact retention** - data saved for historical analysis
✅ **Free tier friendly** - runs within GitHub's free limits

🎉 **Set it and forget it!**
