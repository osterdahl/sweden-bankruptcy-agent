#!/usr/bin/env python3
"""
Swedish Bankruptcy Monitor - TIC.io Version

Scrapes TIC.io open data for monthly bankruptcy announcements.
Single source, complete data, no CAPTCHA, radical simplicity.

Data source: https://tic.io/en/oppna-data/konkurser (free, public)
"""

import logging
import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class BankruptcyRecord:
    """Bankruptcy record from TIC.io."""
    company_name: str
    org_number: str
    initiated_date: str
    court: str
    sni_code: str
    industry_name: str
    trustee: str
    trustee_firm: str
    trustee_address: str
    employees: str
    net_sales: str
    total_assets: str
    region: str = ""


# ============================================================================
# SCRAPER
# ============================================================================

def scrape_tic_bankruptcies(year: int, month: int, max_pages: int = 10) -> List[BankruptcyRecord]:
    """Scrape TIC.io bankruptcies for specified year/month."""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for page_num in range(1, max_pages + 1):
            logger.info(f'Fetching TIC.io page {page_num}...')

            url = f'https://tic.io/en/oppna-data/konkurser?pageNumber={page_num}&pageSize=100&q=&sortBy=initiatedDate%3Adesc'
            page.goto(url, timeout=60000)
            page.wait_for_selector('.bankruptcy-card', timeout=10000)
            page.wait_for_timeout(1000)

            cards = page.query_selector_all('.bankruptcy-card')
            logger.info(f'  Found {len(cards)} cards on page {page_num}')

            page_had_target_month = False
            found_past_target = False

            for card in cards:
                try:
                    # Extract initiated date
                    date_value = card.query_selector('.bankruptcy-card__dates .bankruptcy-card__value')
                    initiated_date = date_value.inner_text().strip() if date_value else None

                    if not initiated_date:
                        continue

                    # Parse and filter by month/year
                    parts = initiated_date.split('/')
                    if len(parts) != 3:
                        continue

                    init_month = int(parts[0])
                    init_year = int(parts[2])

                    if init_month == month and init_year == year:
                        page_had_target_month = True
                    elif init_month < month and init_year == year:
                        found_past_target = True
                        break
                    elif init_year < year:
                        found_past_target = True
                        break
                    elif not (init_month == month and init_year == year):
                        continue

                    # Extract company name
                    name_elem = card.query_selector('.bankruptcy-card__name a')
                    company_name = name_elem.inner_text().strip() if name_elem else 'N/A'

                    # Extract org number
                    org_elem = card.query_selector('.bankruptcy-card__org-number')
                    org_number = org_elem.inner_text().strip() if org_elem else 'N/A'

                    # Extract region
                    region_elem = card.query_selector('.bankruptcy-card__detail .bankruptcy-card__value')
                    region = region_elem.inner_text().strip() if region_elem else 'N/A'

                    # Extract court
                    court_elem = card.query_selector('.bankruptcy-card__court .bankruptcy-card__value')
                    if court_elem:
                        court_text = court_elem.inner_text().strip()
                        court = court_text.split('\n')[0]
                    else:
                        court = 'N/A'

                    # Extract SNI code and industry
                    sni_items = card.query_selector_all('.bankruptcy-card__sni-item')
                    sni_code = 'N/A'
                    industry_name = 'N/A'

                    if sni_items and len(sni_items) > 0:
                        first_sni = sni_items[0]
                        code_elem = first_sni.query_selector('.bankruptcy-card__sni-code')
                        name_elem = first_sni.query_selector('.bankruptcy-card__sni-name')
                        if code_elem:
                            sni_code = code_elem.inner_text().strip() or 'N/A'
                        if name_elem:
                            industry_name = name_elem.inner_text().strip() or 'N/A'

                    # Extract trustee
                    trustee_name_elem = card.query_selector('.bankruptcy-card__trustee-name')
                    trustee = trustee_name_elem.inner_text().strip() if trustee_name_elem else 'N/A'

                    trustee_firm_elem = card.query_selector('.bankruptcy-card__trustee-company')
                    trustee_firm = trustee_firm_elem.inner_text().strip() if trustee_firm_elem else 'N/A'
                    trustee_firm = re.sub(r'^c/o\s+', '', trustee_firm)

                    # Extract trustee address
                    address_elem = card.query_selector('.bankruptcy-card__trustee-address')
                    trustee_address = address_elem.inner_text().strip().replace('\n', ', ') if address_elem else 'N/A'

                    # Extract financial data
                    financial_items = card.query_selector_all('.bankruptcy-card__financial-item')
                    employees = 'N/A'
                    net_sales = 'N/A'
                    total_assets = 'N/A'

                    for item in financial_items:
                        label = item.query_selector('.bankruptcy-card__financial-label')
                        value = item.query_selector('.bankruptcy-card__financial-value')

                        if not label or not value:
                            continue

                        label_text = label.inner_text().strip()
                        value_text = value.inner_text().strip()

                        if 'Number of employees' in label_text:
                            employees = value_text
                        elif 'Net sales' in label_text:
                            net_sales = value_text
                        elif 'Total assets' in label_text:
                            total_assets = value_text

                    results.append(BankruptcyRecord(
                        company_name=company_name,
                        org_number=org_number,
                        initiated_date=initiated_date,
                        court=court,
                        sni_code=sni_code,
                        industry_name=industry_name,
                        trustee=trustee,
                        trustee_firm=trustee_firm,
                        trustee_address=trustee_address,
                        employees=employees,
                        net_sales=net_sales,
                        total_assets=total_assets,
                        region=region
                    ))

                except Exception as e:
                    logger.warning(f'Error processing card: {e}')
                    continue

            # Stop pagination if we've passed the target month
            if found_past_target:
                logger.info(f'Passed target month {year}-{month:02d}, stopping pagination')
                break

            # Continue if we haven't found target month yet (still in future months)
            if not page_had_target_month:
                logger.debug(f'No matches on page {page_num}, continuing to next page')
                continue

        browser.close()

    return results


