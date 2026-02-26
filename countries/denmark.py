"""
Denmark country plugin for the Nordic Bankruptcy Monitor.

Data sources:
    1. Statstidende (Official Gazette) — primary source for bankruptcy decrees
       URL: https://www.telestatstidende.dk/telestatstidende/do/teleSearch/skifteretten
       Publishes official "konkursdekret" (bankruptcy decrees).

    2. CVR API (cvrapi.dk) — supplementary company data
       URL: https://cvrapi.dk/api?search={name}&country=dk
       Free REST API for company lookups by name or CVR number.
       Returns: vat, name, address, city, zipcode, industrycode, industrydesc, etc.

    3. Advokatnoeglen.dk / Advokatsamfundet — trustee email lookup (not yet implemented)

Industry codes:
    Denmark uses DB07 (Dansk Branchekode 2007), the Danish implementation of
    NACE Rev. 2. The 2-digit prefixes are identical to Swedish SNI codes, so
    the same scoring maps are reused.

Currency:
    DKK (Danish Krone). Financial data is often reported in "TDKK" (tusind DKK,
    i.e. thousands of DKK).
"""

import logging
import re
import time
from calendar import monthrange
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from core.models import BankruptcyRecord
from countries import register_country

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

USER_AGENT = "NordicBankruptcyMonitor/1.0 (research; contact@redpine.ai)"

# Conservative rate limiting (1 second between requests)
REQUEST_DELAY_S = 1.0

CVR_API_BASE = "https://cvrapi.dk/api"

STATSTIDENDE_SEARCH_URL = (
    "https://www.telestatstidende.dk/telestatstidende/do/teleSearch/skifteretten"
)

# DB07 (Dansk Branchekode 2007) = NACE Rev. 2 at 2-digit level.
# Same scoring maps as Swedish SNI codes.
HIGH_VALUE_INDUSTRY_CODES: Dict[str, int] = {
    "58": 10,   # Publishing — text/media rights (books, journals, software)
    "59": 10,   # Film, video, sound production — media rights
    "60": 9,    # Broadcasting — audio/video content
    "62": 10,   # Computer programming/consultancy — source code, algorithms
    "63": 9,    # Information services — databases, data products
    "72": 10,   # Scientific R&D — research data, sensor data, datasets
    "742": 9,   # Photography — image libraries
    "90": 8,    # Creative arts — content rights, creative IP
    "91": 7,    # Libraries/archives/museums — collections, rights
    "26": 8,    # Computer/electronic manufacturing — firmware, embedded SW, CAD
    "71": 7,    # Architectural/engineering — CAD drawings, technical specs
    "73": 6,    # Advertising/market research — creative assets, research data
    "85": 6,    # Education — courseware, educational content
}

LOW_VALUE_INDUSTRY_CODES: Dict[str, int] = {
    "56": 1,    # Food/beverage service
    "55": 1,    # Accommodation
    "45": 1,    # Motor vehicle retail/repair
    "47": 1,    # Retail
    "68": 1,    # Real estate
    "96": 1,    # Personal services (hair, laundry, etc.)
    "41": 2,    # Building construction
    "43": 2,    # Specialised construction
    "64": 2,    # Financial services (holding companies)
}

# Maps DB07 prefix -> likely Redpine asset types
ASSET_TYPE_MAP: Dict[str, str] = {
    "58": "media",              # Publishing
    "59": "media",              # Film/video/sound production
    "60": "media",              # Broadcasting
    "62": "code",               # Computer programming
    "63": "database",           # Information services
    "72": "database,sensor",    # Scientific R&D
    "742": "media",             # Photography
    "90": "media",              # Creative arts
    "91": "media,database",     # Libraries/archives
    "26": "code",               # Computer/electronic manufacturing
    "71": "cad",                # Architectural/engineering
    "73": "media,database",     # Advertising/market research
    "85": "media",              # Education
}

