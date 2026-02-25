"""
Sweden country plugin for the Nordic Bankruptcy Monitor.

Extracts all Sweden-specific logic from the original bankruptcy_monitor.py:
- TIC.io scraping (scrape_bankruptcies)
- Advokatsamfundet trustee email lookup (lookup_trustee_email)
- SNI industry code maps (get_industry_code_maps)
- Swedish regions (get_default_regions)
- SEK currency parsing (parse_financial_value)
"""

import logging
import re
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

import requests
import urllib3
from bs4 import BeautifulSoup

from core.models import BankruptcyRecord

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ============================================================================
# INDUSTRY CODE MAPS (SNI — Swedish NACE variant)
# ============================================================================

# SNI codes with strong signal for Redpine-relevant data assets
HIGH_VALUE_SNI_CODES: Dict[str, int] = {
    '58': 10,   # Publishing -- text/media rights (books, journals, software)
    '59': 10,   # Film, video, sound production -- media rights
    '60': 9,    # Broadcasting -- audio/video content
    '62': 10,   # Computer programming/consultancy -- source code, algorithms
    '63': 9,    # Information services -- databases, data products
    '72': 10,   # Scientific R&D -- research data, sensor data, datasets
    '742': 9,   # Photography -- image libraries
    '90': 8,    # Creative arts -- content rights, creative IP
    '91': 7,    # Libraries/archives/museums -- collections, rights
    '26': 8,    # Computer/electronic manufacturing -- firmware, embedded SW, CAD
    '71': 7,    # Architectural/engineering -- CAD drawings, technical specs
    '73': 6,    # Advertising/market research -- creative assets, research data
    '85': 6,    # Education -- courseware, educational content
}

LOW_VALUE_SNI_CODES: Dict[str, int] = {
    '56': 1,    # Food/beverage service
    '55': 1,    # Accommodation
    '45': 1,    # Motor vehicle retail/repair
    '47': 1,    # Retail
    '68': 1,    # Real estate
    '96': 1,    # Personal services (hair, laundry, etc.)
    '41': 2,    # Building construction
    '43': 2,    # Specialised construction
    '64': 2,    # Financial services (holding companies)
}

# Maps SNI prefix -> likely Redpine asset types (rule-based, always populated)
SNI_ASSET_TYPES: Dict[str, str] = {
    '58': 'media',           # Publishing
    '59': 'media',           # Film/video/sound production
    '60': 'media',           # Broadcasting
    '62': 'code',            # Computer programming
    '63': 'database',        # Information services
    '72': 'database,sensor', # Scientific R&D
    '742': 'media',          # Photography
    '90': 'media',           # Creative arts
    '91': 'media,database',  # Libraries/archives
    '26': 'code',            # Computer/electronic manufacturing
    '71': 'cad',             # Architectural/engineering
    '73': 'media,database',  # Advertising/market research
    '85': 'media',           # Education
}

# Swedish default regions
_DEFAULT_REGIONS = [
    "Stockholm",
    "Goteborg",       # Goteborg
    "Malmo",          # Malmo
    "Uppsala",
    "Linkoping",      # Linkoping
    "Vasteras",       # Vasteras
    "Orebro",         # Orebro
    "Norrkoping",     # Norrkoping
    "Helsingborg",
    "Jonkoping",      # Jonkoping
    "Umea",           # Umea
    "Lund",
    "Sundsvall",
    "Gavle",          # Gavle
    "Karlstad",
    "Vaxjo",          # Vaxjo
    "Lulea",          # Lulea
    "Halmstad",
    "Kalmar",
    "Kristianstad",
    "Falun",
    "Skelleftea",     # Skelleftea
]

_SAMFUNDET_BASE = 'https://www.advokatsamfundet.se'


# ============================================================================
# HELPERS (private)
# ============================================================================

