#!/usr/bin/env python3
"""
Swedish Bankruptcy Monitor - Simplified Single-File Version

Scrapes Konkurslistan.se for monthly bankruptcy announcements,
filters by keywords and region, sends plain text email report.

No database. No HTML styling. No enrichment. Just the essentials.
"""

import asyncio
import logging
import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Browser, Page

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

class BankruptcyRecord:
    """Simple bankruptcy record."""

    def __init__(
        self,
        company_name: str,
        org_number: str,
        date: Optional[datetime] = None,
        location: str = "",
        region: str = "",
        court: str = "",
        business_type: str = "",
        administrator: str = "",
        employees: Optional[int] = None,
        revenue: Optional[float] = None,
    ):
        self.company_name = company_name
        self.org_number = org_number
        self.date = date
        self.location = location
        self.region = region
        self.court = court
        self.business_type = business_type
        self.administrator = administrator
        self.employees = employees
        self.revenue = revenue


# ============================================================================
# UTILITIES
# ============================================================================

def normalize_org_number(org_nr: str) -> str:
    """Normalize Swedish organization number to NNNNNN-NNNN format."""
    if not org_nr:
        return ""
    digits = re.sub(r'\D', '', org_nr)
    if len(digits) == 10:
        return f"{digits[:6]}-{digits[6:]}"
    return org_nr


