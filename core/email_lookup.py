"""
Trustee email lookup for the Nordic Bankruptcy Monitor.

Extracts and generalizes the email lookup logic from bankruptcy_monitor.py.
Country-agnostic helpers (regex extraction, Brave Search) live here.
Country-specific lookups (e.g. Advokatsamfundet for SE) stay in their
country plugin and are called first via the CountryPlugin.lookup_trustee_email()
interface; this module provides the Brave Search fallback.
"""

import logging
import os
import re
import time
import urllib.parse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from core.models import BankruptcyRecord

logger = logging.getLogger(__name__)


# ============================================================================
# EMAIL EXTRACTION HELPERS (country-agnostic)
# ============================================================================

def _extract_emails(text: str) -> List[str]:
    """Extract email addresses from text using regex."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, text)
    excluded = {'noreply', 'no-reply', 'example.com', 'google.com',
                'facebook.com', 'twitter.com', 'wixpress.com',
                'sentry.io', 'schema.org', 'w3.org', 'wordpress'}
    return [e for e in emails if not any(x in e.lower() for x in excluded)]


_GENERIC_EMAIL_PREFIXES = {'info', 'kontakt', 'contact', 'mail', 'reception', 'office'}


def _pick_best_email(emails: List[str]) -> Optional[str]:
    """Pick the best email -- prefer individual addresses over generic ones."""
    if not emails:
        return None
    for e in emails:
        if e.split('@')[0].lower() not in _GENERIC_EMAIL_PREFIXES:
            return e
    return emails[0]


# ============================================================================
# BRAVE SEARCH HELPERS (country-agnostic)
# ============================================================================

_TEAM_KEYWORDS = ('medarbetare', 'personal', 'team', 'advokater', 'people', 'kontakt')
_EXCLUDED_DOMAINS = {
    'linkedin.com', 'allabolag.se', 'hitta.se', 'proff.se', 'ratsit.se',
    'bolagsverket.se', 'facebook.com', 'twitter.com', 'wikipedia.org',
    'creditsafe.com', 'tic.io', 'eniro.se', 'merinfo.se',
}

_firm_team_url_cache: dict = {}
_scrape_session = requests.Session()
_scrape_session.headers['User-Agent'] = 'Mozilla/5.0 (compatible; BankruptcyMonitor/2.0)'


def _ascii_lower(s: str) -> str:
    """Lowercase and replace Nordic umlauts with ASCII equivalents."""
    return s.lower().replace('ä', 'a').replace('ö', 'o').replace('å', 'a').replace('ü', 'u')


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
    """Email lookup via firm website scrape then Brave snippet search.

    Note: This does NOT call country-specific lookups (like Advokatsamfundet).
    Those are handled by the country plugin in lookup_trustee_emails().
    """
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


def lookup_trustee_emails(
    records: List[BankruptcyRecord],
    country_plugin=None,
) -> List[BankruptcyRecord]:
    """Look up trustee email addresses.

    Strategy per (trustee, firm) pair:
    1. If country_plugin is provided, try plugin.lookup_trustee_email() first
       (e.g. Advokatsamfundet for Sweden, Advokattilsynet for Norway).
    2. Fall back to Brave Search (firm website scrape + snippet search).

    Deduplicates by trustee/firm pair -- each unique pair is looked up only once.
    Enabled by LOOKUP_TRUSTEE_EMAIL=true environment variable.
    Requires BRAVE_API_KEY for the fallback.
    """
    if os.getenv('LOOKUP_TRUSTEE_EMAIL', 'true').lower() != 'true':
        return records

    has_brave = bool(os.getenv('BRAVE_API_KEY'))
    has_plugin = country_plugin is not None

    if not has_brave and not has_plugin:
        logger.warning(
            "LOOKUP_TRUSTEE_EMAIL enabled but no BRAVE_API_KEY and no country plugin. "
            "Skipping email lookup."
        )
        return records

    if not has_brave:
        logger.info("No BRAVE_API_KEY set; using country plugin lookup only (no Brave fallback).")

    # Build set of unique (trustee_name, firm_name) pairs
    unique_pairs = {(r.trustee, r.trustee_firm) for r in records
                    if r.trustee != 'N/A' and r.trustee_firm and r.trustee_firm != 'N/A'}

    if not unique_pairs:
        return records

    logger.info(f"Looking up emails for {len(unique_pairs)} unique trustee/firm pairs...")
    found = 0
    pair_emails = {}

    for lawyer_name, firm_name in unique_pairs:
        email = None

        # Step 1: country-specific lookup (if plugin available)
        if has_plugin:
            try:
                email = country_plugin.lookup_trustee_email(lawyer_name, firm_name)
            except Exception as e:
                logger.debug(f"Country plugin lookup failed for {lawyer_name}: {e}")

        # Step 2: Brave Search fallback
        if not email and has_brave:
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
