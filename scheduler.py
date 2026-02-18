"""
Scheduling and deduplication module for Swedish Bankruptcy Monitor.

- SQLite-backed dedup store at data/bankruptcies.db
- Composite dedup key: (org_number, initiated_date, trustee_email)
- Optional APScheduler-based scheduling (GitHub Actions cron still works)
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "bankruptcies.db"


def _get_connection() -> sqlite3.Connection:
    """Get a SQLite connection, creating the DB and table if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bankruptcy_records (
            org_number      TEXT NOT NULL,
            initiated_date  TEXT NOT NULL,
            trustee_email   TEXT NOT NULL DEFAULT '',
            company_name    TEXT,
            court           TEXT,
            sni_code        TEXT,
            industry_name   TEXT,
            trustee         TEXT,
            trustee_firm    TEXT,
            trustee_address TEXT,
            employees       TEXT,
            net_sales       TEXT,
            total_assets    TEXT,
            region          TEXT,
            ai_score        INTEGER,
            ai_reason       TEXT,
            priority        TEXT,
            first_seen_at   TEXT NOT NULL,
            PRIMARY KEY (org_number, initiated_date, trustee_email)
        )
    """)
    conn.commit()
    return conn


def deduplicate(records: List) -> List:
    """Filter out records already in the database. Store new ones.

    Dedup key: (org_number, initiated_date, trustee_email).
    Returns only records not previously seen.
    """
    if not records:
        return records

    conn = _get_connection()
    now = datetime.utcnow().isoformat()
    new_records = []
    duplicates = 0

    for r in records:
        key = (r.org_number, r.initiated_date, r.trustee_email or "")
        try:
            conn.execute(
                """INSERT INTO bankruptcy_records (
                    org_number, initiated_date, trustee_email,
                    company_name, court, sni_code, industry_name,
                    trustee, trustee_firm, trustee_address,
                    employees, net_sales, total_assets, region,
                    ai_score, ai_reason, priority, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key[0], key[1], key[2],
                    r.company_name, r.court, r.sni_code, r.industry_name,
                    r.trustee, r.trustee_firm, r.trustee_address,
                    r.employees, r.net_sales, r.total_assets, r.region,
                    r.ai_score, r.ai_reason, r.priority, now,
                ),
            )
            new_records.append(r)
        except sqlite3.IntegrityError:
            duplicates += 1

    conn.commit()
    conn.close()

    logger.info(
        f"Dedup: {len(new_records)} new, {duplicates} duplicates skipped "
        f"(out of {len(records)} scraped)"
    )
    return new_records


def get_cached_keys() -> set:
    """Return set of (org_number, initiated_date) for all records in the DB.

    Intentionally a 2-tuple (not the 3-tuple DB primary key which includes
    trustee_email). The scraper uses this only to stop pagination early — if
    a record is here, we've already processed it and it will never reach
    deduplicate(), so trustee_email differences between runs are irrelevant.
    """
    if not DB_PATH.exists():
        return set()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT org_number, initiated_date FROM bankruptcy_records"
        ).fetchall()
        return {(r[0], r[1]) for r in rows}
    finally:
        conn.close()


def run_scheduled():
    """Entry point for APScheduler — runs the full monitor pipeline."""
    from bankruptcy_monitor import main
    main()


def start_scheduler():
    """Start APScheduler if configured. Falls back to single run."""
    cron_expr = os.getenv("SCHEDULE_CRON")

    if not cron_expr:
        logger.info("No SCHEDULE_CRON set — running once (use GitHub Actions for scheduling)")
        run_scheduled()
        return

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("APScheduler not installed. Install with: pip install apscheduler")
        logger.info("Falling back to single run.")
        run_scheduled()
        return

    # Parse "minute hour day month day_of_week" (same as crontab)
    parts = cron_expr.split()
    if len(parts) != 5:
        logger.error(f"Invalid SCHEDULE_CRON format: '{cron_expr}' (expected 5 fields)")
        run_scheduled()
        return

    scheduler = BlockingScheduler()
    trigger = CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )
    scheduler.add_job(run_scheduled, trigger, id="bankruptcy_monitor")
    logger.info(f"Scheduler started with cron: {cron_expr}")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    start_scheduler()