# Major Danish cities/regions for filtering
DANISH_REGIONS = [
    "Koebenhavn",
    "Aarhus",
    "Odense",
    "Aalborg",
    "Frederiksberg",
    "Esbjerg",
    "Randers",
    "Kolding",
    "Horsens",
    "Vejle",
    "Roskilde",
    "Herning",
]


# ============================================================================
# HTTP helpers
# ============================================================================

def _get_session() -> requests.Session:
    """Create a requests session with standard headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
    })
    return session


def _rate_limited_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET with rate limiting and error handling."""
    time.sleep(REQUEST_DELAY_S)
    kwargs.setdefault("timeout", 30)
    resp = session.get(url, **kwargs)
    resp.raise_for_status()
    return resp


# ============================================================================
# Statstidende scraper
# ============================================================================

def _scrape_statstidende(
    session: requests.Session, year: int, month: int, cached_keys: set
) -> List[BankruptcyRecord]:
    """Scrape Statstidende for bankruptcy decrees in the target month.

    Statstidende (the Danish Official Gazette) publishes all bankruptcy
    decrees ("Konkursdekreter"). The search interface allows filtering by
    date range and category.

    TODO: This scraper needs to be validated against the live Statstidende
    website. The HTML structure may differ from what is assumed here.
    The implementation below is a best-effort based on typical Danish
    government gazette patterns.

    If Statstidende scraping fails, this function returns an empty list
    and logs a warning. The caller should then attempt the CVR API fallback.
    """
    records = []

    # Build date range for the target month
    _, last_day = monthrange(year, month)
    date_from = f"{year:04d}-{month:02d}-01"
    date_to = f"{year:04d}-{month:02d}-{last_day:02d}"

    logger.info(
        f"[DK] Scraping Statstidende for bankruptcy decrees: {date_from} to {date_to}"
    )

    try:
        # Statstidende search parameters (best-effort — may need adjustment)
        # The search form typically takes date range and category filters
        params = {
            "teleAvisNr": "",           # Gazette number (blank = all)
            "teleKategori": "konkurs",  # Category: bankruptcy
            "teleFromDate": date_from,
            "teleToDate": date_to,
            "teleSearchText": "",       # Free text search
            "page": "1",
        }

        resp = _rate_limited_get(session, STATSTIDENDE_SEARCH_URL, params=params)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Parse search results
        # NOTE: The actual HTML structure of Statstidende search results
        # needs to be verified. The selectors below are educated guesses
        # based on typical Danish government web patterns.
        #
        # Expected structure (approximate):
        #   <div class="search-result">
        #     <h3>Company Name (CVR: 12345678)</h3>
        #     <p>Skifteretten i [Court]</p>
        #     <p>Kurator: [Trustee Name], [Trustee Firm]</p>
        #     <p>Dato: DD-MM-YYYY</p>
        #   </div>

        result_items = soup.select(".search-result, .announcement, .entry, tr.result")

        if not result_items:
            # Try alternative selectors
            result_items = soup.find_all("div", class_=re.compile(r"result|entry|item"))

        if not result_items:
            logger.warning(
                "[DK] Statstidende: no results found with known selectors. "
                "The page structure may have changed. "
                "Falling back to CVR API approach."
            )
            return []

        for item in result_items:
            try:
                record = _parse_statstidende_entry(item, year, month)
                if record is None:
                    continue

                key = (record.org_number, record.initiated_date)
                if key in cached_keys:
                    logger.debug(f"[DK] Skipping cached: {record.company_name}")
                    continue

                records.append(record)
            except Exception as e:
                logger.debug(f"[DK] Error parsing Statstidende entry: {e}")
                continue

        logger.info(f"[DK] Statstidende: parsed {len(records)} new bankruptcy records")

    except requests.exceptions.HTTPError as e:
        logger.warning(f"[DK] Statstidende HTTP error: {e}")
        return []
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"[DK] Statstidende connection error: {e}")
        return []
    except requests.exceptions.Timeout:
        logger.warning("[DK] Statstidende request timed out")
        return []
    except Exception as e:
        logger.warning(f"[DK] Statstidende scraping failed: {e}")
        return []

    return records


