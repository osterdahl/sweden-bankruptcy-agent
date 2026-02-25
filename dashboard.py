#!/usr/bin/env python3
"""
Nordic Bankruptcy Monitor — Streamlit Dashboard

Reporting dashboard with outreach approval queue.
Supports multi-country data (Sweden, Norway, Denmark, Finland) with
backward compatibility for Sweden-only databases.
Read-only for analytics; read-write only for outreach_log approval actions.
Run: streamlit run dashboard.py
"""

import os
import sqlite3
from html import escape
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


def query_df(conn, sql: str, params=None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame. Returns empty DF on error."""
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Multi-country support
# ---------------------------------------------------------------------------
COUNTRY_OPTIONS = {"se": "Sweden", "no": "Norway", "dk": "Denmark", "fi": "Finland"}
COUNTRY_FLAGS = {"se": "\U0001f1f8\U0001f1ea", "no": "\U0001f1f3\U0001f1f4", "dk": "\U0001f1e9\U0001f1f0", "fi": "\U0001f1eb\U0001f1ee"}


def _has_country_column(conn, table="bankruptcy_records"):
    """Check if the country column has been added by the migration."""
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return "country" in [row[1] for row in cursor.fetchall()]
    except Exception:
        return False


def _country_filter_sql(has_country, selected_countries, table_alias=""):
    """Build a WHERE clause fragment for country filtering.

    Returns (sql_fragment, params) where sql_fragment is either:
    - empty string (no filter needed), or
    - "AND <alias>country IN (?, ?, ...)" with matching params
    If *has_country* is False (column missing), returns no filter for
    backward compatibility.
    """
    if not has_country:
        return "", []
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ",".join("?" for _ in selected_countries)
    return f"AND {prefix}country IN ({placeholders})", list(selected_countries)


def _country_where_sql(has_country, selected_countries, table_alias=""):
    """Like _country_filter_sql but uses WHERE instead of AND."""
    if not has_country:
        return "", []
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ",".join("?" for _ in selected_countries)
    return f"WHERE {prefix}country IN ({placeholders})", list(selected_countries)


def _country_badge(country_code):
    """Return a flag emoji for a country code, or empty string."""
    return COUNTRY_FLAGS.get(country_code, "")


_SEARCH_BATCH = 75  # 75 records × ~40 output tokens = 3000 tokens/batch


def ai_asset_search(query: str, conn, has_country=False, countries=None) -> pd.DataFrame:
    """Score all records against query using batched AI calls.
    No pre-filter — all records evaluated so financial/industry queries work.
    Returns DataFrame sorted by relevance desc, filtered to score >= 4.
    """
    country_col = ", country" if has_country else ""
    where_sql, where_params = _country_where_sql(has_country, countries or [])
    df = query_df(conn, f"""
        SELECT company_name, org_number, region, industry_name,
               employees, net_sales, total_assets, ai_score, asset_types, trustee_email{country_col}
        FROM bankruptcy_records
        {where_sql}
        ORDER BY ai_score DESC
    """, where_params)
    if df.empty:
        return df

    all_lines = [
        f"{i+1}. {r.company_name} | {r.industry_name} | "
        f"sales:{f'{r.net_sales:,} SEK' if r.net_sales else '-'} | "
        f"assets:{f'{r.total_assets:,} SEK' if r.total_assets else '-'} | "
        f"emp:{r.employees if r.employees is not None else '-'} | {r.asset_types or '-'}"
        for i, r in enumerate(df.itertuples())
    ]
    system_msg = (
        'You evaluate bankrupt Nordic companies for data asset acquisition.\n'
        f'Query: "{query}"\n\n'
        'Rate each company 0-10 for relevance. 0=irrelevant, 10=perfect match.\n'
        'Reply ONLY as JSON array: [{"i":<number>,"score":<0-10>,"reason":"one sentence"}, ...]'
    )

    import json, re as _re
    provider = os.getenv('AI_PROVIDER', 'anthropic').lower()
    score_map: dict = {}
    batches = [all_lines[s:s + _SEARCH_BATCH] for s in range(0, len(all_lines), _SEARCH_BATCH)]

    try:
        progress = st.progress(0, text="Scoring…")
        for idx, batch in enumerate(batches):
            progress.progress(idx / len(batches), text=f"Scoring batch {idx+1}/{len(batches)}…")
            prompt = system_msg + '\n\nCompanies:\n' + '\n'.join(batch)
            if provider == 'openai':
                from openai import OpenAI
                resp = OpenAI(api_key=os.getenv('OPENAI_API_KEY')).chat.completions.create(
                    model=os.getenv('AI_MODEL', 'gpt-4o-mini'),
                    max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.choices[0].message.content.strip()
            else:
                from anthropic import Anthropic
                resp = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY')).messages.create(
                    model=os.getenv('AI_MODEL', 'claude-haiku-4-5-20251001'),
                    max_tokens=3000,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = resp.content[0].text.strip()
            m = _re.search(r'\[.*\]', raw, _re.DOTALL)
            for item in (json.loads(m.group(0)) if m else []):
                score_map[item['i']] = (item['score'], item.get('reason', ''))
        progress.empty()

        df['relevance'] = [score_map.get(i + 1, (0, ''))[0] for i in range(len(df))]
        df['search_reason'] = [score_map.get(i + 1, (0, ''))[1] for i in range(len(df))]
        result = df[df['relevance'] >= 4].sort_values('relevance', ascending=False)
        cols = ['company_name', 'org_number', 'region', 'industry_name', 'ai_score',
                'relevance', 'search_reason', 'asset_types', 'trustee_email']
        if has_country and 'country' in result.columns:
            cols.append('country')
        return result[cols]
    except Exception as e:
        st.error(f"AI search failed: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Page config & global styles
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Bankruptcy Monitor", layout="wide", page_icon="📊")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ── Global — font only, never touch the app background ── */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
[data-testid="block-container"] { padding: 2rem 2.5rem 3rem; }
h1, h2, h3 { letter-spacing: -0.02em; color: var(--text-color); }
h1 { font-weight: 700; font-size: 1.75rem; margin-bottom: 0; }

/* ── Cards
   Uses Streamlit's --secondary-background-color which is always distinct
   from the main background in both themes:
     light → #F0F2F6 on white   (slightly gray card on white page)
     dark  → #262730 on #0E1117 (lighter card on dark page)
─────────────────────────────────────────────────────────────── */
.kpi-card {
    background: var(--secondary-background-color);
    border: 1px solid rgba(128,128,128,0.18);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    min-height: 90px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.email-card {
    background: var(--secondary-background-color);
    border: 1px solid rgba(128,128,128,0.18);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

/* ── KPI text ── */
.kpi-label {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-color);
    opacity: 0.45;
    margin-bottom: 0.5rem;
}
.kpi-value          { font-size: 2rem; font-weight: 700; color: var(--text-color); line-height: 1; }
.kpi-value.positive { color: #059669; }
.kpi-value.warning  { color: #D97706; }
.kpi-value.danger   { color: #E53E3E; }

/* ── Priority badges — light mode defaults ── */
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

/* Dark badge variants (OS dark mode) */
@media (prefers-color-scheme: dark) {
    .badge-high   { background: #450A0A; color: #FCA5A5; border-color: #7F1D1D; }
    .badge-medium { background: #451A03; color: #FCD34D; border-color: #78350F; }
    .badge-low    { background: #052E16; color: #86EFAC; border-color: #166534; }
}

/* ── Asset pills ── */
.pill {
    display: inline-block;
    background: rgba(128,128,128,0.12);
    border: 1px solid rgba(128,128,128,0.22);
    color: var(--text-color);
    opacity: 0.75;
    padding: 1px 8px;
    border-radius: 6px;
    font-size: 0.72rem;
    font-weight: 500;
    margin-right: 4px;
}

/* ── Outreach card internals ── */
.email-card-header { display: flex; align-items: center; gap: 10px; margin-bottom: 0.5rem; flex-wrap: wrap; }
.company-name  { font-weight: 600; font-size: 1rem; color: var(--text-color); }
.score-chip    { font-size: 0.72rem; font-weight: 600; color: var(--text-color); opacity: 0.55;
                 background: rgba(128,128,128,0.1); border: 1px solid rgba(128,128,128,0.2);
                 border-radius: 6px; padding: 2px 8px; }
.ai-reason     { font-size: 0.82rem; color: var(--text-color); opacity: 0.6;
                 line-height: 1.5; margin: 6px 0 10px; }
.email-to      { font-size: 0.8rem; color: var(--text-color); opacity: 0.45; margin-bottom: 10px; }
.email-to span { opacity: 1; font-weight: 500; }
.company-meta  { font-size: 0.78rem; color: var(--text-color); opacity: 0.55;
                 margin: 4px 0 8px; }

/* ── Status text ── */
.status-ok      { color: #059669; font-size: 0.82rem; font-weight: 500; }
.status-warn    { color: #D97706; font-size: 0.82rem; font-weight: 500; }
.status-error   { color: #E53E3E; font-size: 0.82rem; font-weight: 500; }
.status-neutral { color: var(--text-color); opacity: 0.4; font-size: 0.82rem; }

/* ── Section headers ── */
.section-header {
    font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--text-color); opacity: 0.4;
    padding-bottom: 0.5rem; border-bottom: 1px solid rgba(128,128,128,0.18); margin-bottom: 1rem;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0; border-bottom: 1px solid rgba(128,128,128,0.2);
    background: transparent; padding: 0;
}
.stTabs [data-baseweb="tab"] {
    padding: 0.75rem 1.25rem; font-size: 0.875rem; font-weight: 500;
    color: var(--text-color); opacity: 0.5; background: transparent;
    border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.stTabs [aria-selected="true"] {
    color: var(--text-color) !important; opacity: 1 !important;
    border-bottom: 2px solid var(--text-color) !important; background: transparent !important;
}
.stTabs [data-baseweb="tab-panel"] { padding-top: 1.5rem; }

/* ── Buttons — shape/font only, let Streamlit own the colours ── */
.stButton > button {
    border-radius: 8px !important;
    font-size: 0.875rem !important;
    font-weight: 500 !important;
    transition: all 0.15s ease !important;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15) !important;
}

/* ── Inputs ── */
.stTextInput input, .stTextArea textarea {
    border-radius: 8px !important;
    border: 1px solid rgba(128,128,128,0.25) !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    box-shadow: 0 0 0 3px rgba(128,128,128,0.15) !important;
}

hr { border-color: rgba(128,128,128,0.2) !important; margin: 1.5rem 0 !important; }
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
# Sidebar — Country filter
# ---------------------------------------------------------------------------
_br_has_country = _has_country_column(conn, "bankruptcy_records")
_ol_has_country = _has_country_column(conn, "outreach_log")

selected_countries = st.sidebar.multiselect(
    "Countries",
    options=list(COUNTRY_OPTIONS.keys()),
    default=list(COUNTRY_OPTIONS.keys()),
    format_func=lambda x: f"{COUNTRY_FLAGS.get(x, '')} {COUNTRY_OPTIONS[x]}",
)

if not selected_countries:
    st.sidebar.warning("Select at least one country.")
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_overview, tab_queue, tab_search = st.tabs(["Overview", "Outreach Queue", "\U0001f50d Asset Search"])

# ---------------------------------------------------------------------------
# TAB 1: Overview
# ---------------------------------------------------------------------------
with tab_overview:

    # ── KPI row ──
    total_filings, duplicates, sent, failed, high_count, med_count = 0, 0, 0, 0, 0, 0

    br_filter, br_params = _country_where_sql(_br_has_country, selected_countries)
    br_and, br_and_params = _country_filter_sql(_br_has_country, selected_countries)
    ol_and, ol_and_params = _country_filter_sql(_ol_has_country, selected_countries)

    if table_exists(conn, "bankruptcy_records"):
        total_filings = conn.execute(
            f"SELECT COUNT(*) FROM bankruptcy_records {br_filter}", br_params
        ).fetchone()[0]
        dup_row = conn.execute(
            f"SELECT COUNT(*) FROM ("
            f"  SELECT org_number, initiated_date FROM bankruptcy_records"
            f"  {br_filter}"
            f"  GROUP BY org_number, initiated_date HAVING COUNT(*) > 1"
            f")",
            br_params,
        ).fetchone()
        duplicates = dup_row[0] if dup_row else 0
        high_count = conn.execute(
            f"SELECT COUNT(*) FROM bankruptcy_records WHERE priority='HIGH' {br_and}",
            br_and_params,
        ).fetchone()[0]
        med_count = conn.execute(
            f"SELECT COUNT(*) FROM bankruptcy_records WHERE priority='MEDIUM' {br_and}",
            br_and_params,
        ).fetchone()[0]

    if table_exists(conn, "outreach_log"):
        sent = conn.execute(
            f"SELECT COUNT(*) FROM outreach_log WHERE status='sent' {ol_and}",
            ol_and_params,
        ).fetchone()[0]
        failed = conn.execute(
            f"SELECT COUNT(*) FROM outreach_log WHERE status IN ('failed','bounced') {ol_and}",
            ol_and_params,
        ).fetchone()[0]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.markdown(kpi("Total Filings", total_filings), unsafe_allow_html=True)
    c2.markdown(kpi("High Priority", high_count, "danger" if high_count else ""), unsafe_allow_html=True)
    c3.markdown(kpi("Medium Priority", med_count, "warning" if med_count else ""), unsafe_allow_html=True)
    c4.markdown(kpi("Emails Sent", sent, "positive" if sent else ""), unsafe_allow_html=True)
    c5.markdown(kpi("Failed / Bounced", failed, "danger" if failed else ""), unsafe_allow_html=True)
    c6.markdown(kpi("Duplicate Records", duplicates), unsafe_allow_html=True)

    st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)

    # ── Per-country analytics breakdown ──
    if _br_has_country and table_exists(conn, "bankruptcy_records"):
        st.markdown('<div class="section-header">Country Breakdown</div>', unsafe_allow_html=True)

        # Record counts per country
        country_counts_df = query_df(conn, f"""
            SELECT country, COUNT(*) as count
            FROM bankruptcy_records
            {br_filter}
            GROUP BY country
            ORDER BY count DESC
        """, br_params)

        if not country_counts_df.empty:
            country_counts_df["country_label"] = country_counts_df["country"].apply(
                lambda c: f"{COUNTRY_FLAGS.get(c, '')} {COUNTRY_OPTIONS.get(c, c)}"
            )

            col_chart, col_priority = st.columns(2)

            with col_chart:
                st.markdown("**Records per Country**")
                chart_df = country_counts_df.set_index("country_label")[["count"]]
                st.bar_chart(chart_df)

            # Per-country priority distribution
            with col_priority:
                st.markdown("**Priority Distribution by Country**")
                priority_df = query_df(conn, f"""
                    SELECT country,
                           SUM(CASE WHEN priority = 'HIGH' THEN 1 ELSE 0 END) as HIGH,
                           SUM(CASE WHEN priority = 'MEDIUM' THEN 1 ELSE 0 END) as MEDIUM,
                           SUM(CASE WHEN priority = 'LOW' THEN 1 ELSE 0 END) as LOW
                    FROM bankruptcy_records
                    {br_filter}
                    GROUP BY country
                    ORDER BY country
                """, br_params)

                if not priority_df.empty:
                    priority_df["country_label"] = priority_df["country"].apply(
                        lambda c: f"{COUNTRY_FLAGS.get(c, '')} {COUNTRY_OPTIONS.get(c, c)}"
                    )
                    display_df = priority_df.set_index("country_label")[["HIGH", "MEDIUM", "LOW"]]
                    st.dataframe(display_df, use_container_width=True)

            st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── AI scoring status ──
    if table_exists(conn, "bankruptcy_records"):
        ai_enabled      = os.getenv("AI_SCORING_ENABLED", "false").lower() == "true"
        has_key         = bool(os.getenv("ANTHROPIC_API_KEY"))
        ai_failed_count = conn.execute(
            f"SELECT COUNT(*) FROM bankruptcy_records WHERE ai_reason LIKE '[AI failed%' {br_and}",
            br_and_params,
        ).fetchone()[0]
        unscored = conn.execute(
            f"SELECT COUNT(*) FROM bankruptcy_records WHERE ai_score IS NULL {br_and}",
            br_and_params,
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

        no_email = conn.execute(
            f"SELECT COUNT(*) FROM bankruptcy_records WHERE trustee_email = '' AND trustee <> 'N/A' {br_and}",
            br_and_params,
        ).fetchone()[0]
        if no_email > 0:
            if os.getenv("BRAVE_API_KEY"):
                st.info(f"{no_email} records are missing trustee emails.")
                if st.button(f"Look up emails for {no_email} records"):
                    from scheduler import backfill_emails
                    with st.spinner("Looking up emails via Brave Search — this may take several minutes..."):
                        count = backfill_emails()
                    st.success(f"Found emails for {count} records.")
                    st.rerun()
            else:
                st.markdown('<p class="status-neutral">Set BRAVE_API_KEY to enable trustee email lookup.</p>', unsafe_allow_html=True)

    # ── Bankruptcy records table ──
    st.markdown('<div class="section-header">Bankruptcy Records</div>', unsafe_allow_html=True)

    if table_exists(conn, "bankruptcy_records"):
        _country_col = ", country" if _br_has_country else ""
        df = query_df(conn, f"""
            SELECT company_name, initiated_date, region, industry_name,
                   sni_code, employees, net_sales, total_assets,
                   trustee, trustee_firm,
                   priority, ai_score, asset_types, ai_reason,
                   org_number, trustee_email{_country_col}
            FROM bankruptcy_records
            {br_filter}
            ORDER BY initiated_date DESC
        """, br_params)
        if df.empty:
            st.markdown('<p style="color:#94A3B8; font-size:0.875rem;">No records yet.</p>', unsafe_allow_html=True)
        else:
            # Add country flag next to company name
            if _br_has_country and "country" in df.columns:
                df["company_display"] = df.apply(
                    lambda r: f"{_country_badge(r['country'])} {r['company_name']}", axis=1
                )
            else:
                df["company_display"] = df["company_name"]

            df["_stage"] = False
            row_height = 35
            table_height = min(len(df) * row_height + 60, 700)
            edited_df = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                height=table_height,
                column_order=[
                    "_stage", "company_display", "initiated_date", "region", "industry_name",
                    "sni_code", "employees", "net_sales", "total_assets", "trustee",
                    "trustee_firm", "priority", "ai_score", "asset_types", "ai_reason", "org_number",
                ],
                column_config={
                    "_stage":           st.column_config.CheckboxColumn("Stage", default=False),
                    "company_display":  st.column_config.TextColumn("Company", disabled=True),
                    "company_name":     st.column_config.TextColumn("Company (raw)", disabled=True),
                    "initiated_date":   st.column_config.TextColumn("Date", disabled=True),
                    "region":           st.column_config.TextColumn("Region", disabled=True),
                    "industry_name":    st.column_config.TextColumn("Industry", disabled=True),
                    "sni_code":         st.column_config.TextColumn("SNI", disabled=True),
                    "employees":        st.column_config.NumberColumn("Employees", format="%d", disabled=True),
                    "net_sales":        st.column_config.NumberColumn("Net Sales (SEK)", format="%,d", disabled=True),
                    "total_assets":     st.column_config.NumberColumn("Assets (SEK)", format="%,d", disabled=True),
                    "trustee":          st.column_config.TextColumn("Trustee", disabled=True),
                    "trustee_firm":     st.column_config.TextColumn("Firm", disabled=True),
                    "priority":         st.column_config.TextColumn("Priority", disabled=True),
                    "ai_score":         st.column_config.NumberColumn("Score", format="%.0f / 10", disabled=True),
                    "asset_types":      st.column_config.TextColumn("Asset Types", disabled=True),
                    "ai_reason":        st.column_config.TextColumn("AI Reason", width="large", disabled=True),
                    "org_number":       st.column_config.TextColumn("Org \u2116", disabled=True),
                },
            )
            staged_rows = edited_df[edited_df["_stage"] == True]  # noqa: E712
            if len(staged_rows) > 0:
                if st.button(f"Stage {len(staged_rows)} selected for outreach", type="primary"):
                    from outreach import stage_records_direct
                    records = staged_rows.fillna("").to_dict("records")
                    result = stage_records_direct(records)
                    if result["staged"] > 0:
                        parts = [f"{result['staged']} staged"]
                        if result["skipped"]:
                            parts.append(f"{result['skipped']} already contacted")
                        if result["opted_out"]:
                            parts.append(f"{result['opted_out']} opted out")
                        if result["no_email"]:
                            parts.append(f"{result['no_email']} skipped (no trustee email)")
                        st.success(" · ".join(parts))
                        st.rerun()
                    else:
                        reasons = []
                        if result["skipped"]:
                            reasons.append(f"{result['skipped']} already in outreach queue")
                        if result["opted_out"]:
                            reasons.append(f"{result['opted_out']} opted out")
                        if result["no_email"]:
                            reasons.append(
                                f"{result['no_email']} have no trustee email "
                                "(set BRAVE_API_KEY to enable automatic lookup)"
                            )
                        st.warning("Nothing staged — " + " · ".join(reasons) if reasons else "Nothing staged.")
    else:
        st.markdown('<p style="color:#94A3B8;">Table not found yet.</p>', unsafe_allow_html=True)

    # ── Outreach log ──
    st.markdown('<div class="section-header" style="margin-top:2rem;">Outreach Log</div>', unsafe_allow_html=True)

    if table_exists(conn, "outreach_log"):
        _ol_country_col = ", country" if _ol_has_country else ""
        _ol_where, _ol_params = _country_where_sql(_ol_has_country, selected_countries)
        df_out = query_df(conn, f"""
            SELECT company_name, trustee_email, status, subject, sent_at, error_message{_ol_country_col}
            FROM outreach_log
            {_ol_where}
            ORDER BY sent_at DESC
            LIMIT 200
        """, _ol_params)
        if df_out.empty:
            st.markdown('<p style="color:#94A3B8; font-size:0.875rem;">No outreach entries yet.</p>', unsafe_allow_html=True)
        else:
            # Add country flag next to company name in outreach log
            if _ol_has_country and "country" in df_out.columns:
                df_out["company_name"] = df_out.apply(
                    lambda r: f"{_country_badge(r['country'])} {r['company_name']}", axis=1
                )
                df_out = df_out.drop(columns=["country"])
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
        _rw_ol_has_country = _has_country_column(rw_conn, "outreach_log")
        _rw_country_col = ", o.country" if _rw_ol_has_country else ""
        _rw_ol_and, _rw_ol_params = _country_filter_sql(_rw_ol_has_country, selected_countries, "o")

        pending = rw_conn.execute(
            f"""SELECT o.id, o.org_number, o.trustee_email, o.company_name,
                      o.subject, o.body,
                      b.priority, b.ai_score, b.asset_types, b.ai_reason,
                      b.employees, b.net_sales, b.total_assets, b.industry_name{_rw_country_col}
               FROM outreach_log o
               LEFT JOIN bankruptcy_records b ON o.org_number = b.org_number
               WHERE o.status = 'pending' {_rw_ol_and}
               GROUP BY o.id
               ORDER BY COALESCE(b.ai_score, 0) DESC, o.id""",
            _rw_ol_params,
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

            def fmt_sek(v):
                if not v:
                    return None
                if v >= 1_000_000:
                    return f"{v / 1_000_000:.1f}M SEK"
                if v >= 1_000:
                    return f"{v / 1_000:.0f}k SEK"
                return f"{v} SEK"

            for row in pending:
                row_id, org_num, email, company = row[0], row[1], row[2], row[3]
                subject, body = row[4], row[5]
                priority, ai_score, asset_types, ai_reason = row[6], row[7], row[8], row[9]
                employees, net_sales, total_assets, industry_name = row[10], row[11], row[12], row[13]
                row_country = row[14] if _rw_ol_has_country and len(row) > 14 else None

                score_str = f"{ai_score}/10" if ai_score else "\u2014"
                country_flag = f"{_country_badge(row_country)} " if row_country else ""

                meta_parts = []
                if industry_name:
                    meta_parts.append(escape(industry_name))
                if employees:
                    meta_parts.append(f"{employees:,} employees")
                rev = fmt_sek(net_sales)
                if rev:
                    meta_parts.append(f"Revenue {rev}")
                assets = fmt_sek(total_assets)
                if assets:
                    meta_parts.append(f"Assets {assets}")
                meta_html = " \u00b7 ".join(meta_parts)

                header_html = f"""
                <div class="email-card-header">
                    <span class="company-name">{country_flag}{escape(company or '')}</span>
                    {badge(priority)}
                    <span class="score-chip">Score {score_str}</span>
                    {pills(asset_types)}
                </div>
                {f'<div class="company-meta">{meta_html}</div>' if meta_html else ''}
                {f'<div class="ai-reason">{escape(ai_reason)}</div>' if ai_reason else ''}
                <div class="email-to">To: <span>{escape(email or '')}</span> &nbsp;\u00b7&nbsp; {escape(org_num or '')}</div>
                """
                st.markdown(f'<div class="email-card">{header_html}</div>', unsafe_allow_html=True)

                with st.container():
                    edited_subject = st.text_input("Subject", value=subject or "", key=f"subj_{row_id}", label_visibility="collapsed")
                    edited_body = st.text_area(
                        "Message", value=body or "", height=180,
                        key=f"body_{row_id}", label_visibility="collapsed"
                    )
                    col_a, col_r, _ = st.columns([1, 1, 6])
                    with col_a:
                        if st.button("Approve", key=f"approve_{row_id}", type="primary"):
                            rw_conn.execute(
                                "UPDATE outreach_log SET status = 'approved', subject = ?, body = ? WHERE id = ?",
                                (edited_subject, edited_body, row_id),
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

        # ── Re-queue dry-run section ──
        dry_run_count = rw_conn.execute(
            f"SELECT COUNT(*) FROM outreach_log WHERE status = 'dry-run' {_rw_ol_and}",
            _rw_ol_params,
        ).fetchone()[0]

        if dry_run_count > 0:
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown(f'<div class="section-header">Dry-Run Queue &nbsp;\u00b7&nbsp; {dry_run_count} not sent</div>', unsafe_allow_html=True)
            st.caption("These were approved while MAILGUN_LIVE=false so nothing was delivered. Set MAILGUN_LIVE=true, then re-queue to send for real.")
            if st.button(f"Re-queue {dry_run_count} dry-run emails as pending"):
                rw_conn.execute("UPDATE outreach_log SET status = 'pending' WHERE status = 'dry-run'")
                rw_conn.commit()
                st.rerun()

        # ── Send approved section ──
        approved_count = rw_conn.execute(
            f"SELECT COUNT(*) FROM outreach_log WHERE status = 'approved' {_rw_ol_and}",
            _rw_ol_params,
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

# ---------------------------------------------------------------------------
# TAB 3: Asset Search
# ---------------------------------------------------------------------------
with tab_search:
    st.markdown("### 🔍 AI Asset Search")
    st.caption("Describe the type of data assets you're looking for. AI scores all candidate records in one call.")

    api_ready = bool(os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY'))
    if not api_ready:
        st.info("Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env to enable AI search.")
    else:
        search_query = st.text_input(
            "Search query",
            placeholder='e.g. "source code", "ML training data", "media rights and photo libraries"',
            label_visibility="collapsed",
        )
        if st.button("Search", type="primary", disabled=not search_query) and search_query:
            results = ai_asset_search(search_query, conn, _br_has_country, selected_countries)
            if results.empty:
                st.info("No matching companies found (relevance \u2265 4/10).")
            else:
                st.success(f"**{len(results)} matching companies** \u2014 sorted by relevance")
                # Add country flag to company name in search results
                if _br_has_country and "country" in results.columns:
                    results["company_display"] = results.apply(
                        lambda r: f"{_country_badge(r['country'])} {r['company_name']}", axis=1
                    )
                else:
                    results["company_display"] = results["company_name"]
                results["_stage"] = False
                edited = st.data_editor(
                    results,
                    use_container_width=True,
                    hide_index=True,
                    column_order=["_stage", "company_display", "region", "industry_name",
                                  "ai_score", "relevance", "search_reason", "asset_types",
                                  "org_number"],
                    column_config={
                        "_stage":           st.column_config.CheckboxColumn("Stage", default=False),
                        "company_display":  st.column_config.TextColumn("Company", disabled=True),
                        "company_name":     st.column_config.TextColumn("Company (raw)", disabled=True),
                        "region":           st.column_config.TextColumn("Region", disabled=True),
                        "industry_name":    st.column_config.TextColumn("Industry", disabled=True),
                        "ai_score":         st.column_config.NumberColumn("Score", format="%.0f / 10", disabled=True),
                        "relevance":        st.column_config.NumberColumn("Relevance", format="%.0f / 10", disabled=True),
                        "search_reason":    st.column_config.TextColumn("Search Reason", width="large", disabled=True),
                        "asset_types":      st.column_config.TextColumn("Asset Types", disabled=True),
                        "org_number":       st.column_config.TextColumn("Org \u2116", disabled=True),
                    },
                )
                staged_rows = edited[edited["_stage"] == True]  # noqa: E712
                if len(staged_rows) > 0:
                    if st.button(f"Stage {len(staged_rows)} selected for outreach", type="primary"):
                        from outreach import stage_records_direct
                        records = staged_rows.fillna("").to_dict("records")
                        result = stage_records_direct(records)
                        if result["staged"] > 0:
                            parts = [f"{result['staged']} staged"]
                            if result["skipped"]:
                                parts.append(f"{result['skipped']} already contacted")
                            if result["opted_out"]:
                                parts.append(f"{result['opted_out']} opted out")
                            if result["no_email"]:
                                parts.append(f"{result['no_email']} skipped (no trustee email)")
                            st.success(" · ".join(parts))
                            st.rerun()
                        else:
                            reasons = []
                            if result["skipped"]:
                                reasons.append(f"{result['skipped']} already in outreach queue")
                            if result["opted_out"]:
                                reasons.append(f"{result['opted_out']} opted out")
                            if result["no_email"]:
                                reasons.append(f"{result['no_email']} have no trustee email")
                            st.warning("Nothing staged — " + " · ".join(reasons) if reasons else "Nothing staged.")
