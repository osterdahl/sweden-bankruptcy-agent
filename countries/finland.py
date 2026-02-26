"""
Finland country plugin for the Nordic Bankruptcy Monitor.

Data source:
    PRH (Patentti- ja rekisterihallitus) / YTJ Open Data API v3.
    Base URL: https://avoindata.prh.fi/opendata-ytj-api/v3
    - /companies  — search by name, businessId, registrationDate range, etc.
    - /companies/{businessId}  — single-company detail.
    Response includes a ``companySituations`` array whose entries indicate
    bankruptcy ("Konkurssi"), liquidation, or restructuring.

    No authentication required; free under CC BY 4.0.  Rate limit: 300
    queries / minute shared across all users — we stay well under that.

Industry codes:
    Finland uses TOL 2008, the Finnish implementation of NACE Rev. 2.
    At the 2-digit level the codes are identical to Swedish SNI, so the
    same scoring maps are reused.

Currency:
    EUR.  Financial figures are sometimes reported in "TEUR" (thousands
    of euros).
"""

import logging
import re
import time
from calendar import monthrange
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from core.models import BankruptcyRecord
from countries import register_country

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

PRH_API_BASE = "https://avoindata.prh.fi/opendata-ytj-api/v3"
COMPANIES_URL = f"{PRH_API_BASE}/companies"

USER_AGENT = "NordicBankruptcyMonitor/1.0 (research; contact@redpine.ai)"

# Conservative rate limiting — PRH caps at 300 req/min for all users
REQUEST_DELAY_S = 0.5

# TOL 2008 = NACE Rev. 2 at the 2-digit level (same as Swedish SNI)
HIGH_VALUE_INDUSTRY_CODES: Dict[str, int] = {
    "58": 10,   # Publishing — text/media rights
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
    "96": 1,    # Personal services
    "41": 2,    # Building construction
    "43": 2,    # Specialised construction
    "64": 2,    # Financial services (holding companies)
}

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

# Major Finnish cities for default region filtering
FINNISH_REGIONS = [
    "Helsinki",
    "Espoo",
    "Tampere",
    "Vantaa",
    "Oulu",
    "Turku",
    "Jyväskylä",
    "Kuopio",
    "Lahti",
    "Rovaniemi",
]


# ============================================================================
# HTTP helpers
# ============================================================================

def _get_session() -> requests.Session:
    """Create a requests session with standard headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9,fi;q=0.8",
    })
    return session


def _rate_limited_get(
    session: requests.Session, url: str, **kwargs
) -> requests.Response:
    """GET with rate limiting and error handling."""
    time.sleep(REQUEST_DELAY_S)
    kwargs.setdefault("timeout", 30)
    resp = session.get(url, **kwargs)
    resp.raise_for_status()
    return resp


# ============================================================================
# PRH API helpers
# ============================================================================

def _fetch_companies_page(
    session: requests.Session,
    params: dict,
) -> Optional[dict]:
    """Fetch a single page of /companies results.  Returns the JSON body or None."""
    try:
        resp = _rate_limited_get(session, COMPANIES_URL, params=params)
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning("[FI] PRH API HTTP error: %s", exc)
    except requests.exceptions.ConnectionError as exc:
        logger.warning("[FI] PRH API connection error: %s", exc)
    except requests.exceptions.Timeout:
        logger.warning("[FI] PRH API request timed out")
    except Exception as exc:
        logger.warning("[FI] PRH API unexpected error: %s", exc)
    return None


def _company_is_bankrupt(company: dict) -> bool:
    """Return True if the company's ``companySituations`` contains a
    bankruptcy entry (Finnish: "Konkurssi")."""
    situations = company.get("companySituations") or []
    for sit in situations:
        # The situation type may appear in Finnish ("Konkurssi"),
        # Swedish ("Konkurs"), or English ("Bankruptcy").
        sit_type = (sit.get("type") or sit.get("name") or "").lower()
        if any(kw in sit_type for kw in ("konkurssi", "konkurs", "bankruptcy")):
            return True
    return False


def _extract_situation_date(company: dict) -> str:
    """Extract the bankruptcy situation registration date (if present).

    Returns MM/DD/YYYY or empty string.
    """
    situations = company.get("companySituations") or []
    for sit in situations:
        sit_type = (sit.get("type") or sit.get("name") or "").lower()
        if any(kw in sit_type for kw in ("konkurssi", "konkurs", "bankruptcy")):
            date_str = sit.get("registrationDate") or sit.get("date") or ""
            parsed = _parse_date(date_str)
            if parsed:
                return parsed
    return ""


def _parse_date(date_str: str) -> Optional[str]:
    """Parse an ISO date (YYYY-MM-DD) to MM/DD/YYYY.  Returns None on failure."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")
        return dt.strftime("%m/%d/%Y")
    except (ValueError, TypeError):
        return None