def _parse_statstidende_entry(
    item: BeautifulSoup, year: int, month: int
) -> Optional[BankruptcyRecord]:
    """Parse a single Statstidende search result entry.

    TODO: This parser needs to be validated against actual Statstidende HTML.
    The parsing logic below is a best-effort approximation.
    """
    text = item.get_text(separator="\n", strip=True)
    if not text:
        return None

    # Extract CVR number (8-digit Danish business ID)
    cvr_match = re.search(r"(?:CVR|cvr)[:\s-]*(\d{8})", text)
    cvr_number = cvr_match.group(1) if cvr_match else ""

    # Extract company name — typically the first line or heading
    name_el = item.find(["h3", "h4", "strong", "a"])
    company_name = ""
    if name_el:
        company_name = name_el.get_text(strip=True)
        # Remove CVR number from name if present
        company_name = re.sub(r"\s*\(CVR[:\s]*\d+\)\s*", "", company_name).strip()
    if not company_name:
        # Fall back to first line of text
        company_name = text.split("\n")[0].strip()

    if not company_name:
        return None

    # Extract court (Skifteret)
    court = ""
    court_match = re.search(
        r"(?:Skifteretten\s+i\s+|Skifteret[:\s]+)([A-Za-z\u00C0-\u00FF\s]+)",
        text
    )
    if court_match:
        court = court_match.group(1).strip()

    # Extract trustee (kurator)
    trustee_name = ""
    trustee_firm = ""
    kurator_match = re.search(
        r"[Kk]urator[:\s]+([^\n,]+?)(?:,\s*(.+?))?(?:\n|$)", text
    )
    if kurator_match:
        trustee_name = kurator_match.group(1).strip()
        trustee_firm = (kurator_match.group(2) or "").strip()

    # Extract date
    date_str = ""
    date_match = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if date_match:
        day, mon, yr = date_match.groups()
        # Normalize to MM/DD/YYYY
        date_str = f"{int(mon):02d}/{int(day):02d}/{yr}"
    else:
        # Default to first of the target month
        date_str = f"{month:02d}/01/{year}"

    # Derive region from court name
    region = court if court else ""

    return BankruptcyRecord(
        country="dk",
        company_name=company_name,
        org_number=cvr_number,
        initiated_date=date_str,
        court=court,
        industry_code="",       # Enriched later via CVR API
        industry_name="",       # Enriched later via CVR API
        trustee=trustee_name,
        trustee_firm=trustee_firm,
        trustee_address="",
        employees=None,         # Enriched later via CVR API
        net_sales=None,
        total_assets=None,
        region=region,
    )


# ============================================================================
# CVR API enrichment
# ============================================================================

def _enrich_via_cvr_api(
    session: requests.Session, records: List[BankruptcyRecord]
) -> List[BankruptcyRecord]:
    """Enrich bankruptcy records with company data from the CVR API.

    For each record that has a CVR number (org_number), look up the company
    in cvrapi.dk to fill in industry code, employee count, address, etc.

    Rate limited: 1 second between requests (conservative — cvrapi.dk has
    undocumented rate limits).

    CVR API response format (JSON object):
        {
            "vat": 12345678,
            "name": "Company Name ApS",
            "address": "Street 1",
            "zipcode": "1234",
            "city": "Koebenhavn",
            "industrycode": 620100,
            "industrydesc": "Computer programming activities",
            "companydesc": "...",
            "employees": "10-19",
            "startdate": "01/01/2015",
            ...
        }
    """
    if not records:
        return records

    logger.info(f"[DK] Enriching {len(records)} records via CVR API")
    enriched_count = 0

    for record in records:
        # Skip if no CVR number to look up
        if not record.org_number:
            continue

        try:
            resp = _rate_limited_get(
                session,
                CVR_API_BASE,
                params={"vat": record.org_number, "country": "dk"},
                headers={"Accept": "application/json"},
            )

            if resp.status_code != 200:
                logger.debug(
                    f"[DK] CVR API returned {resp.status_code} for {record.org_number}"
                )
                continue

            data = resp.json()

            # Industry code (DB07 — 6-digit, we keep it all but score on prefix)
            if data.get("industrycode"):
                record.industry_code = str(data["industrycode"])
            if data.get("industrydesc"):
                record.industry_name = data["industrydesc"]

            # Employee count — CVR returns ranges like "10-19"
            if data.get("employees"):
                record.employees = _parse_employee_range(data["employees"])

            # Address / region
            if data.get("city") and not record.region:
                record.region = data["city"]
            if data.get("address"):
                parts = [
                    data.get("address", ""),
                    data.get("zipcode", ""),
                    data.get("city", ""),
                ]
                record.trustee_address = ", ".join(p for p in parts if p)

            # Use company name from CVR if we don't have a good one
            if data.get("name") and not record.company_name:
                record.company_name = data["name"]

            enriched_count += 1

        except requests.exceptions.RequestException as e:
            logger.debug(f"[DK] CVR API error for {record.org_number}: {e}")
            continue
        except (ValueError, KeyError) as e:
            logger.debug(f"[DK] CVR API parse error for {record.org_number}: {e}")
            continue

    logger.info(f"[DK] CVR API enrichment: {enriched_count}/{len(records)} records enriched")
    return records


