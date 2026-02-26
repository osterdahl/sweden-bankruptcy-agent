# Nordic Bankruptcy Monitor — Multi-Country Expansion Plan

## Executive Summary

Expand from Sweden-only to all Nordic countries (Norway, Denmark, Finland) using a
**country-plugin architecture**. Each country is an independent Python module implementing
a shared protocol. The core pipeline (scoring, dedup, outreach, dashboard) remains shared.

**Priority order**: Sweden (done) → Norway → Denmark → Finland

---

## Architecture: Country Plugin Pattern

### Directory Structure

```
sweden-bankruptcy-agent/          →  nordic-bankruptcy-agent/
├── core/
│   ├── __init__.py
│   ├── models.py                 # BankruptcyRecord dataclass (country-aware)
│   ├── scoring.py                # Scoring engine (NACE code maps, AI validation)
│   ├── email_lookup.py           # Shared email extraction helpers (Brave, regex)
│   ├── pipeline.py               # Orchestrator: scrape → dedup → score → lookup → stage
│   ├── database.py               # SQLite layer (from scheduler.py, with country column)
│   └── reporting.py              # Email report generation (HTML + plain text)
├── countries/
│   ├── __init__.py               # Country registry + discovery
│   ├── protocol.py               # CountryPlugin Protocol definition
│   ├── sweden.py                 # SE: TIC.io scraper + Advokatsamfundet lookup
│   ├── norway.py                 # NO: brreg.no API + Advokattilsynet/Advokatforeningen
│   ├── denmark.py                # DK: Statstidende + Advokatsamfundet.dk
│   └── finland.py                # FI: PRH open data + Suomen Asianajajaliitto
├── templates/
│   ├── outreach_se.md            # Swedish outreach (existing)
│   ├── outreach_no.md            # Norwegian outreach (English primary)
│   ├── outreach_dk.md            # Danish outreach (English primary)
│   ├── outreach_fi.md            # Finnish outreach (English primary)
│   └── email_report.html         # Shared HTML report template (from email_template.html)
├── outreach.py                   # Mailgun outreach (country-aware, mostly unchanged)
├── dashboard.py                  # Streamlit dashboard (country filter added)
├── scheduler.py                  # Entry point + scheduling (calls core/pipeline.py)
├── bankruptcy_monitor.py         # Legacy entry point (thin wrapper → core/pipeline.py)
├── requirements.txt
└── .github/workflows/
    └── monthly-report.yml        # Updated for multi-country
```

### Country Protocol (countries/protocol.py)

```python
from typing import Protocol, List, Dict, Optional
from core.models import BankruptcyRecord

class CountryPlugin(Protocol):
    """Each country module must expose these attributes and methods."""

    # --- Identity ---
    code: str                     # ISO 3166-1 alpha-2: "se", "no", "dk", "fi"
    name: str                     # Display name: "Sweden", "Norway", etc.
    currency: str                 # "SEK", "NOK", "DKK", "EUR"
    language: str                 # Primary outreach language: "sv", "en", "no", "da", "fi"

    # --- Data Ingestion ---
    def scrape_bankruptcies(self, year: int, month: int,
                            cached_keys: set) -> List[BankruptcyRecord]:
        """Fetch bankruptcy records for the given month.
        cached_keys: set of (org_number, initiated_date) already in DB.
        Returns only new records."""
        ...

    # --- Trustee Contact Lookup ---
    def lookup_trustee_email(self, trustee_name: str,
                             trustee_firm: str) -> Optional[str]:
        """Look up a trustee's email from the country's lawyer directory.
        Return None if not found. Brave Search fallback handled by core."""
        ...

    # --- Scoring Configuration ---
    def get_industry_code_maps(self) -> tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        """Return (high_value_codes, low_value_codes, asset_type_map).
        Keys are NACE/SNI code prefixes, values are scores or asset type strings.
        Most Nordic countries share NACE Rev. 2 — override only where needed."""
        ...

    # --- Filtering Defaults ---
    def get_default_regions(self) -> List[str]:
        """Return the list of valid region names for this country."""
        ...

    def parse_financial_value(self, raw: str) -> Optional[int]:
        """Parse currency-specific financial strings.
        SE: '134 TSEK' → 134000, NO: '1 200 TNOK' → 1200000, etc."""
        ...
```

