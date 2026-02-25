"""
Scheduling and deduplication module for the Nordic Bankruptcy Monitor.

- Multi-country support via COUNTRIES env var (default: Sweden only)
- Database layer delegated to core.database (country-aware SQLite)
- Composite dedup key: (country, org_number, initiated_date, trustee_email)
- Optional APScheduler-based scheduling (GitHub Actions cron still works)
- Fully backward-compatible: runs Sweden-only when COUNTRIES is unset
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# Canonical DB path — tests monkeypatch these on the scheduler module.
DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "bankruptcies.db"

# ---------------------------------------------------------------------------
# Database layer — imported from core.database when available, otherwise
# fall back to a minimal inline implementation so the module never breaks.
# ---------------------------------------------------------------------------
_USE_CORE_DB = False
try:
    import core.database as _core_db
    _USE_CORE_DB = True
except ImportError:
    _core_db = None
    logger.debug("core.database not available — using inline DB functions")


def _sync_db_paths():
    """Push scheduler's (possibly monkeypatched) DB_DIR/DB_PATH to core.database.

    Tests patch ``scheduler.DB_DIR`` and ``scheduler.DB_PATH``.  Since
    core.database has its own copies of those globals, we sync before every
    database call so the overridden paths take effect.
    """
    if _core_db is not None:
        _core_db.DB_DIR = DB_DIR
        _core_db.DB_PATH = DB_PATH


def _get_connection() -> sqlite3.Connection:
    """Get a SQLite connection, creating the DB and tables if needed."""
    _sync_db_paths()
    if _USE_CORE_DB:
        return _core_db.get_connection()

    # Inline fallback — original schema (no country column)
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
            employees       INTEGER,
            net_sales       INTEGER,
            total_assets    INTEGER,
            region          TEXT,
            ai_score        INTEGER,
            ai_reason       TEXT,
            priority        TEXT,
            asset_types     TEXT,
            first_seen_at   TEXT NOT NULL,
            PRIMARY KEY (org_number, initiated_date, trustee_email)
        )
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(bankruptcy_records)").fetchall()}
    if "asset_types" not in cols:
        conn.execute("ALTER TABLE bankruptcy_records ADD COLUMN asset_types TEXT")
    conn.commit()
    return conn


def get_cached_keys(country: str = "se") -> set:
    """Return set of (org_number, initiated_date) for a specific country.

    Intentionally a 2-tuple (not the full PK which includes trustee_email).
    The scraper uses this only to stop pagination early.
    """
    _sync_db_paths()
    if _USE_CORE_DB:
        return _core_db.get_cached_keys(country)

    # Inline fallback
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


def deduplicate(records: List, country: str = "se") -> List:
    """Filter out records already in the database. Store new ones.

    Dedup key: (country, org_number, initiated_date, trustee_email).
    Returns only records not previously seen.
    """
    _sync_db_paths()
    if _USE_CORE_DB:
        return _core_db.deduplicate(records, country)

    # Inline fallback — original behaviour (no country column)
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


def update_scores(records: List) -> None:
    """Write ai_score, ai_reason, priority, asset_types back to bankruptcy_records.

    Called after score_bankruptcies() so scores persist across sessions and
    are visible in the dashboard outreach queue.
    """
    _sync_db_paths()
    if _USE_CORE_DB:
        return _core_db.update_scores(records)

    # Inline fallback
    if not records:
        return
    conn = _get_connection()
    try:
        for r in records:
            conn.execute(
                "UPDATE bankruptcy_records SET ai_score=?, ai_reason=?, priority=?, asset_types=? "
                "WHERE org_number=? AND initiated_date=?",
                (r.ai_score, r.ai_reason, r.priority, getattr(r, 'asset_types', None),
                 r.org_number, r.initiated_date),
            )
        conn.commit()
    finally:
        conn.close()


def update_trustee_email(org_number: str, initiated_date: str, email: str, country: str = "se") -> None:
    """Update trustee email for a specific record."""
    _sync_db_paths()
    if _USE_CORE_DB:
        return _core_db.update_trustee_email(org_number, initiated_date, email, country)

    # Inline fallback
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE OR IGNORE bankruptcy_records SET trustee_email = ? "
            "WHERE org_number = ? AND initiated_date = ? AND trustee_email = ''",
            (email, org_number, initiated_date),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pipeline — imported from core.pipeline when available (may not exist yet)
# ---------------------------------------------------------------------------
try:
    from core.pipeline import run_pipeline
except ImportError:
    run_pipeline = None

# ---------------------------------------------------------------------------
# Country registry — imported from countries package when available
# ---------------------------------------------------------------------------
try:
    from countries import get_active_countries
except ImportError:
    get_active_countries = None


# ============================================================================
# BACKFILL HELPERS (use _get_connection, kept in scheduler for dashboard compat)
# ============================================================================

def backfill_scores() -> int:
    """Score all unscored records in bankruptcy_records.

    Two-pass strategy to minimise API calls:
    1. Rule-based scoring runs on ALL unscored records immediately.
    2. AI scoring (Claude) runs only on HIGH/MEDIUM rule-based results,
       since LOW records (food, retail, construction) don't need refinement.

    Returns the number of records scored.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT org_number, initiated_date, company_name, sni_code, industry_name, "
            "employees, net_sales, total_assets, region "
            "FROM bankruptcy_records WHERE ai_score IS NULL"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    from bankruptcy_monitor import score_bankruptcies, calculate_base_score, BankruptcyRecord
    import os

    records = [
        BankruptcyRecord(
            company_name=row[2] or 'N/A',
            org_number=row[0],
            initiated_date=row[1],
            court='N/A',
            sni_code=row[3] or 'N/A',
            industry_name=row[4] or 'N/A',
            trustee='N/A',
            trustee_firm='N/A',
            trustee_address='N/A',
            employees=row[5] or 'N/A',
            net_sales=row[6] or 'N/A',
            total_assets=row[7] or 'N/A',
            region=row[8] or 'N/A',
        )
        for row in rows
    ]

    # Pass 1: rule-based on everything (fast, no API calls)
    # Temporarily disable AI so score_bankruptcies only does rule-based
    original = os.environ.get('AI_SCORING_ENABLED')
    os.environ['AI_SCORING_ENABLED'] = 'false'
    score_bankruptcies(records)
    if original is not None:
        os.environ['AI_SCORING_ENABLED'] = original
    else:
        del os.environ['AI_SCORING_ENABLED']

    # Persist rule-based scores immediately so dashboard shows progress
    update_scores(records)
    logger.info(f"Rule-based scoring complete for {len(records)} records")

    # Pass 2: AI scoring only on HIGH/MEDIUM candidates
    ai_enabled = os.getenv('AI_SCORING_ENABLED', 'false').lower() == 'true'
    if ai_enabled and os.getenv('ANTHROPIC_API_KEY'):
        candidates = [r for r in records if r.priority in ('HIGH', 'MEDIUM')]
        logger.info(f"AI scoring {len(candidates)} HIGH/MEDIUM candidates (skipping {len(records)-len(candidates)} LOW)")
        if candidates:
            score_bankruptcies(candidates)
            update_scores(candidates)

    return len(records)


