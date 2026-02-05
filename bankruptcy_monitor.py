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
from typing import List, Optional, Tuple
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
    ai_score: Optional[int] = None      # 1-10 priority score
    ai_reason: Optional[str] = None     # Brief explanation
    priority: Optional[str] = None      # "HIGH", "MEDIUM", "LOW"


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
# AI SCORING (HYBRID)
# ============================================================================

# SNI code scoring table - High-value industries for data acquisition
HIGH_VALUE_SNI_CODES = {
    '26': 10,  # Computer/electronic/optical manufacturing
    '28': 9,   # Machinery and equipment
    '33': 8,   # Repair/installation of machinery
    '465': 9,  # ICT equipment wholesale
    '58': 8,   # Publishing
    '62': 10,  # Computer programming/consultancy
    '63': 9,   # Information services
    '69': 7,   # Legal/accounting
    '71': 8,   # Architectural/engineering
    '72': 10,  # Scientific R&D
    '749': 8,  # Other professional/scientific/technical
}

LOW_VALUE_SNI_CODES = {
    '56': 2,   # Food/beverage service
    '68': 3,   # Real estate
    '64': 2,   # Financial services (holding companies)
    '96': 2,   # Personal services
}

def calculate_base_score(record: BankruptcyRecord) -> int:
    """Rule-based scoring: SNI code + company size."""
    score = 5  # Neutral baseline

    # SNI code scoring (primary signal)
    sni = record.sni_code
    if sni and sni != 'N/A' and len(sni) >= 2:
        # Check 2-digit match
        sni_prefix = sni[:2]
        if sni_prefix in HIGH_VALUE_SNI_CODES:
            score = HIGH_VALUE_SNI_CODES[sni_prefix]
        elif sni_prefix in LOW_VALUE_SNI_CODES:
            score = LOW_VALUE_SNI_CODES[sni_prefix]
        # Check 3-digit match (more specific)
        if len(sni) >= 3:
            sni_3 = sni[:3]
            if sni_3 in HIGH_VALUE_SNI_CODES:
                score = HIGH_VALUE_SNI_CODES[sni_3]

    # Company size boost (larger = more data infrastructure)
    try:
        if record.employees and record.employees != 'N/A':
            emp_count = int(record.employees.replace(',', ''))
            if emp_count >= 50:
                score = min(score + 2, 10)
            elif emp_count >= 20:
                score = min(score + 1, 10)
    except:
        pass

    # Keyword analysis in company name
    data_keywords = ['data', 'tech', 'software', 'analytics', 'ai', 'cloud', 'digital']
    name_lower = record.company_name.lower()
    if any(kw in name_lower for kw in data_keywords):
        score = min(score + 1, 10)

    return score


def validate_with_ai(record: BankruptcyRecord) -> Tuple[int, str]:
    """Use Claude API to validate/refine high-priority scores."""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return (record.ai_score, "Rule-based only (no API key)")

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        prompt = f"""Analyze this Swedish bankruptcy for data acquisition value (1-10 scale):

Company: {record.company_name}
Industry: [{record.sni_code}] {record.industry_name}
Employees: {record.employees}
Revenue: {record.net_sales}
Assets: {record.total_assets}
Region: {record.region}

Focus on: likely data infrastructure, customer databases, valuable IP.
Respond with ONLY: SCORE:X REASON:brief_explanation"""

        message = client.messages.create(
            model="claude-3-haiku-20240307",  # Cheaper model for validation
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )

        response = message.content[0].text.strip()

        # Parse "SCORE:8 REASON:Tech company with large customer database"
        score_match = re.search(r'SCORE:(\d+)', response)
        reason_match = re.search(r'REASON:(.+)', response)

        if score_match:
            ai_score = int(score_match.group(1))
            ai_reason = reason_match.group(1) if reason_match else response
            return (ai_score, ai_reason)
        else:
            return (record.ai_score, f"AI validation: {response}")

    except Exception as e:
        logger.debug(f"AI validation failed for {record.company_name}: {e}")
        return (record.ai_score, "Rule-based score (AI unavailable)")