### BankruptcyRecord (core/models.py)

Add one field to existing dataclass:

```python
@dataclass
class BankruptcyRecord:
    country: str = ""             # ISO code: "se", "no", "dk", "fi" — NEW
    company_name: str = ""
    org_number: str = ""
    initiated_date: str = ""
    court: str = ""
    industry_code: str = ""       # Renamed from sni_code → generic NACE
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
    priority: Optional[str] = None
    asset_types: Optional[str] = None
    trustee_email: Optional[str] = None
```

### Database Schema Change

```sql
-- Add country column (defaults to 'se' for existing data)
ALTER TABLE bankruptcy_records ADD COLUMN country TEXT NOT NULL DEFAULT 'se';

-- New composite primary key includes country
-- PRIMARY KEY (country, org_number, initiated_date, trustee_email)
```

### Configuration

```bash
# Which countries to process (comma-separated ISO codes, default: se)
COUNTRIES=se,no
# Or run a single country:
COUNTRY=no

# Per-country env vars follow pattern: {COUNTRY}_VARIABLE
# e.g. NO_FILTER_REGIONS="Oslo,Bergen,Trondheim"
# Falls back to generic FILTER_REGIONS if country-specific not set
```

---

## Data Sources Per Country

### Sweden (done) — TIC.io
- **URL**: https://tic.io/en/oppna-data/konkurser
- **Method**: HTTP + BeautifulSoup HTML parsing
- **Auth**: None (public)
- **Lawyer directory**: Advokatsamfundet (advokatsamfundet.se) — directory scraping

### Norway (next) — Brønnøysundregistrene
- **Primary**: brreg.no open API — `data.brreg.no/enhetsregisteret/api`
  - Query for entities with `konkurs` status
  - JSON API, no auth, free, well-documented
  - Fields: org number, name, business codes (NACE), address, registration date
  - **Gap**: Does NOT include trustee/lawyer info directly
- **Supplementary**: Konkursregisteret announcements at domstol.no
  - Court-published bankruptcy announcements include trustee info
  - May require scraping from individual court pages
- **Lawyer directory**:
  - Advokattilsynet register (tilsynet.no/register) — web scraping
  - Advokatforeningen member search (advokatforeningen.no) — web scraping
  - Altinn API (data.altinn.no) — requires Maskinporten auth (apply later)
- **Industry codes**: NACE Rev. 2 (identical prefix structure to SNI)
- **Currency**: NOK — parse "TNOK" format

### Denmark (after Norway) — Statstidende
- **Primary**: Statstidende (statstidende.dk) — Official Gazette
  - Publishes bankruptcy decrees (konkursdekret)
  - Has an API but requires certificate-based auth
  - Alternative: scrape public search at statstidende.dk
- **Supplementary**: CVR register (virk.dk/cvr) — Danish business register
  - Open API for company data (business codes, size, address)
  - Can cross-reference with Statstidende bankruptcy notices
- **Lawyer directory**: Advokatnøglen (advokatnoeglen.dk) — Danish Bar search
- **Industry codes**: DB07 (Danish NACE variant — same 2-digit prefixes)
- **Currency**: DKK — parse "TDKK" format

### Finland (after Denmark) — PRH Open Data
- **Primary**: PRH open data API (avoindata.prh.fi)
  - REST API, free, daily updates
  - Trade Register announcements include bankruptcy filings
  - JSON format, well-documented
