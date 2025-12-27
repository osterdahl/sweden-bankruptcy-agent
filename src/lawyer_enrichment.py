"""
Lawyer contact information enrichment from Bolagsverket POIT system.

Scrapes lawyer (konkursförvaltare) contact details from official
Swedish Companies Registration Office (Bolagsverket) bankruptcy announcements.
"""
import asyncio
import logging
import re
from typing import Optional, Tuple
from datetime import datetime

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout

from .models import BankruptcyRecord, BankruptcyAdministrator

logger = logging.getLogger(__name__)


class BolagsverketLawyerEnricher:
    """
    Enriches bankruptcy records with lawyer contact information from Bolagsverket.

    Searches the POIT system for bankruptcy announcements (konkursbeslut) and
    extracts administrator contact details.
    """

    BASE_URL = "https://poit.bolagsverket.se"
    SEARCH_URL = f"{BASE_URL}/poit-app/sok"

    def __init__(
        self,
        headless: bool = True,
        timeout: float = 30.0,
        request_delay: float = 1.0
    ):
        self.headless = headless
        self.timeout = timeout * 1000  # Convert to milliseconds
        self.request_delay = request_delay
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self):
        """Initialize browser."""
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(headless=self.headless)
        context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await context.new_page()
        logger.debug("Bolagsverket browser initialized")

    async def close(self):
        """Close browser."""
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        logger.debug("Bolagsverket browser closed")

    async def enrich_record(self, record: BankruptcyRecord) -> BankruptcyRecord:
        """
        Enrich a bankruptcy record with lawyer contact information.

        Args:
            record: BankruptcyRecord to enrich

        Returns:
            Updated record with lawyer email and phone (if found)
        """
        org_number = record.company.org_number
        logger.debug(f"Enriching lawyer info for {org_number}")

        try:
            lawyer_info = await self._fetch_lawyer_contact(org_number)

            if lawyer_info:
                email, phone = lawyer_info

                # Update existing administrator or create new one
                if record.administrator:
                    if email and not record.administrator.email:
                        record.administrator.email = email
                        logger.info(f"Added email {email} for {record.administrator.name}")
                    if phone and not record.administrator.phone:
                        record.administrator.phone = phone
                        logger.info(f"Added phone {phone} for {record.administrator.name}")
                else:
                    # If we found contact info but no administrator, create placeholder
                    if email or phone:
                        logger.warning(f"Found contact info but no administrator name for {org_number}")
            else:
                logger.debug(f"No lawyer contact info found for {org_number}")

        except Exception as e:
            logger.warning(f"Failed to enrich lawyer info for {org_number}: {e}")

        return record

    async def _fetch_lawyer_contact(self, org_number: str) -> Optional[Tuple[Optional[str], Optional[str]]]:
        """
        Fetch lawyer email and phone from Bolagsverket POIT.

        Args:
            org_number: Swedish organization number (NNNNNN-NNNN)

        Returns:
            Tuple of (email, phone) or None if not found
        """
        if not self.page:
            await self.start()

        try:
            # Navigate to search page
            await self.page.goto(self.SEARCH_URL, timeout=self.timeout)
            await asyncio.sleep(self.request_delay)

            # Clean org number (remove dash)
            clean_org = org_number.replace("-", "").replace(" ", "")

            # Enter organization number in search field
            # The search field typically has name="orgnr" or similar
            search_input = await self.page.query_selector('input[name*="org"], input[id*="org"], input[placeholder*="org"]')
            if not search_input:
                # Try more generic search
                search_input = await self.page.query_selector('input[type="text"]')

            if search_input:
                await search_input.fill(clean_org)
                logger.debug(f"Entered org number: {clean_org}")
            else:
                logger.warning("Could not find search input field")
                return None

            # Submit search (look for search button)
            search_button = await self.page.query_selector('button[type="submit"], input[type="submit"], button:has-text("Sök")')
            if search_button:
                await search_button.click()
                logger.debug("Clicked search button")
            else:
                # Try pressing Enter
                await search_input.press("Enter")
                logger.debug("Pressed Enter to search")

            # Wait for results to load
            await asyncio.sleep(self.request_delay * 2)

            # Look for "konkursbeslut" announcements
            # This varies by site structure, but typically they list announcements by type
            page_content = await self.page.content()

            # Find konkursbeslut links or sections
            konkursbeslut_links = await self.page.query_selector_all(
                'a:has-text("Konkursbeslut"), a:has-text("konkurs"), '
                '[class*="konkursbeslut"], [id*="konkursbeslut"]'
            )

            if not konkursbeslut_links:
                # Try to find in the results text
                logger.debug(f"No konkursbeslut links found in search results for {org_number}")
                # Look for "Typ av kungörelse" text
                if "konkursbeslut" in page_content.lower():
                    logger.debug("Found konkursbeslut mention in page content")
                else:
                    logger.debug("No konkursbeslut found in page content")
                    return None

            # Click first konkursbeslut link
            if konkursbeslut_links:
                await konkursbeslut_links[0].click()
                await asyncio.sleep(self.request_delay)
                page_content = await self.page.content()

            # Extract email and phone from the page
            email = self._extract_email(page_content)
            phone = self._extract_phone(page_content)

            if email or phone:
                logger.info(f"Found lawyer contact for {org_number}: email={email}, phone={phone}")
                return (email, phone)
            else:
                logger.debug(f"No contact information found for {org_number}")
                return None

        except PlaywrightTimeout:
            logger.warning(f"Timeout while fetching lawyer info for {org_number}")
            return None
        except Exception as e:
            logger.error(f"Error fetching lawyer info for {org_number}: {e}")
            return None

    def _extract_email(self, html_content: str) -> Optional[str]:
        """Extract email address from HTML content."""
        # Common email pattern
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        matches = re.findall(email_pattern, html_content)

        if matches:
            # Filter out common non-lawyer emails
            for email in matches:
                email_lower = email.lower()
                if not any(skip in email_lower for skip in ['noreply', 'example', 'test']):
                    return email

        return None

    def _extract_phone(self, html_content: str) -> Optional[str]:
        """Extract Swedish phone number from HTML content."""
        # Swedish phone patterns:
        # +46 XX XXX XX XX
        # 0XX-XXX XX XX
        # 08-XXX XX XX
        phone_patterns = [
            r'\+46[\s-]?\d{1,3}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}',  # International format
            r'0\d{1,3}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}',  # Local format
            r'0\d{2,3}[\s-]?\d{6,8}',  # Compact format
        ]

        for pattern in phone_patterns:
            matches = re.findall(pattern, html_content)
            if matches:
                # Clean up the phone number
                phone = matches[0].strip()
                return phone

        return None

    async def enrich_batch(
        self,
        records: list[BankruptcyRecord],
        concurrency: int = 1  # Keep low to avoid rate limiting
    ) -> list[BankruptcyRecord]:
        """
        Enrich multiple records with lawyer contact information.

        Args:
            records: List of bankruptcy records to enrich
            concurrency: Number of concurrent requests (keep low for politeness)

        Returns:
            List of enriched records
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def enrich_with_limit(record):
            async with semaphore:
                return await self.enrich_record(record)
                await asyncio.sleep(self.request_delay)  # Be polite

        # Start browser once for all requests
        await self.start()

        try:
            tasks = [enrich_with_limit(r) for r in records]
            enriched_records = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out exceptions
            results = []
            for i, result in enumerate(enriched_records):
                if isinstance(result, Exception):
                    logger.error(f"Failed to enrich record {i}: {result}")
                    results.append(records[i])  # Return original record
                else:
                    results.append(result)

            return results
        finally:
            await self.close()
