"""Tests for outreach.py — Mailgun email outreach module."""

import os
from dataclasses import dataclass
from unittest.mock import patch, MagicMock

import pytest


@dataclass
class FakeRecord:
    company_name: str = "Test AB"
    org_number: str = "556677-8899"
    trustee: str = "Anna Svensson"
    trustee_email: str = "anna@lawfirm.se"
    trustee_firm: str = "Law Firm AB"
    initiated_date: str = "01/15/2026"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Patch DB_PATH to a temp directory and return the path."""
    db_path = tmp_path / "data" / "bankruptcies.db"
    monkeypatch.setattr("outreach.DB_PATH", db_path)
    return db_path


# ============================================================================
# stage_outreach tests
# ============================================================================

def test_stage_disabled_by_default(tmp_db):
    """stage_outreach is a no-op when MAILGUN_OUTREACH_ENABLED is not set."""
    from outreach import stage_outreach

    env = {k: v for k, v in os.environ.items() if not k.startswith("MAILGUN")}
    with patch.dict(os.environ, env, clear=True):
        result = stage_outreach([FakeRecord()])
    assert result["staged"] == 0


def test_stage_creates_pending_rows(tmp_db, monkeypatch):
    """stage_outreach inserts rows with status='pending' and stores subject/body."""
    from outreach import stage_outreach, _get_db

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")

    result = stage_outreach([FakeRecord()])
    assert result["staged"] == 1
    assert result["skipped"] == 0
    assert result["opted_out"] == 0

    conn = _get_db()
    row = conn.execute(
        "SELECT status, subject, body FROM outreach_log WHERE org_number = '556677-8899'"
    ).fetchone()
    conn.close()

    assert row[0] == "pending"
    assert "Test AB" in row[1]
    assert "Dear Anna Svensson" in row[2]


def test_stage_no_http_calls(tmp_db, monkeypatch):
    """stage_outreach never makes any HTTP calls."""
    from outreach import stage_outreach

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")

    with patch("outreach.requests.post") as mock_post:
        stage_outreach([FakeRecord()])
        mock_post.assert_not_called()


def test_stage_dedup(tmp_db, monkeypatch):
    """stage_outreach skips records already staged (pending/approved)."""
    from outreach import stage_outreach

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")

    record = FakeRecord()
    result1 = stage_outreach([record])
    assert result1["staged"] == 1

    result2 = stage_outreach([record])
    assert result2["skipped"] == 1
    assert result2["staged"] == 0


def test_stage_opted_out_skipped(tmp_db, monkeypatch):
    """stage_outreach skips opted-out emails."""
    from outreach import stage_outreach, add_opt_out

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")

    add_opt_out("anna@lawfirm.se")
    result = stage_outreach([FakeRecord()])
    assert result["opted_out"] == 1
    assert result["staged"] == 0


def test_stage_records_without_email_skipped(tmp_db, monkeypatch):
    """Records missing trustee_email are not staged."""
    from outreach import stage_outreach

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")
    result = stage_outreach([FakeRecord(trustee_email=None)])
    assert result["staged"] == 0


def test_stage_records_with_na_trustee_skipped(tmp_db, monkeypatch):
    """Records with trustee='N/A' are not staged."""
    from outreach import stage_outreach

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")
    result = stage_outreach([FakeRecord(trustee="N/A")])
    assert result["staged"] == 0


# ============================================================================
# Opt-out tests
# ============================================================================

def test_opt_out_case_insensitive(tmp_db, monkeypatch):
    """Opt-out check is case-insensitive."""
    from outreach import add_opt_out, is_opted_out, _get_db

    add_opt_out("Anna@LawFirm.SE")
    conn = _get_db()
    assert is_opted_out(conn, "anna@lawfirm.se")
    conn.close()


# ============================================================================
# send_approved_emails tests
# ============================================================================

def test_send_approved_dryrun(tmp_db, monkeypatch):
    """send_approved_emails sends approved rows (dry-run by default)."""
    from outreach import stage_outreach, send_approved_emails, _get_db

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")
    monkeypatch.delenv("MAILGUN_LIVE", raising=False)

    stage_outreach([FakeRecord()])

    conn = _get_db()
    conn.execute("UPDATE outreach_log SET status = 'approved' WHERE status = 'pending'")
    conn.commit()
    conn.close()

    result = send_approved_emails()
    assert result["dry_run"] == 1
    assert result["sent"] == 0
    assert result["failed"] == 0


def test_send_approved_live(tmp_db, monkeypatch):
    """send_approved_emails calls Mailgun in live mode."""
    from outreach import stage_outreach, send_approved_emails, _get_db

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")
    monkeypatch.setenv("MAILGUN_LIVE", "true")
    monkeypatch.setenv("MAILGUN_DOMAIN", "test.mailgun.org")
    monkeypatch.setenv("MAILGUN_API_KEY", "key-test123")
    monkeypatch.setenv("MAILGUN_FROM_EMAIL", "sender@test.com")

    stage_outreach([FakeRecord()])

    conn = _get_db()
    conn.execute("UPDATE outreach_log SET status = 'approved' WHERE status = 'pending'")
    conn.commit()
    conn.close()

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "<msg-id>"}
    mock_resp.raise_for_status = MagicMock()

    with patch("outreach.requests.post", return_value=mock_resp) as mock_post:
        result = send_approved_emails()

    mock_post.assert_called_once()
    assert result["sent"] == 1

    conn = _get_db()
    row = conn.execute("SELECT status FROM outreach_log WHERE org_number = '556677-8899'").fetchone()
    conn.close()
    assert row[0] == "sent"


def test_send_approved_skips_pending(tmp_db, monkeypatch):
    """send_approved_emails ignores pending (unapproved) rows."""
    from outreach import stage_outreach, send_approved_emails

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")
    stage_outreach([FakeRecord()])

    result = send_approved_emails()
    assert result["sent"] == 0
    assert result["dry_run"] == 0


def test_send_approved_rechecks_optout(tmp_db, monkeypatch):
    """send_approved_emails rejects rows if trustee opted out after approval."""
    from outreach import stage_outreach, send_approved_emails, add_opt_out, _get_db

    monkeypatch.setenv("MAILGUN_OUTREACH_ENABLED", "true")
    stage_outreach([FakeRecord()])

    conn = _get_db()
    conn.execute("UPDATE outreach_log SET status = 'approved' WHERE status = 'pending'")
    conn.commit()
    conn.close()

    # Opt out after approval
    add_opt_out("anna@lawfirm.se")

    result = send_approved_emails()
    assert result["opted_out"] == 1
    assert result["sent"] == 0
    assert result["dry_run"] == 0

    conn = _get_db()
    row = conn.execute("SELECT status FROM outreach_log WHERE org_number = '556677-8899'").fetchone()
    conn.close()
    assert row[0] == "rejected"
