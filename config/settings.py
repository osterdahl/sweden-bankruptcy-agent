"""
Configuration settings for Sweden Bankruptcy Monitoring Agent.
Loads from environment variables with sensible defaults.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FilterCriteria:
    """Criteria for filtering relevant bankruptcies."""
    min_employees: Optional[int] = None
    max_employees: Optional[int] = None
    min_revenue: Optional[float] = None  # In SEK
    max_revenue: Optional[float] = None
    business_types: list[str] = field(default_factory=list)  # SNI codes or descriptions
    regions: list[str] = field(default_factory=list)  # Swedish län/regions
    exclude_keywords: list[str] = field(default_factory=list)
    include_keywords: list[str] = field(default_factory=list)


@dataclass
class EmailConfig:
    """Email configuration."""
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    use_tls: bool = True
    sender_email: str = ""
    sender_password: str = ""
    recipient_emails: list[str] = field(default_factory=list)


@dataclass
class Settings:
    """Main application settings."""
    # Data source URLs
    poit_base_url: str = "https://poit.bolagsverket.se/poit-app"
    poit_search_url: str = "https://poit.bolagsverket.se/poit-app/sok"
    
    # Company info enrichment
    company_info_base_url: str = "https://www.hitta.se/företag"
    
    # Database
    database_path: str = "data/bankruptcies.db"
    export_path: str = "data/exports"
    
    # Scraping settings
    request_delay: float = 1.0  # Delay between requests in seconds
    max_retries: int = 3
    timeout: int = 30
    headless: bool = True
    
    # Filtering
    filter_criteria: FilterCriteria = field(default_factory=FilterCriteria)
    
    # Email
    email_config: EmailConfig = field(default_factory=EmailConfig)
    
    # Schedule (cron expression or 'monthly')
    schedule: str = "monthly"
    run_day_of_month: int = 1
    
    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables."""
        filter_criteria = FilterCriteria(
            min_employees=int(os.getenv("FILTER_MIN_EMPLOYEES", 0)) or None,
            max_employees=int(os.getenv("FILTER_MAX_EMPLOYEES", 0)) or None,
            min_revenue=float(os.getenv("FILTER_MIN_REVENUE", 0)) or None,
            max_revenue=float(os.getenv("FILTER_MAX_REVENUE", 0)) or None,
            business_types=[t.strip() for t in os.getenv("FILTER_BUSINESS_TYPES", "").split(",") if t.strip()],
            regions=[r.strip() for r in os.getenv("FILTER_REGIONS", "").split(",") if r.strip()],
            exclude_keywords=[k.strip() for k in os.getenv("FILTER_EXCLUDE_KEYWORDS", "").split(",") if k.strip()],
            include_keywords=[k.strip() for k in os.getenv("FILTER_INCLUDE_KEYWORDS", "").split(",") if k.strip()],
        )
        
        email_config = EmailConfig(
            smtp_server=os.getenv("SMTP_SERVER", "smtp.gmail.com"),
            smtp_port=int(os.getenv("SMTP_PORT", 587)),
            use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
            sender_email=os.getenv("SENDER_EMAIL", ""),
            sender_password=os.getenv("SENDER_PASSWORD", ""),
            recipient_emails=[e.strip() for e in os.getenv("RECIPIENT_EMAILS", "").split(",") if e.strip()],
        )
        
        return cls(
            database_path=os.getenv("DATABASE_PATH", "data/bankruptcies.db"),
            export_path=os.getenv("EXPORT_PATH", "data/exports"),
            request_delay=float(os.getenv("REQUEST_DELAY", 1.0)),
            max_retries=int(os.getenv("MAX_RETRIES", 3)),
            timeout=int(os.getenv("TIMEOUT", 30)),
            headless=os.getenv("HEADLESS", "true").lower() == "true",
            filter_criteria=filter_criteria,
            email_config=email_config,
            schedule=os.getenv("SCHEDULE", "monthly"),
            run_day_of_month=int(os.getenv("RUN_DAY_OF_MONTH", 1)),
        )