def backfill_emails() -> int:
    """Look up trustee emails for all records currently missing one.

    Deduplicates by trustee/firm pair -- each unique pair is looked up once.
    Updates bankruptcy_records in-place via UPDATE OR IGNORE.

    Returns the number of records updated with a found email.
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT org_number, initiated_date, trustee, trustee_firm "
            "FROM bankruptcy_records WHERE trustee_email = '' AND trustee <> 'N/A'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    from bankruptcy_monitor import lookup_trustee_emails, BankruptcyRecord

    records = [
        BankruptcyRecord(
            org_number=row[0],
            initiated_date=row[1],
            company_name='N/A', court='N/A', sni_code='N/A', industry_name='N/A',
            trustee=row[2] or 'N/A',
            trustee_firm=row[3] or 'N/A',
            trustee_address='N/A', employees='N/A', net_sales='N/A',
            total_assets='N/A', region='N/A',
        )
        for row in rows
    ]

    records = lookup_trustee_emails(records)

    found = 0
    conn = _get_connection()
    try:
        for r in records:
            if r.trustee_email:
                cursor = conn.execute(
                    "UPDATE OR IGNORE bankruptcy_records SET trustee_email = ? "
                    "WHERE org_number = ? AND initiated_date = ? AND trustee_email = ''",
                    (r.trustee_email, r.org_number, r.initiated_date),
                )
                found += cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    logger.info(f"Email backfill complete: {found} records updated")
    return found


# ============================================================================
# SCHEDULING
# ============================================================================

def _get_countries() -> list:
    """Parse COUNTRIES env var into a list of country codes.

    Returns ``['se']`` when COUNTRIES is unset (backward compatible).
    """
    raw = os.getenv("COUNTRIES", "").strip()
    if not raw:
        return ["se"]
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def run_scheduled():
    """Entry point for APScheduler -- runs the full monitor pipeline.

    When the ``countries`` package and ``core.pipeline`` are available,
    loops over every active country. Otherwise falls back to the legacy
    Sweden-only ``bankruptcy_monitor.main()``.
    """
    countries = _get_countries()
    logger.info(f"Running pipeline for countries: {countries}")

    # Try the new multi-country pipeline first
    if run_pipeline is not None:
        for country_code in countries:
            try:
                logger.info(f"--- Pipeline start: {country_code.upper()} ---")
                run_pipeline(country_code)
                logger.info(f"--- Pipeline done:  {country_code.upper()} ---")
            except Exception:
                logger.exception(f"Pipeline failed for {country_code}")
        return

    # Fallback: legacy Sweden-only pipeline
    if countries == ["se"] or not countries:
        logger.info("Falling back to legacy Sweden-only pipeline")
        from bankruptcy_monitor import main
        main()
    else:
        # Multi-country requested but core.pipeline not available yet
        logger.warning(
            f"Multi-country requested ({countries}) but core.pipeline is not available. "
            "Running Sweden-only as fallback."
        )
        from bankruptcy_monitor import main
        main()


def start_scheduler():
    """Start APScheduler if configured. Falls back to single run."""
    cron_expr = os.getenv("SCHEDULE_CRON")

    if not cron_expr:
        logger.info("No SCHEDULE_CRON set -- running once (use GitHub Actions for scheduling)")
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