- **Supplementary**: Insolvency Register (oikeusrekisterikeskus.fi)
  - More detailed insolvency data, includes trustee info
  - API via Suomi.fi Data Exchange Layer (requires authorization)
- **Lawyer directory**: Suomen Asianajajaliitto (asianajajaliitto.fi) — search
- **Industry codes**: TOL 2008 (Finnish NACE variant — same 2-digit prefixes)
- **Currency**: EUR — parse "TEUR" format

---

## Parallel Work Streams

The work is split into **8 independent streams** that can be assigned to parallel
working agents. Dependencies are minimal and clearly marked.

### Dependency Graph

```
Stream 0 (Protocol + Models)     ← MUST COMPLETE FIRST (small, ~1 hour)
    │
    ├── Stream 1 (Core extraction)        ← can start immediately after S0
    ├── Stream 2 (Sweden migration)       ← can start immediately after S0
    ├── Stream 3 (Norway plugin)          ← can start immediately after S0
    ├── Stream 4 (Denmark plugin)         ← can start immediately after S0
    ├── Stream 5 (Finland plugin)         ← can start immediately after S0
    ├── Stream 6 (Database + scheduler)   ← can start immediately after S0
    ├── Stream 7 (Dashboard)              ← can start immediately after S0
    └── Stream 8 (Outreach)              ← can start immediately after S0
```

**After Stream 0 completes, all other streams run in parallel.**

Integration testing happens after streams converge.

---

### Stream 0: Protocol Definition + Shared Models (PREREQUISITE)

**Owner**: Orchestrator / architect
**Dependency**: None
**Output**: `countries/protocol.py`, `core/models.py`, `core/__init__.py`, `countries/__init__.py`

**Tasks**:
1. Create `core/` and `countries/` directories
2. Write `core/models.py` — BankruptcyRecord dataclass with `country` field
   - Rename `sni_code` → `industry_code` (alias `sni_code` for backward compat)
3. Write `countries/protocol.py` — CountryPlugin Protocol class (as defined above)
4. Write `countries/__init__.py` — Country registry:
   ```python
   COUNTRY_REGISTRY: Dict[str, CountryPlugin] = {}

   def register_country(plugin: CountryPlugin):
       COUNTRY_REGISTRY[plugin.code] = plugin

   def get_country(code: str) -> CountryPlugin:
       return COUNTRY_REGISTRY[code]

   def get_active_countries() -> List[CountryPlugin]:
       codes = os.environ.get('COUNTRIES', 'se').split(',')
       return [COUNTRY_REGISTRY[c.strip()] for c in codes]
   ```
5. Write `core/__init__.py` — exports

**Deliverable**: Frozen interface contract that all other streams code against.

---

### Stream 1: Core Pipeline Extraction

**Owner**: Agent 1
**Dependency**: Stream 0 (protocol + models only — frozen interface)
**Input**: Current `bankruptcy_monitor.py` lines 640-879 (scoring), lines 269-598 (email lookup shared parts)
**Output**: `core/scoring.py`, `core/email_lookup.py`, `core/pipeline.py`, `core/reporting.py`

**Tasks**:
1. **`core/scoring.py`** — Extract scoring engine from bankruptcy_monitor.py
   - Move `calculate_base_score()` — make it accept industry code maps as parameter
   - Move `validate_with_ai()` — country-aware prompt (replace "Swedish" with country name)
   - Move `score_bankruptcies()` orchestrator
   - The NACE code maps (HIGH_VALUE, LOW_VALUE, ASSET_TYPES) become **default maps**
     that countries can override or extend
   - Signature: `calculate_base_score(record, high_codes, low_codes, asset_map) → int`

2. **`core/email_lookup.py`** — Extract shared email utilities
   - Move `_extract_emails()`, `_pick_best_email()`, `_GENERIC_EMAIL_PREFIXES`
   - Move Brave Search email lookup (`_scrape_firm_email`, `_search_brave_email`)
   - These are country-agnostic (Brave works for all countries)
   - Country-specific lookups (Advokatsamfundet etc.) stay in country modules
   - `lookup_trustee_emails(records, country_plugin)` → tries country-specific first,
     falls back to Brave Search

