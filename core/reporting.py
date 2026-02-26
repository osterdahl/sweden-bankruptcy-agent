"""
Email report generation and sending for the Nordic Bankruptcy Monitor.

Extracts and generalizes the reporting logic from bankruptcy_monitor.py.
The format functions accept an optional country_name parameter to customize
the report title and header. Defaults to "Swedish" for backward compatibility.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from string import Template
from typing import List, Optional

from core.models import BankruptcyRecord

logger = logging.getLogger(__name__)

# Country flag emojis for email headers
_COUNTRY_EMOJI = {
    'Sweden': '\U0001f1f8\U0001f1ea',    # SE flag
    'Norway': '\U0001f1f3\U0001f1f4',    # NO flag
    'Denmark': '\U0001f1e9\U0001f1f0',   # DK flag
    'Finland': '\U0001f1eb\U0001f1ee',   # FI flag
}


def format_email_html(
    records: List[BankruptcyRecord],
    year: int,
    month: int,
    country_name: Optional[str] = None,
) -> str:
    """Generate modern card-based HTML email report with priority sections.

    Args:
        records: List of BankruptcyRecord to include.
        year: Report year.
        month: Report month.
        country_name: Display name (e.g. "Sweden", "Norway"). Defaults to "Swedish"
                      for backward compatibility with the original report title.
    """
    if country_name is None:
        country_name = "Swedish"
        report_title = "Swedish Bankruptcy Report"
    else:
        report_title = f"{country_name} Bankruptcy Report"

    emoji = _COUNTRY_EMOJI.get(country_name, '\U0001f1f8\U0001f1ea')

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
                    <span class="ai-score">Score: {r.ai_score}/10</span>
                    <span class="ai-text">{r.ai_reason}</span>
                </div>
                """

            # Company info section — use industry_code (aliased as sni_code)
            industry_code = r.industry_code
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
                        <span class="value"><code>{industry_code}</code> {r.industry_name}</span>
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
                if r.trustee_email:
                    trustee_parts.append(f"<a href='mailto:{r.trustee_email}' style='color:#1d4ed8'>{r.trustee_email}</a>")
                if r.trustee_address != 'N/A':
                    trustee_parts.append(r.trustee_address)

                trustee_text = " <span class='trustee-separator'>•</span> ".join(trustee_parts)

                trustee_section = f"""
                <div class="card-section trustee-section">
                    <span class="trustee-label">\U0001f464 Trustee Contact:</span>
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
                    <span class="card-number">#{i}</span>
                    {priority_badge}
                    <h3>{r.company_name}</h3>
                    <br>
                    <a href="{poit_link}" class="poit-link">View in POIT \u2197</a>
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
        sections_html += render_section(high_risk, "\u2b50 HIGH PRIORITY", "high", current_index)
        current_index += len(high_risk)

    if med_risk:
        sections_html += render_section(med_risk, "\u26a0\ufe0f MEDIUM PRIORITY", "medium", current_index)
        current_index += len(med_risk)

    if low_risk:
        sections_html += render_section(low_risk, "\u2139\ufe0f LOW PRIORITY", "low", current_index)
        current_index += len(low_risk)

    # Fallback for no scoring
    if no_score:
        sections_html += render_section(no_score, "Bankruptcies", "default", current_index)

    # Load HTML template and fill placeholders
    template_path = Path(__file__).parent.parent / 'email_template.html'
    template = Template(template_path.read_text(encoding='utf-8'))

    html = template.substitute(
        EMOJI=emoji,
        month_name=month_name,
        total_count=len(records),
        priority_summary=priority_summary,
        sections_html=sections_html,
        generated_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Replace the hardcoded title in the template with the country-specific one.
    # The template has "Swedish Bankruptcy Report" baked in; for other countries
    # we swap it. For Sweden (default) this is a no-op.
    html = html.replace('Swedish Bankruptcy Report', report_title)

    return html


def format_email_plain(
    records: List[BankruptcyRecord],
    year: int,
    month: int,
    country_name: Optional[str] = None,
) -> str:
    """Generate plain text email report with priority sections.

    Args:
        records: List of BankruptcyRecord to include.
        year: Report year.
        month: Report month.
        country_name: Display name (e.g. "Sweden", "Norway"). Defaults to "SWEDISH"
                      for backward compatibility.
    """
    if country_name is None:
        report_label = "SWEDISH"
    else:
        report_label = country_name.upper()

    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Split by priority
    high_risk = [r for r in records if r.priority == "HIGH"]
    med_risk = [r for r in records if r.priority == "MEDIUM"]
    low_risk = [r for r in records if r.priority == "LOW"]
    no_score = [r for r in records if not r.priority]

    # Header
    text = f"""
{report_label} BANKRUPTCY REPORT - {month_name}
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
            industry_code = r.industry_code

            section_text += f"""
{i}. {r.company_name} ({r.org_number})"""

            if r.priority and r.ai_reason:
                section_text += f"""
   AI Score: {r.ai_score}/10 | {r.ai_reason}"""

            section_text += f"""
   Date: {r.initiated_date}
   Region: {r.region}
   Court: {r.court}
   Industry: [{industry_code}] {r.industry_name}
   Trustee: {r.trustee}
   Firm: {r.trustee_firm}
   Address: {r.trustee_address}
"""

            if r.trustee_email:
                section_text += f"   Email: {r.trustee_email}\n"

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
        text += format_section(high_risk, "\u2b50 HIGH PRIORITY", current_index)
        current_index += len(high_risk)

    if med_risk:
        text += format_section(med_risk, "\u26a0\ufe0f MEDIUM PRIORITY", current_index)
        current_index += len(med_risk)

    if low_risk:
        text += format_section(low_risk, "\u2139\ufe0f LOW PRIORITY", current_index)
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


def send_email(subject: str, html_body: str, plain_body: str) -> None:
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
