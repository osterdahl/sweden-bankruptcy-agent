"""Tests for dashboard.py — Streamlit dashboard."""

import re
import sqlite3
from pathlib import Path

import pytest


DASHBOARD_SRC = Path(__file__).parent.parent / "dashboard.py"


# ---- Write operations limited to outreach_log only ----

def test_writes_only_to_outreach_log():
    """Dashboard write operations (UPDATE) only target outreach_log table."""
    content = DASHBOARD_SRC.read_text()
    # Find all UPDATE statements
    updates = re.findall(r'(?i)(UPDATE\s+\w+)', content)
    for update in updates:
        table = update.split()[-1]
        assert table == "outreach_log", (
            f"Dashboard writes to '{table}' — only outreach_log writes are allowed"
        )


def test_no_destructive_operations():
    """Dashboard source code contains no INSERT, DELETE, DROP, or ALTER statements."""
    content = DASHBOARD_SRC.read_text().upper()
    for keyword in ["INSERT ", "DELETE ", "DROP ", "ALTER "]:
        assert keyword not in content, f"Found destructive operation '{keyword.strip()}' in dashboard.py"


def test_only_updates_status_and_body_columns():
    """Dashboard UPDATE statements only modify status and body columns."""
    content = DASHBOARD_SRC.read_text()
    update_stmts = re.findall(r'(?i)UPDATE\s+outreach_log\s+SET\s+([^"\']+?)(?:\s+WHERE)', content)
    allowed_columns = {"status", "body"}
    for stmt in update_stmts:
        # Extract column names from "col1 = ?, col2 = ?" patterns
        columns = re.findall(r'(\w+)\s*=', stmt)
        for col in columns:
            assert col in allowed_columns, (
                f"Dashboard updates column '{col}' — only {allowed_columns} are allowed"
            )


# ---- Read-only connection for analytics ----

def test_db_uri_uses_readonly_mode():
    """DB_URI includes ?mode=ro for read-only access."""
    content = DASHBOARD_SRC.read_text()
    assert "?mode=ro" in content, "Dashboard must use read-only SQLite mode"


# ---- Missing DB handled gracefully ----

def test_source_checks_db_exists_before_connect():
    """get_connection checks DB_PATH.exists() before connecting."""
    content = DASHBOARD_SRC.read_text()
    assert "DB_PATH.exists()" in content or "not DB_PATH.exists()" in content


# ---- No imports from scraper ----

def test_no_import_from_bankruptcy_monitor():
    """Dashboard does not import from bankruptcy_monitor."""
    content = DASHBOARD_SRC.read_text()
    assert "bankruptcy_monitor" not in content, "Dashboard should not import from bankruptcy_monitor"


# ---- Functional tests using direct sqlite to verify table_exists logic ----

def test_table_exists_logic(tmp_path):
    """The table_exists query pattern works correctly."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))

    # No table yet
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", ("nonexistent",)
    ).fetchone()
    assert row is None

    # Create table
    conn.execute("CREATE TABLE test_tbl (id INTEGER)")
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", ("test_tbl",)
    ).fetchone()
    assert row is not None
    conn.close()


def test_readonly_connection_rejects_writes(tmp_path):
    """A ?mode=ro connection should reject write operations."""
    db_path = tmp_path / "test.db"
    # Create DB with a table first
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()

    # Open read-only
    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("INSERT INTO t VALUES (1)")
    ro_conn.close()


def test_rw_connection_function_exists():
    """Dashboard has a get_rw_connection function for the outreach queue."""
    content = DASHBOARD_SRC.read_text()
    assert "def get_rw_connection" in content