3. **`core/pipeline.py`** — Main orchestrator
   ```python
   def run_country(country: CountryPlugin, year: int, month: int):
       """Full pipeline for one country."""
       cached = get_cached_keys(country.code)
       records = country.scrape_bankruptcies(year, month, cached)
       new_records = deduplicate(records, country.code)
       scored = score_bankruptcies(new_records, country)
       if lookup_enabled:
           lookup_trustee_emails(scored, country)
       stage_outreach(scored, country)
       return scored

   def run_all(year: int, month: int):
       """Run pipeline for all active countries."""
       all_records = []
       for country in get_active_countries():
           all_records.extend(run_country(country, year, month))
       return all_records
   ```

4. **`core/reporting.py`** — Extract email report formatting
   - Move `format_email_html()` and `format_email_plain()` from bankruptcy_monitor.py
   - Parameterize: country name, currency symbol, report title
   - Group records by country if running multi-country

**Testing**: Unit tests with mock CountryPlugin that returns fake records.

---

### Stream 2: Sweden Migration (Reference Plugin)

**Owner**: Agent 2
**Dependency**: Stream 0 (protocol definition)
**Input**: Current `bankruptcy_monitor.py` (all Sweden-specific code)
**Output**: `countries/sweden.py`, `templates/outreach_se.md`

**Tasks**:
1. **`countries/sweden.py`** — Implement CountryPlugin for Sweden
   ```python
   class SwedenPlugin:
       code = "se"
       name = "Sweden"
       currency = "SEK"
       language = "sv"

       def scrape_bankruptcies(self, year, month, cached_keys):
           # Move scrape_tic_bankruptcies() logic here
           # Move _parse_card() here
           ...

       def lookup_trustee_email(self, trustee_name, trustee_firm):
           # Move _search_advokatsamfundet() here
           ...

       def get_industry_code_maps(self):
           return (HIGH_VALUE_SNI_CODES, LOW_VALUE_SNI_CODES, SNI_ASSET_TYPES)

       def get_default_regions(self):
           return ["Stockholm", "Göteborg", "Malmö", ...]

       def parse_financial_value(self, raw):
           # Move _parse_sek() logic here
           ...
   ```

2. Move `outreach_template.md` → `templates/outreach_se.md`

3. Move Sweden-specific constants (SNI code maps, region lists) into the plugin

4. Keep `bankruptcy_monitor.py` as a thin wrapper that imports and calls core/pipeline

**Testing**: Run existing test suite against the migrated code — output must match current behavior exactly.

---

### Stream 3: Norway Plugin

**Owner**: Agent 3
**Dependency**: Stream 0 (protocol definition)
**Input**: Protocol interface + brreg.no API documentation
**Output**: `countries/norway.py`, `templates/outreach_no.md`

**Tasks**:
1. **Research & spike**: Confirm brreg.no API endpoints
   - `GET https://data.brreg.no/enhetsregisteret/api/enheter?konkurs=true` (or similar)
   - Document: response format, pagination, rate limits
   - Identify how to get trustee/lawyer data (may need court announcement scraping)

2. **`countries/norway.py`** — Implement CountryPlugin for Norway
   ```python
   class NorwayPlugin:
       code = "no"
       name = "Norway"
       currency = "NOK"
       language = "en"  # English primary for outreach

       def scrape_bankruptcies(self, year, month, cached_keys):
           # brreg.no API call
           # Parse JSON response into BankruptcyRecord objects
           # Map NACE codes to industry_code field
           ...

       def lookup_trustee_email(self, trustee_name, trustee_firm):
           # Scrape tilsynet.no/register OR advokatforeningen.no member search
           # Cache directory on first call (same pattern as Advokatsamfundet)
           ...

       def get_industry_code_maps(self):
           # NACE Rev. 2 — same 2-digit prefixes as SNI
           # Can reuse Sweden's maps with minor adjustments
           return (NACE_HIGH_VALUE, NACE_LOW_VALUE, NACE_ASSET_TYPES)

       def parse_financial_value(self, raw):
           # Parse "1 200 TNOK" → 1200000
           ...
   ```

