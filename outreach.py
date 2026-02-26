"""
Mailgun outreach module for sending templated emails to bankruptcy trustees.

SAFETY: Sandbox/dry-run mode is ON by default.
- Set MAILGUN_OUTREACH_ENABLED=true to enable the module at all.
- Set MAILGUN_LIVE=true to actually send emails via Mailgun.
- Without MAILGUN_LIVE=true, all sends are logged but NOT delivered.

Workflow:
    1. Scraper calls stage_outreach(records) -> inserts pending rows in outreach_log
    2. User reviews pending emails in Streamlit dashboard
    3. Dashboard calls send_approved_emails() -> sends only approved rows

Required env vars:
    MAILGUN_OUTREACH_ENABLED - Gate: must be "true" to run anything
    MAILGUN_DOMAIN           - Your Mailgun sending domain
    MAILGUN_API_KEY          - Mailgun API key
    MAILGUN_FROM_EMAIL       - Sender address (e.g. "Name <you@domain.com>")

Optional env vars:
    MAILGUN_LIVE             - Set to "true" to send real emails (default: dry-run)
    MAILGUN_RATE_LIMIT       - Max emails per minute (default: 10)

SQLite tables (in data/bankruptcies.db):
    outreach_log  - Delivery tracking with approval workflow
    opt_out       - Unsubscribe list checked before every send
"""

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "bankruptcies.db"

# ============================================================================
# DATABASE
# ============================================================================

def _get_db() -> sqlite3.Connection:
    """Get a connection to the SQLite database, creating tables if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outreach_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_number TEXT NOT NULL,
            trustee_email TEXT NOT NULL,
            company_name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            mailgun_id TEXT,
            sent_at TEXT NOT NULL,
            error_message TEXT,
            subject TEXT,
            body TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS opt_out (
            email TEXT PRIMARY KEY,
            opted_out_at TEXT NOT NULL
        )
    """)
    _migrate_add_columns(conn)
    conn.commit()
    return conn


def _migrate_add_columns(conn: sqlite3.Connection) -> None:
    """Add subject/body/country columns if missing (for existing databases)."""
    cursor = conn.execute("PRAGMA table_info(outreach_log)")
    columns = {row[1] for row in cursor.fetchall()}
    if "subject" not in columns:
        conn.execute("ALTER TABLE outreach_log ADD COLUMN subject TEXT")
    if "body" not in columns:
        conn.execute("ALTER TABLE outreach_log ADD COLUMN body TEXT")
    if "country" not in columns:
        conn.execute("ALTER TABLE outreach_log ADD COLUMN country TEXT DEFAULT 'se'")


def is_opted_out(conn: sqlite3.Connection, email: str) -> bool:
    """Check if an email address is on the opt-out list."""
    row = conn.execute(
        "SELECT 1 FROM opt_out WHERE email = ?", (email.lower(),)
    ).fetchone()
    return row is not None


