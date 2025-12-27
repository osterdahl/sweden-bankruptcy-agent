"""
Data models for Swedish bankruptcy records.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from enum import Enum
import json


class BankruptcyStatus(Enum):
    """Status of a bankruptcy case."""
    INITIATED = "Försatt i konkurs"  # Declared bankrupt
    ONGOING = "Pågående"
    CONCLUDED = "Avslutad"  # Concluded
    UNKNOWN = "Okänd"


@dataclass
class CompanyInfo:
    """Detailed company information."""
    org_number: str  # Swedish organization number (NNNNNN-NNNN)
    name: str
    address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None  # Län
    
    # Business details
    sni_code: Optional[str] = None  # Swedish Industry Classification
    business_type: Optional[str] = None
    description: Optional[str] = None
    
    # Financial info
    employees: Optional[int] = None
    revenue: Optional[float] = None  # In SEK
    profit: Optional[float] = None
    assets: Optional[float] = None
    
    # Registration info
    registration_date: Optional[datetime] = None
    legal_form: Optional[str] = None  # AB, HB, etc.
    
    def to_dict(self) -> dict:
        data = asdict(self)
        if self.registration_date:
            data["registration_date"] = self.registration_date.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "CompanyInfo":
        if data.get("registration_date"):
            data["registration_date"] = datetime.fromisoformat(data["registration_date"])
        return cls(**data)


@dataclass
class BankruptcyAdministrator:
    """Bankruptcy administrator (förvaltare) information."""
    name: str
    title: Optional[str] = None  # e.g., "advokat"
    law_firm: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "BankruptcyAdministrator":
        return cls(**data)


@dataclass
class BankruptcyRecord:
    """Complete bankruptcy record."""
    # Core identifiers
    id: Optional[int] = None
    case_number: Optional[str] = None  # Court case number
    
    # Company info
    company: CompanyInfo = field(default_factory=lambda: CompanyInfo("", ""))
    
    # Bankruptcy details
    status: BankruptcyStatus = BankruptcyStatus.UNKNOWN
    declaration_date: Optional[datetime] = None  # Konkursdag
    publication_date: Optional[datetime] = None  # Kungörelsedatum
    court: Optional[str] = None  # Tingsrätt
    
    # Administrator
    administrator: Optional[BankruptcyAdministrator] = None
    
    # Additional metadata
    creditor_meeting_date: Optional[datetime] = None
    source_url: Optional[str] = None
    scraped_at: Optional[datetime] = None
    
    # Filtering flags
    matches_criteria: bool = False
    filter_notes: Optional[str] = None
    
    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "case_number": self.case_number,
            "company": self.company.to_dict(),
            "status": self.status.value,
            "declaration_date": self.declaration_date.isoformat() if self.declaration_date else None,
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
            "court": self.court,
            "administrator": self.administrator.to_dict() if self.administrator else None,
            "creditor_meeting_date": self.creditor_meeting_date.isoformat() if self.creditor_meeting_date else None,
            "source_url": self.source_url,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
            "matches_criteria": self.matches_criteria,
            "filter_notes": self.filter_notes,
        }
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "BankruptcyRecord":
        company = CompanyInfo.from_dict(data.get("company", {}))
        administrator = BankruptcyAdministrator.from_dict(data["administrator"]) if data.get("administrator") else None
        
        def parse_date(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)
        
        return cls(
            id=data.get("id"),
            case_number=data.get("case_number"),
            company=company,
            status=BankruptcyStatus(data.get("status", "Okänd")),
            declaration_date=parse_date(data.get("declaration_date")),
            publication_date=parse_date(data.get("publication_date")),
            court=data.get("court"),
            administrator=administrator,
            creditor_meeting_date=parse_date(data.get("creditor_meeting_date")),
            source_url=data.get("source_url"),
            scraped_at=parse_date(data.get("scraped_at")),
            matches_criteria=data.get("matches_criteria", False),
            filter_notes=data.get("filter_notes"),
        )
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> "BankruptcyRecord":
        return cls.from_dict(json.loads(json_str))
    
    @property
    def display_name(self) -> str:
        return f"{self.company.name} ({self.company.org_number})"
