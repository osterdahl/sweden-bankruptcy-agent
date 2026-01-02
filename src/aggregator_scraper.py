"""
Multi-source aggregator scraper for Swedish bankruptcy data.

Scrapes bankruptcy announcements from aggregator sites that compile
official court decisions. No API registration required.

Sources:
- Allabolag.se
- Konkurslistan.se  
- Bolagsfakta.se
- Ratsit.se (via search)
"""

import asyncio
import logging
import random
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import AsyncIterator, Iterator, Optional
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeout

from .models import BankruptcyRecord, BankruptcyStatus, CompanyInfo, BankruptcyAdministrator

logger = logging.getLogger(__name__)


# Common Swedish regions mapping
REGION_MAPPING = {
    "stockholm": "Stockholms län",
    "västra götaland": "Västra Götalands län",
    "skåne": "Skåne län",
    "östergötland": "Östergötlands län",
    "uppsala": "Uppsala län",
    "jönköping": "Jönköpings län",
    "halland": "Hallands län",
    "örebro": "Örebro län",
    "gävleborg": "Gävleborgs län",
    "dalarna": "Dalarnas län",
    "västmanland": "Västmanlands län",
    "värmland": "Värmlands län",
    "kronoberg": "Kronobergs län",
    "kalmar": "Kalmar län",
    "blekinge": "Blekinge län",
    "norrbotten": "Norrbottens län",
    "västerbotten": "Västerbottens län",
    "västernorrland": "Västernorrlands län",
    "jämtland": "Jämtlands län",
    "södermanland": "Södermanlands län",
    "gotland": "Gotlands län",
}


def normalize_org_number(org_nr: str) -> str:
    """Normalize Swedish organization number to NNNNNN-NNNN format."""
    if not org_nr:
        return ""
    # Remove all non-digits
    digits = re.sub(r'\D', '', org_nr)
    if len(digits) == 10:
        return f"{digits[:6]}-{digits[6:]}"
    return org_nr


def parse_swedish_date(date_str: str) -> Optional[datetime]:
    """Parse various Swedish date formats."""
    if not date_str:
        return None
    
    date_str = date_str.strip()
    
    formats = [
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y", 
        "%Y%m%d",
        "%d/%m/%Y",
        "%d.%m.%Y",
    ]
    
    # Swedish month names
    swedish_months = {
        "januari": "01", "februari": "02", "mars": "03", "april": "04",
        "maj": "05", "juni": "06", "juli": "07", "augusti": "08",
        "september": "09", "oktober": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09",
        "okt": "10", "nov": "11", "dec": "12",
    }
    
    # Try to replace Swedish month names
    date_lower = date_str.lower()
    for sv_month, num in swedish_months.items():
        if sv_month in date_lower:
            date_str = re.sub(sv_month, num, date_lower, flags=re.IGNORECASE)
            break
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    
    # Try extracting date pattern
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    
    return None


def parse_revenue(revenue_str: str) -> Optional[float]:
    """Parse Swedish revenue string to float (in SEK)."""
    if not revenue_str:
        return None
    
    # Clean string
    revenue_str = revenue_str.lower().strip()
    
    # Remove common words
    revenue_str = re.sub(r'(kr|sek|kronor|omsättning|revenue|:)', '', revenue_str, flags=re.IGNORECASE)
    
    # Handle "X MSEK" or "X KSEK" or "X miljoner"
    multiplier = 1
    if 'msek' in revenue_str or 'miljoner' in revenue_str or 'mkr' in revenue_str:
        multiplier = 1_000_000
        revenue_str = re.sub(r'(msek|miljoner|mkr)', '', revenue_str)
    elif 'ksek' in revenue_str or 'tusen' in revenue_str or 'tkr' in revenue_str:
        multiplier = 1_000
        revenue_str = re.sub(r'(ksek|tusen|tkr)', '', revenue_str)
    
    # Extract number
    revenue_str = revenue_str.replace(' ', '').replace('\xa0', '')
    match = re.search(r'([\d,.\s]+)', revenue_str)
    if match:
        num_str = match.group(1).replace(' ', '').replace(',', '.')
        # Handle Swedish format: 1.234.567 or 1 234 567
        parts = num_str.split('.')
        if len(parts) > 2:
            # Assume dots are thousand separators
            num_str = ''.join(parts)
        try:
            return float(num_str) * multiplier
        except ValueError:
            pass
    
    return None