def score_bankruptcies(records: List[BankruptcyRecord]) -> List[BankruptcyRecord]:
    """Hybrid scoring: rule-based filter + AI validation for high candidates."""
    ai_enabled = os.getenv('AI_SCORING_ENABLED', 'false').lower() == 'true'

    if not ai_enabled:
        # Feature disabled: add null scores, maintain current behavior
        for r in records:
            r.ai_score = None
            r.ai_reason = None
            r.priority = None
        return records

    logger.info("AI scoring enabled - analyzing bankruptcies...")

    # Phase 1: Rule-based scoring for ALL records
    for record in records:
        base_score = calculate_base_score(record)
        record.ai_score = base_score

        # Assign priority tier
        if base_score >= 8:
            record.priority = "HIGH"
            record.ai_reason = "High-value industry + data-rich profile"
        elif base_score >= 5:
            record.priority = "MEDIUM"
            record.ai_reason = "Moderate data acquisition potential"
        else:
            record.priority = "LOW"
            record.ai_reason = "Limited data assets expected"

    # Phase 2: AI validation for HIGH priority only (cost control)
    high_priority = [r for r in records if r.priority == "HIGH"]

    if high_priority and os.getenv('ANTHROPIC_API_KEY'):
        logger.info(f"Validating {len(high_priority)} HIGH priority candidates with Claude API...")

        for record in high_priority:
            ai_score, ai_reason = validate_with_ai(record)
            record.ai_score = ai_score
            record.ai_reason = ai_reason

            # Re-classify if AI disagrees
            if ai_score < 8:
                record.priority = "MEDIUM" if ai_score >= 5 else "LOW"

    return records


# ============================================================================
# EMAIL
# ============================================================================

