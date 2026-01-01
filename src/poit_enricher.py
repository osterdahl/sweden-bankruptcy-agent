"""
Enriches bankruptcy records with administrator information from Bolagsverket POIT.

POIT (Post- och Inrikes Tidningar) is the official Swedish gazette where
all bankruptcy announcements (konkurskungörelser) are published with
complete administrator information.
"""

import asyncio
import logging
import re
from typing import Optional
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .models import BankruptcyRecord, BankruptcyAdministrator

logger = logging.getLogger(__name__)


class PoitEnricher:
    """Enriches records with administrator info from Bolagsverket POIT."""

    POIT_SEARCH_URL = "https://poit.bolagsverket.se/poit-app/sok"

    def __init__(self, page: Page, timeout: int = 30000):
        self.page = page
        self.timeout = timeout

    async def enrich_record(self, record: BankruptcyRecord) -> BankruptcyRecord:
        """
        Enrich record with administrator information from POIT.

        Args:
            record: Bankruptcy record to enrich

        Returns:
            Enriched record
        """
        if not record.company.org_number:
            logger.debug("No org number, skipping POIT enrichment")
            return record

        try:
            # Search for bankruptcy announcement
            await self._search_poit(record.company.org_number)

            # Extract administrator information
            admin_info = await self._extract_administrator_info()

            if admin_info:
                admin_name, law_firm, court = admin_info

                if not record.administrator and admin_name:
                    record.administrator = BankruptcyAdministrator(
                        name=admin_name,
                        law_firm=law_firm
                    )
                    logger.info(f"✓ Found administrator from POIT: {admin_name}" + (f" ({law_firm})" if law_firm else ""))

                if not record.court and court:
                    record.court = court
                    logger.info(f"✓ Found court from POIT: {court}")

        except Exception as e:
            logger.debug(f"POIT enrichment failed for {record.company.org_number}: {e}")

        return record

    async def _search_poit(self, org_number: str):
        """
        Search POIT for bankruptcy announcement.

        Args:
            org_number: Company organization number
        """
        try:
            await self.page.goto(self.POIT_SEARCH_URL, wait_until='domcontentloaded', timeout=self.timeout)
            await asyncio.sleep(1)

            # Fill in organization number
            org_input = await self.page.query_selector('input[name="orgnr"], input[id="orgnr"], input[placeholder*="organisationsnummer"]')
            if org_input:
                await org_input.fill(org_number.replace('-', ''))

            # Select "Konkursbeslut" type
            type_select = await self.page.query_selector('select[name="kungtyp"], select[id="kungtyp"]')
            if type_select:
                await type_select.select_option(label="Konkursbeslut")

            # Click search button
            search_button = await self.page.query_selector('button[type="submit"], input[type="submit"], button:has-text("Sök")')
            if search_button:
                await search_button.click()
                await self.page.wait_for_load_state('networkidle', timeout=self.timeout)

        except PlaywrightTimeout:
            logger.debug(f"Timeout searching POIT for {org_number}")
        except Exception as e:
            logger.debug(f"Error searching POIT: {e}")

    async def _extract_administrator_info(self) -> Optional[tuple[str, Optional[str], Optional[str]]]:
        """
        Extract administrator name, law firm, and court from POIT results.

        Returns:
            Tuple of (admin_name, law_firm, court) or None
        """
        try:
            await asyncio.sleep(2)

            # Get page content
            content = await self.page.inner_text('body')

            # Look for administrator patterns in Swedish bankruptcy announcements
            # Common format: "Förvaltare: Name, Law Firm"
            admin_patterns = [
                r'(?:Konkurs)?[Ff]örvaltare[:\s]+([A-ZÅÄÖ][a-zåäöA-ZÅÄÖ\s\-\.]+?)(?:,\s*([A-ZÅÄÖ][^,\n]+))?(?:\n|$)',
                r'Förvaltare[:\s]+advokat\s+([A-ZÅÄÖ][a-zåäö\s\-]+)(?:,\s*([^,\n]+))?',
            ]

            admin_name = None
            law_firm = None

            for pattern in admin_patterns:
                match = re.search(pattern, content)
                if match:
                    admin_name = match.group(1).strip()
                    if match.lastindex >= 2:
                        law_firm = match.group(2).strip() if match.group(2) else None
                    break

            # Look for court (tingsrätt)
            court_pattern = r'([A-ZÅÄÖ][a-zåäö]+\s+tingsrätt)'
            court_match = re.search(court_pattern, content)
            court = court_match.group(1) if court_match else None

            if admin_name or court:
                return (admin_name, law_firm, court)

            return None

        except Exception as e:
            logger.debug(f"Error extracting administrator info: {e}")
            return None

    async def enrich_batch(
        self,
        records: list[BankruptcyRecord],
        concurrency: int = 1
    ) -> list[BankruptcyRecord]:
        """
        Enrich multiple records with rate limiting.

        Args:
            records: Records to enrich
            concurrency: Number of concurrent enrichments (keep low for POIT)

        Returns:
            Enriched records
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def enrich_with_limit(record):
            async with semaphore:
                return await self.enrich_record(record)

        tasks = [enrich_with_limit(r) for r in records]
        return await asyncio.gather(*tasks)
