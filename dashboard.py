#!/usr/bin/env python3
"""
Swedish Bankruptcy Monitor — Streamlit Dashboard

Reporting dashboard with outreach approval queue.
Read-only for analytics; read-write only for outreach_log approval actions.
Run: streamlit run dashboard.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent / "data" / "bankruptcies.db"
DB_URI = f"file:{DB_PATH}?mode=ro"


def get_connection():
    """Open a read-only SQLite connection. Returns None if DB missing."""
    if not DB_PATH.exists():
        return None
    return sqlite3.connect(DB_URI, uri=True)


def get_rw_connection():
    """Open a read-write SQLite connection for outreach queue actions."""
    if not DB_PATH.exists():
        return None
    return sqlite3.connect(str(DB_PATH))


def table_exists(conn, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def query_df(conn, sql: str) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame. Returns empty DF on error."""
    try:
        return pd.read_sql_query(sql, conn)
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Bankruptcy Monitor", layout="wide")
st.title("Swedish Bankruptcy Monitor")

if st.button("Refresh"):
    st.rerun()

conn = get_connection()

if conn is None:
    st.warning("Database not found. Run the scraper first to populate data.")
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_queue = st.tabs(["Overview", "Outreach Queue"])

# ---------------------------------------------------------------------------
# TAB 1: Overview (read-only)
# ---------------------------------------------------------------------------
with tab_overview:
    # KPI cards
    col1, col2, col3, col4 = st.columns(4)

    if table_exists(conn, "bankruptcy_records"):
        total_filings = conn.execute("SELECT COUNT(*) FROM bankruptcy_records").fetchone()[0]
        duplicates = 0
        dup_row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT org_number, initiated_date FROM bankruptcy_records"
            "  GROUP BY org_number, initiated_date HAVING COUNT(*) > 1"
            ")"
        ).fetchone()
        if dup_row:
            duplicates = dup_row[0]
    else:
        total_filings = 0
        duplicates = 0

    col1.metric("Total Filings", total_filings)
    col2.metric("Duplicate Records", duplicates)

    if table_exists(conn, "outreach_log"):
        sent = conn.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE status='sent'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE status IN ('failed','bounced')"
        ).fetchone()[0]
    else:
        sent = 0
        failed = 0

    col3.metric("Emails Sent", sent)
    col4.metric("Emails Failed/Bounced", failed)


    # Bankruptcy records table
    st.subheader("Bankruptcy Records")
    if table_exists(conn, "bankruptcy_records"):
        df = query_df(conn, "SELECT * FROM bankruptcy_records ORDER BY initiated_date DESC")
        if df.empty:
            st.write("No records yet.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.write("Table `bankruptcy_records` does not exist yet.")

    # Outreach log
    st.subheader("Outreach Log")
    if table_exists(conn, "outreach_log"):
        df_out = query_df(conn, "SELECT * FROM outreach_log ORDER BY sent_at DESC LIMIT 200")
        if df_out.empty:
            st.write("No outreach entries yet.")
        else:
            st.dataframe(df_out, use_container_width=True, hide_index=True)
    else:
        st.write("Table `outreach_log` does not exist yet.")

conn.close()

# ---------------------------------------------------------------------------
# TAB 2: Outreach Queue (read-write for outreach_log only)
# ---------------------------------------------------------------------------
with tab_queue:
    st.subheader("Pending Outreach Emails")
    st.caption("Review, edit, approve or reject outreach emails before sending.")

    rw_conn = get_rw_connection()

    if rw_conn is None:
        st.warning("Database not found. Run the scraper first.")
    elif not table_exists(rw_conn, "outreach_log"):
        st.write("No outreach log table yet. Run the scraper with MAILGUN_OUTREACH_ENABLED=true.")
        rw_conn.close()
    else:
        pending = rw_conn.execute(
            """SELECT o.id, o.org_number, o.trustee_email, o.company_name,
                      o.subject, o.body,
                      b.priority, b.ai_score, b.asset_types, b.ai_reason
               FROM outreach_log o
               LEFT JOIN bankruptcy_records b ON o.org_number = b.org_number
               WHERE o.status = 'pending'
               GROUP BY o.id
               ORDER BY COALESCE(b.ai_score, 0) DESC, o.id"""
        ).fetchall()

        if not pending:
            st.info("No pending emails. Run the scraper to stage new outreach.")
        else:
            # Bulk actions
            col_approve_all, col_reject_all, _ = st.columns([1, 1, 3])
            with col_approve_all:
                if st.button("Approve All Pending"):
                    rw_conn.execute("UPDATE outreach_log SET status = 'approved' WHERE status = 'pending'")
                    rw_conn.commit()
                    st.rerun()
            with col_reject_all:
                if st.button("Reject All Pending"):
                    rw_conn.execute("UPDATE outreach_log SET status = 'rejected' WHERE status = 'pending'")
                    rw_conn.commit()
                    st.rerun()

            st.divider()

            _priority_badge = {'HIGH': '🔴 HIGH', 'MEDIUM': '🟡 MEDIUM', 'LOW': '🟢 LOW'}

            for row_id, org_num, email, company, subject, body, priority, ai_score, asset_types, ai_reason in pending:
                with st.container(border=True):
                    badge = _priority_badge.get(priority, '⚪ —')
                    score_str = f"{ai_score}/10" if ai_score else "—"
                    st.markdown(f"**{company}** ({org_num}) &nbsp; {badge} &nbsp; Score: {score_str}")
                    if asset_types:
                        st.markdown(" ".join(f"`{t}`" for t in asset_types.split(",")))
                    if ai_reason:
                        st.caption(ai_reason)
                    st.markdown(f"To: `{email}`")
                    st.text_input("Subject", value=subject or "", key=f"subj_{row_id}", disabled=True)

                    edited_body = st.text_area(
                        "Message", value=body or "", height=150, key=f"body_{row_id}"
                    )

                    col_a, col_r, _ = st.columns([1, 1, 4])
                    with col_a:
                        if st.button("Approve", key=f"approve_{row_id}", type="primary"):
                            rw_conn.execute(
                                "UPDATE outreach_log SET status = 'approved', body = ? WHERE id = ?",
                                (edited_body, row_id),
                            )
                            rw_conn.commit()
                            st.rerun()
                    with col_r:
                        if st.button("Reject", key=f"reject_{row_id}"):
                            rw_conn.execute(
                                "UPDATE outreach_log SET status = 'rejected' WHERE id = ?",
                                (row_id,),
                            )
                            rw_conn.commit()
                            st.rerun()

        # Send approved section
        st.divider()
        approved_count = rw_conn.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE status = 'approved'"
        ).fetchone()[0]

        st.subheader(f"Ready to Send ({approved_count} approved)")

        if approved_count > 0:
            if st.button("Send All Approved", type="primary"):
                from outreach import send_approved_emails
                with st.spinner("Sending emails..."):
                    result = send_approved_emails()
                st.success(
                    f"Done: {result['sent']} sent, {result['dry_run']} dry-run, "
                    f"{result['failed']} failed"
                )
                st.rerun()
        else:
            st.info("No approved emails to send. Approve pending emails above first.")

        rw_conn.close()
