"""
Bankruptcy scraper for Bolagsverket POIT portal.
Uses Playwright for browser automation to handle JavaScript-heavy pages.
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional, AsyncGenerator

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from .models import BankruptcyRecord, CompanyInfo, BankruptcyAdministrator, BankruptcyStatus

logger = logging.getLogger(__name__)


class BankruptcyScraper:
    """Scrapes bankruptcy announcements from Bolagsverket POIT portal."""
    
    BASE_URL = "https://poit.bolagsverket.se/poit-app"
    SEARCH_URL = "https://poit.bolagsverket.se/poit-app/sok"
    
    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        request_delay: float = 1.0
    ):
        self.headless = headless
        self.timeout = timeout
        self.request_delay = request_delay
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def start(self):
        """Initialize browser."""
        playwright = await async_playwright().start()
        self._browser = await playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="sv-SE",
            timezone_id="Europe/Stockholm"
        )
        self._page = await context.new_page()
        self._page.set_default_timeout(self.timeout)
    
    async def close(self):
        """Close browser."""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
    
    async def _delay(self):
        """Add delay between requests to be respectful."""
        await asyncio.sleep(self.request_delay)
    
    async def search_bankruptcies(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
        region: Optional[str] = None
    ) -> AsyncGenerator[BankruptcyRecord, None]:
        """
        Search for bankruptcy announcements.
        
        Args:
            year: Filter by year (defaults to current year)
            month: Filter by month (1-12)
            region: Filter by Swedish region (län)
        
        Yields:
            BankruptcyRecord objects
        """
        if not self._page:
            raise RuntimeError("Scraper not started. Use 'async with' or call start()")
        
        year = year or datetime.now().year
        month = month or datetime.now().month
        
        logger.info(f"Searching bankruptcies for {year}-{month:02d}")
        
        try:
            await self._page.goto(self.SEARCH_URL)
            await self._page.wait_for_load_state("networkidle")
            
            # Wait for page to fully load and check for CAPTCHA
            await asyncio.sleep(2)
            
            # Check if CAPTCHA is present
            captcha_present = await self._check_captcha()
            if captcha_present:
                logger.warning("CAPTCHA detected. Manual intervention may be required.")
                # In cloud environment, this would trigger an alert
                raise RuntimeError("CAPTCHA detected - manual intervention required")
            
            # Select search type: Konkurs (bankruptcy)
            await self._select_announcement_type("Konkurs")
            
            # Set date range for the month
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)
            
            await self._set_date_range(start_date, end_date)
            
            # Execute search
            await self._click_search()
            
            # Parse results
            async for record in self._parse_results():
                yield record
                
        except PlaywrightTimeout as e:
            logger.error(f"Timeout during search: {e}")
            raise
        except Exception as e:
            logger.error(f"Error during search: {e}")
            raise
    
    async def _check_captcha(self) -> bool:
        """Check if CAPTCHA is present on page."""
        captcha_indicators = [
            "captcha",
            "recaptcha",
            "hcaptcha",
            "challenge",
            "robot"
        ]
        page_content = await self._page.content()
        return any(indicator in page_content.lower() for indicator in captcha_indicators)
    
    async def _select_announcement_type(self, type_name: str):
        """Select announcement type from dropdown."""
        try:
            # Try to find and click the announcement type selector
            selector = await self._page.query_selector('select[name*="typ"], select[id*="typ"]')
            if selector:
                await selector.select_option(label=type_name)
            else:
                # Try clicking radio button or checkbox
                label = await self._page.query_selector(f'label:has-text("{type_name}")')
                if label:
                    await label.click()
        except Exception as e:
            logger.debug(f"Could not select announcement type: {e}")
    
    async def _set_date_range(self, start: datetime, end: datetime):
        """Set date range for search."""
        try:
            # Find date input fields
            start_input = await self._page.query_selector('input[name*="from"], input[id*="from"], input[name*="start"]')
            end_input = await self._page.query_selector('input[name*="to"], input[id*="to"], input[name*="end"]')
            
            if start_input:
                await start_input.fill(start.strftime("%Y-%m-%d"))
            if end_input:
                await end_input.fill(end.strftime("%Y-%m-%d"))
        except Exception as e:
            logger.debug(f"Could not set date range: {e}")
    
    async def _click_search(self):
        """Click search button and wait for results."""
        search_btn = await self._page.query_selector(
            'button[type="submit"], input[type="submit"], button:has-text("Sök"), button:has-text("Search")'
        )
        if search_btn:
            await search_btn.click()
            await self._page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
    
    async def _parse_results(self) -> AsyncGenerator[BankruptcyRecord, None]:
        """Parse search results from the page."""
        # Look for result table or list
        results = await self._page.query_selector_all(
            'table.results tr, .result-item, .announcement-item, [class*="result"]'
        )
        
        for i, result in enumerate(results):
            try:
                record = await self._parse_result_row(result)
                if record:
                    yield record
                    await self._delay()
            except Exception as e:
                logger.warning(f"Failed to parse result {i}: {e}")
                continue
        
        # Check for pagination
        while True:
            next_btn = await self._page.query_selector(
                'a:has-text("Nästa"), button:has-text("Nästa"), a.next, button.next'
            )
            if not next_btn or not await next_btn.is_visible():
                break
            
            await next_btn.click()
            await self._page.wait_for_load_state("networkidle")
            await self._delay()
            
            results = await self._page.query_selector_all(
                'table.results tr, .result-item, .announcement-item'
            )
            for result in results:
                try:
                    record = await self._parse_result_row(result)
                    if record:
                        yield record
                except Exception as e:
                    logger.warning(f"Failed to parse result: {e}")
    
    async def _parse_result_row(self, element) -> Optional[BankruptcyRecord]:
        """Parse a single result row into a BankruptcyRecord."""
        text = await element.inner_text()
        if not text or len(text.strip()) < 10:
            return None
        
        # Extract org number (format: NNNNNN-NNNN)
        org_match = re.search(r'\b(\d{6}-\d{4})\b', text)
        if not org_match:
            return None
        
        org_number = org_match.group(1)
        
        # Extract company name (usually before org number)
        name_match = re.search(r'^([^0-9]+?)(?=\d{6}-\d{4})', text)
        company_name = name_match.group(1).strip() if name_match else "Unknown"
        
        # Extract date (Swedish format: YYYY-MM-DD or DD/MM/YYYY)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})', text)
        declaration_date = None
        if date_match:
            try:
                date_str = date_match.group(1)
                if "-" in date_str:
                    declaration_date = datetime.strptime(date_str, "%Y-%m-%d")
                else:
                    declaration_date = datetime.strptime(date_str, "%d/%m/%Y")
            except ValueError:
                pass
        
        # Try to get detail URL
        link = await element.query_selector("a")
        detail_url = await link.get_attribute("href") if link else None
        if detail_url and not detail_url.startswith("http"):
            detail_url = f"{self.BASE_URL}/{detail_url.lstrip('/')}"
        
        company = CompanyInfo(
            org_number=org_number,
            name=company_name
        )
        
        record = BankruptcyRecord(
            company=company,
            status=BankruptcyStatus.INITIATED,
            declaration_date=declaration_date,
            source_url=detail_url,
            scraped_at=datetime.now()
        )
        
        # If we have a detail URL, fetch more info
        if detail_url:
            try:
                await self._enrich_from_detail_page(record, detail_url)
            except Exception as e:
                logger.debug(f"Could not enrich record: {e}")
        
        return record
    
    async def _enrich_from_detail_page(self, record: BankruptcyRecord, url: str):
        """Fetch additional details from detail page."""
        page = await self._browser.new_page()
        try:
            await page.goto(url)
            await page.wait_for_load_state("networkidle")
            
            content = await page.content()
            text = await page.inner_text("body")
            
            # Extract administrator info (Förvaltare)
            # Pattern: "Förvaltare är advokat Petter Vaeren"
            admin_match = re.search(
                r'Förvaltare\s+(?:är|:)?\s*(advokat|jur\.?\s*kand\.?)?\s*([A-ZÅÄÖ][a-zåäö]+(?:\s+[A-ZÅÄÖ][a-zåäö]+)*)',
                text,
                re.IGNORECASE
            )
            if admin_match:
                title = admin_match.group(1) or None
                name = admin_match.group(2)
                record.administrator = BankruptcyAdministrator(
                    name=name,
                    title=title
                )
            
            # Extract court (Tingsrätt)
            court_match = re.search(
                r'([\wåäöÅÄÖ]+\s+tingsrätt)',
                text,
                re.IGNORECASE
            )
            if court_match:
                record.court = court_match.group(1)
            
            # Extract case number
            case_match = re.search(r'(?:mål(?:nummer)?|ärende)\s*:?\s*([A-Z]?\d+[-/]\d+)', text, re.IGNORECASE)
            if case_match:
                record.case_number = case_match.group(1)
            
            # Extract address info
            address_match = re.search(
                r'(?:adress|säte)\s*:?\s*([^,\n]+),?\s*(\d{3}\s?\d{2})?\s*([A-ZÅÄÖ][a-zåäö]+)?',
                text,
                re.IGNORECASE
            )
            if address_match:
                record.company.address = address_match.group(1).strip()
                if address_match.group(2):
                    record.company.postal_code = address_match.group(2).replace(" ", "")
                if address_match.group(3):
                    record.company.city = address_match.group(3)
            
        finally:
            await page.close()


class MockBankruptcyScraper:
    """
    Mock scraper for testing without hitting real servers.
    Generates realistic test data.
    """
    
    def __init__(self, **kwargs):
        pass
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
    
    async def start(self):
        pass
    
    async def close(self):
        pass
    
    async def search_bankruptcies(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
        region: Optional[str] = None
    ) -> AsyncGenerator[BankruptcyRecord, None]:
        """Generate mock bankruptcy records."""
        import random
        
        year = year or datetime.now().year
        month = month or datetime.now().month
        
        mock_companies = [
            ("123456-7890", "Byggföretaget AB", "Bygg", 15, 5000000),
            ("234567-8901", "IT Solutions Sverige AB", "IT", 45, 12000000),
            ("345678-9012", "Restaurang Smak AB", "Restaurang", 8, 2000000),
            ("456789-0123", "Transport & Logistik AB", "Transport", 120, 50000000),
            ("567890-1234", "Konsultbolaget AB", "Konsult", 3, 800000),
            ("678901-2345", "Detaljhandel Sverige AB", "Handel", 25, 8000000),
            ("789012-3456", "Tillverknings AB", "Tillverkning", 85, 35000000),
            ("890123-4567", "Städservice Malmö AB", "Städ", 12, 3500000),
        ]
        
        courts = ["Stockholms tingsrätt", "Göteborgs tingsrätt", "Malmö tingsrätt", "Uppsala tingsrätt"]
        administrators = [
            ("Petter Vaeren", "advokat", "Lindahl Advokatbyrå"),
            ("Anna Svensson", "advokat", "Mannheimer Swartling"),
            ("Erik Andersson", "jur. kand.", "Vinge"),
        ]
        
        num_records = random.randint(5, 15)
        selected = random.sample(mock_companies, min(num_records, len(mock_companies)))
        
        for org, name, biz_type, empl, rev in selected:
            admin = random.choice(administrators)
            court = random.choice(courts)
            day = random.randint(1, 28)
            
            company = CompanyInfo(
                org_number=org,
                name=name,
                business_type=biz_type,
                employees=empl,
                revenue=float(rev),
                city=random.choice(["Stockholm", "Göteborg", "Malmö", "Uppsala"]),
                region=random.choice(["Stockholms län", "Västra Götalands län", "Skåne län"]),
            )
            
            record = BankruptcyRecord(
                company=company,
                status=BankruptcyStatus.INITIATED,
                declaration_date=datetime(year, month, day),
                court=court,
                administrator=BankruptcyAdministrator(
                    name=admin[0],
                    title=admin[1],
                    law_firm=admin[2]
                ),
                scraped_at=datetime.now(),
                source_url=f"https://poit.bolagsverket.se/poit-app/visa/{org}",
            )
            
            yield record
            await asyncio.sleep(0.1)