def parse_swedish_date(date_str: str) -> Optional[datetime]:
    """Parse Swedish date formats."""
    if not date_str:
        return None

    date_str = date_str.strip()

    # Swedish month names
    swedish_months = {
        "januari": "01", "februari": "02", "mars": "03", "april": "04",
        "maj": "05", "juni": "06", "juli": "07", "augusti": "08",
        "september": "09", "oktober": "10", "november": "11", "december": "12",
    }

    # Replace Swedish month names with numbers
    date_lower = date_str.lower()
    for sv_month, num in swedish_months.items():
        if sv_month in date_lower:
            date_str = re.sub(sv_month, num, date_lower, flags=re.IGNORECASE)
            break

    # Try common formats
    formats = ["%Y-%m-%d", "%d %m %Y", "%Y%m%d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Try extracting YYYY-MM-DD pattern
    match = re.search(r'(\d{4}-\d{2}-\d{2})', date_str)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            pass

    return None


# ============================================================================
# SCRAPER
# ============================================================================

async def scrape_konkurslistan(year: int, month: int) -> List[BankruptcyRecord]:
    """
    Scrape Konkurslistan.se for bankruptcies in given year/month.

    Returns list of BankruptcyRecord objects.
    """
    base_url = "https://www.konkurslistan.se"
    list_url = "https://www.konkurslistan.se/alla-konkurser"

    records = []
    seen_org_numbers = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            logger.info(f"Fetching {list_url}...")
            await page.goto(list_url, timeout=30000)
            await page.wait_for_load_state('networkidle', timeout=30000)
            await asyncio.sleep(2)  # Let JS render

            # Find all bankruptcy entries (links to /konkurser/{org_number})
            entries = await page.query_selector_all('a[href*="/konkurser/"]')
            logger.info(f"Found {len(entries)} potential entries")

            # Collect entry data
            entry_data_list = []
            for entry in entries[:150]:  # Limit to first 150
                try:
                    text = await entry.inner_text()
                    href = await entry.get_attribute('href')

                    if not href or 'page=' in href or len(text) < 20:
                        continue

                    entry_data_list.append((text, href))
                except Exception as e:
                    logger.debug(f"Error collecting entry: {e}")
                    continue

            # Parse each entry
            for text, href in entry_data_list:
                try:
                    record = parse_entry(text, href, base_url)

                    if not record:
                        continue

                    # Filter by year/month
                    if record.date:
                        if record.date.year != year or record.date.month != month:
                            continue

                    # Deduplicate
                    if record.org_number in seen_org_numbers:
                        continue
                    seen_org_numbers.add(record.org_number)

                    # Enrich with detail page (for administrator and court)
                    detail_url = urljoin(base_url, href)
                    await enrich_from_detail_page(page, record, detail_url)

                    records.append(record)
                    logger.info(f"  ✓ {record.company_name} ({record.org_number})")

                except Exception as e:
                    logger.debug(f"Error parsing entry: {e}")
                    continue

        finally:
            await browser.close()

    logger.info(f"Scraped {len(records)} bankruptcies for {year}-{month:02d}")
    return records


def parse_entry(text: str, href: str, base_url: str) -> Optional[BankruptcyRecord]:
    """
    Parse a Konkurslistan entry.

    Format:
        5567665616
        Company Name AB
        City, Region län
        Datum 2025-12-23
        Status Konkurs inledd
        Verksamhet (SNI) 46320 Description
        Anställda 1
        Omsättning 1359000
    """
    if not text or len(text) < 20:
        return None

    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Extract org number from URL
    org_number = ""
    if href:
        org_match = re.search(r'/konkurser/(\d{10})', href)
        if org_match:
            org_number = normalize_org_number(org_match.group(1))

    # If not in URL, try first line
    if not org_number:
        for line in lines:
            if re.match(r'^\d{10}$', line.replace('-', '')):
                org_number = normalize_org_number(line)
                break

    # Extract fields
    company_name = None
    location = ""
    region = ""
    date = None
    business_type = ""
    employees = None
    revenue = None

    for i, line in enumerate(lines):
        line_lower = line.lower()

        # Skip org number line
        if re.match(r'^\d{10}$', line.replace('-', '')):
            continue

        # Company name (ends with AB, HB, KB, etc.)
        if not company_name and re.search(r'\b(AB|HB|KB|Ek\.?\s*för\.?)\b', line):
            company_name = line.strip()
            continue

        # Location: "City, Region län" or just "City"
        if not location and ',' in line and 'län' in line_lower:
            parts = line.split(',')
            location = parts[0].strip()
            region = parts[1].strip() if len(parts) > 1 else ""
            continue

        # Sometimes location is just city name on a line by itself
        if not location and not company_name and len(line) > 3 and len(line) < 50:
            # Check if it looks like a city name (capitalized, not a date or number)
            if line[0].isupper() and not re.match(r'^\d', line) and 'datum' not in line_lower:
                # This might be location, but keep looking for company name first
                pass

        # Date
        if 'datum' in line_lower or re.match(r'^\d{4}-\d{2}-\d{2}$', line):
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
            if date_match:
                date = parse_swedish_date(date_match.group(1))
            continue

        # Business type - look for SNI code + description
        if 'verksamhet' in line_lower or 'sni' in line_lower:
            # Pattern: "Verksamhet (SNI) 46320 Partihandel med livsmedel"
            sni_match = re.search(r'(?:verksamhet|sni)[^\d]*(\d{5})\s+(.+)', line, re.IGNORECASE)
            if sni_match:
                business_type = sni_match.group(2).strip()
            else:
                # Sometimes it's just the description without code
                desc_match = re.search(r'(?:verksamhet|sni)[:\s]+([^\d][^\n]+)', line, re.IGNORECASE)
                if desc_match:
                    business_type = desc_match.group(1).strip()
            continue

        # Employees
        if 'anställda' in line_lower:
            emp_match = re.search(r'(\d+)', line)
            if emp_match:
                employees = int(emp_match.group(1))
            continue

        # Revenue
        if 'omsättning' in line_lower:
            rev_match = re.search(r'(\d[\d\s]*)', line)
            if rev_match:
                revenue = float(rev_match.group(1).replace(' ', ''))
            continue

    # If no company name, try to find it in first few lines
    if not company_name:
        for line in lines[1:5]:
            if len(line) > 5 and not re.match(r'^[\d\s,-]+$', line):
                if 'datum' not in line.lower() and 'status' not in line.lower():
                    company_name = line.strip()
                    break

    if not company_name:
        return None

    if not org_number:
        org_number = "000000-0000"

    return BankruptcyRecord(
        company_name=company_name,
        org_number=org_number,
        date=date,
        location=location,
        region=region,
        business_type=business_type,
        employees=employees,
        revenue=revenue,
    )


async def enrich_from_detail_page(page: Page, record: BankruptcyRecord, detail_url: str):
    """Enrich record with administrator and court from detail page."""
    try:
        await page.goto(detail_url, timeout=15000, wait_until='domcontentloaded')
        await asyncio.sleep(1)

        text_content = await page.inner_text('body')

        # Extract administrator
        admin_patterns = [
            r'(?:Konkurs)?[Ff]örvaltare[:\s]+([^\n]+)',
            r'Administrator[:\s]+([^\n]+)',
        ]

        for pattern in admin_patterns:
            match = re.search(pattern, text_content)
            if match:
                record.administrator = match.group(1).strip()
                break

        # Extract court - be specific to avoid matching company names
        court_patterns = [
            r'Tingsrätt[:\s]+([^\n]+)',  # Try labeled field first
            r'\b(Stockholms tingsrätt)\b',
            r'\b(Göteborgs tingsrätt)\b',
            r'\b(Malmö tingsrätt)\b',
            r'\b(Uppsala tingsrätt)\b',
            r'\b(Linköpings tingsrätt)\b',
            r'\b(Västerås tingsrätt)\b',
            r'\b(Örebro tingsrätt)\b',
            r'\b(Norrköpings tingsrätt)\b',
            r'\b(Helsingborgs tingsrätt)\b',
            r'\b(Jönköpings tingsrätt)\b',
            r'\b(Umeå tingsrätt)\b',
            r'\b(Lunds tingsrätt)\b',
            r'\b(Borås tingsrätt)\b',
            r'\b(Sundsvalls tingsrätt)\b',
            r'\b(Gävle tingsrätt)\b',
            r'\b(Eskilstuna tingsrätt)\b',
            r'\b(Karlstads tingsrätt)\b',
            r'\b(Växjö tingsrätt)\b',
            r'\b(Halmstads tingsrätt)\b',
            r'\b(Södertörns tingsrätt)\b',
            r'\b(Attunda tingsrätt)\b',
            # Generic pattern as last resort - must start with capital and end with tingsrätt
            r'(?:Domstol|Tingsrätt)[:\s]*([A-ZÅÄÖ][a-zåäöA-ZÅÄÖ\s]+tingsrätt)',
        ]

        for pattern in court_patterns:
            match = re.search(pattern, text_content, re.IGNORECASE)
            if match:
                court_name = match.group(1).strip() if match.lastindex else match.group(0).strip()
                # Validate it looks like a court name (not a company)
                if 'tingsrätt' in court_name.lower() and not any(suffix in court_name for suffix in ['AB', 'HB', 'KB']):
                    record.court = court_name
                    break

    except Exception as e:
        logger.debug(f"Could not enrich from detail page: {e}")


# ============================================================================
# FILTERING
# ============================================================================

def filter_records(records: List[BankruptcyRecord]) -> List[BankruptcyRecord]:
    """Filter records based on environment variables."""
    filter_regions = [r.strip() for r in os.getenv("FILTER_REGIONS", "").split(",") if r.strip()]
    filter_keywords = [k.strip().lower() for k in os.getenv("FILTER_INCLUDE_KEYWORDS", "").split(",") if k.strip()]
    min_employees = int(os.getenv("FILTER_MIN_EMPLOYEES", "0") or "0")
    min_revenue = float(os.getenv("FILTER_MIN_REVENUE", "0") or "0")

    filtered = []

    for record in records:
        # Region filter
        if filter_regions and record.region:
            if not any(region.lower() in record.region.lower() for region in filter_regions):
                continue

        # Keyword filter (match in company name or business type)
        if filter_keywords:
            searchable = f"{record.company_name} {record.business_type}".lower()
            if not any(kw in searchable for kw in filter_keywords):
                continue

        # Employee filter
        if min_employees > 0 and record.employees is not None:
            if record.employees < min_employees:
                continue

        # Revenue filter
        if min_revenue > 0 and record.revenue is not None:
            if record.revenue < min_revenue:
                continue

        filtered.append(record)

    return filtered


# ============================================================================
# EMAIL
# ============================================================================

def format_email_html(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate beautiful HTML email report with table."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Generate table rows
    table_rows = ""
    for i, r in enumerate(records, 1):
        date_str = r.date.strftime("%Y-%m-%d") if r.date else "Unknown"
        row_class = "even" if i % 2 == 0 else "odd"

        # Details for expandable section (secondary info only)
        details = []
        if r.administrator:
            details.append(f"<strong>Administrator:</strong> {r.administrator}")
        if r.employees is not None:
            details.append(f"<strong>Employees:</strong> {r.employees}")
        if r.revenue is not None:
            details.append(f"<strong>Revenue:</strong> {r.revenue:,.0f} SEK")

        details_html = " &nbsp;|&nbsp; ".join(details) if details else "No additional details"

        org_clean = r.org_number.replace('-', '')
        poit_link = f"https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}"

        location_display = r.location or "Unknown"
        if r.region and r.region != location_display:
            location_display += f", {r.region}"

        table_rows += f"""
        <tr class="{row_class}">
            <td style="text-align: center;">{i}</td>
            <td><strong>{r.company_name}</strong></td>
            <td style="text-align: center;"><code>{r.org_number}</code></td>
            <td style="text-align: center;">{date_str}</td>
            <td>{location_display}</td>
            <td>{r.court or 'Unknown'}</td>
            <td>{r.business_type or 'Not specified'}</td>
            <td style="text-align: center;">
                <a href="{poit_link}" style="color: #0066cc; text-decoration: none;">POIT ↗</a>
            </td>
        </tr>
        <tr class="{row_class}-detail">
            <td colspan="8" style="padding: 8px 20px; font-size: 12px; color: #555; background: #fafafa;">
                {details_html}
            </td>
        </tr>
        """

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 28px;
            font-weight: 600;
        }}
        .header p {{
            margin: 0;
            opacity: 0.9;
            font-size: 16px;
        }}
        .summary {{
            background: #f8fafc;
            padding: 20px 30px;
            border-bottom: 2px solid #e5e7eb;
        }}
        .summary-stat {{
            display: inline-block;
            margin-right: 30px;
            font-size: 14px;
        }}
        .summary-stat strong {{
            color: #1e3a8a;
            font-size: 24px;
            display: block;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th {{
            background: #1e40af;
            color: white;
            padding: 12px 10px;
            text-align: left;
            font-weight: 600;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        th:first-child {{
            text-align: center;
        }}
        td {{
            padding: 12px 10px;
            border-bottom: 1px solid #e5e7eb;
        }}
        tr.odd {{
            background: #ffffff;
        }}
        tr.even {{
            background: #f9fafb;
        }}
        tr.odd-detail, tr.even-detail {{
            background: #fafafa;
        }}
        tr:hover.odd, tr:hover.even {{
            background: #eff6ff;
        }}
        code {{
            background: #f1f5f9;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            color: #334155;
        }}
        a {{
            color: #0066cc;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .footer {{
            background: #f8fafc;
            padding: 20px 30px;
            text-align: center;
            font-size: 12px;
            color: #64748b;
            border-top: 2px solid #e5e7eb;
        }}
        .footer a {{
            color: #3b82f6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🇸🇪 Swedish Bankruptcy Report</h1>
            <p>{month_name}</p>
        </div>

        <div class="summary">
            <div class="summary-stat">
                <strong>{len(records)}</strong>
                <span>Total Bankruptcies</span>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width: 40px;">#</th>
                    <th style="width: 20%;">Company</th>
                    <th style="width: 120px;">Org Number</th>
                    <th style="width: 100px;">Date</th>
                    <th style="width: 15%;">Location</th>
                    <th style="width: 15%;">Court</th>
                    <th style="width: 20%;">Business Type</th>
                    <th style="width: 70px;">Link</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>

        <div class="footer">
            <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} UTC</p>
            <p>Data source: <a href="https://www.konkurslistan.se">Konkurslistan.se</a></p>
            <p style="margin-top: 10px; font-size: 11px;">
                Automated bankruptcy monitoring •
                <a href="https://poit.bolagsverket.se">POIT Official Gazette</a>
            </p>
        </div>
    </div>
</body>
</html>
    """

    return html


def format_email_plain(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate plain text email report as fallback."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    text = f"""
SWEDISH BANKRUPTCY REPORT - {month_name}
{'=' * 80}

Total bankruptcies: {len(records)}

"""

    for i, r in enumerate(records, 1):
        date_str = r.date.strftime("%Y-%m-%d") if r.date else "Unknown"
        org_clean = r.org_number.replace('-', '')

        text += f"""
{i}. {r.company_name} ({r.org_number})
   Date: {date_str}
   Location: {r.location or 'Unknown'}{', ' + r.region if r.region else ''}
   Court: {r.court or 'Unknown'}
   Administrator: {r.administrator or 'Unknown'}
   Business Type: {r.business_type or 'Not specified'}
"""

        if r.employees is not None:
            text += f"   Employees: {r.employees}\n"
        if r.revenue is not None:
            text += f"   Revenue: {r.revenue:,.0f} SEK\n"

        text += f"   POIT: https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}\n"

    text += f"""
{'=' * 80}
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Source: Konkurslistan.se
"""

    return text


def send_email(subject: str, html_body: str, plain_body: str):
    """Send HTML email with plain text fallback via SMTP."""
    sender_email = os.getenv('SENDER_EMAIL')
    sender_password = os.getenv('SENDER_PASSWORD')
    recipient_emails = os.getenv('RECIPIENT_EMAILS', '')

    if not sender_email or not sender_password:
        logger.error("Email credentials not configured (SENDER_EMAIL, SENDER_PASSWORD)")
        return

    if not recipient_emails:
        logger.error("No recipients configured (RECIPIENT_EMAILS)")
        return

    # Create multipart message with HTML and plain text
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = recipient_emails

    # Attach plain text and HTML versions
    part1 = MIMEText(plain_body, 'plain', 'utf-8')
    part2 = MIMEText(html_body, 'html', 'utf-8')

    msg.attach(part1)
    msg.attach(part2)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        logger.info(f"Email sent to {recipient_emails}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Main entry point."""
    # Get year/month from environment or use previous month
    now = datetime.now()
    year = int(os.getenv('YEAR', str(now.year)))
    month = int(os.getenv('MONTH', str(now.month)))

    # If running on 1st-3rd of month, default to previous month
    if os.getenv('MONTH') is None and now.day <= 3:
        month = month - 1 if month > 1 else 12
        if month == 12:
            year -= 1

    logger.info(f"=== Swedish Bankruptcy Monitor ===")
    logger.info(f"Processing: {year}-{month:02d}")

    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Step 1: Scrape
    records = await scrape_konkurslistan(year, month)

    if not records:
        logger.warning("No bankruptcies found!")
        return

    # Step 2: Filter
    filtered = filter_records(records)
    logger.info(f"Filtered to {len(filtered)} matching bankruptcies")

    if not filtered:
        logger.info("No bankruptcies match your filter criteria")
        return

    # Step 3: Email
    subject = f"🇸🇪 Swedish Bankruptcies - {month_name}"
    html_body = format_email_html(filtered, year, month)
    plain_body = format_email_plain(filtered, year, month)

    # Print plain text to console
    print("\n" + plain_body)

    # Send email
    if os.getenv('NO_EMAIL') != 'true':
        send_email(subject, html_body, plain_body)
    else:
        logger.info("Email sending skipped (NO_EMAIL=true)")


if __name__ == "__main__":
    asyncio.run(main())
