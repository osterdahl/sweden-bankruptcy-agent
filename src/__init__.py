"""
Sweden Bankruptcy Monitoring Agent
===================================

An AI agent that monitors bankruptcies in Sweden from aggregator sites,
filters them based on custom criteria, and sends monthly email reports.

Usage:
    from src import BankruptcyMonitorAgent, Settings
    
    settings = Settings.from_env()
    agent = BankruptcyMonitorAgent(settings)
    result = await agent.run_monthly_report()
"""

from .models import (
    BankruptcyRecord,
    CompanyInfo,
    BankruptcyAdministrator,
    BankruptcyStatus,
)
from .scraper import BankruptcyScraper, MockBankruptcyScraper
from .aggregator_scraper import (
    MultiSourceScraper,
    AllabolagScraper,
    KonkurslistanScraper,
    BolagsfaktaScraper,
    scrape_swedish_bankruptcies,
)
from .database import BankruptcyDatabase
from .enrichment import CompanyEnricher, SNICodeLookup
from .filter import BankruptcyFilter
from .email_notifier import EmailNotifier
from .agent import BankruptcyMonitorAgent, run_agent

__all__ = [
    "BankruptcyRecord",
    "CompanyInfo",
    "BankruptcyAdministrator",
    "BankruptcyStatus",
    "BankruptcyScraper",
    "MockBankruptcyScraper",
    "MultiSourceScraper",
    "AllabolagScraper",
    "KonkurslistanScraper",
    "BolagsfaktaScraper",
    "scrape_swedish_bankruptcies",
    "BankruptcyDatabase",
    "CompanyEnricher",
    "SNICodeLookup",
    "BankruptcyFilter",
    "EmailNotifier",
    "BankruptcyMonitorAgent",
    "run_agent",
]

__version__ = "1.0.0"