def parse_employees(emp_str: str) -> Optional[int]:
    """Parse employee count string."""
    if not emp_str:
        return None
    
    # Clean string
    emp_str = re.sub(r'(anställda|employees|st|personer)', '', emp_str, flags=re.IGNORECASE)
    
    # Extract number
    match = re.search(r'(\d+)', emp_str.replace(' ', ''))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    
    return None


class BaseScraper(ABC):
    """Base class for aggregator scrapers."""
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    
    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        request_delay: float = 2.0,
    ):
        self.headless = headless
        self.timeout = timeout
        self.request_delay = request_delay
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
    
    async def __aenter__(self):
        playwright = await async_playwright().start()
        self.browser = await playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = await self.browser.new_context(
            user_agent=random.choice(self.USER_AGENTS),
            viewport={'width': 1920, 'height': 1080},
            locale='sv-SE',
        )
        self.page = await context.new_page()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            await self.browser.close()
    
    async def random_delay(self):
        """Add random delay between requests."""
        delay = self.request_delay + random.uniform(0.5, 1.5)
        await asyncio.sleep(delay)
    
    @abstractmethod
    async def scrape_bankruptcies(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> AsyncIterator[BankruptcyRecord]:
        """Scrape bankruptcies for given month/year."""
        pass
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Name of this data source."""
        pass


class AllabolagScraper(BaseScraper):
    """Scraper for Allabolag.se bankruptcy listings."""
    
    BASE_URL = "https://www.allabolag.se"
    BANKRUPTCIES_URL = "https://www.allabolag.se/konkurser"
    
    @property
    def source_name(self) -> str:
        return "Allabolag.se"
    
    async def scrape_bankruptcies(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> AsyncIterator[BankruptcyRecord]:
        """Scrape bankruptcies from Allabolag.se."""
        
        if not self.page:
            raise RuntimeError("Scraper not initialized. Use 'async with' context.")
        
        logger.info(f"Scraping {self.source_name}...")
        
        try:
            await self.page.goto(self.BANKRUPTCIES_URL, timeout=self.timeout)
            await self.page.wait_for_load_state('networkidle', timeout=self.timeout)
        except PlaywrightTimeout:
            logger.warning(f"Timeout loading {self.BANKRUPTCIES_URL}")
            return
        except Exception as e:
            logger.error(f"Error loading {self.BANKRUPTCIES_URL}: {e}")
            return
        
        # Wait for JS content to load
        await asyncio.sleep(3)
        
        # Allabolag uses <li> elements or <a> tags with /foretag/ links
        # Try multiple selector strategies
        entries = await self.page.query_selector_all('ul li a[href*="/foretag/"]')
        
        if not entries:
            entries = await self.page.query_selector_all('a[href*="/foretag/"]')
        
        if not entries:
            # Fallback: parse page content directly with regex
            content = await self.page.content()
            records = list(self._parse_allabolag_html(content, year, month))
            for record in records:
                yield record
            return
        
        logger.info(f"Found {len(entries)} potential entries on {self.source_name}")
        
        seen_orgs = set()
        for entry in entries[:100]:
            try:
                # Get the parent li element text if possible
                parent = await entry.evaluate_handle('el => el.closest("li") || el.parentElement')
                if parent:
                    text = await parent.evaluate('el => el.textContent')
                else:
                    text = await entry.inner_text()
                
                href = await entry.get_attribute('href')
                company_url = urljoin(self.BASE_URL, href) if href else None
                
                # Parse entry text
                record = self._parse_allabolag_entry(text, company_url)
                if record and self._matches_period(record, year, month):
                    # Dedupe by org number
                    if record.company.org_number not in seen_orgs:
                        seen_orgs.add(record.company.org_number)
                        yield record
                    
            except Exception as e:
                logger.debug(f"Error parsing entry: {e}")
                continue
    
    def _parse_allabolag_html(self, html: str, year: Optional[int], month: Optional[int]) -> Iterator[BankruptcyRecord]:
        """Parse Allabolag HTML content using regex."""
        # Pattern: "Company Name AB ... Konkurs inledd 2025-12-03"
        # Format from page: "[Company AB](/foretag/...) Region TypeKonkurs inledd YYYY-MM-DD"
        
        patterns = [
            # Match: CompanyName Region BusinessTypeKonkurs inledd YYYY-MM-DD
            r'\[([^\]]+)\]\(/foretag/[^)]+/(\d{10})\)[^\n]*?([A-ZÅÄÖa-zåäö\s]+?)Konkurs\s+(inledd|avslutad)\s+(\d{4}-\d{2}-\d{2})',
            # Simpler pattern
            r'(\d{10})[^\n]*?Konkurs\s+(inledd|avslutad)\s+(\d{4}-\d{2}-\d{2})',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE):
                try:
                    groups = match.groups()
                    if len(groups) >= 3:
                        if len(groups) == 5:
                            company_name, org_digits, region_type, status_text, date_str = groups
                        else:
                            org_digits, status_text, date_str = groups[:3]
                            company_name = "Unknown"
                            region_type = ""
                        
                        org_number = normalize_org_number(org_digits)
                        date = parse_swedish_date(date_str)
                        
                        if date:
                            if year and date.year != year:
                                continue
                            if month and date.month != month:
                                continue
                        
                        status = BankruptcyStatus.INITIATED if 'inledd' in status_text.lower() else BankruptcyStatus.CONCLUDED
                        
                        company = CompanyInfo(
                            org_number=org_number,
                            name=company_name.strip(),
                        )
                        
                        yield BankruptcyRecord(
                            company=company,
                            status=status,
                            declaration_date=date,
                            source_url=self.BANKRUPTCIES_URL,
                            scraped_at=datetime.now(),
                        )
                except Exception as e:
                    logger.debug(f"Error parsing match: {e}")
                    continue
    
    def _parse_allabolag_entry(self, text: str, source_url: Optional[str]) -> Optional[BankruptcyRecord]:
        """Parse a single Allabolag entry text."""
        if not text or len(text) < 10:
            return None
        
        # Extract org number (10 digits, possibly with hyphen)
        org_match = re.search(r'(\d{6}-?\d{4})', text)
        org_number = normalize_org_number(org_match.group(1)) if org_match else None
        
        # Extract from URL if not in text
        if not org_number and source_url:
            url_match = re.search(r'/(\d{10})(?:\)|$|/)', source_url)
            if url_match:
                org_number = normalize_org_number(url_match.group(1))
        
        # Extract date
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        date = parse_swedish_date(date_match.group(1)) if date_match else None
        
        # Extract status
        status = BankruptcyStatus.INITIATED
        if 'avslutad' in text.lower():
            status = BankruptcyStatus.CONCLUDED
        elif 'likvidation' in text.lower():
            status = BankruptcyStatus.CONCLUDED
        
        # Extract company name - usually at the start before region
        # Format: "Company Name AB Stockholm BusinessType Konkurs..."
        lines = text.strip().split('\n')
        company_name = None
        region = None
        business_type = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # First substantial line is usually company name
            if not company_name and len(line) > 3:
                # Remove date and status words
                clean = re.sub(r'\d{4}-\d{2}-\d{2}', '', line)
                clean = re.sub(r'Konkurs\s*(inledd|avslutad)', '', clean, flags=re.IGNORECASE)
                clean = re.sub(r'Likvidation\s*(beslutad)?', '', clean, flags=re.IGNORECASE)
                
                # Extract region
                for region_key, region_name in REGION_MAPPING.items():
                    if region_key in clean.lower():
                        region = region_name
                        clean = re.sub(region_key, '', clean, flags=re.IGNORECASE)
                        break
                
                # What's left should be company name and possibly business type
                parts = clean.strip().split()
                if parts:
                    # Find where company name ends (usually at AB, HB, etc or capital word for region)
                    name_parts = []
                    for i, part in enumerate(parts):
                        name_parts.append(part)
                        if part.upper() in ['AB', 'HB', 'KB'] or part.endswith('AB') or part.endswith('HB'):
                            break
                    company_name = ' '.join(name_parts).strip()
                    
                    # Rest might be business type
                    if len(parts) > len(name_parts):
                        business_type = ' '.join(parts[len(name_parts):]).strip()
                break
        
        if not company_name or len(company_name) < 2:
            return None
        
        if not org_number:
            org_number = "000000-0000"
        
        company = CompanyInfo(
            org_number=org_number,
            name=company_name,
            region=region,
            business_type=business_type,
        )
        
        return BankruptcyRecord(
            company=company,
            status=status,
            declaration_date=date,
            source_url=source_url,
            scraped_at=datetime.now(),
        )
    
    def _matches_period(self, record: BankruptcyRecord, year: Optional[int], month: Optional[int]) -> bool:
        """Check if record matches the requested period."""
        if not record.declaration_date:
            return True  # Include if no date (will be filtered later)
        
        if year and record.declaration_date.year != year:
            return False
        if month and record.declaration_date.month != month:
            return False
        return True
    
    async def enrich_company(self, org_number: str) -> Optional[dict]:
        """Fetch additional company details from Allabolag."""
        
        if not self.page or org_number == "000000-0000":
            return None
        
        url = f"{self.BASE_URL}/{org_number.replace('-', '')}"
        
        try:
            await self.page.goto(url, timeout=self.timeout)
            await self.page.wait_for_load_state('networkidle', timeout=10000)
            
            data = {}
            
            # Try to extract key figures
            employees_el = await self.page.query_selector('[data-employees], .employees, .anstallda')
            if employees_el:
                data['employees'] = parse_employees(await employees_el.inner_text())
            
            revenue_el = await self.page.query_selector('[data-revenue], .revenue, .omsattning')
            if revenue_el:
                data['revenue'] = parse_revenue(await revenue_el.inner_text())
            
            return data if data else None
            
        except Exception as e:
            logger.debug(f"Error enriching {org_number}: {e}")
            return None


class KonkurslistanScraper(BaseScraper):
    """Scraper for Konkurslistan.se."""
    
    BASE_URL = "https://www.konkurslistan.se"
    BANKRUPTCIES_URL = "https://www.konkurslistan.se/alla-konkurser"
    
    @property
    def source_name(self) -> str:
        return "Konkurslistan.se"
    
    async def scrape_bankruptcies(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> AsyncIterator[BankruptcyRecord]:
        """Scrape bankruptcies from Konkurslistan.se."""
        
        if not self.page:
            raise RuntimeError("Scraper not initialized. Use 'async with' context.")
        
        logger.info(f"Scraping {self.source_name}...")
        
        try:
            await self.page.goto(self.BANKRUPTCIES_URL, timeout=self.timeout)
            await self.page.wait_for_load_state('networkidle', timeout=self.timeout)
        except PlaywrightTimeout:
            logger.warning(f"Timeout loading {self.BANKRUPTCIES_URL}")
            return
        except Exception as e:
            logger.error(f"Error loading {self.BANKRUPTCIES_URL}: {e}")
            return
        
        # Wait for JS to render
        await asyncio.sleep(3)
        
        # Konkurslistan uses links to /konkurser/{org_number}
        entries = await self.page.query_selector_all('a[href*="/konkurser/"]')

        logger.info(f"Found {len(entries)} potential entries on {self.source_name}")

        # First, collect all entry data without navigation
        entry_data_list = []
        for entry in entries[:150]:
            try:
                text = await entry.inner_text()
                href = await entry.get_attribute('href')

                # Skip pagination and non-company links
                if not href or 'page=' in href or len(text) < 20:
                    continue

                entry_data_list.append((text, href))

            except Exception as e:
                logger.debug(f"Error collecting entry data: {e}")
                continue

        # Now process entries (can navigate safely)
        seen_orgs = set()
        for text, href in entry_data_list:
            try:
                record = self._parse_konkurslistan_entry(text, href)
                if record and self._matches_period(record, year, month):
                    if record.company.org_number not in seen_orgs:
                        seen_orgs.add(record.company.org_number)

                        # Enrich with detail page data (administrator, court)
                        detail_url = urljoin(self.BASE_URL, href) if href else None
                        if detail_url:
                            await self._enrich_from_detail_page(record, detail_url)

                        yield record

            except Exception as e:
                logger.debug(f"Error parsing entry: {e}")
                continue
    
    def _parse_konkurslistan_entry(self, text: str, href: str) -> Optional[BankruptcyRecord]:
        """Parse a Konkurslistan entry.
        
        Format from site:
        5567665616
        Charkeriet i Huskvarna AB
        Jönköping, Jönköpings län
        Datum 2025-12-23
        Status Konkurs inledd
        Verksamhet (SNI) 46320 Description
        Tillgångar 59000
        Omsättning 1359000
        Anställda 1
        """
        
        if not text or len(text) < 20:
            return None
        
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        # Extract org number from URL or text
        org_number = None
        if href:
            org_match = re.search(r'/konkurser/(\d{10})', href)
            if org_match:
                org_number = normalize_org_number(org_match.group(1))
        
        if not org_number:
            # Try first line which is often just the org number
            for line in lines:
                if re.match(r'^\d{10}$', line.replace('-', '')):
                    org_number = normalize_org_number(line)
                    break
        
        # Extract other fields
        company_name = None
        city = None
        region = None
        date = None
        status = BankruptcyStatus.INITIATED
        sni_code = None
        business_type = None
        employees = None
        revenue = None
        assets = None
        
        full_text = ' '.join(lines)
        
        for i, line in enumerate(lines):
            line_lower = line.lower()
            
            # Skip org number line
            if re.match(r'^\d{10}$', line.replace('-', '')):
                continue
            
            # Company name is usually early, ends with AB/HB/KB or similar
            if not company_name and (
                line.endswith(' AB') or 
                line.endswith(' HB') or 
                line.endswith(' KB') or
                ' AB ' in line or
                'AB ' in line or
                re.search(r'\b(AB|HB|KB|Ek\.?\s*för\.?)\b', line)
            ):
                company_name = line.strip()
                continue
            
            # Location line: "City, Region län"
            if not region and ',' in line and 'län' in line_lower:
                parts = line.split(',')
                city = parts[0].strip()
                region = parts[1].strip() if len(parts) > 1 else None
                continue
            
            # Date line
            if 'datum' in line_lower or re.match(r'^\d{4}-\d{2}-\d{2}$', line):
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                if date_match:
                    date = parse_swedish_date(date_match.group(1))
                continue
            
            # Status line
            if 'konkurs inledd' in line_lower:
                status = BankruptcyStatus.INITIATED
                continue
            elif 'konkurs avslutad' in line_lower or 'avslutad' in line_lower:
                status = BankruptcyStatus.CONCLUDED
                continue
            
            # SNI/Business type
            if 'verksamhet' in line_lower or 'sni' in line_lower:
                # Next line might have SNI code
                sni_match = re.search(r'(\d{5})\s+(.+)', line)
                if sni_match:
                    sni_code = sni_match.group(1)
                    business_type = sni_match.group(2).strip()
                continue
            
            # Employees
            if 'anställda' in line_lower:
                emp_match = re.search(r'(\d+)', line)
                if emp_match:
                    employees = int(emp_match.group(1))
                continue
            
            # Revenue (Omsättning)
            if 'omsättning' in line_lower:
                rev_match = re.search(r'(\d[\d\s]*)', line)
                if rev_match:
                    revenue = float(rev_match.group(1).replace(' ', ''))
                continue
            
            # Assets (Tillgångar)
            if 'tillgångar' in line_lower:
                asset_match = re.search(r'(-?\d[\d\s]*)', line)
                if asset_match:
                    assets = float(asset_match.group(1).replace(' ', ''))
                continue
        
        # If no company name found, try to extract from text
        if not company_name:
            for line in lines[1:5]:  # Check first few lines after org number
                if len(line) > 5 and not re.match(r'^[\d\s,-]+$', line):
                    if 'datum' not in line.lower() and 'status' not in line.lower():
                        company_name = line.strip()
                        break
        
        if not company_name:
            return None
        
        if not org_number:
            org_number = "000000-0000"
        
        company = CompanyInfo(
            org_number=org_number,
            name=company_name,
            city=city,
            region=region,
            sni_code=sni_code,
            business_type=business_type,
            employees=employees,
            revenue=revenue,
            assets=assets,
        )
        
        return BankruptcyRecord(
            company=company,
            status=status,
            declaration_date=date,
            source_url=f"{self.BASE_URL}/konkurser/{org_number.replace('-', '')}" if org_number != "000000-0000" else self.BANKRUPTCIES_URL,
            scraped_at=datetime.now(),
        )
    
    def _matches_period(self, record: BankruptcyRecord, year: Optional[int], month: Optional[int]) -> bool:
        """Check if record matches the requested period."""
        if not record.declaration_date:
            return True
        if year and record.declaration_date.year != year:
            return False
        if month and record.declaration_date.month != month:
            return False
        return True

    async def _enrich_from_detail_page(self, record: BankruptcyRecord, detail_url: str):
        """
        Enrich record with data from detail page (administrator, court, etc.).

        Args:
            record: Bankruptcy record to enrich
            detail_url: URL of the detail page
        """
        try:
            logger.debug(f"Fetching detail page: {detail_url}")
            await self.page.goto(detail_url, timeout=self.timeout, wait_until='domcontentloaded')
            await self.random_delay()

            # Get page content
            content = await self.page.content()
            text_content = await self.page.inner_text('body')

            # Extract administrator information
            # Look for patterns like "Förvaltare:" or "Konkursförvaltare:"
            admin_patterns = [
                r'(?:Konkurs)?[Ff]örvaltare[:\s]+([^\n]+)',
                r'Administrator[:\s]+([^\n]+)',
            ]

            for pattern in admin_patterns:
                match = re.search(pattern, text_content)
                if match:
                    admin_text = match.group(1).strip()

                    # Parse administrator name and law firm
                    # Common formats:
                    # "Name, Law Firm"
                    # "Name (Law Firm)"
                    # "Law Firm / Name"
                    admin_name = None
                    law_firm = None

                    if ',' in admin_text:
                        parts = admin_text.split(',', 1)
                        admin_name = parts[0].strip()
                        law_firm = parts[1].strip() if len(parts) > 1 else None
                    elif '(' in admin_text and ')' in admin_text:
                        # Format: "Name (Law Firm)"
                        name_match = re.match(r'([^(]+)\(([^)]+)\)', admin_text)
                        if name_match:
                            admin_name = name_match.group(1).strip()
                            law_firm = name_match.group(2).strip()
                    elif '/' in admin_text:
                        # Format: "Law Firm / Name"
                        parts = admin_text.split('/', 1)
                        if len(parts) == 2:
                            law_firm = parts[0].strip()
                            admin_name = parts[1].strip()
                    else:
                        # Just a name
                        admin_name = admin_text

                    if admin_name:
                        record.administrator = BankruptcyAdministrator(
                            name=admin_name,
                            law_firm=law_firm
                        )
                        logger.debug(f"Found administrator: {admin_name}" + (f" ({law_firm})" if law_firm else ""))
                    break

            # Extract court information
            # Konkurslistan format: "gick i konkurs DD month YYYY efter beslut vid COURT"
            court_patterns = [
                r'efter beslut vid\s+([A-ZÅÄÖ][a-zåäö\s]+tingsrätt)',
                r'Tingsrätt[:\s]+([^\n]+)',
                r'Domstol[:\s]+([^\n]+)',
                r'([A-ZÅÄÖ][a-zåäö]+\s+tingsrätt)',
            ]

            for pattern in court_patterns:
                match = re.search(pattern, text_content)
                if match:
                    court = match.group(1).strip()
                    # Remove any trailing text after "tingsrätt"
                    court = re.sub(r'(tingsrätt).*', r'\1', court)
                    record.court = court
                    logger.debug(f"Found court: {court}")
                    break

        except Exception as e:
            logger.warning(f"Failed to enrich {record.company.name} from detail page: {e}")


class BolagsfaktaScraper(BaseScraper):
    """Scraper for Bolagsfakta.se."""
    
    BASE_URL = "https://www.bolagsfakta.se"
    BANKRUPTCIES_URL = "https://www.bolagsfakta.se/listor/konkurser"
    
    @property
    def source_name(self) -> str:
        return "Bolagsfakta.se"
    
    async def scrape_bankruptcies(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> AsyncIterator[BankruptcyRecord]:
        """Scrape bankruptcies from Bolagsfakta.se."""
        
        if not self.page:
            raise RuntimeError("Scraper not initialized. Use 'async with' context.")
        
        logger.info(f"Scraping {self.source_name}...")
        
        try:
            await self.page.goto(self.BANKRUPTCIES_URL, timeout=self.timeout)
            await self.page.wait_for_load_state('networkidle', timeout=self.timeout)
        except PlaywrightTimeout:
            logger.warning(f"Timeout loading {self.BANKRUPTCIES_URL}")
            return
        except Exception as e:
            logger.error(f"Error loading {self.BANKRUPTCIES_URL}: {e}")
            return
        
        await asyncio.sleep(2)
        
        # Get page content and parse
        content = await self.page.content()
        
        # Find table rows or list items
        entries = await self.page.query_selector_all('tr, .list-item, article, .company-item')
        
        logger.info(f"Found {len(entries)} potential entries on {self.source_name}")
        
        for entry in entries[:100]:
            try:
                text = await entry.inner_text()
                record = self._parse_entry(text)
                if record and self._matches_period(record, year, month):
                    yield record
                    
            except Exception as e:
                logger.debug(f"Error parsing entry: {e}")
                continue
    
    def _parse_entry(self, text: str) -> Optional[BankruptcyRecord]:
        """Parse a Bolagsfakta entry."""
        
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if len(lines) < 1:
            return None
        
        full_text = ' '.join(lines)
        
        # Extract org number
        org_number = None
        org_match = re.search(r'(\d{6}-?\d{4})', full_text)
        if org_match:
            org_number = normalize_org_number(org_match.group(1))
        
        # Extract date
        date = None
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', full_text)
        if date_match:
            date = parse_swedish_date(date_match.group(1))
        
        # Extract company name
        company_name = None
        for line in lines:
            if len(line) > 5 and not re.match(r'^[\d\s-]+$', line):
                company_name = re.sub(r'\d{6}-?\d{4}', '', line).strip()
                company_name = re.sub(r'\d{4}-\d{2}-\d{2}', '', company_name).strip()
                if company_name and len(company_name) > 3:
                    break
        
        if not company_name:
            return None
        
        if not org_number:
            org_number = "000000-0000"
        
        # Determine status
        status = BankruptcyStatus.INITIATED
        if 'avslutad' in full_text.lower():
            status = BankruptcyStatus.CONCLUDED
        
        # Extract region
        region = None
        for region_key, region_name in REGION_MAPPING.items():
            if region_key in full_text.lower():
                region = region_name
                break
        
        company = CompanyInfo(
            org_number=org_number,
            name=company_name,
            region=region,
        )
        
        return BankruptcyRecord(
            company=company,
            status=status,
            declaration_date=date,
            source_url=self.BANKRUPTCIES_URL,
            scraped_at=datetime.now(),
        )
    
    def _matches_period(self, record: BankruptcyRecord, year: Optional[int], month: Optional[int]) -> bool:
        if not record.declaration_date:
            return True
        if year and record.declaration_date.year != year:
            return False
        if month and record.declaration_date.month != month:
            return False
        return True


class MultiSourceScraper:
    """
    Aggregates data from multiple Swedish bankruptcy sources.
    Deduplicates by organization number and merges enrichment data.
    """
    
    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        request_delay: float = 2.0,
        sources: Optional[list] = None,
    ):
        self.headless = headless
        self.timeout = timeout
        self.request_delay = request_delay
        
        # Default sources in priority order
        self.source_classes = sources or [
            AllabolagScraper,
            KonkurslistanScraper,
            BolagsfaktaScraper,
        ]
    
    async def scrape_all(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
        enrich: bool = True,
    ) -> list[BankruptcyRecord]:
        """
        Scrape bankruptcies from all sources, deduplicate, and optionally enrich.
        
        Args:
            year: Filter by year (None for all)
            month: Filter by month (None for all)
            enrich: Whether to fetch additional company details
            
        Returns:
            List of deduplicated BankruptcyRecord objects
        """
        
        all_records: dict[str, BankruptcyRecord] = {}  # keyed by org_number
        
        for scraper_class in self.source_classes:
            try:
                scraper = scraper_class(
                    headless=self.headless,
                    timeout=self.timeout,
                    request_delay=self.request_delay,
                )
                
                async with scraper:
                    async for record in scraper.scrape_bankruptcies(year=year, month=month):
                        key = record.company.org_number
                        
                        if key == "000000-0000":
                            # Use name as fallback key for records without org number
                            key = f"name:{record.company.name.lower()}"
                        
                        if key not in all_records:
                            all_records[key] = record
                            logger.debug(f"Added: {record.company.name} from {scraper.source_name}")
                        else:
                            # Merge data - prefer non-null values
                            existing = all_records[key]
                            self._merge_records(existing, record)
                            logger.debug(f"Merged: {record.company.name}")
                            
            except Exception as e:
                logger.error(f"Error with {scraper_class.__name__}: {e}")
                continue
        
        records = list(all_records.values())
        logger.info(f"Total unique records after deduplication: {len(records)}")
        
        # Enrich with additional company data
        if enrich and records:
            records = await self._enrich_records(records)
        
        return records
    
    def _merge_records(self, existing: BankruptcyRecord, new: BankruptcyRecord):
        """Merge new record data into existing record (prefer non-null values)."""
        
        # Merge company info
        for field in ['employees', 'revenue', 'profit', 'assets', 'business_type', 
                      'region', 'city', 'address', 'postal_code', 'sni_code', 'description']:
            existing_val = getattr(existing.company, field)
            new_val = getattr(new.company, field)
            if new_val and not existing_val:
                setattr(existing.company, field, new_val)
        
        # Merge bankruptcy record fields
        if new.declaration_date and not existing.declaration_date:
            existing.declaration_date = new.declaration_date
        if new.court and not existing.court:
            existing.court = new.court
        if new.administrator and not existing.administrator:
            existing.administrator = new.administrator
    
    async def _enrich_records(self, records: list[BankruptcyRecord]) -> list[BankruptcyRecord]:
        """Enrich records with additional company data."""
        
        logger.info(f"Enriching {len(records)} records with company data...")
        
        # Use Allabolag for enrichment as it has good company data
        try:
            async with AllabolagScraper(
                headless=self.headless,
                timeout=self.timeout,
                request_delay=self.request_delay,
            ) as scraper:
                for record in records:
                    if record.company.org_number and record.company.org_number != "000000-0000":
                        if not record.company.employees or not record.company.revenue:
                            try:
                                data = await scraper.enrich_company(record.company.org_number)
                                if data:
                                    if data.get('employees') and not record.company.employees:
                                        record.company.employees = data['employees']
                                    if data.get('revenue') and not record.company.revenue:
                                        record.company.revenue = data['revenue']
                                    logger.debug(f"Enriched: {record.company.name}")
                                await scraper.random_delay()
                            except Exception as e:
                                logger.debug(f"Error enriching {record.company.name}: {e}")
                                continue
        except Exception as e:
            logger.warning(f"Enrichment failed: {e}")
        
        return records


# Convenience function for simple usage
async def scrape_swedish_bankruptcies(
    year: Optional[int] = None,
    month: Optional[int] = None,
    headless: bool = True,
    enrich: bool = True,
) -> list[BankruptcyRecord]:
    """
    Scrape Swedish bankruptcies from multiple aggregator sources.
    
    Args:
        year: Filter by year (e.g., 2025)
        month: Filter by month (1-12)
        headless: Run browser in headless mode
        enrich: Fetch additional company details
        
    Returns:
        List of BankruptcyRecord objects
        
    Example:
        records = await scrape_swedish_bankruptcies(year=2025, month=12)
    """
    scraper = MultiSourceScraper(headless=headless)
    return await scraper.scrape_all(year=year, month=month, enrich=enrich)


# For testing
if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    async def main():
        year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
        month = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        
        print(f"Scraping bankruptcies for {year}-{month:02d}...")
        
        records = await scrape_swedish_bankruptcies(
            year=year,
            month=month,
            headless=True,
            enrich=False,  # Skip enrichment for quick test
        )
        
        print(f"\nFound {len(records)} bankruptcies:")
        for r in records[:20]:
            date_str = r.declaration_date.strftime("%Y-%m-%d") if r.declaration_date else "Unknown"
            print(f"  {r.company.name} ({r.company.org_number}) - {date_str}")
    
    asyncio.run(main())
