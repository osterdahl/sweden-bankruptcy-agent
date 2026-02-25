"""
Shared data models for the Nordic Bankruptcy Monitor.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class BankruptcyRecord:
    """A single bankruptcy filing from any Nordic country.

    Fields are intentionally flat and simple. Country-specific data sources
    populate whatever fields they can; missing data stays as default.
    """
    country: str = ""              # ISO 3166-1 alpha-2: "se", "no", "dk", "fi"
    company_name: str = ""
    org_number: str = ""
    initiated_date: str = ""       # MM/DD/YYYY (normalized across countries)
    court: str = ""
    industry_code: str = ""        # NACE Rev. 2 code (called SNI in SE, SN in NO, etc.)
    industry_name: str = ""
    trustee: str = ""
    trustee_firm: str = ""
    trustee_address: str = ""
    employees: Optional[int] = None
    net_sales: Optional[int] = None      # In country's currency, converted to base units
    total_assets: Optional[int] = None   # In country's currency, converted to base units
    region: str = ""
    ai_score: Optional[int] = None       # 1-10 acquisition value score
    ai_reason: Optional[str] = None      # Brief explanation
    priority: Optional[str] = None       # "HIGH", "MEDIUM", "LOW"
    asset_types: Optional[str] = None    # e.g. "code,media"
    trustee_email: Optional[str] = None

    # Backward compatibility alias
    @property
    def sni_code(self) -> str:
        return self.industry_code

    @sni_code.setter
    def sni_code(self, value: str):
        self.industry_code = value