def _parse_employee_range(raw) -> Optional[int]:
    """Parse CVR employee range into a representative integer.

    CVR API returns employee counts as ranges: "1-4", "5-9", "10-19", etc.
    We use the midpoint of the range.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("N/A", "-", ""):
        return None

    # Try range format: "10-19"
    range_match = re.match(r"(\d+)\s*-\s*(\d+)", s)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return (low + high) // 2

    # Try "1000+" format
    plus_match = re.match(r"(\d+)\+", s)
    if plus_match:
        return int(plus_match.group(1))

    # Try plain number
    try:
        return int(float(s.replace(",", "").replace(".", "")))
    except (ValueError, TypeError):
        return None


# ============================================================================
# DenmarkPlugin class
# ============================================================================

class DenmarkPlugin:
    """Country plugin for Denmark.

    Implements the CountryPlugin protocol for scraping Danish bankruptcy
    data from Statstidende and enriching it via the CVR API.
    """

    code = "dk"
    name = "Denmark"
    currency = "DKK"
    language = "en"  # English for outreach

    def scrape_bankruptcies(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Fetch Danish bankruptcy records for the given year/month.

        Strategy:
            1. Scrape Statstidende for official bankruptcy decrees
            2. Enrich results with company data from CVR API
            3. If Statstidende fails, log a warning and return empty list

        IMPORTANT: Statstidende scraping is best-effort. The HTML structure
        has not been fully validated against the live site. If scraping yields
        no results, the function returns an empty list with a clear warning.

        TODO (future improvements):
            - Validate Statstidende HTML selectors against live site
            - Add pagination support for months with many decrees
            - Implement CVR API-based fallback for finding recent bankruptcies
              (search for companies with status "KONKURS" or "UNDER KONKURS")
            - Add support for scraping the Danish Business Authority's
              (Erhvervsstyrelsen) data exports
        """
        session = _get_session()

        logger.info(
            f"[DK] Starting bankruptcy scrape for {year}-{month:02d} "
            f"({len(cached_keys)} cached records)"
        )

        # Step 1: Scrape Statstidende
        records = _scrape_statstidende(session, year, month, cached_keys)

        if not records:
            logger.warning(
                "[DK] No records from Statstidende. This is expected if the "
                "scraper has not yet been validated against the live site. "
                "See TODO comments in countries/denmark.py for next steps."
            )
            # TODO: Implement CVR API fallback here.
            # The CVR API does not have a direct "bankruptcies this month" endpoint,
            # but one possible approach is to:
            #   1. Query Virk.dk's open data (data.virk.dk) for companies with
            #      status "KONKURS" and a recent status change date
            #   2. Use the CVR API to get company details for each match
            # This requires separate investigation of the Virk.dk data portal.
            return []

        # Step 2: Enrich via CVR API
        records = _enrich_via_cvr_api(session, records)

        logger.info(f"[DK] Scrape complete: {len(records)} records for {year}-{month:02d}")
        return records

    def lookup_trustee_email(
        self, trustee_name: str, trustee_firm: str
    ) -> Optional[str]:
        """Look up a Danish trustee's email address.

        Currently returns None — Brave Search fallback handles email lookup.

        TODO: Implement lookup via Danish lawyer directories:
            - Advokatnoeglen.dk (https://www.advokatnoeglen.dk)
              Searchable directory of Danish lawyers (advokater)
            - Advokatsamfundet (https://www.advokatsamfundet.dk/soeg-advokat)
              The Danish Bar and Law Society's official search
            Strategy:
              1. Search by trustee name and/or firm name
              2. Parse results for email addresses
              3. Cache results to avoid redundant lookups
        """
        return None

    def get_industry_code_maps(
        self,
    ) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        """Return industry code scoring maps for Denmark.

        Denmark uses DB07 (Dansk Branchekode 2007), the Danish implementation
        of NACE Rev. 2. At the 2-digit level, DB07 codes are identical to
        Swedish SNI codes, so we reuse the same scoring maps.
        """
        return (
            HIGH_VALUE_INDUSTRY_CODES,
            LOW_VALUE_INDUSTRY_CODES,
            ASSET_TYPE_MAP,
        )

    def get_default_regions(self) -> List[str]:
        """Return major Danish cities/regions for filtering.

        Denmark is divided into 5 administrative regions (regioner), but for
        bankruptcy filtering we use the major cities where courts (skifteretter)
        are located, as this is more useful for geographic filtering.
        """
        return list(DANISH_REGIONS)

    def parse_financial_value(self, raw: str) -> Optional[int]:
        """Parse a Danish financial value string into an integer (in DKK).

        Handles:
            "1 200 TDKK"  -> 1200000  (thousands of DKK)
            "1.200 TDKK"  -> 1200000  (European decimal notation for thousands)
            "500 TDKK"    -> 500000
            "52000"        -> 52000    (plain number, assumed DKK)
            "1.200.000"   -> 1200000  (European notation with dots as thousands sep)
            "N/A", "-"     -> None

        TDKK = "tusind danske kroner" (thousands of Danish kroner).
        """
        if not raw:
            return None
        s = str(raw).strip()
        if s in ("N/A", "-", ""):
            return None

        try:
            # Check for TDKK (thousands of DKK)
            if "TDKK" in s.upper():
                numeric = s.upper().replace("TDKK", "").strip()
                # Remove thousand separators (space or dot) but keep comma as decimal
                numeric = numeric.replace(" ", "").replace(".", "")
                # Handle comma as decimal separator
                numeric = numeric.replace(",", ".")
                return int(float(numeric) * 1000)

            # Check for DKK suffix without T prefix
            if "DKK" in s.upper():
                numeric = s.upper().replace("DKK", "").strip()
                numeric = numeric.replace(" ", "").replace(".", "")
                numeric = numeric.replace(",", ".")
                return int(float(numeric))

            # Plain number — handle European notation (dots as thousand separators)
            # Heuristic: if the string has dots and no commas, dots are thousand seps
            if "." in s and "," not in s:
                # Could be European thousands (1.200.000) or a decimal (1.5)
                # If there are multiple dots, they're thousand separators
                if s.count(".") > 1:
                    s = s.replace(".", "")
                elif len(s.split(".")[-1]) == 3:
                    # Single dot with exactly 3 digits after -> thousand separator
                    s = s.replace(".", "")
                # Otherwise keep as decimal

            # Handle comma as decimal separator
            s = s.replace(" ", "")
            if "," in s:
                # "1.200,50" -> European format
                s = s.replace(".", "").replace(",", ".")

            return int(float(s))

        except (ValueError, TypeError, AttributeError):
            return None


# ============================================================================
# Auto-register plugin
# ============================================================================

register_country(DenmarkPlugin())