def _extract_industry(company: dict) -> Tuple[str, str]:
    """Return (industry_code, industry_name) from the PRH response.

    The field is ``mainBusinessLine`` (TOL 2008 code + description).
    """
    bl = company.get("mainBusinessLine") or ""
    if isinstance(bl, dict):
        code = bl.get("code") or ""
        name = bl.get("name") or bl.get("description") or ""
        return str(code), str(name)
    # Sometimes it's a plain string like "62010 Computer programming"
    s = str(bl).strip()
    match = re.match(r"(\d+)\s*(.*)", s)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", s


def _extract_address_and_region(company: dict) -> Tuple[str, str]:
    """Return (address_string, region/city) from a PRH company dict."""
    # Try streetAddress first, then postalAddress
    for key in ("streetAddress", "postalAddress", "addresses"):
        addr = company.get(key)
        if not addr:
            continue
        # addr may be a dict or a list of dicts
        if isinstance(addr, list):
            addr = addr[0] if addr else {}
        if isinstance(addr, dict):
            street = addr.get("street") or ""
            post_code = addr.get("postCode") or addr.get("postalCode") or ""
            city = addr.get("city") or addr.get("postOffice") or ""
            parts = [p for p in (street, post_code, city) if p]
            return ", ".join(parts), city
    # Fallback: location field
    location = company.get("location") or company.get("registeredOffice") or ""
    return "", str(location)


# ============================================================================
# FinlandPlugin class
# ============================================================================

