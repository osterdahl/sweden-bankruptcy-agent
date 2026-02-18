"""Tests for scheduler.py — deduplication and scheduling module."""

import sqlite3
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

import pytest


@dataclass
class FakeRecord:
    company_name: str = "Test AB"
    org_number: str = "556677-8899"
    initiated_date: str = "01/15/2026"
    court: str = "Stockholm"
    sni_code: str = "62"
    industry_name: str = "IT"
    trustee: str = "Anna"
    trustee_firm: str = "Firm"
    trustee_address: str = "Addr"
    employees: str = "10"
    net_sales: str = "1000 TSEK"
    total_assets: str = "500 TSEK"
    region: str = "Stockholm"
    ai_score: Optional[int] = None
    ai_reason: Optional[str] = None
    priority: Optional[str] = None
    trustee_email: Optional[str] = "anna@firm.se"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Patch DB_DIR and DB_PATH to a temp directory."""
    db_dir = tmp_path / "data"
    db_path = db_dir / "bankruptcies.db"
    monkeypatch.setattr("scheduler.DB_DIR", db_dir)
    monkeypatch.setattr("scheduler.DB_PATH", db_path)
    return db_path


# ---- Idempotency ----

def test_deduplicate_returns_new_records(tmp_db):
    """First run returns all records as new."""
    from scheduler import deduplicate
    records = [FakeRecord(), FakeRecord(org_number="111111-2222")]
    result = deduplicate(records)
    assert len(result) == 2


def test_deduplicate_is_idempotent(tmp_db):
    """Second run with same records returns empty list."""
    from scheduler import deduplicate
    records = [FakeRecord()]
    deduplicate(records)
    result = deduplicate(records)
    assert len(result) == 0


def test_deduplicate_different_dates_not_duplicates(tmp_db):
    """Same org_number but different initiated_date is NOT a duplicate."""
    from scheduler import deduplicate
    r1 = FakeRecord(initiated_date="01/15/2026")
    r2 = FakeRecord(initiated_date="02/15/2026")
    deduplicate([r1])
    result = deduplicate([r2])
    assert len(result) == 1


def test_deduplicate_different_emails_not_duplicates(tmp_db):
    """Same org+date but different trustee_email is NOT a duplicate."""
    from scheduler import deduplicate
    r1 = FakeRecord(trustee_email="a@firm.se")
    r2 = FakeRecord(trustee_email="b@firm.se")
    deduplicate([r1])
    result = deduplicate([r2])
    assert len(result) == 1


def test_deduplicate_empty_list(tmp_db):
    """Empty input returns empty output without errors."""
    from scheduler import deduplicate
    assert deduplicate([]) == []


# ---- Composite key ----

def test_composite_key_primary_key(tmp_db):
    """The PK is (org_number, initiated_date, trustee_email)."""
    from scheduler import _get_connection
    conn = _get_connection()
    conn.execute(
        "INSERT INTO bankruptcy_records (org_number, initiated_date, trustee_email, first_seen_at) VALUES (?,?,?,?)",
        ("111", "2026-01-01", "a@b.se", "now"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bankruptcy_records (org_number, initiated_date, trustee_email, first_seen_at) VALUES (?,?,?,?)",
            ("111", "2026-01-01", "a@b.se", "now"),
        )
    conn.close()


# ---- Table creation ----

def test_table_created_if_not_exists(tmp_db):
    """Calling _get_connection twice doesn't fail (IF NOT EXISTS)."""
    from scheduler import _get_connection
    conn1 = _get_connection()
    conn1.close()
    conn2 = _get_connection()
    row = conn2.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bankruptcy_records'"
    ).fetchone()
    assert row is not None
    conn2.close()


# ---- APScheduler optional ----

def test_scheduler_runs_without_cron(tmp_db, monkeypatch):
    """When SCHEDULE_CRON is not set, start_scheduler calls run_scheduled once."""
    monkeypatch.delenv("SCHEDULE_CRON", raising=False)
    with patch("scheduler.run_scheduled") as mock_run:
        from scheduler import start_scheduler
        start_scheduler()
        mock_run.assert_called_once()


def test_scheduler_fallback_when_apscheduler_missing(tmp_db, monkeypatch):
    """When SCHEDULE_CRON is set but apscheduler not installed, falls back to single run."""
    monkeypatch.setenv("SCHEDULE_CRON", "0 8 * * *")

    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if "apscheduler" in name:
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    with patch("scheduler.run_scheduled") as mock_run, \
         patch("builtins.__import__", side_effect=mock_import):
        from scheduler import start_scheduler
        start_scheduler()
        mock_run.assert_called_once()