3. **`templates/outreach_no.md`** — English outreach template for Norwegian trustees
   ```
   Subject: Regarding the bankruptcy of {{company_name}}
   Body: Dear {{trustee_name}}, I noticed you are handling the bankruptcy
   of {{company_name}}. I am interested in acquiring intangible assets...
   ```

4. **Key API endpoints to implement**:
   - Company data: `data.brreg.no/enhetsregisteret/api/enheter/{orgnr}`
   - Search by status: Filter for entities under bankruptcy proceedings
   - Announcements: Check `w2.brreg.no/kunngjoring/` for trustee appointments

**Testing**: Integration test against brreg.no API (real HTTP calls, small date range).

---

### Stream 4: Denmark Plugin

**Owner**: Agent 4
**Dependency**: Stream 0 (protocol definition)
**Input**: Protocol interface + Statstidende/CVR documentation
**Output**: `countries/denmark.py`, `templates/outreach_dk.md`

**Tasks**:
1. **Research & spike**: Determine best data access approach
   - Option A: Scrape Statstidende public search (likely most accessible)
   - Option B: CVR API (virk.dk) for company data + cross-reference insolvency status
   - Option C: Apply for Statstidende API certificate (longer-term)
   - **Recommendation**: Start with CVR API for company data + Statstidende scraping for
     bankruptcy announcements

2. **`countries/denmark.py`** — Implement CountryPlugin for Denmark
   - CVR API: `https://cvrapi.dk/api?search=...&country=dk` (free, rate-limited)
   - Statstidende: Scrape `statstidende.dk` public search for "konkursdekreter"
   - Industry codes: DB07 (same NACE 2-digit structure)
   - Lawyer lookup: Scrape advokatnoeglen.dk or advokatsamfundet.dk

3. **`templates/outreach_dk.md`** — English outreach template for Danish trustees

**Testing**: Integration test against CVR API + Statstidende.

---

### Stream 5: Finland Plugin

**Owner**: Agent 5
**Dependency**: Stream 0 (protocol definition)
**Input**: Protocol interface + PRH/insolvency register documentation
**Output**: `countries/finland.py`, `templates/outreach_fi.md`

**Tasks**:
1. **Research & spike**: PRH open data API
   - `https://avoindata.prh.fi/bis/v1` — Business Information System
   - Check for bankruptcy status in company data
   - Alternative: scrape konkurssit.com (aggregator based on PRH data)

2. **`countries/finland.py`** — Implement CountryPlugin for Finland
   - PRH API for company data + bankruptcy status
   - Industry codes: TOL 2008 (same NACE 2-digit structure)
   - Lawyer lookup: Scrape asianajajaliitto.fi member search
   - Currency: EUR

3. **`templates/outreach_fi.md`** — English outreach template for Finnish trustees

**Testing**: Integration test against PRH API.

---

### Stream 6: Database & Scheduler Updates

**Owner**: Agent 6
**Dependency**: Stream 0 (models)
**Input**: Current `scheduler.py`
**Output**: Updated `scheduler.py` → `core/database.py`, migration logic

**Tasks**:
1. **`core/database.py`** — Extract from scheduler.py, add country support
   - Add `country TEXT NOT NULL DEFAULT 'se'` column
   - Migration: `ALTER TABLE` for existing data (all existing rows get `country='se'`)
   - Update composite primary key: `(country, org_number, initiated_date, trustee_email)`
   - `get_cached_keys(country_code)` — filter by country
   - `deduplicate(records, country_code)` — country-scoped dedup
   - Add `country` column to `outreach_log` table too