def _parse_headcount(val) -> Optional[int]:
    """Parse '134' -> 134, '2,024' -> 2024, any garbage -> None."""
    if not val:
        return None
    s = str(val).strip()
    if s in ('N/A', '-', ''):
        return None
    try:
        return int(float(s.replace(',', '').strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_sek(val) -> Optional[int]:
    """Parse '134 TSEK' -> 134000, '2,340 TSEK' -> 2340000, plain '52000' -> 52000, any garbage -> None."""
    if not val:
        return None
    s = str(val).strip()
    if s in ('N/A', '-', ''):
        return None
    try:
        if 'TSEK' in s.upper():
            return int(float(s.upper().replace('TSEK', '').replace(',', '').strip()) * 1000)
        # Plain number (already in SEK, or unknown unit) -- use as-is
        return int(float(s.replace(',', '').strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def _ascii_lower(s: str) -> str:
    """Lowercase and replace Swedish umlauts with ASCII equivalents."""
    return s.lower().replace('\u00e4', 'a').replace('\u00f6', 'o').replace('\u00e5', 'a').replace('\u00fc', 'u')


def _normalize_firm(name: str) -> str:
    """Lowercase, remove umlauts, and expand legal-form abbreviations for comparison."""
    n = _ascii_lower(name)
    n = re.sub(r'\bkb\b', 'kommanditbolag', n)
    n = re.sub(r'\bab\b', 'aktiebolag', n)
    n = re.sub(r'\bhb\b', 'handelsbolag', n)
    return n.strip()


# ============================================================================
# SWEDEN PLUGIN
# ============================================================================

class SwedenPlugin:
    """CountryPlugin implementation for Sweden.

    Scrapes TIC.io for bankruptcy data and uses the Swedish Bar Association
    (Advokatsamfundet) directory for trustee email lookups.
    """

    code = "se"
    name = "Sweden"
    currency = "SEK"
    language = "sv"

    def __init__(self):
        # Advokatsamfundet directory caches (instance-level)
        self._samfundet_directory: list = []       # [(firm_name_lower, kontors_href), ...]
        self._samfundet_office_cache: dict = {}    # kontors_href -> [(person_name_lower, person_url), ...]
        self._samfundet_session = requests.Session()
        self._samfundet_session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; BankruptcyMonitor/2.0)'
        self._samfundet_session.verify = False

    # ------------------------------------------------------------------
    # Data Ingestion
    # ------------------------------------------------------------------

    def scrape_bankruptcies(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Fetch Swedish bankruptcy records from TIC.io for the given year/month.

        cached_keys: set of (org_number, initiated_date) tuples already in DB.
        Returns only records for the target month. Stops paginating when all
        target-month records on a page are already cached.
        """
        max_pages = 10
        logger.info(f'Cache: {len(cached_keys)} records in DB')

        results: List[BankruptcyRecord] = []
        session = requests.Session()
        session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; BankruptcyMonitor/2.0)'

        for page_num in range(1, max_pages + 1):
            url = (
                f'https://tic.io/en/oppna-data/konkurser'
                f'?pageNumber={page_num}&pageSize=100&q=&sortBy=initiatedDate%3Adesc'
            )
            logger.info(f'Fetching TIC.io page {page_num}...')

            try:
                resp = session.get(url, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error(f'Failed to fetch page {page_num}: {e}')
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.select('.bankruptcy-card')
            logger.info(f'  Found {len(cards)} cards on page {page_num}')

            if not cards:
                break

            found_past_target = False
            target_on_page = 0
            new_on_page = 0

            for card in cards:
                record = self._parse_card(card)
                if record is None:
                    continue

                parts = record.initiated_date.split('/')
                if len(parts) != 3:
                    continue
                try:
                    init_month = int(parts[0])
                    init_year = int(parts[2])
                except ValueError:
                    logger.warning(f'Failed to parse date: {record.initiated_date}')
                    continue

                # Passed the target month -- no point fetching further pages
                if init_year < year or (init_year == year and init_month < month):
                    found_past_target = True
                    break

                # Future month -- skip card, keep going
                if init_month != month or init_year != year:
                    continue

                target_on_page += 1
                results.append(record)
                if (record.org_number, record.initiated_date) not in cached_keys:
                    new_on_page += 1

            if found_past_target:
                logger.info(
                    f'Passed target month {year}-{month:02d} on page {page_num}, stopping '
                    f'({new_on_page} new records collected from this page before cutoff)'
                )
                break

            # All target-month records on this page already in cache -> caught up
            if target_on_page > 0 and new_on_page == 0:
                logger.info(f'Page {page_num}: {target_on_page} records all cached, stopping')
                break

            if page_num < max_pages:
                time.sleep(0.5)

        return results

    def _parse_card(self, card) -> Optional[BankruptcyRecord]:
        """Extract a BankruptcyRecord from a BeautifulSoup card element."""
        try:
            date_el = card.select_one('.bankruptcy-card__dates .bankruptcy-card__value')
            initiated_date = date_el.get_text(strip=True) if date_el else None
            if not initiated_date:
                return None

            name_el = card.select_one('.bankruptcy-card__name a')
            company_name = name_el.get_text(strip=True) if name_el else 'N/A'

            org_el = card.select_one('.bankruptcy-card__org-number')
            org_number = org_el.get_text(strip=True) if org_el else 'N/A'

            region_el = card.select_one('.bankruptcy-card__detail .bankruptcy-card__value')
            region = region_el.get_text(strip=True) if region_el else 'N/A'

            court_el = card.select_one('.bankruptcy-card__court .bankruptcy-card__value')
            court = court_el.get_text().strip().split('\n')[0].strip() if court_el else 'N/A'

            sni_code = 'N/A'
            industry_name = 'N/A'
            sni_items = card.select('.bankruptcy-card__sni-item')
            if sni_items:
                code_el = sni_items[0].select_one('.bankruptcy-card__sni-code')
                iname_el = sni_items[0].select_one('.bankruptcy-card__sni-name')
                sni_code = code_el.get_text(strip=True) or 'N/A' if code_el else 'N/A'
                industry_name = iname_el.get_text(strip=True) or 'N/A' if iname_el else 'N/A'

            trustee_el = card.select_one('.bankruptcy-card__trustee-name')
            trustee = trustee_el.get_text(strip=True) if trustee_el else 'N/A'

            firm_el = card.select_one('.bankruptcy-card__trustee-company')
            trustee_firm = firm_el.get_text(strip=True) if firm_el else 'N/A'
            trustee_firm = re.sub(r'^c/o\s+', '', trustee_firm)

            addr_el = card.select_one('.bankruptcy-card__trustee-address')
            trustee_address = addr_el.get_text().strip().replace('\n', ', ') if addr_el else 'N/A'

            employees = net_sales = total_assets = None
            for item in card.select('.bankruptcy-card__financial-item'):
                label_el = item.select_one('.bankruptcy-card__financial-label')
                value_el = item.select_one('.bankruptcy-card__financial-value')
                if not label_el or not value_el:
                    continue
                label = label_el.get_text(strip=True)
                value = value_el.get_text(strip=True)
                if 'Number of employees' in label:
                    employees = _parse_headcount(value)
                elif 'Net sales' in label:
                    net_sales = _parse_sek(value)
                elif 'Total assets' in label:
                    total_assets = _parse_sek(value)

            return BankruptcyRecord(
                country="se",
                company_name=company_name,
                org_number=org_number,
                initiated_date=initiated_date,
                court=court,
                industry_code=sni_code,
                industry_name=industry_name,
                trustee=trustee,
                trustee_firm=trustee_firm,
                trustee_address=trustee_address,
                employees=employees,
                net_sales=net_sales,
                total_assets=total_assets,
                region=region,
            )
        except Exception as e:
            logger.warning(f'Error parsing card: {e}')
            return None

    # ------------------------------------------------------------------
    # Trustee Contact Lookup
    # ------------------------------------------------------------------

    def lookup_trustee_email(
        self, trustee_name: str, trustee_firm: str
    ) -> Optional[str]:
        """Look up trustee email via the Swedish Bar Association (Advokatsamfundet).

        Navigation: directory -> Kontorsdetaljer (office page) -> Persondetaljer -> mailto.
        Returns None if not found.
        """
        return self._search_advokatsamfundet(trustee_name, trustee_firm)

    def _search_advokatsamfundet(
        self, lawyer_name: str, firm_name: str
    ) -> Optional[str]:
        """Look up trustee email via the Swedish Bar Association directory.

        The directory page returns all ~2950 entries as static HTML regardless of
        query, so we load it once, cache it, then match firms by normalized name.
        Navigation: directory -> Kontorsdetaljer (office page) -> Persondetaljer -> mailto.
        """
        parts = [p.strip() for p in lawyer_name.replace(',', ' ').split() if p.strip()]
        if len(parts) < 2:
            return None
        last, first = _ascii_lower(parts[0]), _ascii_lower(parts[1])

        try:
            # Step 1: lazy-load the full directory once
            if not self._samfundet_directory:
                dir_url = f'{_SAMFUNDET_BASE}/Sok-advokat/Sokresultat/?Query=a'
                soup = BeautifulSoup(
                    self._samfundet_session.get(dir_url, timeout=20).text,
                    'html.parser',
                )
                for a in soup.find_all('a', href=lambda h: h and 'Kontorsdetaljer' in h):
                    self._samfundet_directory.append(
                        (_normalize_firm(a.get_text(strip=True)), a['href'])
                    )

            # Step 2: find all offices whose normalized name matches the target firm
            target = _normalize_firm(firm_name)
            office_hrefs = [href for name, href in self._samfundet_directory if name == target]

            # Step 3: for each matching office, fetch its people list (cached by href)
            for href in office_hrefs:
                if href not in self._samfundet_office_cache:
                    office_url = urllib.parse.urljoin(_SAMFUNDET_BASE, href)
                    fsoup = BeautifulSoup(
                        self._samfundet_session.get(office_url, timeout=15).text,
                        'html.parser',
                    )
                    self._samfundet_office_cache[href] = [
                        (
                            _ascii_lower(a.get_text(strip=True)),
                            urllib.parse.urljoin(_SAMFUNDET_BASE, a['href']),
                        )
                        for a in fsoup.select('a[href*="Persondetaljer"]')
                    ]

                # Step 4: find matching lawyer and extract email from their personal page
                for person_name, person_url in self._samfundet_office_cache[href]:
                    if last in person_name and first in person_name:
                        psoup = BeautifulSoup(
                            self._samfundet_session.get(person_url, timeout=15).text,
                            'html.parser',
                        )
                        for a in psoup.select('a[href^="mailto:"]'):
                            raw = a['href'][7:].split('?')[0].strip()
                            m = re.match(
                                r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', raw
                            )
                            if m:
                                return m.group(0)

        except Exception as e:
            logger.debug(f"Advokatsamfundet lookup failed for {lawyer_name}: {e}")

        return None

    # ------------------------------------------------------------------
    # Scoring Configuration
    # ------------------------------------------------------------------

    def get_industry_code_maps(
        self,
    ) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        """Return (high_value_codes, low_value_codes, asset_type_map) for Swedish SNI codes."""
        return (HIGH_VALUE_SNI_CODES, LOW_VALUE_SNI_CODES, SNI_ASSET_TYPES)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def get_default_regions(self) -> List[str]:
        """Return valid Swedish region/city names."""
        return list(_DEFAULT_REGIONS)

    # ------------------------------------------------------------------
    # Currency Parsing
    # ------------------------------------------------------------------

    def parse_financial_value(self, raw: str) -> Optional[int]:
        """Parse a Swedish currency string into an integer.

        Examples:
            '134 TSEK' -> 134000
            '2,340 TSEK' -> 2340000
            '52000' -> 52000
            'N/A' -> None
        """
        return _parse_sek(raw)


# ============================================================================
# AUTO-REGISTER
# ============================================================================

from countries import register_country  # noqa: E402

register_country(SwedenPlugin())
