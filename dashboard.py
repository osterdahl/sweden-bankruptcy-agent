#!/usr/bin/env python3
"""
Swedish Bankruptcy Monitor — Streamlit Dashboard

Reporting dashboard with outreach approval queue.
Read-only for analytics; read-write only for outreach_log approval actions.
Run: streamlit run dashboard.py
"""

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

# Load .env so AI_SCORING_ENABLED and ANTHROPIC_API_KEY are available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
# Page config & global styles
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Bankruptcy Monitor", layout="wide", page_icon="📊")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ── Global ── */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
[data-testid="block-container"] { padding: 2rem 2.5rem 3rem; }

/* ── Typography ── */
h1, h2, h3 { letter-spacing: -0.02em; color: var(--text-color); }
h1 { font-weight: 700; font-size: 1.75rem; margin-bottom: 0; }

/* ── KPI cards — always slightly lighter than the app background ── */
.kpi-card {
    background: color-mix(in srgb, var(--secondary-background-color) 88%, white 12%);
    border: 1px solid rgba(128,128,128,0.18);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    min-height: 90px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.07);
}
.kpi-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-color);
    opacity: 0.45;
    margin-bottom: 0.5rem;
}
.kpi-value {
    font-size: 2rem;
    font-weight: 700;
    color: var(--text-color);
    line-height: 1;
}
.kpi-value.positive { color: #059669; }
.kpi-value.warning  { color: #D97706; }
.kpi-value.danger   { color: #E53E3E; }

/* ── Priority badges (semantic colors, adapt via media query) ── */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.badge-high   { background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; }
.badge-medium { background: #FEF9C3; color: #854D0E; border: 1px solid #FDE68A; }
.badge-low    { background: #F0FDF4; color: #166534; border: 1px solid #BBF7D0; }

@media (prefers-color-scheme: dark) {
    .badge-high   { background: #450A0A; color: #FCA5A5; border-color: #7F1D1D; }
    .badge-medium { background: #451A03; color: #FCD34D; border-color: #78350F; }
    .badge-low    { background: #052E16; color: #86EFAC; border-color: #166534; }
}

/* ── Asset type pills ── */
.pill {
    display: inline-block;
    background: rgba(128,128,128,0.12);
    border: 1px solid rgba(128,128,128,0.25);
    color: var(--text-color);
    opacity: 0.75;
    padding: 1px 8px;
    border-radius: 6px;
    font-size: 0.72rem;
    font-weight: 500;
    margin-right: 4px;
}

/* ── Outreach card ── */
.email-card {
    background: color-mix(in srgb, var(--secondary-background-color) 88%, white 12%);
    border: 1px solid rgba(128,128,128,0.18);
    box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
}
.email-card-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
}
.company-name  { font-weight: 600; font-size: 1rem;  color: var(--text-color); }
.score-chip    { font-size: 0.72rem; font-weight: 600; color: var(--text-color);
                 opacity: 0.55; background: rgba(128,128,128,0.1);
                 border: 1px solid rgba(128,128,128,0.2); border-radius: 6px; padding: 2px 8px; }
.ai-reason     { font-size: 0.82rem; color: var(--text-color); opacity: 0.6;
                 line-height: 1.5; margin: 6px 0 10px; }
.email-to      { font-size: 0.8rem; color: var(--text-color); opacity: 0.45; margin-bottom: 10px; }
.email-to span { opacity: 1; font-weight: 500; }

/* ── Status indicators ── */
.status-ok      { color: #059669; font-size: 0.82rem; font-weight: 500; }
.status-warn    { color: #D97706; font-size: 0.82rem; font-weight: 500; }
.status-error   { color: #E53E3E; font-size: 0.82rem; font-weight: 500; }
.status-neutral { color: var(--text-color); opacity: 0.4; font-size: 0.82rem; }

/* ── Section headers ── */
.section-header {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-color);
    opacity: 0.4;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid rgba(128,128,128,0.2);
    margin-bottom: 1rem;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid rgba(128,128,128,0.2);
    background: transparent;
    padding: 0;
}
.stTabs [data-baseweb="tab"] {
    padding: 0.75rem 1.25rem;
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--text-color);
    opacity: 0.5;
    background: transparent;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
}
.stTabs [aria-selected="true"] {
    color: var(--text-color) !important;
    opacity: 1 !important;
    border-bottom: 2px solid var(--text-color) !important;
    background: transparent !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.5rem; }

/* ── Buttons ── */
.stButton > button {
    border-radius: 8px !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    transition: all 0.15s ease !important;
}
[data-testid="stBaseButton-secondary"] {
    background: color-mix(in srgb, var(--secondary-background-color) 88%, white 12%) !important;
    border: 1px solid rgba(128,128,128,0.25) !important;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
}

/* ── Inputs / textareas ── */
.stTextInput input, .stTextArea textarea {
    border-radius: 8px !important;
    border: 1px solid rgba(128,128,128,0.25) !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    box-shadow: 0 0 0 3px rgba(128,128,128,0.15) !important;
}

/* ── Dividers ── */
hr { border-color: rgba(128,128,128,0.2) !important; margin: 1.5rem 0 !important; }

/* ── Alerts ── */
[data-testid="stAlert"] { border-radius: 10px !important; font-size: 0.875rem !important; }
</style>
""", unsafe_allow_html=True)


def kpi(label, value, variant=""):
    cls = f"kpi-value {variant}".strip()
    return f"""
    <div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="{cls}">{value:,}</div>
    </div>"""


def badge(priority):
    cls = {"HIGH": "badge-high", "MEDIUM": "badge-medium", "LOW": "badge-low"}.get(priority, "")
    return f'<span class="badge {cls}">{priority or "—"}</span>' if priority else ""


def pills(asset_types):
    if not asset_types:
        return ""
    return "".join(f'<span class="pill">{t.strip()}</span>' for t in asset_types.split(","))


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
col_title, col_refresh = st.columns([8, 1])
with col_title:
    st.markdown("<h1>Bankruptcy Monitor</h1>", unsafe_allow_html=True)
with col_refresh:
    st.markdown("<div style='padding-top:0.6rem'>", unsafe_allow_html=True)
    if st.button("↻ Refresh"):
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

conn = get_connection()

if conn is None:
    st.warning("Database not found. Run the scraper first to populate data.")
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_queue = st.tabs(["Overview", "Outreach Queue"])

# ---------------------------------------------------------------------------
# TAB 1: Overview
# ---------------------------------------------------------------------------
with tab_overview:

    # ── KPI row ──
    total_filings, duplicates, sent, failed, high_count, med_count = 0, 0, 0, 0, 0, 0

    if table_exists(conn, "bankruptcy_records"):
        total_filings = conn.execute("SELECT COUNT(*) FROM bankruptcy_records").fetchone()[0]
        dup_row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT org_number, initiated_date FROM bankruptcy_records"
            "  GROUP BY org_number, initiated_date HAVING COUNT(*) > 1"
            ")"
        ).fetchone()
        duplicates = dup_row[0] if dup_row else 0
        high_count = conn.execute("SELECT COUNT(*) FROM bankruptcy_records WHERE priority='HIGH'").fetchone()[0]
        med_count  = conn.execute("SELECT COUNT(*) FROM bankruptcy_records WHERE priority='MEDIUM'").fetchone()[0]

    if table_exists(conn, "outreach_log"):
        sent   = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE status='sent'").fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE status IN ('failed','bounced')").fetchone()[0]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.markdown(kpi("Total Filings", total_filings), unsafe_allow_html=True)
    c2.markdown(kpi("High Priority", high_count, "danger" if high_count else ""), unsafe_allow_html=True)
    c3.markdown(kpi("Medium Priority", med_count, "warning" if med_count else ""), unsafe_allow_html=True)
    c4.markdown(kpi("Emails Sent", sent, "positive" if sent else ""), unsafe_allow_html=True)
    c5.markdown(kpi("Failed / Bounced", failed, "danger" if failed else ""), unsafe_allow_html=True)
    c6.markdown(kpi("Duplicate Records", duplicates), unsafe_allow_html=True)

    st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)

    # ── AI scoring status ──
    if table_exists(conn, "bankruptcy_records"):
        ai_enabled      = os.getenv("AI_SCORING_ENABLED", "false").lower() == "true"
        has_key         = bool(os.getenv("ANTHROPIC_API_KEY"))
        ai_failed_count = conn.execute(
            "SELECT COUNT(*) FROM bankruptcy_records WHERE ai_reason LIKE '[AI failed%'"
        ).fetchone()[0]
        unscored = conn.execute(
            "SELECT COUNT(*) FROM bankruptcy_records WHERE ai_score IS NULL"
        ).fetchone()[0]

        if not ai_enabled:
            st.markdown('<p class="status-neutral">AI scoring disabled — set AI_SCORING_ENABLED=true in .env to enable.</p>', unsafe_allow_html=True)
        elif not has_key:
            st.warning("AI_SCORING_ENABLED=true but ANTHROPIC_API_KEY is not set. Scores are rule-based only.")
        elif ai_failed_count > 0:
            st.markdown(f'<p class="status-warn">⚠ AI scoring failed for {ai_failed_count} records — check ANTHROPIC_API_KEY and logs.</p>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="status-ok">✓ AI scoring active</p>', unsafe_allow_html=True)

        if unscored > 0:
            st.info(f"{unscored} records have not been scored yet.")
            if st.button(f"Score {unscored} unscored records"):
                from scheduler import backfill_scores
                with st.spinner("Scoring records — this may take a moment..."):
                    count = backfill_scores()
                st.success(f"Scored {count} records.")
                st.rerun()

    # ── Bankruptcy records table ──
    st.markdown('<div class="section-header">Bankruptcy Records</div>', unsafe_allow_html=True)

    if table_exists(conn, "bankruptcy_records"):
        df = query_df(conn, """
            SELECT company_name, initiated_date, region, industry_name,
                   sni_code, employees, net_sales, total_assets,
                   trustee, trustee_firm,
                   priority, ai_score, asset_types, ai_reason,
                   org_number
            FROM bankruptcy_records
            ORDER BY initiated_date DESC
        """)
        if df.empty:
            st.markdown('<p style="color:#94A3B8; font-size:0.875rem;">No records yet.</p>', unsafe_allow_html=True)
        else:
            row_height = 35
            table_height = min(len(df) * row_height + 60, 700)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                height=table_height,
                column_config={
                    "company_name":   st.column_config.TextColumn("Company"),
                    "initiated_date": st.column_config.TextColumn("Date"),
                    "region":         st.column_config.TextColumn("Region"),
                    "industry_name":  st.column_config.TextColumn("Industry"),
                    "sni_code":       st.column_config.TextColumn("SNI"),
                    "employees":      st.column_config.TextColumn("Employees"),
                    "net_sales":      st.column_config.TextColumn("Net Sales"),
                    "total_assets":   st.column_config.TextColumn("Assets"),
                    "trustee":        st.column_config.TextColumn("Trustee"),
                    "trustee_firm":   st.column_config.TextColumn("Firm"),
                    "priority":       st.column_config.TextColumn("Priority"),
                    "ai_score":       st.column_config.NumberColumn("Score", format="%d / 10"),
                    "asset_types":    st.column_config.TextColumn("Asset Types"),
                    "ai_reason":      st.column_config.TextColumn("AI Reason", width="large"),
                    "org_number":     st.column_config.TextColumn("Org №"),
                },
            )
    else:
        st.markdown('<p style="color:#94A3B8;">Table not found yet.</p>', unsafe_allow_html=True)

    # ── Outreach log ──
    st.markdown('<div class="section-header" style="margin-top:2rem;">Outreach Log</div>', unsafe_allow_html=True)

    if table_exists(conn, "outreach_log"):
        df_out = query_df(conn, """
            SELECT company_name, trustee_email, status, subject, sent_at, error_message
            FROM outreach_log
            ORDER BY sent_at DESC
            LIMIT 200
        """)
        if df_out.empty:
            st.markdown('<p style="color:#94A3B8; font-size:0.875rem;">No outreach entries yet.</p>', unsafe_allow_html=True)
        else:
            out_height = min(len(df_out) * 35 + 60, 400)
            st.dataframe(
                df_out,
                use_container_width=True,
                hide_index=True,
                height=out_height,
                column_config={
                    "company_name":  st.column_config.TextColumn("Company"),
                    "trustee_email": st.column_config.TextColumn("Recipient"),
                    "status":        st.column_config.TextColumn("Status"),
                    "subject":       st.column_config.TextColumn("Subject"),
                    "sent_at":       st.column_config.TextColumn("Date"),
                    "error_message": st.column_config.TextColumn("Error"),
                },
            )
    else:
        st.markdown('<p style="color:#94A3B8;">No outreach log yet.</p>', unsafe_allow_html=True)

conn.close()

# ---------------------------------------------------------------------------
# TAB 2: Outreach Queue
# ---------------------------------------------------------------------------
with tab_queue:

    rw_conn = get_rw_connection()

    if rw_conn is None:
        st.warning("Database not found. Run the scraper first.")
    elif not table_exists(rw_conn, "outreach_log"):
        st.markdown('<p style="color:#94A3B8;">No outreach log yet. Run the scraper with MAILGUN_OUTREACH_ENABLED=true.</p>', unsafe_allow_html=True)
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

        # ── Pending section ──
        st.markdown(f'<div class="section-header">Pending Approval &nbsp;·&nbsp; {len(pending)} emails</div>', unsafe_allow_html=True)

        if not pending:
            st.markdown('<p style="color:#94A3B8; font-size:0.875rem;">No pending emails. Run the scraper to stage new outreach.</p>', unsafe_allow_html=True)
        else:
            col_aa, col_ra, _ = st.columns([1.2, 1.2, 6])
            with col_aa:
                if st.button("✓ Approve All", type="primary"):
                    rw_conn.execute("UPDATE outreach_log SET status = 'approved' WHERE status = 'pending'")
                    rw_conn.commit()
                    st.rerun()
            with col_ra:
                if st.button("✕ Reject All"):
                    rw_conn.execute("UPDATE outreach_log SET status = 'rejected' WHERE status = 'pending'")
                    rw_conn.commit()
                    st.rerun()

            st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

            for row_id, org_num, email, company, subject, body, priority, ai_score, asset_types, ai_reason in pending:
                score_str = f"{ai_score}/10" if ai_score else "—"
                header_html = f"""
                <div class="email-card-header">
                    <span class="company-name">{company}</span>
                    {badge(priority)}
                    <span class="score-chip">Score {score_str}</span>
                    {pills(asset_types)}
                </div>
                {f'<div class="ai-reason">{ai_reason}</div>' if ai_reason else ''}
                <div class="email-to">To: <span>{email}</span> &nbsp;·&nbsp; {org_num}</div>
                """
                st.markdown(f'<div class="email-card">{header_html}</div>', unsafe_allow_html=True)

                with st.container():
                    st.text_input("Subject", value=subject or "", key=f"subj_{row_id}", disabled=True, label_visibility="collapsed")
                    edited_body = st.text_area(
                        "Message", value=body or "", height=180,
                        key=f"body_{row_id}", label_visibility="collapsed"
                    )
                    col_a, col_r, _ = st.columns([1, 1, 6])
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

                st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        # ── Send approved section ──
        approved_count = rw_conn.execute(
            "SELECT COUNT(*) FROM outreach_log WHERE status = 'approved'"
        ).fetchone()[0]

        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown(f'<div class="section-header">Ready to Send &nbsp;·&nbsp; {approved_count} approved</div>', unsafe_allow_html=True)

        if approved_count > 0:
            if st.button(f"Send {approved_count} Approved Emails", type="primary"):
                from outreach import send_approved_emails
                with st.spinner("Sending..."):
                    result = send_approved_emails()
                st.success(
                    f"Done — {result['sent']} sent, {result['dry_run']} dry-run, {result['failed']} failed"
                )
                st.rerun()
        else:
            st.markdown('<p style="color:#94A3B8; font-size:0.875rem;">No approved emails to send. Approve emails above first.</p>', unsafe_allow_html=True)

        rw_conn.close()
