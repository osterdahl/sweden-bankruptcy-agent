"""
Country plugin protocol for the Nordic Bankruptcy Monitor.

Each country module (sweden.py, norway.py, etc.) must expose an object
that satisfies this protocol. Use duck typing — no need to inherit.
"""

from typing import Dict, List, Optional, Protocol, Tuple

from core.models import BankruptcyRecord


class CountryPlugin(Protocol):
    """Interface that every country module must implement."""

    # --- Identity ---
    code: str           # ISO 3166-1 alpha-2 lowercase: "se", "no", "dk", "fi"
    name: str           # Display name: "Sweden", "Norway", "Denmark", "Finland"
    currency: str       # "SEK", "NOK", "DKK", "EUR"
    language: str       # Primary outreach language code: "sv", "en"

    # --- Data Ingestion ---
    def scrape_bankruptcies(
        self, year: int, month: int, cached_keys: set
    ) -> List[BankruptcyRecord]:
        """Fetch bankruptcy records for the given year/month.

        cached_keys: set of (org_number, initiated_date) tuples already in DB.
        Return only records for the target month. Implementations should stop
        early when all records on a page are already cached.
        """
        ...

    # --- Trustee Contact Lookup ---
    def lookup_trustee_email(
        self, trustee_name: str, trustee_firm: str
    ) -> Optional[str]:
        """Look up a trustee's email from the country's bar association or
        lawyer directory. Return None if not found.

        The core pipeline will fall back to Brave Search if this returns None.
        """
        ...

    # --- Scoring Configuration ---
    def get_industry_code_maps(
        self,
    ) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, str]]:
        """Return (high_value_codes, low_value_codes, asset_type_map).

        Keys are NACE/SNI code prefixes (2-3 digits as strings).
        high_value_codes: prefix → score (6-10)
        low_value_codes:  prefix → score (1-2)
        asset_type_map:   prefix → comma-separated asset types

        All Nordic countries use NACE Rev. 2 variants, so the default maps
        (defined in core/scoring.py) work for ~95% of cases. Override here
        only for country-specific differences.
        """
        ...

    # --- Filtering ---
    def get_default_regions(self) -> List[str]:
        """Return valid region/county names for this country."""
        ...

    # --- Currency Parsing ---
    def parse_financial_value(self, raw: str) -> Optional[int]:
        """Parse a currency string from this country's data source into an integer.
        e.g. SE: '134 TSEK' → 134000, NO: '1 200 TNOK' → 1200000
        Return None for unparseable values.
        """
        ...