class FinlandPlugin:
    """Country plugin for Finland.

    Uses the PRH (Patent and Registration Office) YTJ Open Data API v3
    to discover companies in bankruptcy.

    Strategy:
        1. Query /companies with registrationDateStart/End for the target month.
        2. For each company in the response, check ``companySituations`` for
           bankruptcy entries.
        3. Build BankruptcyRecord objects from matching companies.

    Limitations:
        - The PRH API does not provide a direct "bankruptcies this month"
          filter. We fetch companies registered in the date window and check
          their situation.  This may miss companies whose bankruptcy was
          registered in a different month than their original registration.
        - Trustee information is NOT available from PRH. The core pipeline's
          Brave Search fallback handles trustee lookup.
        - The API is rate-limited to 300 requests/minute across all users.
    """

    code = "fi"
    name = "Finland"
    currency = "EUR"
    language = "en"  # English for outreach

    def scrape_bankruptcies(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Fetch Finnish bankruptcy records for the given year/month.

        Two-phase approach:
        1. Broad search: query companies whose registration date falls in the
           target month and filter for those with a bankruptcy situation.
        2. If that yields few results, also try fetching recently-registered
           companies from adjacent months that may have entered bankruptcy
           during the target month.
        """
        session = _get_session()
        _, last_day = monthrange(year, month)
        date_start = f"{year:04d}-{month:02d}-01"
        date_end = f"{year:04d}-{month:02d}-{last_day:02d}"

        logger.info(
            "[FI] Starting bankruptcy scrape for %s to %s (%d cached)",
            date_start, date_end, len(cached_keys),
        )

        records = self._search_bankruptcies(
            session, date_start, date_end, cached_keys
        )

        logger.info(
            "[FI] Scrape complete: %d records for %04d-%02d",
            len(records), year, month,
        )
        return records

    def _search_bankruptcies(
        self,
        session: requests.Session,
        date_start: str,
        date_end: str,
        cached_keys: set,
    ) -> List[BankruptcyRecord]:
        """Page through the /companies endpoint and collect bankrupt companies."""
        records: List[BankruptcyRecord] = []
        page = 0
        max_pages = 50  # Safety limit

        while page < max_pages:
            params = {
                "registrationDateStart": date_start,
                "registrationDateEnd": date_end,
            }

            data = _fetch_companies_page(session, params)
            if data is None:
                break

            # The response may have companies at the top level or nested
            companies = data.get("companies") or data.get("results") or []
            if isinstance(data, list):
                companies = data

            if not companies:
                break

            for company in companies:
                if not _company_is_bankrupt(company):
                    continue

                record = self._company_to_record(company)
                if record is None:
                    continue

                key = (record.org_number, record.initiated_date)
                if key in cached_keys:
                    continue

                cached_keys.add(key)
                records.append(record)

            # The PRH v3 API may not support classic pagination with a page
            # parameter. If the response doesn't signal more pages, stop.
            next_page = data.get("nextPage") or data.get("nextUrl")
            if not next_page:
                break
            page += 1

            # If nextPage is a full URL, use it directly
            if isinstance(next_page, str) and next_page.startswith("http"):
                try:
                    resp = _rate_limited_get(session, next_page)
                    data = resp.json()
                    companies = data.get("companies") or data.get("results") or []
                    if isinstance(data, list):
                        companies = data
                    if not companies:
                        break
                    for company in companies:
                        if not _company_is_bankrupt(company):
                            continue
                        record = self._company_to_record(company)
                        if record is None:
                            continue
                        key = (record.org_number, record.initiated_date)
                        if key in cached_keys:
                            continue
                        cached_keys.add(key)
                        records.append(record)
                except Exception as exc:
                    logger.debug("[FI] Error following nextPage: %s", exc)
                    break
            else:
                break

        return records

    def _company_to_record(self, company: dict) -> Optional[BankruptcyRecord]:
        """Map a PRH company dict to a BankruptcyRecord."""
        business_id = company.get("businessId") or ""
        name = company.get("name") or ""
        if not business_id and not name:
            return None

        initiated_date = _extract_situation_date(company)
        if not initiated_date:
            # Fall back to registration date
            reg_date = company.get("registrationDate") or ""
            initiated_date = _parse_date(reg_date) or ""

        industry_code, industry_name = _extract_industry(company)
        address, region = _extract_address_and_region(company)

        return BankruptcyRecord(
            country="fi",
            company_name=name,
            org_number=business_id,
            initiated_date=initiated_date,
            court="",              # Not available from PRH
            industry_code=industry_code,
            industry_name=industry_name,
            trustee="",            # Not available from PRH
            trustee_firm="",       # Not available from PRH
            trustee_address=address,
            employees=None,        # Not reliably available from PRH open data
            net_sales=None,
            total_assets=None,
            region=region,
        )

    # ------------------------------------------------------------------
    # Trustee email lookup
    # ------------------------------------------------------------------

    def lookup_trustee_email(
        self, trustee_name: str, trustee_firm: str
    ) -> Optional[str]:
        """Look up a Finnish trustee's email address.

        Currently returns None — the core pipeline's Brave Search fallback
        handles email discovery.

        TODO: Implement lookup via Finnish Bar Association directory
              (asianajajaliitto.fi/asianajajaluettelo) when a stable
              approach is identified.
        """
        return None

    # ------------------------------------------------------------------
    # Scoring configuration
    # ------------------------------------------------------------------

    def get_industry_code_maps(
        self,
    ) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        """Return industry code scoring maps for Finland.

        Finland uses TOL 2008, the Finnish implementation of NACE Rev. 2.
        At the 2-digit level, TOL 2008 codes are identical to Swedish SNI
        codes, so we reuse the same scoring maps.
        """
        return (
            HIGH_VALUE_INDUSTRY_CODES,
            LOW_VALUE_INDUSTRY_CODES,
            ASSET_TYPE_MAP,
        )

    # ------------------------------------------------------------------
    # Default regions
    # ------------------------------------------------------------------

    def get_default_regions(self) -> List[str]:
        """Return major Finnish cities for filtering."""
        return list(FINNISH_REGIONS)

    # ------------------------------------------------------------------
    # Currency parsing
    # ------------------------------------------------------------------

    def parse_financial_value(self, raw: str) -> Optional[int]:
        """Parse a Finnish financial value string into an integer (in EUR).

        Handles:
            "1 200 TEUR"  -> 1200000  (thousands of EUR)
            "500 TEUR"    -> 500000
            "52000"       -> 52000    (plain number, assumed EUR)
            "1 200,50"    -> 1200     (European decimal notation)
            "N/A", "-"    -> None

        TEUR = "tuhatta euroa" (thousands of euros).
        """
        if not raw:
            return None
        s = str(raw).strip()
        if s in ("N/A", "-", ""):
            return None

        try:
            # Check for TEUR (thousands of EUR)
            if "TEUR" in s.upper():
                numeric = s.upper().replace("TEUR", "").strip()
                # Remove space-based thousand separators
                numeric = numeric.replace(" ", "")
                # Handle comma as decimal separator
                numeric = numeric.replace(",", ".")
                return int(float(numeric) * 1000)

            # Check for EUR suffix
            if "EUR" in s.upper():
                numeric = s.upper().replace("EUR", "").strip()
                numeric = numeric.replace(" ", "")
                numeric = numeric.replace(",", ".")
                return int(float(numeric))

            # Plain number — handle European notation
            # Finnish uses space as thousands separator, comma as decimal
            s = s.replace(" ", "")
            if "," in s:
                # European decimal: "1200,50" -> "1200.50"
                s = s.replace(".", "").replace(",", ".")

            return int(float(s))

        except (ValueError, TypeError, AttributeError):
            return None


# ============================================================================
# Auto-register plugin
# ============================================================================

register_country(FinlandPlugin())
