"""
Norway country plugin for the Nordic Bankruptcy Monitor.

Data source: Brønnøysundregistrene (brreg.no) — the Brønnøysund Register Centre.
Uses the free, open Enhetsregisteret (Entity Register) API.
- Entities endpoint: https://data.brreg.no/enhetsregisteret/api/enheter
- Updates endpoint: https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter
- No authentication required, JSON (HAL) responses.

Trustee/lawyer info is NOT available from brreg.no. This plugin leaves
trustee fields empty; they can be populated later from court announcements
or manual enrichment.
"""

import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

from core.models import BankruptcyRecord

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

BRREG_BASE = "https://data.brreg.no/enhetsregisteret/api"
ENHETER_URL = f"{BRREG_BASE}/enheter"
OPPDATERINGER_URL = f"{BRREG_BASE}/oppdateringer/enheter"

PAGE_SIZE = 100
RATE_LIMIT_DELAY = 0.5  # seconds between API requests

# Major Norwegian regions (municipalities/cities) for default filtering
DEFAULT_REGIONS = [
    "Oslo",
    "Bergen",
    "Trondheim",
    "Stavanger",
    "Tromsø",
    "Kristiansand",
    "Drammen",
    "Fredrikstad",
    "Bodø",
    "Ålesund",
]

# ============================================================================
# Industry code maps (NACE Rev. 2 — identical at 2-digit level to Swedish SNI)
# ============================================================================