def format_email_html(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate modern card-based HTML email report with priority sections."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Split by priority
    high_risk = [r for r in records if r.priority == "HIGH"]
    med_risk = [r for r in records if r.priority == "MEDIUM"]
    low_risk = [r for r in records if r.priority == "LOW"]
    no_score = [r for r in records if not r.priority]

    # Helper function to render a card-based section
    def render_section(section_records, title, badge_color, global_start_index):
        if not section_records:
            return ""

        cards = ""
        for i, r in enumerate(section_records, global_start_index):
            org_clean = r.org_number.replace('-', '')
            poit_link = f"https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}"

            # AI reasoning section (prominent if available)
            ai_section = ""
            if r.priority and r.ai_reason:
                ai_section = f"""
                <div class="card-ai-reason">
                    <div class="ai-score">Score: {r.ai_score}/10</div>
                    <div class="ai-text">{r.ai_reason}</div>
                </div>
                """

            # Company info section
            company_info = f"""
            <div class="card-section">
                <div class="card-row">
                    <div class="card-col">
                        <span class="label">Org Number</span>
                        <span class="value"><code>{r.org_number}</code></span>
                    </div>
                    <div class="card-col">
                        <span class="label">Initiated</span>
                        <span class="value">{r.initiated_date}</span>
                    </div>
                    <div class="card-col">
                        <span class="label">Region</span>
                        <span class="value">{r.region}</span>
                    </div>
                </div>
                <div class="card-row">
                    <div class="card-col full-width">
                        <span class="label">Court</span>
                        <span class="value">{r.court}</span>
                    </div>
                </div>
                <div class="card-row">
                    <div class="card-col full-width">
                        <span class="label">Industry</span>
                        <span class="value"><code>{r.sni_code}</code> {r.industry_name}</span>
                    </div>
                </div>
            </div>
            """

            # Trustee info section (only if available) - single line with separators
            trustee_section = ""
            if r.trustee != 'N/A' or r.trustee_firm != 'N/A' or r.trustee_address != 'N/A':
                trustee_parts = []
                if r.trustee != 'N/A':
                    trustee_parts.append(f"<strong>{r.trustee}</strong>")
                if r.trustee_firm != 'N/A':
                    trustee_parts.append(r.trustee_firm)
                if r.trustee_address != 'N/A':
                    trustee_parts.append(r.trustee_address)

                trustee_text = " <span class='trustee-separator'>•</span> ".join(trustee_parts)

                trustee_section = f"""
                <div class="card-section trustee-section">
                    <span class="trustee-label">👤 Trustee Contact:</span>
                    <span class="trustee-text">{trustee_text}</span>
                </div>
                """

            # Financials section (only if available)
            financials_section = ""
            if r.employees != 'N/A' or r.net_sales != 'N/A' or r.total_assets != 'N/A':
                financial_cols = []
                if r.employees != 'N/A':
                    financial_cols.append(f"""
                    <div class="card-col">
                        <span class="label">Employees</span>
                        <span class="value">{r.employees}</span>
                    </div>
                    """)
                if r.net_sales != 'N/A':
                    financial_cols.append(f"""
                    <div class="card-col">
                        <span class="label">Net Sales</span>
                        <span class="value">{r.net_sales}</span>
                    </div>
                    """)
                if r.total_assets != 'N/A':
                    financial_cols.append(f"""
                    <div class="card-col">
                        <span class="label">Total Assets</span>
                        <span class="value">{r.total_assets}</span>
                    </div>
                    """)

                financials_section = f"""
                <div class="card-section financials-section">
                    <h4>Financials</h4>
                    <div class="card-row">
                        {''.join(financial_cols)}
                    </div>
                </div>
                """

            # Priority badge
            priority_badge = f'<span class="priority-badge {badge_color}">{r.priority}</span>' if r.priority else ''

            cards += f"""
            <div class="bankruptcy-card">
                <div class="card-header">
                    <div class="card-title">
                        <span class="card-number">#{i}</span>
                        {priority_badge}
                        <h3>{r.company_name}</h3>
                    </div>
                    <a href="{poit_link}" class="poit-link">View in POIT ↗</a>
                </div>
                {ai_section}
                {company_info}
                {trustee_section}
                {financials_section}
            </div>
            """

        section_html = f"""
        <div class="section-header {badge_color}">
            <h2>{title} ({len(section_records)})</h2>
        </div>
        <div class="cards-container">
            {cards}
        </div>
        """

        return section_html

    # Priority summary (if AI scoring enabled)
    priority_summary = ""
    if high_risk or med_risk or low_risk:
        priority_summary = f"""
        <div class="priority-summary">
            <div class="priority-stat high">
                <strong>{len(high_risk)}</strong>
                <span>HIGH Priority</span>
            </div>
            <div class="priority-stat medium">
                <strong>{len(med_risk)}</strong>
                <span>MEDIUM Priority</span>
            </div>
            <div class="priority-stat low">
                <strong>{len(low_risk)}</strong>
                <span>LOW Priority</span>
            </div>
        </div>
        """

    # Render sections in priority order
    sections_html = ""
    current_index = 1

    if high_risk:
        sections_html += render_section(high_risk, "⭐ HIGH PRIORITY", "high", current_index)
        current_index += len(high_risk)

    if med_risk:
        sections_html += render_section(med_risk, "⚠️ MEDIUM PRIORITY", "medium", current_index)
        current_index += len(med_risk)

    if low_risk:
        sections_html += render_section(low_risk, "ℹ️ LOW PRIORITY", "low", current_index)
        current_index += len(low_risk)

    # Fallback for no scoring
    if no_score:
        sections_html += render_section(no_score, "Bankruptcies", "default", current_index)

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
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
            color: white;
            padding: 40px 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 32px;
            font-weight: 600;
        }}
        .header p {{
            margin: 0;
            opacity: 0.9;
            font-size: 18px;
        }}
        .summary {{
            background: #f8fafc;
            padding: 24px 30px;
            border-bottom: 2px solid #e5e7eb;
        }}
        .summary-stat {{
            display: inline-block;
            margin-right: 30px;
            font-size: 14px;
        }}
        .summary-stat strong {{
            color: #1e3a8a;
            font-size: 28px;
            display: block;
            font-weight: 700;
        }}
        .priority-summary {{
            display: flex;
            gap: 24px;
            padding: 24px 30px;
            background: #f8fafc;
            border-bottom: 2px solid #e5e7eb;
        }}
        .priority-stat {{
            text-align: center;
            flex: 1;
            padding: 12px;
            border-radius: 8px;
            background: white;
        }}
        .priority-stat.high strong {{
            color: #dc2626;
            font-size: 36px;
            display: block;
            font-weight: 700;
        }}
        .priority-stat.medium strong {{
            color: #ea580c;
            font-size: 36px;
            display: block;
            font-weight: 700;
        }}
        .priority-stat.low strong {{
            color: #64748b;
            font-size: 36px;
            display: block;
            font-weight: 700;
        }}
        .priority-stat span {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: #64748b;
            font-weight: 600;
        }}
        .section-header {{
            padding: 20px 30px;
            margin-top: 8px;
            border-left: 5px solid;
        }}
        .section-header.high {{
            background: #fef2f2;
            border-left-color: #dc2626;
        }}
        .section-header.medium {{
            background: #fff7ed;
            border-left-color: #ea580c;
        }}
        .section-header.low {{
            background: #f8fafc;
            border-left-color: #94a3b8;
        }}
        .section-header h2 {{
            margin: 0;
            font-size: 20px;
            font-weight: 700;
        }}
        .section-header.high h2 {{
            color: #dc2626;
        }}
        .section-header.medium h2 {{
            color: #ea580c;
        }}
        .section-header.low h2 {{
            color: #64748b;
        }}
        .priority-badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .priority-badge.high {{
            background: #fee2e2;
            color: #dc2626;
        }}
        .priority-badge.medium {{
            background: #ffedd5;
            color: #ea580c;
        }}
        .priority-badge.low {{
            background: #f1f5f9;
            color: #64748b;
        }}
        .cards-container {{
            padding: 16px 30px 30px 30px;
        }}
        .bankruptcy-card {{
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            margin-bottom: 20px;
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            transition: box-shadow 0.2s ease;
        }}
        .bankruptcy-card:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 2px solid #f1f5f9;
        }}
        .card-title {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
        }}
        .card-number {{
            background: #f1f5f9;
            color: #64748b;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
        }}
        .card-title h3 {{
            margin: 0;
            font-size: 20px;
            font-weight: 700;
            color: #1e293b;
        }}
        .poit-link {{
            background: #3b82f6;
            color: white;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            white-space: nowrap;
            transition: background 0.2s ease;
        }}
        .poit-link:hover {{
            background: #2563eb;
            text-decoration: none;
        }}
        .card-ai-reason {{
            background: #eff6ff;
            border: 1px solid #bfdbfe;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 20px;
            display: flex;
            gap: 16px;
            align-items: flex-start;
        }}
        .ai-score {{
            background: #3b82f6;
            color: white;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 700;
            white-space: nowrap;
        }}
        .ai-text {{
            flex: 1;
            font-size: 14px;
            color: #1e40af;
            line-height: 1.6;
        }}
        .card-section {{
            margin-bottom: 20px;
        }}
        .card-section h4 {{
            margin: 0 0 12px 0;
            font-size: 14px;
            font-weight: 700;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .card-row {{
            display: flex;
            gap: 20px;
            margin-bottom: 12px;
            align-items: flex-start;
        }}
        .card-row:last-child {{
            margin-bottom: 0;
        }}
        .card-col {{
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 4px;
            align-items: flex-start;
        }}
        .card-col.full-width {{
            flex: none;
            width: 100%;
        }}
        .label {{
            font-size: 12px;
            color: #64748b;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            line-height: 1.2;
        }}
        .value {{
            font-size: 15px;
            color: #1e293b;
            font-weight: 500;
            line-height: 1.4;
        }}
        .trustee-section {{
            background: #fefce8;
            border-left: 3px solid #facc15;
            border-radius: 6px;
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .trustee-label {{
            font-size: 13px;
            font-weight: 700;
            color: #854d0e;
            white-space: nowrap;
        }}
        .trustee-text {{
            font-size: 14px;
            color: #713f12;
            flex: 1;
            line-height: 1.5;
        }}
        .trustee-text strong {{
            font-weight: 700;
        }}
        .trustee-separator {{
            color: #ca8a04;
            font-weight: 600;
            padding: 0 4px;
        }}
        .financials-section {{
            background: #f0fdf4;
            border: 1px solid #bbf7d0;
            border-radius: 8px;
            padding: 16px;
        }}
        .financials-section h4 {{
            color: #166534;
        }}
        code {{
            background: #f1f5f9;
            padding: 3px 7px;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            color: #334155;
            font-weight: 600;
        }}
        .footer {{
            background: #f8fafc;
            padding: 24px 30px;
            text-align: center;
            font-size: 12px;
            color: #64748b;
            border-top: 2px solid #e5e7eb;
        }}
        .footer a {{
            color: #3b82f6;
            text-decoration: none;
        }}
        .footer a:hover {{
            text-decoration: underline;
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

        {priority_summary}

        {sections_html}

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
    """Generate plain text email report with priority sections."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Split by priority
    high_risk = [r for r in records if r.priority == "HIGH"]
    med_risk = [r for r in records if r.priority == "MEDIUM"]
    low_risk = [r for r in records if r.priority == "LOW"]
    no_score = [r for r in records if not r.priority]

    # Header
    text = f"""
SWEDISH BANKRUPTCY REPORT - {month_name}
{'=' * 80}

SUMMARY
Total: {len(records)}"""

    if high_risk or med_risk or low_risk:
        text += f" | HIGH: {len(high_risk)} | MEDIUM: {len(med_risk)} | LOW: {len(low_risk)}"

    text += "\n\n"

    # Helper function to format a section
    def format_section(section_records, title, global_start_index):
        if not section_records:
            return ""

        section_text = f"""
{'=' * 80}
{title} ({len(section_records)})
{'=' * 80}

"""
        for i, r in enumerate(section_records, global_start_index):
            org_clean = r.org_number.replace('-', '')

            section_text += f"""
{i}. {r.company_name} ({r.org_number})"""

            if r.priority and r.ai_reason:
                section_text += f"""
   AI Score: {r.ai_score}/10 | {r.ai_reason}"""

            section_text += f"""
   Date: {r.initiated_date}
   Region: {r.region}
   Court: {r.court}
   Industry: [{r.sni_code}] {r.industry_name}
   Trustee: {r.trustee}
   Firm: {r.trustee_firm}
   Address: {r.trustee_address}
"""

            if r.employees != 'N/A':
                section_text += f"   Employees: {r.employees}\n"
            if r.net_sales != 'N/A':
                section_text += f"   Net Sales: {r.net_sales}\n"
            if r.total_assets != 'N/A':
                section_text += f"   Total Assets: {r.total_assets}\n"

            section_text += f"   POIT: https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}\n"

        return section_text

    # Render sections in priority order
    current_index = 1

    if high_risk:
        text += format_section(high_risk, "⭐ HIGH PRIORITY", current_index)
        current_index += len(high_risk)

    if med_risk:
        text += format_section(med_risk, "⚠️ MEDIUM PRIORITY", current_index)
        current_index += len(med_risk)

    if low_risk:
        text += format_section(low_risk, "ℹ️ LOW PRIORITY", current_index)
        current_index += len(low_risk)

    # Fallback for no scoring
    if no_score:
        text += format_section(no_score, "BANKRUPTCIES", current_index)

    # Footer
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

    # AI Scoring
    scored = score_bankruptcies(filtered)
    high_count = len([r for r in scored if r.priority == "HIGH"])
    if os.getenv('AI_SCORING_ENABLED', 'false').lower() == 'true':
        logger.info(f"AI scoring: {high_count} HIGH priority, {len(scored)-high_count} other")

    # Generate email
    month_name = datetime(year, month, 1).strftime("%B %Y")
    subject = f"Swedish Bankruptcy Report - {month_name} ({len(scored)} bankruptcies)"
    html_body = format_email_html(scored, year, month)
    plain_body = format_email_plain(scored, year, month)

    # Send or print
    if os.getenv('NO_EMAIL', '').lower() == 'true':
        logger.info("Email sending skipped (NO_EMAIL=true)")
        print(plain_body)

        # Save HTML to /tmp for preview
        html_path = '/tmp/bankruptcy_email_sample.html'
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_body)
        logger.info(f"HTML preview saved to {html_path}")
    else:
        send_email(subject, html_body, plain_body)


if __name__ == '__main__':
    main()
