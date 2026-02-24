#!/usr/bin/env python3
"""
Swedish Bankruptcy Monitor - TIC.io Version

Scrapes TIC.io open data for monthly bankruptcy announcements.
Single source, complete data, no CAPTCHA, radical simplicity.

Data source: https://tic.io/en/oppna-data/konkurser (free, public)
"""

import logging
import os
import re
import smtplib
import time
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from dataclasses import dataclass
from pathlib import Path
from string import Template

import urllib3
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load .env file if present (no-op if python-dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class BankruptcyRecord:
    """Bankruptcy record from TIC.io."""
    company_name: str
    org_number: str
    initiated_date: str
    court: str
    sni_code: str
    industry_name: str
    trustee: str
    trustee_firm: str
    trustee_address: str
    employees: str
    net_sales: str
    total_assets: str
    region: str = ""
    ai_score: Optional[int] = None      # 1-10 acquisition value score
    ai_reason: Optional[str] = None     # Brief explanation
    priority: Optional[str] = None      # "HIGH", "MEDIUM", "LOW"
    asset_types: Optional[str] = None   # e.g. "code,media" — what Redpine could acquire
    trustee_email: Optional[str] = None  # Looked up from firm website


# ============================================================================
# SCRAPER
# ============================================================================

def _parse_card(card) -> Optional[BankruptcyRecord]:
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

        employees = net_sales = total_assets = 'N/A'
        for item in card.select('.bankruptcy-card__financial-item'):
            label_el = item.select_one('.bankruptcy-card__financial-label')
            value_el = item.select_one('.bankruptcy-card__financial-value')
            if not label_el or not value_el:
                continue
            label = label_el.get_text(strip=True)
            value = value_el.get_text(strip=True)
            if 'Number of employees' in label:
                employees = value
            elif 'Net sales' in label:
                net_sales = value
            elif 'Total assets' in label:
                total_assets = value

        return BankruptcyRecord(
            company_name=company_name,
            org_number=org_number,
            initiated_date=initiated_date,
            court=court,
            sni_code=sni_code,
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


def scrape_tic_bankruptcies(year: int, month: int, max_pages: int = 10) -> List[BankruptcyRecord]:
    """Scrape TIC.io bankruptcies using HTTP + BeautifulSoup.

    Uses the SQLite DB as a persistent cache. Stops paginating as soon as
    all target-month records on a page are already cached — so repeated runs
    only download new records (delta).
    """
    from scheduler import get_cached_keys
    cached = get_cached_keys()
    logger.info(f'Cache: {len(cached)} records in DB')

    results = []
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
            record = _parse_card(card)
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

            # Passed the target month — no point fetching further pages
            if init_year < year or (init_year == year and init_month < month):
                found_past_target = True
                break

            # Future month — skip card, keep going
            if init_month != month or init_year != year:
                continue

            target_on_page += 1
            results.append(record)
            if (record.org_number, record.initiated_date) not in cached:
                new_on_page += 1

        if found_past_target:
            logger.info(f'Passed target month {year}-{month:02d} on page {page_num}, stopping '
                        f'({new_on_page} new records collected from this page before cutoff)')
            break

        # All target-month records on this page already in cache → caught up
        if target_on_page > 0 and new_on_page == 0:
            logger.info(f'Page {page_num}: {target_on_page} records all cached, stopping')
            break

        if page_num < max_pages:
            time.sleep(0.5)

    return results


# ============================================================================
# TRUSTEE EMAIL LOOKUP
# ============================================================================

def _extract_emails(text: str) -> List[str]:
    """Extract email addresses from text using regex."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)
    excluded = {'noreply', 'no-reply', 'example.com', 'google.com',
                'facebook.com', 'twitter.com', 'wixpress.com',
                'sentry.io', 'schema.org', 'w3.org', 'wordpress'}
    return [e for e in emails if not any(x in e.lower() for x in excluded)]


def _pick_best_email(emails: List[str]) -> Optional[str]:
    """Pick the best email — prefer individual addresses over generic ones."""
    if not emails:
        return None
    for e in emails:
        if e.split('@')[0].lower() not in _GENERIC_EMAIL_PREFIXES:
            return e
    return emails[0]


_GENERIC_EMAIL_PREFIXES = {'info', 'kontakt', 'contact', 'mail', 'reception', 'office'}

_SAMFUNDET_BASE = 'https://www.advokatsamfundet.se'
_samfundet_directory: list = []   # lazy-loaded: [(firm_name_lower, kontors_href), ...]
_samfundet_office_cache: dict = {}  # kontors_href → [(person_name_lower, person_url), ...]
_samfundet_session = requests.Session()
_samfundet_session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; BankruptcyMonitor/2.0)'
_samfundet_session.verify = False
_firm_team_url_cache: dict = {}
_scrape_session = requests.Session()
_scrape_session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; BankruptcyMonitor/2.0)'
_TEAM_KEYWORDS = ('medarbetare', 'personal', 'team', 'advokater', 'people', 'kontakt')
_EXCLUDED_DOMAINS = {
    'linkedin.com', 'allabolag.se', 'hitta.se', 'proff.se', 'ratsit.se',
    'bolagsverket.se', 'facebook.com', 'twitter.com', 'wikipedia.org',
    'creditsafe.com', 'tic.io', 'eniro.se', 'merinfo.se',
}


def _ascii_lower(s: str) -> str:
    """Lowercase and replace Swedish umlauts with ASCII equivalents."""
    return s.lower().replace('ä', 'a').replace('ö', 'o').replace('å', 'a').replace('ü', 'u')


def _normalize_firm(name: str) -> str:
    """Lowercase, remove umlauts, and expand legal-form abbreviations for comparison."""
    n = _ascii_lower(name)
    n = re.sub(r'\bkb\b', 'kommanditbolag', n)
    n = re.sub(r'\bab\b', 'aktiebolag', n)
    n = re.sub(r'\bhb\b', 'handelsbolag', n)
    return n.strip()


def _search_advokatsamfundet(lawyer_name: str, firm_name: str) -> Optional[str]:
    """Look up trustee email via the Swedish Bar Association directory.

    The directory page returns all ~2950 entries as static HTML regardless of
    query, so we load it once, cache it, then match firms by normalized name.
    Navigation: directory → Kontorsdetaljer (office page) → Persondetaljer → mailto.
    """
    parts = [p.strip() for p in lawyer_name.replace(',', ' ').split() if p.strip()]
    if len(parts) < 2:
        return None
    last, first = _ascii_lower(parts[0]), _ascii_lower(parts[1])

    try:
        # Step 1: lazy-load the full directory once
        if not _samfundet_directory:
            dir_url = f'{_SAMFUNDET_BASE}/Sok-advokat/Sokresultat/?Query=a'
            soup = BeautifulSoup(_samfundet_session.get(dir_url, timeout=20).text, 'html.parser')
            for a in soup.find_all('a', href=lambda h: h and 'Kontorsdetaljer' in h):
                _samfundet_directory.append(
                    (_normalize_firm(a.get_text(strip=True)), a['href'])
                )

        # Step 2: find all offices whose normalized name matches the target firm
        target = _normalize_firm(firm_name)
        office_hrefs = [href for name, href in _samfundet_directory if name == target]

        # Step 3: for each matching office, fetch its people list (cached by href)
        for href in office_hrefs:
            if href not in _samfundet_office_cache:
                office_url = urllib.parse.urljoin(_SAMFUNDET_BASE, href)
                fsoup = BeautifulSoup(_samfundet_session.get(office_url, timeout=15).text, 'html.parser')
                _samfundet_office_cache[href] = [
                    (_ascii_lower(a.get_text(strip=True)),
                     urllib.parse.urljoin(_SAMFUNDET_BASE, a['href']))
                    for a in fsoup.select('a[href*="Persondetaljer"]')
                ]

            # Step 4: find matching lawyer and extract email from their personal page
            for person_name, person_url in _samfundet_office_cache[href]:
                if last in person_name and first in person_name:
                    psoup = BeautifulSoup(_samfundet_session.get(person_url, timeout=15).text, 'html.parser')
                    for a in psoup.select('a[href^="mailto:"]'):
                        raw = a['href'][7:].split('?')[0].strip()
                        m = re.match(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', raw)
                        if m:
                            return m.group(0)

    except Exception as e:
        logger.debug(f"Advokatsamfundet lookup failed for {lawyer_name}: {e}")

    return None


def _scrape_firm_email(lawyer_name: str, firm_name: str) -> Optional[str]:
    """Scrape firm's team page for the trustee's email via mailto links."""
    api_key = os.getenv('BRAVE_API_KEY')
    if not api_key:
        return None

    parts = [p.strip().lower() for p in lawyer_name.replace(',', ' ').split() if p.strip()]
    if len(parts) < 2:
        return None

    if firm_name not in _firm_team_url_cache:
        team_url = None

        def _brave_first_url(query: str) -> Optional[str]:
            try:
                resp = requests.get(
                    'https://api.search.brave.com/res/v1/web/search',
                    params={'q': query, 'count': 5},
                    headers={'X-Subscription-Token': api_key, 'Accept': 'application/json'},
                    timeout=10,
                )
                resp.raise_for_status()
                results = resp.json().get('web', {}).get('results', [])
                return next(
                    (r.get('url') for r in results
                     if not any(d in r.get('url', '') for d in _EXCLUDED_DOMAINS)),
                    None
                )
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code
                if code in (429, 403, 401):
                    logger.warning(f"Brave API error {code} for '{firm_name}' — check API key/quota")
                else:
                    logger.debug(f"Brave lookup failed ({code}): {e}")
                return None
            except Exception as e:
                logger.debug(f"Brave lookup failed: {e}")
                return None

        # Step 1: query directly for the team/staff page (1 Brave call)
        team_url_candidate = _brave_first_url(f'"{firm_name}" medarbetare')
        if team_url_candidate and any(kw in team_url_candidate.lower() for kw in _TEAM_KEYWORDS):
            team_url = team_url_candidate
        else:
            # Step 2: get firm homepage and find team page link (1 Brave call + 1 HTTP fetch)
            # Small delay to respect Brave API rate limits between the two calls
            if team_url_candidate is None:
                time.sleep(1)
            firm_url = team_url_candidate or _brave_first_url(f'"{firm_name}"')
            if firm_url:
                try:
                    resp = _scrape_session.get(firm_url, timeout=15)
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    for kw in _TEAM_KEYWORDS:
                        found = next((a for a in soup.find_all('a', href=True)
                                      if kw in a['href'].lower()), None)
                        if not found:
                            found = next((a for a in soup.find_all('a', href=True)
                                          if kw in a.get_text(strip=True).lower()), None)
                        if found:
                            team_url = urllib.parse.urljoin(firm_url, found['href'])
                            break
                except Exception as e:
                    logger.debug(f"Team page discovery failed for {firm_url}: {e}")

        _firm_team_url_cache[firm_name] = team_url

    team_url = _firm_team_url_cache.get(firm_name)
    if not team_url:
        return None

    try:
        resp = _scrape_session.get(team_url, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
    except Exception as e:
        logger.debug(f"Failed to fetch team page {team_url}: {e}")
        return None

    last, first = _ascii_lower(parts[0]), _ascii_lower(parts[1])
    mailto_emails = []
    candidate = None  # low-confidence match (name near email but email doesn't encode name)

    for a in soup.find_all('a', href=True):
        href = a['href']
        if not href.startswith('mailto:'):
            continue
        raw = href[7:].split('?')[0].strip()
        m = re.match(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', raw)
        if not m:
            continue
        email = m.group(0)
        mailto_emails.append(email)

        # Walk up ancestors; stop if context grows beyond one person card (~600 chars)
        node = a
        for _ in range(4):
            node = node.parent
            if not node or node.name in ('body', 'html'):
                break
            context = _ascii_lower(node.get_text(' '))
            if len(context) > 600:
                break
            if all(p in context for p in [last, first]):
                email_local = _ascii_lower(email.split('@')[0])
                # High confidence: email local part encodes the person's last name
                if last in email_local:
                    return email
                # Low confidence: name in context but email doesn't encode name
                if candidate is None:
                    candidate = email
                break

    # Email-string fallback: last name (and partial first) must appear in email local part
    for email in mailto_emails:
        local = _ascii_lower(email.split('@')[0])
        if last in local and (first in local or first[:3] in local):
            return email

    return candidate  # generic/office email from a matching card, or None


def _search_brave_email(lawyer_name: str, firm_name: str) -> Optional[str]:
    """Email lookup: Bar Association (primary) → firm website → Brave snippets."""
    email = _search_advokatsamfundet(lawyer_name, firm_name)
    if email:
        return email
    email = _scrape_firm_email(lawyer_name, firm_name)
    if email:
        return email

    api_key = os.getenv('BRAVE_API_KEY')
    if not api_key:
        return None

    # Try exact-quoted names first (precise), then unquoted (handles "Last, First" comma format better)
    queries = [
        f'"{lawyer_name}" "{firm_name}" email',
        f'{lawyer_name} {firm_name} email',
        f'"{lawyer_name}" "{firm_name}" kontakt e-post',
        f'{lawyer_name} {firm_name} kontakt',
    ]

    seen_emails = []
    for q in queries:
        try:
            resp = requests.get(
                'https://api.search.brave.com/res/v1/web/search',
                params={'q': q, 'count': 5, 'extra_snippets': 'true'},
                headers={'X-Subscription-Token': api_key, 'Accept': 'application/json'},
                timeout=10,
            )
            resp.raise_for_status()
            for result in resp.json().get('web', {}).get('results', []):
                texts = [result.get('title', ''), result.get('url', ''), result.get('description', '')]
                texts.extend(result.get('extra_snippets') or [])
                for c in _extract_emails(' '.join(texts)):
                    if c not in seen_emails:
                        seen_emails.append(c)
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code
            if code in (429, 403, 401):
                logger.warning(f"Brave API error {code} in snippet fallback — check API key/quota")
                break
            logger.debug(f"Brave search error for query '{q}': {e}")
        except Exception as e:
            logger.debug(f"Brave search error for query '{q}': {e}")

        # Early exit if we already have a personal (non-generic) email
        best = _pick_best_email(seen_emails)
        if best and best.split('@')[0].lower() not in _GENERIC_EMAIL_PREFIXES:
            return best

        time.sleep(1)  # respect Brave API rate limits between queries

    return _pick_best_email(seen_emails)


def lookup_trustee_emails(records: List[BankruptcyRecord]) -> List[BankruptcyRecord]:
    """Look up trustee email addresses via Brave Search API.

    Deduplicates by trustee/firm pair — each unique pair is looked up only once.
    Enabled by LOOKUP_TRUSTEE_EMAIL=true environment variable.
    Requires BRAVE_API_KEY environment variable.
    """
    if os.getenv('LOOKUP_TRUSTEE_EMAIL', 'true').lower() != 'true':
        return records

    if not os.getenv('BRAVE_API_KEY'):
        logger.warning("LOOKUP_TRUSTEE_EMAIL enabled but BRAVE_API_KEY not set. Skipping email lookup.")
        return records

    # Build set of unique (trustee_name, firm_name) pairs
    unique_pairs = {(r.trustee, r.trustee_firm) for r in records
                    if r.trustee != 'N/A' and r.trustee_firm and r.trustee_firm != 'N/A'}

    if not unique_pairs:
        return records

    logger.info(f"Looking up emails for {len(unique_pairs)} unique trustee/firm pairs...")
    found = 0
    pair_emails = {}

    for lawyer_name, firm_name in unique_pairs:
        email = _search_brave_email(lawyer_name, firm_name)
        if email:
            pair_emails[(lawyer_name, firm_name)] = email
            found += 1
            logger.info(f"  Found: {lawyer_name} ({firm_name}) → {email}")
        else:
            logger.debug(f"  No email found for {lawyer_name} ({firm_name})")

    success_rate = (found / len(unique_pairs) * 100) if unique_pairs else 0
    logger.info(f"Found emails for {found}/{len(unique_pairs)} pairs ({success_rate:.0f}% success)")

    for r in records:
        key = (r.trustee, r.trustee_firm)
        if key in pair_emails:
            r.trustee_email = pair_emails[key]

    return records


# ============================================================================
# FILTERING
# ============================================================================

def filter_records(records: List[BankruptcyRecord]) -> List[BankruptcyRecord]:
    """Filter records based on environment variables."""
    filter_regions = [r.strip() for r in os.getenv("FILTER_REGIONS", "").split(",") if r.strip()]
    filter_keywords = [k.strip().lower() for k in os.getenv("FILTER_INCLUDE_KEYWORDS", "").split(",") if k.strip()]
    min_employees = int(os.getenv("FILTER_MIN_EMPLOYEES", "5") or "5")  # Default: 5 employees
    min_revenue = int(os.getenv("FILTER_MIN_REVENUE", "1000000") or "1000000")  # Default: 1M SEK

    filtered = []

    for record in records:
        # Region filter
        if filter_regions and record.region:
            if not any(region.lower() in record.region.lower() for region in filter_regions):
                continue

        # Keyword filter
        if filter_keywords:
            searchable = f"{record.company_name} {record.industry_name}".lower()
            if not any(kw in searchable for kw in filter_keywords):
                continue

        # Employee filter
        if min_employees > 0 and record.employees != 'N/A':
            try:
                emp_count = int(record.employees.replace(',', ''))
                if emp_count < min_employees:
                    continue
            except ValueError:
                pass

        # Revenue filter (Net Sales in TSEK = thousands of SEK)
        if min_revenue > 0 and record.net_sales != 'N/A':
            try:
                # Parse "7,739 TSEK" format - remove commas, extract number, convert TSEK to SEK
                revenue_str = record.net_sales.replace(',', '').replace('TSEK', '').strip()
                revenue_tsek = int(revenue_str)
                revenue_sek = revenue_tsek * 1000  # Convert thousands to actual SEK
                if revenue_sek < min_revenue:
                    continue
            except ValueError:
                pass

        filtered.append(record)

    return filtered


# ============================================================================
# SCORING — Redpine data asset acquisition
# ============================================================================

# SNI codes with strong signal for Redpine-relevant data assets
HIGH_VALUE_SNI_CODES = {
    '58': 10,  # Publishing — text/media rights (books, journals, software)
    '59': 10,  # Film, video, sound production — media rights
    '60': 9,   # Broadcasting — audio/video content
    '62': 10,  # Computer programming/consultancy — source code, algorithms
    '63': 9,   # Information services — databases, data products
    '72': 10,  # Scientific R&D — research data, sensor data, datasets
    '742': 9,  # Photography — image libraries
    '90': 8,   # Creative arts — content rights, creative IP
    '91': 7,   # Libraries/archives/museums — collections, rights
    '26': 8,   # Computer/electronic manufacturing — firmware, embedded SW, CAD
    '71': 7,   # Architectural/engineering — CAD drawings, technical specs
    '73': 6,   # Advertising/market research — creative assets, research data
    '85': 6,   # Education — courseware, educational content
}

LOW_VALUE_SNI_CODES = {
    '56': 1,   # Food/beverage service
    '55': 1,   # Accommodation
    '45': 1,   # Motor vehicle retail/repair
    '47': 1,   # Retail
    '68': 1,   # Real estate
    '96': 1,   # Personal services (hair, laundry, etc.)
    '41': 2,   # Building construction
    '43': 2,   # Specialised construction
    '64': 2,   # Financial services (holding companies)
}

# Maps SNI prefix → likely Redpine asset types (rule-based, always populated)
SNI_ASSET_TYPES = {
    '58': 'media',          # Publishing
    '59': 'media',          # Film/video/sound production
    '60': 'media',          # Broadcasting
    '62': 'code',           # Computer programming
    '63': 'database',       # Information services
    '72': 'database,sensor',# Scientific R&D
    '742': 'media',         # Photography
    '90': 'media',          # Creative arts
    '91': 'media,database', # Libraries/archives
    '26': 'code',           # Computer/electronic manufacturing
    '71': 'cad',            # Architectural/engineering
    '73': 'media,database', # Advertising/market research
    '85': 'media',          # Education
}


def calculate_base_score(record: BankruptcyRecord) -> int:
    """Rule-based scoring for Redpine data asset acquisition potential."""
    score = 3  # Low baseline — most bankruptcies are not relevant

    sni = record.sni_code
    if sni and sni != 'N/A' and len(sni) >= 2:
        sni_prefix = sni[:2]
        if sni_prefix in HIGH_VALUE_SNI_CODES:
            score = HIGH_VALUE_SNI_CODES[sni_prefix]
        elif sni_prefix in LOW_VALUE_SNI_CODES:
            score = LOW_VALUE_SNI_CODES[sni_prefix]
        if len(sni) >= 3 and sni[:3] in HIGH_VALUE_SNI_CODES:
            score = HIGH_VALUE_SNI_CODES[sni[:3]]

    # Size boost — more employees = more accumulated data assets
    try:
        if record.employees and record.employees != 'N/A':
            emp_count = int(record.employees.replace(',', ''))
            if emp_count >= 50:
                score = min(score + 2, 10)
            elif emp_count >= 20:
                score = min(score + 1, 10)
    except ValueError:
        pass

    # Company name signals — Redpine-specific keywords
    asset_keywords = [
        'data', 'tech', 'software', 'analytics', 'ai', 'cloud', 'digital',
        'media', 'photo', 'film', 'studio', 'content', 'publish', 'förlag',
        'sensor', 'robot', 'cad', 'design', 'research', 'lab',
    ]
    if any(kw in record.company_name.lower() for kw in asset_keywords):
        score = min(score + 1, 10)

    return score


def validate_with_ai(record: BankruptcyRecord) -> tuple[int, str]:
    """Score a record with an AI model, identifying Redpine-relevant asset types.

    Provider is selected via AI_PROVIDER env var: 'openai' or 'anthropic' (default).
    """
    prompt = f"""You assess bankrupt Swedish companies for Redpine, which acquires data assets for AI training and licensing.

Redpine buys:
- code: software, firmware, ML models, algorithms, APIs
- media: books, articles, images, photos, video, audio (with rights)
- cad: engineering drawings, 3D models, technical specifications
- sensor: sensor recordings, robotics data, scientific measurements
- database: annotated datasets, research databases, domain corpora

Company: {record.company_name}
Industry: [{record.sni_code}] {record.industry_name}
Employees: {record.employees}
Revenue: {record.net_sales}
Assets: {record.total_assets}
Region: {record.region}

Score 1-10 acquisition value (10=must contact, 1=no interest).
Pick asset types from: code, media, cad, sensor, database, none.

Reply ONLY: SCORE:N ASSETS:type1,type2 REASON:one sentence"""

    provider = os.getenv('AI_PROVIDER', 'anthropic').lower()

    try:
        if provider == 'openai':
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                return (record.ai_score, record.ai_reason or "Rule-based only (no OPENAI_API_KEY)")
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=os.getenv('AI_MODEL', 'gpt-4o-mini'),
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            response = resp.choices[0].message.content.strip()
        else:
            api_key = os.getenv('ANTHROPIC_API_KEY')
            if not api_key:
                return (record.ai_score, record.ai_reason or "Rule-based only (no ANTHROPIC_API_KEY)")
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=os.getenv('AI_MODEL', 'claude-haiku-4-5-20251001'),
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            response = resp.content[0].text.strip()

        score_match  = re.search(r'SCORE:(\d+)', response)
        assets_match = re.search(r'ASSETS:([\w,]+)', response)
        reason_match = re.search(r'REASON:(.+)', response)

        ai_score = max(1, min(10, int(score_match.group(1)))) if score_match else record.ai_score
        if assets_match and assets_match.group(1) != 'none':
            record.asset_types = assets_match.group(1)
        ai_reason = reason_match.group(1).strip() if reason_match else response

        return (ai_score, ai_reason)

    except Exception as e:
        logger.warning(f"AI scoring failed for {record.company_name}: {e}")
        return (record.ai_score, f"[AI failed: {type(e).__name__}] {record.ai_reason or 'Rule-based only'}")


def score_bankruptcies(records: List[BankruptcyRecord]) -> List[BankruptcyRecord]:
    """Score all records for Redpine data asset acquisition value.

    Rule-based scoring always runs. AI scoring runs on ALL records when
    AI_SCORING_ENABLED=true and ANTHROPIC_API_KEY is set.
    """
    # Rule-based always runs
    for record in records:
        base_score = calculate_base_score(record)
        record.ai_score = base_score
        if base_score >= 8:
            record.priority = "HIGH"
            record.ai_reason = "High-value data asset profile"
        elif base_score >= 5:
            record.priority = "MEDIUM"
            record.ai_reason = "Potential data assets"
        else:
            record.priority = "LOW"
            record.ai_reason = "Limited data asset potential"

        # Infer asset types from SNI code (AI may override this later)
        if not record.asset_types and record.sni_code and record.sni_code != 'N/A':
            sni = record.sni_code
            record.asset_types = (
                SNI_ASSET_TYPES.get(sni[:3])
                or SNI_ASSET_TYPES.get(sni[:2])
            )

    # AI scoring: all records, not just HIGH
    ai_enabled = os.getenv('AI_SCORING_ENABLED', 'false').lower() == 'true'
    if not ai_enabled:
        logger.info("AI scoring disabled (AI_SCORING_ENABLED != true) — rule-based scores only")
        return records

    provider = os.getenv('AI_PROVIDER', 'anthropic').lower()
    key_name = 'OPENAI_API_KEY' if provider == 'openai' else 'ANTHROPIC_API_KEY'
    if not os.getenv(key_name):
        logger.warning(
            f"AI_SCORING_ENABLED=true but {key_name} is not set — "
            "falling back to rule-based scores."
        )
        return records

    # Proactive rate limiting — avoids 429s and the retry penalty.
    # Default 0.5s works for OpenAI (500+ RPM). Set AI_RATE_DELAY=12 for
    # Anthropic free/Tier-1 (~5 RPM).
    rate_delay = float(os.getenv('AI_RATE_DELAY', '0.5'))
    provider = os.getenv('AI_PROVIDER', 'anthropic')
    model = os.getenv('AI_MODEL', 'gpt-4o-mini' if provider == 'openai' else 'claude-haiku-4-5-20251001')
    logger.info(
        f"AI scoring {len(records)} records via {provider}/{model} "
        f"(~{len(records) * rate_delay / 60:.1f} min — set AI_RATE_DELAY in .env to adjust)"
    )
    ai_ok = 0
    ai_failed = 0
    for i, record in enumerate(records):
        if i > 0:
            time.sleep(rate_delay)
        ai_score, ai_reason = validate_with_ai(record)
        record.ai_score = ai_score
        record.ai_reason = ai_reason
        if ai_score >= 8:
            record.priority = "HIGH"
        elif ai_score >= 5:
            record.priority = "MEDIUM"
        else:
            record.priority = "LOW"
        if ai_reason.startswith("[AI failed"):
            ai_failed += 1
        else:
            ai_ok += 1

    high = sum(1 for r in records if r.priority == "HIGH")
    med = sum(1 for r in records if r.priority == "MEDIUM")
    logger.info(
        f"Scoring complete: {high} HIGH, {med} MEDIUM, {len(records)-high-med} LOW — "
        f"AI scored {ai_ok}/{len(records)}"
        + (f", {ai_failed} failed (rule-based fallback)" if ai_failed else "")
    )
    if ai_failed == len(records):
        logger.warning(
            f"AI scoring failed for ALL {ai_failed} records — check ANTHROPIC_API_KEY is valid "
            "and the Anthropic API is reachable."
        )
    return records


# ============================================================================
# EMAIL
# ============================================================================

def format_email_html(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate modern card-based HTML email report with priority sections."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Split by priority
    high_risk = [r for r in records if r.priority == "HIGH"]
    med_risk = [r for r in records if r.priority == "MEDIUM"]
    low_risk = [r for r in records if r.priority == "LOW"]
    no_score = [r for r in records if not r.priority]

    # Helper function to render a card-based section
    def render_section(section_records, title, badge_color, global_start_index):
        if not section_records:
            return ""

        cards = ""
        for i, r in enumerate(section_records, global_start_index):
            org_clean = r.org_number.replace('-', '')
            poit_link = f"https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}"

            # AI reasoning section (prominent if available)
            ai_section = ""
            if r.priority and r.ai_reason:
                ai_section = f"""
                <div class="card-ai-reason">
                    <span class="ai-score">Score: {r.ai_score}/10</span>
                    <span class="ai-text">{r.ai_reason}</span>
                </div>
                """

            # Company info section
            company_info = f"""
            <div class="card-section">
                <div class="card-row">
                    <div class="card-col">
                        <span class="label">Org Number</span>
                        <span class="value"><code>{r.org_number}</code></span>
                    </div>
                    <div class="card-col">
                        <span class="label">Initiated</span>
                        <span class="value">{r.initiated_date}</span>
                    </div>
                    <div class="card-col">
                        <span class="label">Region</span>
                        <span class="value">{r.region}</span>
                    </div>
                </div>
                <div class="card-row">
                    <div class="card-col full-width">
                        <span class="label">Court</span>
                        <span class="value">{r.court}</span>
                    </div>
                </div>
                <div class="card-row">
                    <div class="card-col full-width">
                        <span class="label">Industry</span>
                        <span class="value"><code>{r.sni_code}</code> {r.industry_name}</span>
                    </div>
                </div>
            </div>
            """

            # Trustee info section (only if available) - single line with separators
            trustee_section = ""
            if r.trustee != 'N/A' or r.trustee_firm != 'N/A' or r.trustee_address != 'N/A':
                trustee_parts = []
                if r.trustee != 'N/A':
                    trustee_parts.append(f"<strong>{r.trustee}</strong>")
                if r.trustee_firm != 'N/A':
                    trustee_parts.append(r.trustee_firm)
                if r.trustee_email:
                    trustee_parts.append(f"<a href='mailto:{r.trustee_email}' style='color:#1d4ed8'>{r.trustee_email}</a>")
                if r.trustee_address != 'N/A':
                    trustee_parts.append(r.trustee_address)

                trustee_text = " <span class='trustee-separator'>•</span> ".join(trustee_parts)

                trustee_section = f"""
                <div class="card-section trustee-section">
                    <span class="trustee-label">👤 Trustee Contact:</span>
                    <span class="trustee-text">{trustee_text}</span>
                </div>
                """

            # Financials section (only if available)
            financials_section = ""
            if r.employees != 'N/A' or r.net_sales != 'N/A' or r.total_assets != 'N/A':
                financial_cols = []
                if r.employees != 'N/A':
                    financial_cols.append(f"""
                    <div class="card-col">
                        <span class="label">Employees</span>
                        <span class="value">{r.employees}</span>
                    </div>
                    """)
                if r.net_sales != 'N/A':
                    financial_cols.append(f"""
                    <div class="card-col">
                        <span class="label">Net Sales</span>
                        <span class="value">{r.net_sales}</span>
                    </div>
                    """)
                if r.total_assets != 'N/A':
                    financial_cols.append(f"""
                    <div class="card-col">
                        <span class="label">Total Assets</span>
                        <span class="value">{r.total_assets}</span>
                    </div>
                    """)

                financials_section = f"""
                <div class="card-section financials-section">
                    <h4>Financials</h4>
                    <div class="card-row">
                        {''.join(financial_cols)}
                    </div>
                </div>
                """

            # Priority badge
            priority_badge = f'<span class="priority-badge {badge_color}">{r.priority}</span>' if r.priority else ''

            cards += f"""
            <div class="bankruptcy-card">
                <div class="card-header">
                    <span class="card-number">#{i}</span>
                    {priority_badge}
                    <h3>{r.company_name}</h3>
                    <br>
                    <a href="{poit_link}" class="poit-link">View in POIT ↗</a>
                </div>
                {ai_section}
                {company_info}
                {trustee_section}
                {financials_section}
            </div>
            """

        section_html = f"""
        <div class="section-header {badge_color}">
            <h2>{title} ({len(section_records)})</h2>
        </div>
        <div class="cards-container">
            {cards}
        </div>
        """

        return section_html

    # Priority summary (if AI scoring enabled)
    priority_summary = ""
    if high_risk or med_risk or low_risk:
        priority_summary = f"""
        <div class="priority-summary">
            <div class="priority-stat high">
                <strong>{len(high_risk)}</strong>
                <span>HIGH Priority</span>
            </div>
            <div class="priority-stat medium">
                <strong>{len(med_risk)}</strong>
                <span>MEDIUM Priority</span>
            </div>
            <div class="priority-stat low">
                <strong>{len(low_risk)}</strong>
                <span>LOW Priority</span>
            </div>
        </div>
        """

    # Render sections in priority order
    sections_html = ""
    current_index = 1

    if high_risk:
        sections_html += render_section(high_risk, "⭐ HIGH PRIORITY", "high", current_index)
        current_index += len(high_risk)

    if med_risk:
        sections_html += render_section(med_risk, "⚠️ MEDIUM PRIORITY", "medium", current_index)
        current_index += len(med_risk)

    if low_risk:
        sections_html += render_section(low_risk, "ℹ️ LOW PRIORITY", "low", current_index)
        current_index += len(low_risk)

    # Fallback for no scoring
    if no_score:
        sections_html += render_section(no_score, "Bankruptcies", "default", current_index)

    # Load HTML template and fill placeholders
    template_path = Path(__file__).parent / 'email_template.html'
    template = Template(template_path.read_text(encoding='utf-8'))

    return template.substitute(
        EMOJI='\U0001f1f8\U0001f1ea',
        month_name=month_name,
        total_count=len(records),
        priority_summary=priority_summary,
        sections_html=sections_html,
        generated_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def format_email_plain(records: List[BankruptcyRecord], year: int, month: int) -> str:
    """Generate plain text email report with priority sections."""
    month_name = datetime(year, month, 1).strftime("%B %Y")

    # Split by priority
    high_risk = [r for r in records if r.priority == "HIGH"]
    med_risk = [r for r in records if r.priority == "MEDIUM"]
    low_risk = [r for r in records if r.priority == "LOW"]
    no_score = [r for r in records if not r.priority]

    # Header
    text = f"""
SWEDISH BANKRUPTCY REPORT - {month_name}
{'=' * 80}

SUMMARY
Total: {len(records)}"""

    if high_risk or med_risk or low_risk:
        text += f" | HIGH: {len(high_risk)} | MEDIUM: {len(med_risk)} | LOW: {len(low_risk)}"

    text += "\n\n"

    # Helper function to format a section
    def format_section(section_records, title, global_start_index):
        if not section_records:
            return ""

        section_text = f"""
{'=' * 80}
{title} ({len(section_records)})
{'=' * 80}

"""
        for i, r in enumerate(section_records, global_start_index):
            org_clean = r.org_number.replace('-', '')

            section_text += f"""
{i}. {r.company_name} ({r.org_number})"""

            if r.priority and r.ai_reason:
                section_text += f"""
   AI Score: {r.ai_score}/10 | {r.ai_reason}"""

            section_text += f"""
   Date: {r.initiated_date}
   Region: {r.region}
   Court: {r.court}
   Industry: [{r.sni_code}] {r.industry_name}
   Trustee: {r.trustee}
   Firm: {r.trustee_firm}
   Address: {r.trustee_address}
"""

            if r.trustee_email:
                section_text += f"   Email: {r.trustee_email}\n"

            if r.employees != 'N/A':
                section_text += f"   Employees: {r.employees}\n"
            if r.net_sales != 'N/A':
                section_text += f"   Net Sales: {r.net_sales}\n"
            if r.total_assets != 'N/A':
                section_text += f"   Total Assets: {r.total_assets}\n"

            section_text += f"   POIT: https://poit.bolagsverket.se/poit-app/sok?orgnr={org_clean}\n"

        return section_text

    # Render sections in priority order
    current_index = 1

    if high_risk:
        text += format_section(high_risk, "⭐ HIGH PRIORITY", current_index)
        current_index += len(high_risk)

    if med_risk:
        text += format_section(med_risk, "⚠️ MEDIUM PRIORITY", current_index)
        current_index += len(med_risk)

    if low_risk:
        text += format_section(low_risk, "ℹ️ LOW PRIORITY", current_index)
        current_index += len(low_risk)

    # Fallback for no scoring
    if no_score:
        text += format_section(no_score, "BANKRUPTCIES", current_index)

    # Footer
    text += f"""
{'=' * 80}
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Source: TIC.io Open Data (https://tic.io/en/oppna-data/konkurser)
"""

    return text


def send_email(subject: str, html_body: str, plain_body: str):
    """Send HTML email with plain text fallback via SMTP."""
    sender_email = os.getenv('SENDER_EMAIL')
    sender_password = os.getenv('SENDER_PASSWORD')
    recipient_emails = os.getenv('RECIPIENT_EMAILS', '')

    if not sender_email or not sender_password:
        logger.error("Email credentials not configured (SENDER_EMAIL, SENDER_PASSWORD)")
        return

    if not recipient_emails:
        logger.error("No recipient emails configured (RECIPIENT_EMAILS)")
        return

    recipients = [email.strip() for email in recipient_emails.split(',')]

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = ', '.join(recipients)

    part1 = MIMEText(plain_body, 'plain')
    part2 = MIMEText(html_body, 'html')

    msg.attach(part1)
    msg.attach(part2)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipients, msg.as_string())
            logger.info(f"Email sent successfully to {len(recipients)} recipients")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point."""
    logger.info("=== Swedish Bankruptcy Monitor (TIC.io) ===")

    # Ensure DB tables exist (outreach_log needs to be visible in dashboard
    # even when no new records are found this run)
    from outreach import _get_db as _init_outreach_db
    _init_outreach_db().close()

    # Determine target month
    year_env = os.getenv('YEAR')
    month_env = os.getenv('MONTH')

    year = int(year_env) if year_env else datetime.now().year
    month = int(month_env) if month_env else datetime.now().month

    # Auto-select previous month if in first 7 days (only when not explicitly set)
    if not month_env and not year_env and datetime.now().day <= 7:
        month = month - 1 if month > 1 else 12
        year = year - 1 if month == 12 else year

    logger.info(f"Processing: {year}-{month:02d}")

    # Scrape bankruptcies
    records = scrape_tic_bankruptcies(year, month)
    logger.info(f"Scraped {len(records)} bankruptcies for {year}-{month:02d}")

    if not records:
        logger.warning(f"No bankruptcies found for {year}-{month:02d}. This may indicate:")
        logger.warning("  1. TIC.io site structure changed (needs code update)")
        logger.warning("  2. No bankruptcies filed this month (unlikely)")
        logger.warning("  3. Network/timeout issue during scraping")
        return

    # Deduplicate — only process genuinely new records
    from scheduler import deduplicate, update_scores
    records = deduplicate(records)

    if not records:
        logger.info("No new bankruptcies after deduplication. Nothing to report.")
        return

    # Score ALL new records (rule-based always; AI when enabled)
    records = score_bankruptcies(records)
    update_scores(records)  # persist scores to DB

    # Email lookup and outreach only for HIGH/MEDIUM candidates
    candidates = [r for r in records if r.priority in ('HIGH', 'MEDIUM')]
    if candidates:
        lookup_trustee_emails(candidates)
        from outreach import stage_outreach
        stage_outreach(candidates)
        logger.info(f"Staged outreach for {len(candidates)} HIGH/MEDIUM candidates")

    # Filter for email report
    filtered = filter_records(records)
    logger.info(f"Filtered to {len(filtered)} matching bankruptcies")

    if not filtered:
        logger.warning(f"All {len(records)} bankruptcies were filtered out.")
        logger.warning("Check filter settings: FILTER_REGIONS, FILTER_INCLUDE_KEYWORDS, FILTER_MIN_EMPLOYEES, FILTER_MIN_REVENUE")
        return

    # Generate email
    month_name = datetime(year, month, 1).strftime("%B %Y")
    subject = f"Swedish Bankruptcy Report - {month_name} ({len(filtered)} bankruptcies)"
    html_body = format_email_html(filtered, year, month)
    plain_body = format_email_plain(filtered, year, month)

    # Send or print
    if os.getenv('NO_EMAIL', '').lower() == 'true':
        logger.info("Email sending skipped (NO_EMAIL=true)")
        print(plain_body)

        # Save HTML to /tmp for preview
        html_path = '/tmp/bankruptcy_email_sample.html'
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_body)
        logger.info(f"HTML preview saved to {html_path}")
    else:
        send_email(subject, html_body, plain_body)


if __name__ == '__main__':
    main()