HIGH_VALUE_CODES: Dict[str, int] = {
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

LOW_VALUE_CODES: Dict[str, int] = {
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

ASSET_TYPE_MAP: Dict[str, str] = {
    "58": "media",           # Publishing
    "59": "media",           # Film/video/sound production
    "60": "media",           # Broadcasting
    "62": "code",            # Computer programming
    "63": "database",        # Information services
    "72": "database,sensor", # Scientific R&D
    "742": "media",          # Photography
    "90": "media",           # Creative arts
    "91": "media,database",  # Libraries/archives
    "26": "code",            # Computer/electronic manufacturing
    "71": "cad",             # Architectural/engineering
    "73": "media,database",  # Advertising/market research
    "85": "media",           # Education
}


# ============================================================================
# Helpers
# ============================================================================


def _safe_get(d: dict, *keys, default=""):
    """Safely traverse nested dicts, returning *default* if any key is missing."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _parse_brreg_date(date_str: str) -> Optional[str]:
    """Parse a brreg.no ISO date string (YYYY-MM-DD) into MM/DD/YYYY format.

    Returns None if unparseable.
    """
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")
        return dt.strftime("%m/%d/%Y")
    except (ValueError, TypeError):
        return None


def _date_in_target_month(date_str: str, year: int, month: int) -> bool:
    """Check whether a YYYY-MM-DD date falls in the target year/month."""
    if not date_str:
        return False
    try:
        dt = datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")
        return dt.year == year and dt.month == month
    except (ValueError, TypeError):
        return False


# ============================================================================
# NorwayPlugin
# ============================================================================


class NorwayPlugin:
    """Country plugin for Norway — uses brreg.no Enhetsregisteret API."""

    code = "no"
    name = "Norway"
    currency = "NOK"
    language = "en"  # English for outreach

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def scrape_bankruptcies(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Fetch Norwegian bankruptcy records for the given year/month.

        Strategy:
        1. Use the oppdateringer (updates) endpoint to find entities whose
           records changed during the target month.
        2. For each updated entity, fetch full details and check if
           ``konkurs`` is True.
        3. Fall back to the bulk ``konkurs=true`` search and filter by
           ``registreringsdatoEnhetsregisteret`` if the updates approach
           yields nothing (covers historical back-fills).

        Returns only records not already present in *cached_keys*.
        """
        records: List[BankruptcyRecord] = []

        # --- Approach 1: Updates endpoint for the target month ---
        try:
            records = self._fetch_via_updates(year, month, cached_keys)
        except Exception as exc:
            logger.warning(
                "Norway updates endpoint failed (%s), falling back to bulk search",
                exc,
            )

        # --- Approach 2: Bulk konkurs=true search (fallback / back-fill) ---
        if not records:
            try:
                records = self._fetch_via_bulk_search(year, month, cached_keys)
            except Exception as exc:
                logger.error("Norway bulk search also failed: %s", exc)

        logger.info(
            "Norway: found %d new bankruptcy records for %04d-%02d",
            len(records), year, month,
        )
        return records

    # ------------------------------------------------------------------
    # Updates-based fetch
    # ------------------------------------------------------------------

    def _fetch_via_updates(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Poll the oppdateringer endpoint for entity changes in the target month.

        The endpoint returns update events ordered by date.  We filter for
        events in the target month, then fetch each entity to see if it is
        now in bankruptcy.
        """
        # Build ISO-8601 date range for the target month
        start_date = f"{year:04d}-{month:02d}-01T00:00:00.000Z"
        if month == 12:
            end_date = f"{year + 1:04d}-01-01T00:00:00.000Z"
        else:
            end_date = f"{year:04d}-{month + 1:02d}-01T00:00:00.000Z"

        seen_orgnrs: set = set()
        records: List[BankruptcyRecord] = []
        page = 0

        while True:
            params = {
                "dato": start_date,
                "size": PAGE_SIZE,
                "page": page,
            }
            resp = self._api_get(OPPDATERINGER_URL, params=params)
            if resp is None:
                break

            data = resp.json()
            updates = data.get("_embedded", {}).get("oppdateringer", [])
            if not updates:
                break

            for update in updates:
                update_date = update.get("dato", "")
                # Stop if we've gone past the target month
                if update_date >= end_date:
                    return records

                orgnr = update.get("organisasjonsnummer", "")
                if not orgnr or orgnr in seen_orgnrs:
                    continue
                seen_orgnrs.add(orgnr)

                # Fetch full entity to check konkurs status
                entity = self._fetch_entity(orgnr)
                if entity and entity.get("konkurs") is True:
                    key = (orgnr, "")
                    if key not in cached_keys:
                        record = self._entity_to_record(entity, year, month)
                        if record:
                            cached_keys.add(key)
                            records.append(record)

            # Check pagination
            page_info = data.get("page", {})
            total_pages = page_info.get("totalPages", 1)
            if page + 1 >= total_pages:
                break
            page += 1

        return records

    # ------------------------------------------------------------------
    # Bulk search fallback
    # ------------------------------------------------------------------

    def _fetch_via_bulk_search(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Fetch all entities currently in bankruptcy and filter to those
        whose registration date falls in the target month.

        This is less precise than the updates approach (it can only filter
        by registreringsdatoEnhetsregisteret, not the actual konkurs date)
        but works well for back-filling historical data.
        """
        records: List[BankruptcyRecord] = []
        page = 0

        # Date range filters (ISO-8601 date, no time component)
        fra_dato = f"{year:04d}-{month:02d}-01"
        if month == 12:
            til_dato = f"{year + 1:04d}-01-01"
        else:
            til_dato = f"{year:04d}-{month + 1:02d}-01"

        while True:
            params = {
                "konkurs": "true",
                "size": PAGE_SIZE,
                "page": page,
                "fraRegistreringsdatoEnhetsregisteret": fra_dato,
                "tilRegistreringsdatoEnhetsregisteret": til_dato,
            }
            resp = self._api_get(ENHETER_URL, params=params)
            if resp is None:
                break

            data = resp.json()
            entities = data.get("_embedded", {}).get("enheter", [])
            if not entities:
                break

            for entity in entities:
                orgnr = entity.get("organisasjonsnummer", "")
                initiated = entity.get("registreringsdatoEnhetsregisteret", "")
                key = (orgnr, _parse_brreg_date(initiated) or "")

                if key in cached_keys:
                    continue

                record = self._entity_to_record(entity, year, month)
                if record:
                    cached_keys.add(key)
                    records.append(record)

            # Check pagination
            page_info = data.get("page", {})
            total_pages = page_info.get("totalPages", 1)
            if page + 1 >= total_pages:
                break
            page += 1

        return records

    # ------------------------------------------------------------------
    # Entity detail fetch
    # ------------------------------------------------------------------

    def _fetch_entity(self, orgnr: str) -> Optional[dict]:
        """Fetch a single entity by organisasjonsnummer."""
        url = f"{ENHETER_URL}/{orgnr}"
        resp = self._api_get(url)
        if resp is None:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Record mapping
    # ------------------------------------------------------------------

    def _entity_to_record(
        self, entity: dict, year: int, month: int
    ) -> Optional[BankruptcyRecord]:
        """Map a brreg.no entity dict to a BankruptcyRecord.

        Returns None if the entity cannot be meaningfully mapped.
        """
        orgnr = entity.get("organisasjonsnummer", "")
        if not orgnr:
            return None

        name = entity.get("navn", "")
        reg_date = entity.get("registreringsdatoEnhetsregisteret", "")
        initiated = _parse_brreg_date(reg_date) or ""

        # Industry code (NACE / SN)
        nk1 = entity.get("naeringskode1") or {}
        industry_code = nk1.get("kode", "")
        industry_name = nk1.get("beskrivelse", "")

        # Address / region — prefer forretningsadresse, fall back to postadresse
        addr = entity.get("forretningsadresse") or entity.get("postadresse") or {}
        region = addr.get("kommune", "")
        address_parts = addr.get("adresse", [])
        if isinstance(address_parts, list):
            address_str = ", ".join(p for p in address_parts if p)
        else:
            address_str = str(address_parts)
        postnr = addr.get("postnummer", "")
        poststed = addr.get("poststed", "")
        if postnr and poststed:
            address_str = f"{address_str}, {postnr} {poststed}".strip(", ")

        # Employees
        employees = entity.get("antallAnsatte")
        if employees is not None:
            try:
                employees = int(employees)
            except (ValueError, TypeError):
                employees = None

        # Trustee info is NOT available from brreg.no.
        # Leave empty — the pipeline's Brave Search fallback or future
        # court-announcement scraping will populate these.

        return BankruptcyRecord(
            country="no",
            company_name=name,
            org_number=orgnr,
            initiated_date=initiated,
            court="",              # Not available from brreg.no
            industry_code=industry_code,
            industry_name=industry_name,
            trustee="",            # Not available from brreg.no
            trustee_firm="",       # Not available from brreg.no
            trustee_address=address_str,
            employees=employees,
            net_sales=None,        # Not available from brreg.no
            total_assets=None,     # Not available from brreg.no
            region=region,
        )

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _api_get(
        self, url: str, params: Optional[dict] = None
    ) -> Optional[requests.Response]:
        """Issue a GET request to brreg.no with rate limiting and error handling."""
        time.sleep(RATE_LIMIT_DELAY)
        headers = {"Accept": "application/json"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                logger.debug("brreg.no 404 for %s", url)
                return None
            logger.warning(
                "brreg.no returned %d for %s: %s",
                resp.status_code, url, resp.text[:200],
            )
            return None
        except requests.RequestException as exc:
            logger.warning("brreg.no request failed for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Trustee email lookup
    # ------------------------------------------------------------------

    def lookup_trustee_email(
        self, trustee_name: str, trustee_firm: str
    ) -> Optional[str]:
        """Attempt to find a trustee's email from Norwegian lawyer directories.

        Tries tilsynet.no (Advokattilsynet) register.  If scraping is too
        fragile or the lawyer is not found, returns None so the core
        pipeline's Brave Search fallback handles it.

        TODO: Implement more robust scraping of tilsynet.no/register or
              advokatforeningen.no member search when a stable approach
              is identified.
        """
        if not trustee_name:
            return None

        # --- Best-effort scrape of tilsynet.no ---
        try:
            return self._search_tilsynet(trustee_name)
        except Exception as exc:
            logger.debug(
                "tilsynet.no lookup failed for '%s': %s", trustee_name, exc
            )

        return None

    def _search_tilsynet(self, name: str) -> Optional[str]:
        """Search tilsynet.no/register for a lawyer by name.

        The register at https://tilsynet.no/register provides a public
        search for licensed lawyers in Norway.  We attempt a GET request
        with the name as a query parameter and look for email addresses
        in the response.

        Returns the first email found, or None.
        """
        search_url = "https://tilsynet.no/register"
        try:
            resp = requests.get(
                search_url,
                params={"q": name},
                headers={
                    "Accept": "text/html",
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; NordicBankruptcyMonitor/1.0)"
                    ),
                },
                timeout=15,
            )
            if resp.status_code != 200:
                return None

            # Look for email addresses in the response body
            # This is intentionally broad; the page structure may change.
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try mailto links first
            for link in soup.select("a[href^='mailto:']"):
                href = link.get("href", "")
                email = href.replace("mailto:", "").strip()
                if "@" in email:
                    return email

            # Fallback: regex scan for email-like strings
            emails = re.findall(
                r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}",
                resp.text,
            )
            if emails:
                return emails[0]

        except requests.RequestException:
            pass

        return None

    # ------------------------------------------------------------------
    # Scoring configuration
    # ------------------------------------------------------------------

    def get_industry_code_maps(
        self,
    ) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        """Return (high_value_codes, low_value_codes, asset_type_map).

        Norway uses SN (Standard for naeringsgruppering) which is NACE
        Rev. 2 — identical to Swedish SNI at the 2-digit level.  We reuse
        the same scoring maps.
        """
        return HIGH_VALUE_CODES, LOW_VALUE_CODES, ASSET_TYPE_MAP

    # ------------------------------------------------------------------
    # Default regions
    # ------------------------------------------------------------------

    def get_default_regions(self) -> List[str]:
        """Return major Norwegian municipalities/cities for filtering."""
        return list(DEFAULT_REGIONS)

    # ------------------------------------------------------------------
    # Currency parsing
    # ------------------------------------------------------------------

    def parse_financial_value(self, raw: str) -> Optional[int]:
        """Parse Norwegian currency strings into integer NOK values.

        Handles:
        - "1 200 TNOK" → 1200000
        - "500 TNOK"   → 500000
        - "1200000"    → 1200000
        - "1,200"      → 1200
        - "N/A", "-"   → None
        """
        if not raw:
            return None
        s = str(raw).strip()
        if s in ("N/A", "-", ""):
            return None
        try:
            if "TNOK" in s.upper():
                # Remove "TNOK", spaces used as thousands separators, commas
                num_str = s.upper().replace("TNOK", "").replace(",", "").strip()
                # Norwegian uses space as thousands separator
                num_str = num_str.replace(" ", "")
                return int(float(num_str) * 1000)
            # Plain number — may have spaces or commas as thousands separators
            cleaned = s.replace(" ", "").replace(",", "")
            return int(float(cleaned))
        except (ValueError, TypeError, AttributeError):
            return None


# ============================================================================
# Auto-register
# ============================================================================

from countries import register_country  # noqa: E402

register_country(NorwayPlugin())
