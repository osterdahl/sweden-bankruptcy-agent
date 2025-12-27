"""
Main bankruptcy monitoring agent.
Orchestrates scraping, filtering, storage, and notifications.
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import Settings
from .scraper import MockBankruptcyScraper
from .aggregator_scraper import MultiSourceScraper
from .database import BankruptcyDatabase
from .enrichment import CompanyEnricher
from .filter import BankruptcyFilter
from .email_notifier import EmailNotifier
from .models import BankruptcyRecord

logger = logging.getLogger(__name__)


class BankruptcyMonitorAgent:
    """
    Main agent for monitoring Swedish bankruptcies.
    
    Workflow:
    1. Scrape bankruptcy announcements from Bolagsverket
    2. Enrich company information
    3. Filter based on criteria
    4. Store in database
    5. Generate reports and send notifications
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        use_mock: bool = False
    ):
        self.settings = settings or Settings.from_env()
        self.use_mock = use_mock
        
        # Initialize components
        self.db = BankruptcyDatabase(self.settings.database_path)
        self.enricher = CompanyEnricher()
        self.filter = BankruptcyFilter(self.settings.filter_criteria)
        self.notifier = EmailNotifier(self.settings.email_config)
        
        # Ensure directories exist
        Path(self.settings.export_path).mkdir(parents=True, exist_ok=True)
    
    async def run_monthly_report(
        self,
        year: Optional[int] = None,
        month: Optional[int] = None,
        send_email: bool = True,
        export_files: bool = True
    ) -> dict:
        """
        Run the complete monthly bankruptcy monitoring workflow.
        
        Args:
            year: Year to process (defaults to current)
            month: Month to process (defaults to current)
            send_email: Whether to send email notification
            export_files: Whether to export to files
        
        Returns:
            Summary dict with statistics
        """
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        
        logger.info(f"Starting monthly bankruptcy report for {year}-{month:02d}")
        
        # Start scrape run tracking
        run_id = self.db.start_scrape_run(str(month), year)
        
        try:
            # 1. Scrape bankruptcies
            records = await self._scrape_bankruptcies(year, month)
            total_found = len(records)
            logger.info(f"Scraped {total_found} bankruptcy records")
            
            # 2. Enrich records
            records = await self._enrich_records(records)
            
            # 3. Filter records
            matched_records = self.filter.filter_records(records)
            total_matched = len(matched_records)
            logger.info(f"Filtered to {total_matched} matching records")
            
            # 4. Store in database
            for record in records:
                self.db.upsert_record(record)
            logger.info(f"Stored {len(records)} records in database")
            
            # 5. Export files
            if export_files:
                self._export_files(matched_records, year, month)
            
            # 6. Send email notification
            email_sent = False
            if send_email:
                email_sent = self.notifier.send_monthly_report(
                    matched_records, year, month, total_found
                )
            
            # Complete run tracking
            self.db.complete_scrape_run(run_id, total_found, total_matched, "completed")
            
            summary = {
                "year": year,
                "month": month,
                "total_found": total_found,
                "total_matched": total_matched,
                "email_sent": email_sent,
                "records": matched_records,
            }
            
            logger.info(f"Monthly report completed: {total_found} found, {total_matched} matched")
            return summary
            
        except Exception as e:
            logger.error(f"Error during monthly report: {e}")
            self.db.complete_scrape_run(run_id, 0, 0, "failed", str(e))
            raise
    
    async def _scrape_bankruptcies(
        self,
        year: int,
        month: int
    ) -> list[BankruptcyRecord]:
        """Scrape bankruptcy announcements from aggregator sites."""
        
        if self.use_mock:
            # Use mock scraper for testing
            records = []
            async with MockBankruptcyScraper(
                headless=self.settings.headless,
                timeout=self.settings.timeout * 1000,
                request_delay=self.settings.request_delay
            ) as scraper:
                async for record in scraper.search_bankruptcies(year=year, month=month):
                    records.append(record)
            return records
        
        # Use multi-source aggregator scraper for production
        scraper = MultiSourceScraper(
            headless=self.settings.headless,
            timeout=self.settings.timeout * 1000,
            request_delay=self.settings.request_delay,
        )
        
        records = await scraper.scrape_all(
            year=year,
            month=month,
            enrich=True,  # Let aggregator do initial enrichment
        )
        
        return records
    
    async def _enrich_records(
        self,
        records: list[BankruptcyRecord]
    ) -> list[BankruptcyRecord]:
        """Enrich records with additional company information."""
        return await self.enricher.enrich_batch(records)
    
    def _export_files(
        self,
        records: list[BankruptcyRecord],
        year: int,
        month: int
    ):
        """Export records to JSON and CSV files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"bankruptcies_{year}_{month:02d}_{timestamp}"
        
        json_path = f"{self.settings.export_path}/{base_name}.json"
        csv_path = f"{self.settings.export_path}/{base_name}.csv"
        
        self.db.export_to_json(records, json_path)
        self.db.export_to_csv(records, csv_path)
        
        logger.info(f"Exported to {json_path} and {csv_path}")
    
    def get_historical_data(
        self,
        start_date: datetime,
        end_date: datetime,
        matched_only: bool = True
    ) -> list[BankruptcyRecord]:
        """Get historical bankruptcy data from database."""
        return self.db.get_by_date_range(start_date, end_date, matched_only)
    
    def get_monthly_data(
        self,
        year: int,
        month: int,
        matched_only: bool = True
    ) -> list[BankruptcyRecord]:
        """Get data for a specific month."""
        return self.db.get_for_month(year, month, matched_only)


async def run_agent(
    settings: Optional[Settings] = None,
    use_mock: bool = False,
    year: Optional[int] = None,
    month: Optional[int] = None,
    send_email: bool = True
) -> dict:
    """
    Convenience function to run the bankruptcy monitoring agent.
    
    Args:
        settings: Configuration settings
        use_mock: Use mock data for testing
        year: Year to process
        month: Month to process
        send_email: Whether to send email
    
    Returns:
        Summary dictionary
    """
    agent = BankruptcyMonitorAgent(settings=settings, use_mock=use_mock)
    return await agent.run_monthly_report(
        year=year,
        month=month,
        send_email=send_email
    )


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Run with mock data for testing
    result = asyncio.run(run_agent(use_mock=True, send_email=False))
    print(f"\nResults: {result['total_found']} found, {result['total_matched']} matched")