def add_opt_out(email: str) -> None:
    """Add an email to the opt-out list."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO opt_out (email, opted_out_at) VALUES (?, ?)",
            (email.lower(), datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _has_country_column(conn: sqlite3.Connection) -> bool:
    """Check if outreach_log has the country column."""
    cursor = conn.execute("PRAGMA table_info(outreach_log)")
    columns = {row[1] for row in cursor.fetchall()}
    return "country" in columns


def log_send(conn: sqlite3.Connection, org_number: str, trustee_email: str,
             company_name: str, status: str, mailgun_id: Optional[str] = None,
             error_message: Optional[str] = None,
             subject: Optional[str] = None, body: Optional[str] = None,
             country: Optional[str] = None) -> None:
    """Record an outreach attempt in the log."""
    if country and _has_country_column(conn):
        conn.execute(
            """INSERT INTO outreach_log
               (org_number, trustee_email, company_name, status, mailgun_id, sent_at,
                error_message, subject, body, country)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (org_number, trustee_email, company_name, status, mailgun_id,
             datetime.now().isoformat(), error_message, subject, body, country),
        )
    else:
        conn.execute(
            """INSERT INTO outreach_log
               (org_number, trustee_email, company_name, status, mailgun_id, sent_at,
                error_message, subject, body)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (org_number, trustee_email, company_name, status, mailgun_id,
             datetime.now().isoformat(), error_message, subject, body),
        )
    conn.commit()


def already_contacted(conn: sqlite3.Connection, org_number: str, trustee_email: str) -> bool:
    """Check if we already have an outreach entry for this org/email pair."""
    row = conn.execute(
        "SELECT 1 FROM outreach_log WHERE org_number = ? AND trustee_email = ? "
        "AND status IN ('sent', 'dry-run', 'pending', 'approved')",
        (org_number, trustee_email),
    ).fetchone()
    return row is not None


# ============================================================================
# EMAIL TEMPLATE
# ============================================================================

TEMPLATE_PATH = Path(__file__).parent / "outreach_template.md"


def _load_template(country_code: str = "se") -> tuple[str, str]:
    """Load outreach template for a country. Returns (subject, body).

    Falls back to outreach_template.md if country-specific not found,
    then to hardcoded defaults if that is also missing.
    """
    template_path = Path(__file__).parent / "templates" / f"outreach_{country_code}.md"
    if not template_path.exists():
        template_path = TEMPLATE_PATH
    if not template_path.exists():
        logger.warning(f"No outreach template found for country '{country_code}' — using built-in fallback")
        subject = "Regarding {{company_name}} bankruptcy proceedings"
        body = (
            "Dear {{trustee_name}},\n\n"
            "We noticed that {{company_name}} recently entered bankruptcy proceedings. "
            "We specialize in acquiring data assets and digital infrastructure from "
            "companies in transition and would welcome the opportunity to discuss "
            "whether there are assets we could help monetize for the estate.\n\n"
            "We would be happy to arrange a brief call at your convenience.\n\n"
            "Best regards\n\n"
            "---\n"
            "To unsubscribe from future messages, reply with 'UNSUBSCRIBE' in the subject line."
        )
        return subject, body

    content = template_path.read_text(encoding="utf-8")

    # Parse subject (line after "## Subject")
    subject = ""
    body_lines = []
    in_subject = False
    in_body = False

    for line in content.splitlines():
        if line.strip() == "## Subject":
            in_subject = True
            in_body = False
            continue
        if line.strip() == "## Body":
            in_subject = False
            in_body = True
            continue
        if line.startswith("## ") or line.startswith("# "):
            in_subject = False
            in_body = False
            continue
        if line.strip() == "---":
            in_subject = False
            if in_body:
                body_lines.append(line)
            continue
        if in_subject and line.strip():
            subject = line.strip()
        if in_body:
            body_lines.append(line)

    body = "\n".join(body_lines).strip()
    return subject, body


def _render(template: str, company_name: str, trustee_name: str) -> str:
    """Replace {{placeholders}} in a template string."""
    return template.replace("{{company_name}}", company_name).replace("{{trustee_name}}", trustee_name)


# ============================================================================
# MAILGUN SENDING
# ============================================================================

def _send_via_mailgun(to_email: str, subject: str, body: str) -> tuple[str, Optional[str], Optional[str]]:
    """Send one email via Mailgun API.

    Returns (status, mailgun_id, error_message).
    status is one of: 'sent', 'dry-run', 'failed'.
    """
    live = os.getenv("MAILGUN_LIVE", "false").lower() == "true"

    if not live:
        logger.info(f"  [DRY-RUN] Would send to {to_email}: {subject}")
        return ("dry-run", None, None)

    domain = os.getenv("MAILGUN_DOMAIN", "")
    api_key = os.getenv("MAILGUN_API_KEY", "")
    from_email = os.getenv("MAILGUN_FROM_EMAIL", "")

    if not all([domain, api_key, from_email]):
        msg = "Missing MAILGUN_DOMAIN, MAILGUN_API_KEY, or MAILGUN_FROM_EMAIL"
        logger.error(f"  [FAILED] {msg}")
        return ("failed", None, msg)

    api_url = os.getenv("MAILGUN_API_URL", "https://api.eu.mailgun.net/v3")
    try:
        resp = requests.post(
            f"{api_url}/{domain}/messages",
            auth=("api", api_key),
            data={"from": from_email, "to": [to_email], "bcc": os.getenv("OUTREACH_BCC_EMAIL", "david@redpine.ai"), "subject": subject, "text": body},
            timeout=15,
        )
        resp.raise_for_status()
        mailgun_id = resp.json().get("id")
        logger.info(f"  [SENT] {to_email} (id={mailgun_id})")
        return ("sent", mailgun_id, None)
    except requests.RequestException as e:
        error = str(e)
        logger.error(f"  [FAILED] {to_email}: {error}")
        return ("failed", None, error)


# ============================================================================
# STAGING (called by scraper)
# ============================================================================

def stage_outreach(records, country_code: str = "se") -> dict:
    """Stage outreach emails as 'pending' for dashboard approval.

    Checks opt-out and dedup, then inserts into outreach_log with status='pending'.
    No emails are sent — they wait for dashboard approval.

    Args:
        records: List of BankruptcyRecord (or similar) objects.
        country_code: ISO country code for template selection (default "se").

    Returns:
        Summary dict: {'staged': N, 'skipped': N, 'opted_out': N}.
    """
    if os.getenv("MAILGUN_OUTREACH_ENABLED", "false").lower() != "true":
        logger.info("Mailgun outreach disabled (MAILGUN_OUTREACH_ENABLED != true)")
        return {"staged": 0, "skipped": 0, "opted_out": 0}

    conn = _get_db()
    counts = {"staged": 0, "skipped": 0, "opted_out": 0}

    eligible = [r for r in records if r.trustee_email and r.trustee != "N/A"]
    logger.info(f"Outreach staging: {len(eligible)} records with trustee emails (of {len(records)} total)")

    subj_tpl, body_tpl = _load_template(country_code)

    try:
        for r in eligible:
            email = r.trustee_email.lower()

            if is_opted_out(conn, email):
                logger.debug(f"  Skipping {email} (opted out)")
                counts["opted_out"] += 1
                continue

            if already_contacted(conn, r.org_number, email):
                logger.debug(f"  Skipping {r.org_number}/{email} (already contacted)")
                counts["skipped"] += 1
                continue

            # Use record's country if available, otherwise use the function parameter
            record_country = getattr(r, "country", None) or country_code
            subject = _render(subj_tpl, r.company_name, r.trustee)
            body = _render(body_tpl, r.company_name, r.trustee)
            log_send(conn, r.org_number, email, r.company_name, "pending",
                     subject=subject, body=body, country=record_country)
            counts["staged"] += 1

        logger.info(
            f"Outreach staging complete: {counts['staged']} staged, "
            f"{counts['skipped']} skipped, {counts['opted_out']} opted-out"
        )
    finally:
        conn.close()

    return counts


# ============================================================================
# DIRECT STAGING (called by dashboard for manual selection)
# ============================================================================

def stage_records_direct(records: list, country_code: str = "se") -> dict:
    """Stage specific records for outreach, bypassing priority filters.

    Does not gate on MAILGUN_OUTREACH_ENABLED — this is a manual user action.
    Each record must be a dict with: org_number, company_name, trustee, trustee_email.
    Optionally include 'country' per record.

    Args:
        records: List of dicts with record data.
        country_code: Default ISO country code for template selection (default "se").

    Returns:
        Summary dict: {'staged': N, 'skipped': N, 'opted_out': N, 'no_email': N}.
    """
    conn = _get_db()
    counts = {"staged": 0, "skipped": 0, "opted_out": 0, "no_email": 0}
    subj_tpl, body_tpl = _load_template(country_code)

    try:
        for r in records:
            email = str(r.get("trustee_email") or "").strip().lower()
            if not email:
                counts["no_email"] += 1
                continue

            org_number = r.get("org_number", "")
            company_name = r.get("company_name", "")
            trustee = r.get("trustee", "")
            record_country = r.get("country") or country_code

            if is_opted_out(conn, email):
                counts["opted_out"] += 1
                continue

            if already_contacted(conn, org_number, email):
                counts["skipped"] += 1
                continue

            subject = _render(subj_tpl, company_name, trustee)
            body = _render(body_tpl, company_name, trustee)
            log_send(conn, org_number, email, company_name, "pending",
                     subject=subject, body=body, country=record_country)
            counts["staged"] += 1
    finally:
        conn.close()

    logger.info(
        f"Direct staging complete: {counts['staged']} staged, {counts['skipped']} skipped, "
        f"{counts['opted_out']} opted-out, {counts['no_email']} no-email"
    )
    return counts


# ============================================================================
# SENDING APPROVED EMAILS (called by dashboard)
# ============================================================================

def send_approved_emails() -> dict:
    """Send all emails with status='approved' via Mailgun.

    Re-checks opt-out before sending (in case trustee unsubscribed after approval).

    Returns:
        Summary dict: {'sent': N, 'dry_run': N, 'failed': N, 'opted_out': N}.
    """
    rate_limit = int(os.getenv("MAILGUN_RATE_LIMIT", "10"))
    conn = _get_db()
    counts = {"sent": 0, "dry_run": 0, "failed": 0, "opted_out": 0}
    sends_this_minute = 0
    minute_start = time.monotonic()

    try:
        rows = conn.execute(
            "SELECT id, trustee_email, company_name, org_number, subject, body "
            "FROM outreach_log WHERE status = 'approved'"
        ).fetchall()

        logger.info(f"Sending {len(rows)} approved outreach emails")

        for row_id, email, company, org_num, subject, body in rows:
            # Re-check opt-out at send time
            if is_opted_out(conn, email):
                conn.execute(
                    "UPDATE outreach_log SET status = 'rejected', "
                    "error_message = 'opted out before send' WHERE id = ?",
                    (row_id,),
                )
                conn.commit()
                counts["opted_out"] += 1
                continue

            # Rate limiting (only counts real sends)
            if sends_this_minute >= rate_limit:
                elapsed = time.monotonic() - minute_start
                if elapsed < 60:
                    wait = 60 - elapsed
                    logger.info(f"  Rate limit reached ({rate_limit}/min), waiting {wait:.0f}s...")
                    time.sleep(wait)
                sends_this_minute = 0
                minute_start = time.monotonic()

            status, mailgun_id, error = _send_via_mailgun(email, subject, body or "")

            conn.execute(
                "UPDATE outreach_log SET status = ?, mailgun_id = ?, error_message = ?, "
                "sent_at = ? WHERE id = ?",
                (status, mailgun_id, error, datetime.now().isoformat(), row_id),
            )
            conn.commit()

            if status == "sent":
                counts["sent"] += 1
                sends_this_minute += 1
            elif status == "dry-run":
                counts["dry_run"] += 1
            else:
                counts["failed"] += 1

        logger.info(
            f"Send complete: {counts['sent']} sent, {counts['dry_run']} dry-run, "
            f"{counts['failed']} failed, {counts['opted_out']} opted-out"
        )
    finally:
        conn.close()

    return counts
