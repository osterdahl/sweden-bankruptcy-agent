"""
Lawyer contact information enrichment from law firm websites.

Searches for lawyer contact details by:
1. Finding the law firm's website
2. Locating the lawyer's profile page
3. Extracting email and phone number from their contact information
"""
import asyncio
import logging
import re
from typing import Optional, Tuple
from datetime import datetime
from urllib.parse import quote_plus, urljoin

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout

from .models import BankruptcyRecord, BankruptcyAdministrator

logger = logging.getLogger(__name__)


class LawyerContactEnricher:
    """
    Enriches bankruptcy records with lawyer contact information from law firm websites.

    Strategy:
    1. Uses lawyer name and law firm from existing bankruptcy record
    2. Searches for the law firm's website using Google
    3. Finds the lawyer's profile on the firm's website
    4. Extracts email and phone number from their profile
    """

    GOOGLE_SEARCH_URL = "https://www.google.com/search?q="

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
        logger.debug("Lawyer enrichment browser initialized")

    async def close(self):
        """Close browser."""
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        logger.debug("Lawyer enrichment browser closed")

    async def enrich_record(self, record: BankruptcyRecord) -> BankruptcyRecord:
        """
        Enrich a bankruptcy record with lawyer contact information.

        Args:
            record: BankruptcyRecord to enrich

        Returns:
            Updated record with lawyer email and phone (if found)
        """
        if not record.administrator:
            logger.debug(f"No administrator for {record.company.name}, skipping enrichment")
            return record

        lawyer_name = record.administrator.name
        law_firm = record.administrator.law_firm

        if not lawyer_name or not law_firm:
            logger.debug(f"Missing lawyer name or firm for {record.company.name}, skipping")
            return record

        logger.debug(f"Enriching contact info for {lawyer_name} at {law_firm}")

        try:
            # Search for lawyer contact information
            email, phone = await self._find_lawyer_contact(lawyer_name, law_firm)

            if email or phone:
                # Update administrator contact info (only if not already set)
                if email and not record.administrator.email:
                    record.administrator.email = email
                    logger.info(f"✓ Found email for {lawyer_name}: {email}")
                if phone and not record.administrator.phone:
                    record.administrator.phone = phone
                    logger.info(f"✓ Found phone for {lawyer_name}: {phone}")
            else:
                logger.debug(f"No contact info found for {lawyer_name} at {law_firm}")

        except Exception as e:
            logger.warning(f"Failed to enrich {lawyer_name}: {e}")

        return record

    async def _find_lawyer_contact(
        self,
        lawyer_name: str,
        law_firm: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Find lawyer contact information by searching law firm website.

        Args:
            lawyer_name: Name of the lawyer (e.g., "Anna Svensson")
            law_firm: Name of the law firm (e.g., "Mannheimer Swartling")

        Returns:
            Tuple of (email, phone) or (None, None) if not found
        """
        if not self.page:
            await self.start()

        try:
            # Step 1: Find the law firm's website
            firm_url = await self._find_law_firm_website(law_firm)
            if not firm_url:
                logger.debug(f"Could not find website for {law_firm}")
                return (None, None)

            logger.debug(f"Found law firm website: {firm_url}")

            # Step 2: Search for the lawyer on the firm's website
            lawyer_page_url = await self._find_lawyer_page(firm_url, lawyer_name)
            if not lawyer_page_url:
                logger.debug(f"Could not find profile page for {lawyer_name}")
                return (None, None)

            logger.debug(f"Found lawyer profile: {lawyer_page_url}")

            # Step 3: Extract contact information from the lawyer's page
            email, phone = await self._extract_contact_from_page(lawyer_page_url, lawyer_name)

            return (email, phone)

        except Exception as e:
            logger.error(f"Error finding contact for {lawyer_name}: {e}")
            return (None, None)

    async def _find_law_firm_website(self, law_firm: str) -> Optional[str]:
        """
        Find the law firm's website URL using Google search.

        Args:
            law_firm: Name of the law firm

        Returns:
            URL of the law firm's website, or None if not found
        """
        try:
            # Search query: law firm name + "advokatbyrå" or "law firm"
            search_query = f'"{law_firm}" advokatbyrå Sverige'
            search_url = f"{self.GOOGLE_SEARCH_URL}{quote_plus(search_query)}"

            logger.debug(f"Searching for: {search_query}")
            await self.page.goto(search_url, timeout=self.timeout)
            await asyncio.sleep(self.request_delay)

            # Accept cookies if popup appears
            try:
                cookie_button = await self.page.query_selector('button:has-text("Accept"), button:has-text("Acceptera"), button:has-text("Godkänn")')
                if cookie_button:
                    await cookie_button.click()
                    await asyncio.sleep(0.5)
            except:
                pass

            # Extract first search result URL
            # Google search results typically have links in <a> tags with specific classes
            search_results = await self.page.query_selector_all('a[href]')

            for result in search_results[:15]:  # Check first 15 results
                href = await result.get_attribute('href')
                if href and href.startswith('http') and not any(skip in href for skip in ['google.', 'youtube.', 'facebook.', 'linkedin.', 'wikipedia.']):
                    # Check if this looks like a law firm website
                    firm_indicators = ['.se', '.com', 'advokat', law_firm.lower().replace(' ', '')]
                    if any(indicator in href.lower() for indicator in firm_indicators):
                        logger.debug(f"Found potential law firm URL: {href}")
                        return href

            logger.debug(f"No law firm website found for {law_firm}")
            return None

        except Exception as e:
            logger.error(f"Error finding law firm website: {e}")
            return None

    async def _find_lawyer_page(self, firm_url: str, lawyer_name: str) -> Optional[str]:
        """
        Find the lawyer's profile page on the law firm's website.

        Args:
            firm_url: URL of the law firm's website
            lawyer_name: Name of the lawyer to find

        Returns:
            URL of the lawyer's profile page, or None if not found
        """
        try:
            # Navigate to the law firm's website
            await self.page.goto(firm_url, timeout=self.timeout)
            await asyncio.sleep(self.request_delay)

            # Accept cookies if they appear
            try:
                cookie_button = await self.page.query_selector('button:has-text("Accept"), button:has-text("Acceptera"), button:has-text("Godkänn")')
                if cookie_button:
                    await cookie_button.click()
                    await asyncio.sleep(0.5)
            except:
                pass

            # Try to find a search function or people/team directory
            page_content = await self.page.content()

            # Common patterns for lawyer/people sections
            people_links = await self.page.query_selector_all(
                'a[href*="medarbetare"], a[href*="people"], a[href*="team"], '
                'a[href*="advokat"], a[href*="lawyer"], a:has-text("Medarbetare"), '
                'a:has-text("Team"), a:has-text("People"), a:has-text("Advokater")'
            )

            if people_links and len(people_links) > 0:
                # Click on people/team section
                try:
                    await people_links[0].click()
                    await asyncio.sleep(self.request_delay)
                    page_content = await self.page.content()
                except:
                    pass  # Page might be single-page, continue

            # Search for the lawyer's name on the page
            name_parts = lawyer_name.split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[-1] if len(name_parts) > 1 else ""

            # Look for links containing the lawyer's name
            all_links = await self.page.query_selector_all('a[href]')

            for link in all_links:
                try:
                    link_text = await link.text_content()
                    href = await link.get_attribute('href')

                    if link_text and href:
                        # Check if link text contains lawyer's name
                        if (first_name.lower() in link_text.lower() and last_name.lower() in link_text.lower()):
                            # Make absolute URL if needed
                            if href.startswith('/'):
                                href = urljoin(firm_url, href)
                            elif not href.startswith('http'):
                                continue

                            logger.debug(f"Found lawyer link: {href}")
                            return href
                except:
                    continue

            # If not found in links, search in page content
            if first_name.lower() in page_content.lower() and last_name.lower() in page_content.lower():
                # Lawyer is mentioned on this page, use current URL
                logger.debug(f"Lawyer mentioned on page: {self.page.url}")
                return self.page.url

            logger.debug(f"Lawyer {lawyer_name} not found on {firm_url}")
            return None

        except Exception as e:
            logger.error(f"Error finding lawyer page: {e}")
            return None

    async def _extract_contact_from_page(
        self,
        page_url: str,
        lawyer_name: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract email and phone from the lawyer's profile page.

        Args:
            page_url: URL of the lawyer's profile page
            lawyer_name: Name of the lawyer (for verification)

        Returns:
            Tuple of (email, phone)
        """
        try:
            await self.page.goto(page_url, timeout=self.timeout)
            await asyncio.sleep(self.request_delay)

            page_content = await self.page.content()

            # Extract email and phone
            email = self._extract_email(page_content)
            phone = self._extract_phone(page_content)

            return (email, phone)

        except Exception as e:
            logger.error(f"Error extracting contact from page: {e}")
            return (None, None)

    def _extract_email(self, html_content: str) -> Optional[str]:
        """Extract email address from HTML content."""
        # Common email pattern
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        matches = re.findall(email_pattern, html_content)

        if matches:
            # Filter out common non-lawyer emails
            for email in matches:
                email_lower = email.lower()
                if not any(skip in email_lower for skip in ['noreply', 'example', 'test', 'info@', 'kontakt@']):
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
                result = await self.enrich_record(record)
                await asyncio.sleep(self.request_delay)  # Be polite
                return result

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


# Backward compatibility alias
BolagsverketLawyerEnricher = LawyerContactEnricher
