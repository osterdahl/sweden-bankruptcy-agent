# Nordic Bankruptcy Monitor — Architecture Reference

> **Status**: Nordic expansion complete (merged 2026-03-01).
> This document describes the implemented architecture.

---

## Overview

The system monitors bankruptcy filings across Sweden, Norway, Denmark, and Finland using a **country-plugin architecture**. Each country is an independent Python module. Core logic (scoring, deduplication, outreach, reporting) is shared.

**Entry point**: `run.py` — imports country plugins and calls `core.pipeline.run_all()`.

---

## Directory Structure

```
sweden-bankruptcy-agent/
├── run.py                        # Entry point (CLI + GitHub Actions)
├── core/
│   ├── models.py                 # BankruptcyRecord dataclass (country-aware)
│   ├── scoring.py                # Rule-based + AI scoring engine (NACE code maps)
│   ├── email_lookup.py           # Shared email helpers (Brave Search, regex)
│   ├── pipeline.py               # Orchestrator: scrape → dedup → score → lookup → email
│   ├── database.py               # SQLite layer (multi-country schema)
│   └── reporting.py              # Email formatting (HTML + plain text)
├── countries/
│   ├── protocol.py               # CountryPlugin Protocol definition
│   ├── __init__.py               # Country registry + get_active_countries()
│   ├── sweden.py                 # SE: TIC.io scraper + Advokatsamfundet lookup
│   ├── norway.py                 # NO: brreg.no API
│   ├── denmark.py                # DK: Statstidende + CVR registry
│   └── finland.py                # FI: PRH open data API
├── templates/
│   ├── outreach_se.md            # Swedish outreach template
│   ├── outreach_no.md            # Norwegian outreach (English)
│   ├── outreach_dk.md            # Danish outreach (English)
│   └── outreach_fi.md            # Finnish outreach (English)
├── scheduler.py                  # APScheduler entry point (local/cron use)
├── dashboard.py                  # Streamlit dashboard (multi-country)
├── outreach.py                   # Mailgun outreach staging
├── bankruptcy_monitor.py         # Legacy Sweden-only entry point (still works)
└── .github/workflows/
    └── monthly-report.yml        # GitHub Actions — scheduled monthly runs
```

---

## Country Plugin Protocol

Each country module exposes a plugin class registered at import time.

```python
# countries/protocol.py
class CountryPlugin(Protocol):
    code: str          # ISO 3166-1 alpha-2: "se", "no", "dk", "fi"
    name: str          # Display name: "Sweden", "Norway", etc.
    currency: str      # "SEK", "NOK", "DKK", "EUR"
    language: str      # Primary outreach language

    def scrape_bankruptcies(self, year: int, month: int,
                            cached_keys: set) -> List[BankruptcyRecord]: ...

    def lookup_trustee_email(self, trustee_name: str,
                             trustee_firm: str) -> Optional[str]: ...

    def get_industry_code_maps(self) -> tuple: ...

    def get_default_regions(self) -> List[str]: ...

    def parse_financial_value(self, raw: str) -> Optional[int]: ...
```

Country modules self-register at import time:

```python
# Bottom of each countries/*.py file
register_country(SwedenPlugin())   # triggers on: import countries.sweden
```

This means `run.py` must import all country modules before calling `run_all()`. If a module is not imported, that country's plugin is never added to the registry.

---

## Pipeline Flow

```
run.py
  └── run_all()                         # core/pipeline.py
        └── get_active_countries()      # reads COUNTRIES env var
              └── for each plugin:
                    run_country(plugin, year, month)
                      1. scrape_bankruptcies(year, month, cached_keys)
                      2. deduplicate(records)                 → scheduler.py / core/database.py
                      3. score_bankruptcies(records, plugin)  → core/scoring.py
                      4. lookup_trustee_emails(candidates)    → core/email_lookup.py
                      5. stage_outreach(candidates)           → outreach.py
                      6. format + send email report           → core/reporting.py
```

---

## Data Model

```python
# core/models.py
@dataclass
class BankruptcyRecord:
    country: str = ""              # ISO code: "se", "no", "dk", "fi"
    company_name: str = ""
    org_number: str = ""
    initiated_date: str = ""
    court: str = ""
    industry_code: str = ""        # NACE/SNI/TOL code
    industry_name: str = ""
    trustee: str = ""
    trustee_firm: str = ""
    trustee_address: str = ""
    employees: Optional[int] = None
    net_sales: Optional[int] = None
    total_assets: Optional[int] = None
    region: str = ""
    ai_score: Optional[int] = None
    ai_reason: Optional[str] = None
    priority: Optional[str] = None   # "HIGH", "MEDIUM", "LOW"
    asset_types: Optional[str] = None
    trustee_email: Optional[str] = None
```

---

## Database Schema

SQLite at `data/bankruptcies.db`. Composite primary key is country-aware.

```sql
CREATE TABLE bankruptcy_records (
    country          TEXT NOT NULL DEFAULT 'se',
    org_number       TEXT NOT NULL,
    initiated_date   TEXT NOT NULL,
    trustee_email    TEXT NOT NULL DEFAULT '',
    company_name     TEXT,
    -- ... all other fields ...
    PRIMARY KEY (country, org_number, initiated_date, trustee_email)
);
```

Existing Sweden records have `country='se'` (migration default).

---

## Data Sources

| Country | Primary source | Method | Trustee coverage |
|---------|---------------|--------|-----------------|
| Sweden | [TIC.io](https://tic.io/en/oppna-data/konkurser) | HTTP + BeautifulSoup | 100% direct |
| Norway | [brreg.no](https://data.brreg.no/enhetsregisteret/api) | REST API (JSON) | Via court announcements |
| Denmark | [Statstidende](https://statstidende.dk) + [CVR](https://virk.dk/cvr) | Scraping + API | Via Statstidende notices |
| Finland | [PRH](https://avoindata.prh.fi/bis/v1) | REST API (JSON) | Via PRH announcements |

All use NACE Rev. 2 industry codes at the 2-digit level, so scoring maps are shared across countries.

---

## Scoring

Two-pass to minimise API cost:

1. **Rule-based** (all records) — NACE code maps, employee/revenue thresholds, keyword detection
2. **AI validation** (HIGH/MEDIUM only) — Claude API refines the top ~10–15%; provides reasoning

The NACE code maps (high-value industries, low-value industries, asset type labels) are defined in `core/scoring.py` as defaults. Each country plugin can return overrides via `get_industry_code_maps()`.

---

## Adding a New Country

1. Create `countries/{code}.py` implementing `CountryPlugin`
2. Add `register_country(MyPlugin())` at the bottom of the file
3. Add `import countries.{code}` in `run.py`
4. Add an outreach template at `templates/outreach_{code}.md`

No changes to core pipeline logic required.

---

## Key Design Decisions

- **NACE codes are universal**: SNI (SE), SN (NO), DB07 (DK), TOL (FI) are all national implementations of NACE Rev. 2. The 2-digit prefix scoring maps are ~95% identical — countries inherit the default and override only where needed.
- **English outreach for new countries**: Norway/Denmark/Finland outreach is in English. Swedish stays in Swedish.
- **Brave Search fallback is shared**: Country-specific lawyer directories are tried first; Brave Search is the universal fallback for trustee email lookup.
- **Backward compatibility**: `COUNTRIES=se` default means existing Swedish-only workflows continue unchanged. `bankruptcy_monitor.py` remains as a working legacy entry point.
- **No over-engineering**: Each country plugin is a single file. No abstract base classes or factory patterns — just a Protocol and plain implementations.