# ============================================================================
# FILTERING
# ============================================================================

def filter_records(records: List[BankruptcyRecord]) -> List[BankruptcyRecord]:
    """Filter records based on environment variables."""
    filter_regions = [r.strip() for r in os.getenv("FILTER_REGIONS", "").split(",") if r.strip()]
    filter_keywords = [k.strip().lower() for k in os.getenv("FILTER_INCLUDE_KEYWORDS", "").split(",") if k.strip()]
    min_employees = int(os.getenv("FILTER_MIN_EMPLOYEES", "0") or "0")

    filtered = []

    for record in records:
        # Region filter
        if filter_regions and record.region:
            if not any(region.lower() in record.region.lower() for region in filter_regions):
                continue

        # Keyword filter
        if filter_keywords:
            searchable = f"{record.company_name} {record.industry_name}".lower()
            if not any(kw in searchable for kw in filter_keywords):
                continue

        # Employee filter
        if min_employees > 0 and record.employees != 'N/A':
            try:
                emp_count = int(record.employees.replace(',', ''))
                if emp_count < min_employees:
                    continue
            except:
                pass

        filtered.append(record)

    return filtered


# ============================================================================
# EMAIL
# ============================================================================

def format_email_html(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate HTML email report."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    table_rows = ""
    for i, r in enumerate(records, 1):
        row_class = "even" if i % 2 == 0 else "odd"

        # Details section
        details = []
        if r.trustee != 'N/A':
            details.append(f"<strong>Trustee:</strong> {r.trustee}")
        if r.trustee_firm != 'N/A':
            details.append(f"<strong>Firm:</strong> {r.trustee_firm}")
        if r.trustee_address != 'N/A':
            details.append(f"<strong>Address:</strong> {r.trustee_address}")
        if r.employees != 'N/A':
            details.append(f"<strong>Employees:</strong> {r.employees}")
        if r.net_sales != 'N/A':
            details.append(f"<strong>Net Sales:</strong> {r.net_sales}")
        if r.total_assets != 'N/A':
            details.append(f"<strong>Total Assets:</strong> {r.total_assets}")

        details_html = " &nbsp;|&nbsp; ".join(details) if details else "No details"

        org_clean = r.org_number.replace('-', '')
        poit_link = f"https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}"

        table_rows += f"""
        <tr class="{row_class}">
            <td style="text-align: center;">{i}</td>
            <td><strong>{r.company_name}</strong></td>
            <td style="text-align: center;"><code>{r.org_number}</code></td>
            <td style="text-align: center;">{r.initiated_date}</td>
            <td>{r.region}</td>
            <td>{r.court}</td>
            <td><code>{r.sni_code}</code> {r.industry_name}</td>
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
            max-width: 1400px;
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
                    <th style="width: 18%;">Company</th>
                    <th style="width: 120px;">Org Number</th>
                    <th style="width: 100px;">Date</th>
                    <th style="width: 12%;">Region</th>
                    <th style="width: 15%;">Court</th>
                    <th style="width: 25%;">Industry</th>
                    <th style="width: 70px;">Link</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>

        <div class="footer">
            <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} UTC</p>
            <p>Data source: <a href="https://tic.io/en/oppna-data/konkurser">TIC.io Open Data</a></p>
        </div>
    </div>
</body>
</html>
    """

    return html


def format_email_plain(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate plain text email report."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    text = f"""
SWEDISH BANKRUPTCY REPORT - {month_name}
{'=' * 80}

Total bankruptcies: {len(records)}

"""

    for i, r in enumerate(records, 1):
        org_clean = r.org_number.replace('-', '')

        text += f"""
{i}. {r.company_name} ({r.org_number})
   Date: {r.initiated_date}
   Region: {r.region}
   Court: {r.court}
   Industry: [{r.sni_code}] {r.industry_name}
   Trustee: {r.trustee}
   Firm: {r.trustee_firm}
   Address: {r.trustee_address}
"""

        if r.employees != 'N/A':
            text += f"   Employees: {r.employees}\n"
        if r.net_sales != 'N/A':
            text += f"   Net Sales: {r.net_sales}\n"
        if r.total_assets != 'N/A':
            text += f"   Total Assets: {r.total_assets}\n"

        text += f"   POIT: https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}\n"

    text += f"""
{'=' * 80}
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Source: TIC.io Open Data (https://tic.io/en/oppna-data/konkurser)
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
        logger.error("No recipient emails configured (RECIPIENT_EMAILS)")
        return

    recipients = [email.strip() for email in recipient_emails.split(',')]

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = ', '.join(recipients)

    part1 = MIMEText(plain_body, 'plain')
    part2 = MIMEText(html_body, 'html')

    msg.attach(part1)
    msg.attach(part2)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())
            logger.info(f"Email sent successfully to {len(recipients)} recipients")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point."""
    logger.info("=== Swedish Bankruptcy Monitor (TIC.io) ===")

    # Determine target month
    year_env = os.getenv('YEAR')
    month_env = os.getenv('MONTH')

    year = int(year_env) if year_env else datetime.now().year
    month = int(month_env) if month_env else datetime.now().month

    # Auto-select previous month if in first 7 days (only when not explicitly set)
    if not month_env and not year_env and datetime.now().day <= 7:
        month = month - 1 if month > 1 else 12
        year = year - 1 if month == 12 else year

    logger.info(f"Processing: {year}-{month:02d}")

    # Scrape bankruptcies
    records = scrape_tic_bankruptcies(year, month)
    logger.info(f"Scraped {len(records)} bankruptcies for {year}-{month:02d}")

    # Filter
    filtered = filter_records(records)
    logger.info(f"Filtered to {len(filtered)} matching bankruptcies")

    # Generate email
    month_name = datetime(year, month, 1).strftime("%B %Y")
    subject = f"Swedish Bankruptcy Report - {month_name} ({len(filtered)} bankruptcies)"
    html_body = format_email_html(filtered, year, month)
    plain_body = format_email_plain(filtered, year, month)

    # Send or print
    if os.getenv('NO_EMAIL', '').lower() == 'true':
        logger.info("Email sending skipped (NO_EMAIL=true)")
        print(plain_body)
    else:
        send_email(subject, html_body, plain_body)


if __name__ == '__main__':
    main()
