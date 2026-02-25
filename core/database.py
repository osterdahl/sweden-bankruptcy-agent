"""
Multi-country database layer for the Nordic Bankruptcy Monitor.

Extracted from scheduler.py with added multi-country support:
- country column on bankruptcy_records and outreach_log
- Composite primary key: (country, org_number, initiated_date, trustee_email)
- Migration logic preserves existing Sweden data (country='se')

All public functions accept an optional country parameter (default 'se')
so callers that don't pass it behave exactly as before.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "bankruptcies.db"


# ============================================================================
# CONNECTION & SCHEMA
# ============================================================================

def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection, creating the DB and tables if needed.

    Runs migrations automatically on first connect so existing databases
    gain the ``country`` column transparently.
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # Create tables if brand-new database
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bankruptcy_records (
            country         TEXT NOT NULL DEFAULT 'se',
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
            PRIMARY KEY (country, org_number, initiated_date, trustee_email)
        )
    """)
    conn.commit()

    # Run any pending migrations (adds country column to legacy DBs, etc.)
    _run_migrations(conn)

    # Legacy migration from scheduler.py — convert TEXT financials to INTEGER
    _migrate_financial_to_int(conn)

    return conn


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Add ``country`` column to legacy tables that lack it.

    Existing rows receive ``country='se'`` (backward-compatible default).
    For bankruptcy_records the primary key also needs updating to include
    country — SQLite requires a table rebuild for PK changes.
    """
    # --- bankruptcy_records ---
    cursor = conn.execute("PRAGMA table_info(bankruptcy_records)")
    columns = [row[1] for row in cursor.fetchall()]

    if "country" not in columns:
        logger.info("Migration: rebuilding bankruptcy_records with 'country' column")
        _rebuild_bankruptcy_records_with_country(conn)

    # --- outreach_log ---
    cursor = conn.execute("PRAGMA table_info(outreach_log)")
    ol_columns = [row[1] for row in cursor.fetchall()]
    if ol_columns and "country" not in ol_columns:
        conn.execute(
            "ALTER TABLE outreach_log ADD COLUMN country TEXT NOT NULL DEFAULT 'se'"
        )
        conn.commit()
        logger.info("Migration: added 'country' column to outreach_log")

    # Also make sure asset_types column exists (legacy migration)
    cursor = conn.execute("PRAGMA table_info(bankruptcy_records)")
    cols = {row[1] for row in cursor.fetchall()}
    if "asset_types" not in cols:
        conn.execute("ALTER TABLE bankruptcy_records ADD COLUMN asset_types TEXT")
        conn.commit()


def _rebuild_bankruptcy_records_with_country(conn: sqlite3.Connection) -> None:
    """Rebuild bankruptcy_records to add country column and update the PK.

    Uses a rename-create-copy-drop strategy identical to the financial
    migration already in the codebase.
    """
    # Grab existing column names so the INSERT order is correct
    col_info = conn.execute("PRAGMA table_info(bankruptcy_records)").fetchall()
    old_col_names = [row[1] for row in col_info]
    rows = conn.execute(f"SELECT {', '.join(old_col_names)} FROM bankruptcy_records").fetchall()

    conn.execute("ALTER TABLE bankruptcy_records RENAME TO _bk_country_mig")
    conn.execute("""
        CREATE TABLE bankruptcy_records (
            country         TEXT NOT NULL DEFAULT 'se',
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
            PRIMARY KEY (country, org_number, initiated_date, trustee_email)
        )
    """)

    if rows:
        # Build INSERT that maps old columns to new columns, plus country='se'
        # The new table has 'country' as the first column; old rows don't have it.
        placeholders = ", ".join(["?"] * (len(old_col_names) + 1))
        target_cols = "country, " + ", ".join(old_col_names)
        conn.executemany(
            f"INSERT INTO bankruptcy_records ({target_cols}) VALUES ({placeholders})",
            [("se", *row) for row in rows],
        )

    conn.execute("DROP TABLE _bk_country_mig")
    conn.commit()
    logger.info(f"Migration: rebuilt bankruptcy_records with country column ({len(rows)} rows preserved)")


def _migrate_financial_to_int(conn: sqlite3.Connection) -> None:
    """One-time migration: change employees/net_sales/total_assets from TEXT to INTEGER.

    SQLite doesn't support ALTER COLUMN, so we recreate the table. Uses _bk_old
    as the data source, which also acts as a recovery path if a prior run was
    interrupted mid-flight.
    """
    from bankruptcy_monitor import _parse_sek, _parse_headcount

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    # If _bk_old exists, a previous run was interrupted -- use it as source
    if "_bk_old" in tables:
        source = "_bk_old"
    else:
        col_types = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(bankruptcy_records)").fetchall()
        }
        if col_types.get("net_sales") == "INTEGER":
            return  # already migrated
        source = "bankruptcy_records"

    logger.info("Migrating financial columns to INTEGER affinity...")
    col_names = [row[1] for row in conn.execute(f"PRAGMA table_info({source})").fetchall()]

    emp_idx = col_names.index("employees")
    ns_idx = col_names.index("net_sales")
    ta_idx = col_names.index("total_assets")
    fsa_idx = col_names.index("first_seen_at")
    rows = conn.execute(f"SELECT * FROM {source}").fetchall()

    def _convert(row):
        row = list(row)
        row[emp_idx] = _parse_headcount(row[emp_idx])
        row[ns_idx] = _parse_sek(row[ns_idx])
        row[ta_idx] = _parse_sek(row[ta_idx])
        if not row[fsa_idx]:
            row[fsa_idx] = "1970-01-01T00:00:00"
        return row

    converted = [_convert(r) for r in rows]

    if source == "bankruptcy_records":
        conn.execute("ALTER TABLE bankruptcy_records RENAME TO _bk_old")

    conn.execute("DROP TABLE IF EXISTS bankruptcy_records")
    conn.execute("""
        CREATE TABLE bankruptcy_records (
            country         TEXT NOT NULL DEFAULT 'se',
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
            PRIMARY KEY (country, org_number, initiated_date, trustee_email)
        )
    """)

    conn.executemany(
        f"INSERT INTO bankruptcy_records ({', '.join(col_names)}) "
        f"VALUES ({','.join('?' * len(col_names))})",
        converted,
    )
    conn.execute("DROP TABLE IF EXISTS _bk_old")
    conn.commit()
    logger.info(f"Migrated {len(rows)} records to INTEGER financial columns.")


# ============================================================================
# PUBLIC API — all country-aware, backward-compatible (default country='se')
# ============================================================================

def get_cached_keys(country: str = "se") -> set:
    """Return set of ``(org_number, initiated_date)`` for a specific country.

    Used by the scraper to stop pagination early — if a record is here,
    we've already processed it. The 2-tuple key is intentional (not the
    full PK which includes trustee_email).
    """
    if not DB_PATH.exists():
        return set()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT org_number, initiated_date FROM bankruptcy_records WHERE country = ?",
            (country,),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}
    finally:
        conn.close()


def deduplicate(records: List, country: str = "se") -> List:
    """Insert new records, skip duplicates. Returns only genuinely new records.

    Dedup key: ``(country, org_number, initiated_date, trustee_email)``.
    """
    if not records:
        return records

    conn = get_connection()
    now = datetime.utcnow().isoformat()
    new_records = []
    duplicates = 0

    for r in records:
        key = (country, r.org_number, r.initiated_date, r.trustee_email or "")
        try:
            conn.execute(
                """INSERT INTO bankruptcy_records (
                    country, org_number, initiated_date, trustee_email,
                    company_name, court, sni_code, industry_name,
                    trustee, trustee_firm, trustee_address,
                    employees, net_sales, total_assets, region,
                    ai_score, ai_reason, priority, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key[0], key[1], key[2], key[3],
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
        f"Dedup [{country}]: {len(new_records)} new, {duplicates} duplicates skipped "
        f"(out of {len(records)} scraped)"
    )
    return new_records


def update_scores(records: List) -> None:
    """Write ai_score, ai_reason, priority, asset_types back to bankruptcy_records.

    Uses ``(org_number, initiated_date)`` as the match key — same as the
    original implementation. Country is handled via the record's own data
    if it has a ``country`` attribute, otherwise defaults to ``'se'``.
    """
    if not records:
        return
    conn = get_connection()
    try:
        for r in records:
            country = getattr(r, "country", "se") or "se"
            conn.execute(
                "UPDATE bankruptcy_records SET ai_score=?, ai_reason=?, priority=?, asset_types=? "
                "WHERE country=? AND org_number=? AND initiated_date=?",
                (
                    r.ai_score,
                    r.ai_reason,
                    r.priority,
                    getattr(r, "asset_types", None),
                    country,
                    r.org_number,
                    r.initiated_date,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def update_trustee_email(
    org_number: str,
    initiated_date: str,
    email: str,
    country: str = "se",
) -> None:
    """Update trustee email for a specific record."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE OR IGNORE bankruptcy_records SET trustee_email = ? "
            "WHERE country = ? AND org_number = ? AND initiated_date = ? AND trustee_email = ''",
            (email, country, org_number, initiated_date),
        )
        conn.commit()
    finally:
        conn.close()