2. Update `scheduler.py` entry point:
   - Parse `COUNTRIES` env var
   - Call `core.pipeline.run_all()` or `run_country()` based on config
   - Keep backward compatible: `COUNTRIES=se` is the default

3. Update `.github/workflows/monthly-report.yml`:
   - Add `COUNTRIES` input for workflow_dispatch
   - Default to all configured countries

**Testing**: Migration test on existing SQLite database — verify no data loss.

---

### Stream 7: Dashboard Multi-Country

**Owner**: Agent 7
**Dependency**: Stream 0 (models) + Stream 6 (database schema)
**Input**: Current `dashboard.py`
**Output**: Updated `dashboard.py`

**Tasks**:
1. Add country filter sidebar widget (multiselect: SE, NO, DK, FI)
2. All queries add `WHERE country IN (...)` clause
3. Analytics page: add "by country" breakdown charts
4. Outreach queue: show country badge on each record
5. AI search: include country in search context

**Testing**: Manual Streamlit testing with multi-country test data.

---

### Stream 8: Outreach Multi-Country

**Owner**: Agent 8
**Dependency**: Stream 0 (models)
**Input**: Current `outreach.py` + template
**Output**: Updated `outreach.py`

**Tasks**:
1. Load template based on record's country: `templates/outreach_{country_code}.md`
2. Add country column to outreach_log queries
3. Stage outreach: tag emails with country code
4. Dashboard approval: show country in queue
5. Ensure opt-out is global (email-based, not country-specific)

**Testing**: Dry-run outreach staging with mock records from multiple countries.

---

## Integration & Testing Strategy

### Phase 1: Contract (Stream 0)
- Protocol frozen, all streams can start

### Phase 2: Parallel Build (Streams 1-8)
- Each stream works against the protocol contract
- Each stream has its own test suite
- No cross-stream dependencies during build

### Phase 3: Integration
- Merge all streams
- Run Sweden end-to-end (regression test — must match current behavior)
- Run Norway end-to-end (new country test)
- Run multi-country (both SE + NO together)
- Dashboard smoke test with multi-country data

### Phase 4: Rollout
- Deploy Sweden + Norway first
- Add Denmark and Finland as plugins are validated
- Each country can be enabled/disabled via COUNTRIES env var

---

## Key Design Decisions

1. **NACE codes are universal**: SNI (SE), SN (NO), DB07 (DK), TOL (FI) are all
   national implementations of NACE Rev. 2. The 2-digit prefix scoring maps are
   ~95% identical. Each country inherits the default map and can override specifics.

2. **English outreach for new countries**: Norway/Denmark/Finland outreach is in
   English (user's preference). Swedish stays in Swedish.

3. **Brave Search fallback is shared**: The Brave Search email lookup works for any
   country. Country-specific lawyer directory lookups are tried first, Brave is the
   universal fallback.

4. **Backward compatibility**: Existing Swedish data and workflows continue to work
   unchanged. The `country='se'` default means zero migration friction.

5. **No over-engineering**: Each country plugin is a single file. No abstract base
   classes, no factory patterns, no dependency injection — just a Protocol and simple
   implementations.

---

## Open Research Questions (per country)

### Norway
- [ ] Confirm brreg.no API returns bankruptcy date (not just current status)
- [ ] Identify where trustee/lawyer assignments are published (court announcements?)
- [ ] Rate limits on brreg.no API

### Denmark
- [ ] Evaluate Statstidende public search scrapability
- [ ] CVR API: does it expose insolvency status? Rate limits?
- [ ] Advokatnøglen: is the directory scrape-friendly?

### Finland
- [ ] PRH API: does it expose bankruptcy filings with dates?
- [ ] Is konkurssit.com a viable alternative data source?
- [ ] Suomen Asianajajaliitto: member search format?
