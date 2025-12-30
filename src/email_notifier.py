"""
Email notification module for bankruptcy reports.
Sends formatted HTML emails with bankruptcy data in tables.
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

from .models import BankruptcyRecord
from config import EmailConfig

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Send bankruptcy report emails."""
    
    def __init__(self, config: EmailConfig):
        self.config = config
    
    def send_monthly_report(
        self,
        records: list[BankruptcyRecord],
        year: int,
        month: int,
        total_found: int,
        subject_prefix: str = ""
    ) -> bool:
        """
        Send monthly bankruptcy report email.
        
        Args:
            records: Filtered bankruptcy records to include
            year: Report year
            month: Report month
            total_found: Total bankruptcies found (before filtering)
            subject_prefix: Optional prefix for email subject
        
        Returns:
            True if email sent successfully
        """
        if not self.config.recipient_emails:
            logger.warning("No recipient emails configured")
            return False
        
        month_name = datetime(year, month, 1).strftime("%B %Y")
        subject = f"{subject_prefix}Swedish Bankruptcy Report - {month_name}"
        
        html_content = self._generate_html_report(records, year, month, total_found)
        text_content = self._generate_text_report(records, year, month, total_found)
        
        return self._send_email(subject, html_content, text_content)
    
    def _generate_html_report(
        self,
        records: list[BankruptcyRecord],
        year: int,
        month: int,
        total_found: int
    ) -> str:
        """Generate HTML email content with styled table."""
        month_name = datetime(year, month, 1).strftime("%B %Y")
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        .summary {{
            background: #f8f9fa;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .summary-item {{
            display: inline-block;
            margin-right: 30px;
        }}
        .summary-number {{
            font-size: 24px;
            font-weight: bold;
            color: #3498db;
        }}
        .summary-label {{
            font-size: 14px;
            color: #666;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            font-size: 14px;
        }}
        th {{
            background: #3498db;
            color: white;
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
        }}
        td {{
            padding: 10px 8px;
            border-bottom: 1px solid #e0e0e0;
        }}
        tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        tr:hover {{
            background: #e8f4f8;
        }}
        .company-name {{
            font-weight: 600;
            color: #2c3e50;
        }}
        .org-number {{
            font-family: 'Courier New', monospace;
            color: #666;
            font-size: 12px;
        }}
        .date {{
            white-space: nowrap;
        }}
        .revenue {{
            text-align: right;
            font-family: 'Courier New', monospace;
        }}
        .employees {{
            text-align: center;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
            font-size: 12px;
            color: #666;
        }}
        .no-data {{
            color: #999;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <h1>🇸🇪 Swedish Bankruptcy Report</h1>
    <p style="font-size: 18px; color: #666;">{month_name}</p>
    
    <div class="summary">
        <div class="summary-item">
            <div class="summary-number">{total_found}</div>
            <div class="summary-label">Total Bankruptcies</div>
        </div>
        <div class="summary-item">
            <div class="summary-number">{len(records)}</div>
            <div class="summary-label">Matching Your Criteria</div>
        </div>
        <div class="summary-item">
            <div class="summary-number">{self._format_percentage(len(records), total_found)}%</div>
            <div class="summary-label">Match Rate</div>
        </div>
    </div>
"""
        
        if records:
            html += """
    <table>
        <thead>
            <tr>
                <th>Company</th>
                <th>Date</th>
                <th>Business Type</th>
                <th>Employees</th>
                <th>Revenue (SEK)</th>
                <th>Location</th>
                <th>Court</th>
                <th>Administrator</th>
                <th>Contact</th>
            </tr>
        </thead>
        <tbody>
"""
            for record in records:
                company = record.company
                admin = record.administrator

                # Build administrator info
                admin_info = '<span class="no-data">-</span>'
                if admin:
                    admin_info = f'<strong>{self._escape(admin.name)}</strong>'
                    if admin.law_firm:
                        admin_info += f'<br><small style="color: #666;">{self._escape(admin.law_firm)}</small>'

                # Build contact info
                contact_info = '<span class="no-data">-</span>'
                if admin and (admin.email or admin.phone):
                    contact_parts = []
                    if admin.email:
                        contact_parts.append(f'<a href="mailto:{admin.email}" style="color: #3498db; text-decoration: none;">{self._escape(admin.email)}</a>')
                    if admin.phone:
                        contact_parts.append(f'<span style="color: #666;">📞 {self._escape(admin.phone)}</span>')
                    contact_info = '<br>'.join(contact_parts)

                html += f"""
            <tr>
                <td>
                    <div class="company-name">{self._escape(company.name)}</div>
                    <div class="org-number">{company.org_number}</div>
                </td>
                <td class="date">{record.declaration_date.strftime('%Y-%m-%d') if record.declaration_date else '<span class="no-data">-</span>'}</td>
                <td>{self._escape(company.business_type) if company.business_type else '<span class="no-data">-</span>'}</td>
                <td class="employees">{company.employees if company.employees else '<span class="no-data">-</span>'}</td>
                <td class="revenue">{self._format_currency(company.revenue) if company.revenue else '<span class="no-data">-</span>'}</td>
                <td>{self._escape(company.city or company.region) if (company.city or company.region) else '<span class="no-data">-</span>'}</td>
                <td>{self._escape(record.court) if record.court else '<span class="no-data">-</span>'}</td>
                <td>{admin_info}</td>
                <td style="font-size: 12px;">{contact_info}</td>
            </tr>
"""
            
            html += """
        </tbody>
    </table>
"""
        else:
            html += """
    <div style="text-align: center; padding: 40px; background: #f8f9fa; border-radius: 8px;">
        <p style="font-size: 18px; color: #666;">No bankruptcies matching your criteria this month.</p>
    </div>
"""
        
        html += f"""
    <div class="footer">
        <p>Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        <p>Data source: Bolagsverket (Swedish Companies Registration Office)</p>
        <p>This is an automated report from the Swedish Bankruptcy Monitoring Agent.</p>
    </div>
</body>
</html>
"""
        return html
    
    def _generate_text_report(
        self,
        records: list[BankruptcyRecord],
        year: int,
        month: int,
        total_found: int
    ) -> str:
        """Generate plain text version of report."""
        month_name = datetime(year, month, 1).strftime("%B %Y")
        
        text = f"""
SWEDISH BANKRUPTCY REPORT - {month_name}
{'=' * 50}

Summary:
- Total bankruptcies: {total_found}
- Matching your criteria: {len(records)}
- Match rate: {self._format_percentage(len(records), total_found)}%

"""
        
        if records:
            text += "MATCHING BANKRUPTCIES:\n" + "-" * 50 + "\n\n"

            for i, record in enumerate(records, 1):
                company = record.company
                admin = record.administrator

                # Build administrator text
                admin_text = 'Unknown'
                if admin:
                    admin_text = admin.name
                    if admin.law_firm:
                        admin_text += f' ({admin.law_firm})'

                # Build contact info text
                contact_text = []
                if admin:
                    if admin.email:
                        contact_text.append(f'Email: {admin.email}')
                    if admin.phone:
                        contact_text.append(f'Phone: {admin.phone}')

                text += f"""
{i}. {company.name}
   Org Number: {company.org_number}
   Date: {record.declaration_date.strftime('%Y-%m-%d') if record.declaration_date else 'Unknown'}
   Business Type: {company.business_type or 'Unknown'}
   Employees: {company.employees or 'Unknown'}
   Revenue: {self._format_currency(company.revenue) if company.revenue else 'Unknown'} SEK
   Location: {company.city or company.region or 'Unknown'}
   Court: {record.court or 'Unknown'}
   Administrator: {admin_text}"""

                if contact_text:
                    text += "\n   Contact: " + " | ".join(contact_text)

                text += "\n\n"
        else:
            text += "No bankruptcies matching your criteria this month.\n"
        
        text += f"""
{'=' * 50}
Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC
Data source: Bolagsverket (Swedish Companies Registration Office)
"""
        return text
    
    def _send_email(
        self,
        subject: str,
        html_content: str,
        text_content: str
    ) -> bool:
        """Send email via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.config.sender_email
            msg["To"] = ", ".join(self.config.recipient_emails)
            
            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))
            
            if self.config.use_tls:
                server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.config.smtp_server, self.config.smtp_port)
            
            server.login(self.config.sender_email, self.config.sender_password)
            server.send_message(msg)
            server.quit()
            
            logger.info(f"Email sent successfully to {len(self.config.recipient_emails)} recipients")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    @staticmethod
    def _escape(text: str) -> str:
        """Escape HTML special characters."""
        if not text:
            return ""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
    
    @staticmethod
    def _format_currency(amount: Optional[float]) -> str:
        """Format number as Swedish currency."""
        if amount is None:
            return "-"
        return f"{amount:,.0f}".replace(",", " ")
    
    @staticmethod
    def _format_percentage(part: int, total: int) -> str:
        """Calculate and format percentage."""
        if total == 0:
            return "0"
        return f"{(part / total) * 100:.1f}"
