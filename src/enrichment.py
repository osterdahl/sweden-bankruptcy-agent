"""
Company information enrichment from various Swedish data sources.
Enriches bankruptcy records with additional company details.
"""
import asyncio
import logging
import re
from typing import Optional
import httpx

from .models import BankruptcyRecord, CompanyInfo

logger = logging.getLogger(__name__)


class CompanyEnricher:
    """
    Enriches company information from various Swedish sources.
    Uses public APIs and data sources where available.
    """
    
    # Swedish company info APIs (some require registration)
    ROARING_API = "https://api.roaring.io"
    BOLAGSVERKET_API = "https://data.bolagsverket.se"
    
    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
    
    async def enrich_record(self, record: BankruptcyRecord) -> BankruptcyRecord:
        """
        Enrich a bankruptcy record with additional company information.
        
        Tries multiple data sources to fill in missing details.
        """
        org_number = record.company.org_number
        
        # Try to get info from various sources
        enrichment_sources = [
            self._enrich_from_format_validation,
            # self._enrich_from_bolagsverket,  # Would require API key
            # self._enrich_from_allabolag,  # Website scraping alternative
        ]
        
        for source in enrichment_sources:
            try:
                await source(record)
            except Exception as e:
                logger.debug(f"Enrichment source failed: {e}")
                continue
        
        return record
    
    async def _enrich_from_format_validation(self, record: BankruptcyRecord):
        """
        Extract information from org number format.
        Swedish org numbers encode legal form in first digit.
        """
        org = record.company.org_number.replace("-", "")
        if len(org) != 10:
            return
        
        first_digit = org[0]
        
        # Swedish org number coding:
        # 1 = Estate (dödsbo)
        # 2 = Government agency
        # 5 = Limited company (AB)
        # 6 = Partnership (HB, KB) or trading company
        # 7 = Economic association (ekonomisk förening)
        # 8 = Ideal association (ideell förening), foundation (stiftelse)
        # 9 = Limited partnership (KB) or trading company
        
        legal_forms = {
            "1": "Dödsbo",
            "2": "Myndighet",
            "5": "Aktiebolag (AB)",
            "6": "Handelsbolag/Kommanditbolag",
            "7": "Ekonomisk förening",
            "8": "Ideell förening/Stiftelse",
            "9": "Kommanditbolag",
        }
        
        if first_digit in legal_forms and not record.company.legal_form:
            record.company.legal_form = legal_forms[first_digit]
    
    async def enrich_batch(
        self,
        records: list[BankruptcyRecord],
        concurrency: int = 5
    ) -> list[BankruptcyRecord]:
        """Enrich multiple records with rate limiting."""
        semaphore = asyncio.Semaphore(concurrency)
        
        async def enrich_with_limit(record):
            async with semaphore:
                return await self.enrich_record(record)
        
        tasks = [enrich_with_limit(r) for r in records]
        return await asyncio.gather(*tasks)


class SNICodeLookup:
    """
    Swedish Standard Industrial Classification (SNI) code lookup.
    Maps SNI codes to business type descriptions.
    """
    
    # Common SNI codes and their descriptions
    SNI_CODES = {
        "01": "Jordbruk och jakt samt service i anslutning härtill",
        "10": "Livsmedelsframställning",
        "41": "Byggande av hus",
        "42": "Anläggningsarbeten",
        "43": "Specialiserad bygg- och anläggningsverksamhet",
        "45": "Handel med samt reparation av motorfordon",
        "46": "Parti- och provisionshandel utom med motorfordon",
        "47": "Detaljhandel utom med motorfordon",
        "49": "Landtransport; transport i rörsystem",
        "52": "Magasinering och stödtjänster till transport",
        "55": "Hotell- och logiverksamhet",
        "56": "Restaurang-, catering och barverksamhet",
        "62": "Dataprogrammering, datakonsultverksamhet o.d.",
        "63": "Informationstjänster",
        "64": "Finansiella tjänster utom försäkring och pensionsfondsverksamhet",
        "66": "Stödtjänster till finans- och försäkringsverksamhet",
        "68": "Fastighetsverksamhet",
        "69": "Juridisk och ekonomisk konsultverksamhet",
        "70": "Verksamheter som utövas av huvudkontor; konsultbyråer",
        "71": "Arkitekt- och teknisk konsultverksamhet; teknisk provning och analys",
        "72": "Vetenskaplig forskning och utveckling",
        "73": "Reklam och marknadsundersökning",
        "74": "Annan verksamhet inom juridik, ekonomi, vetenskap och teknik",
        "77": "Uthyrning och leasing",
        "78": "Arbetsförmedling, bemanning och andra personalrelaterade tjänster",
        "79": "Resebyrå- och researrangörsverksamhet och turistservice",
        "80": "Säkerhets- och bevakningsverksamhet",
        "81": "Fastighetsservice samt skötsel och underhåll av grönytor",
        "82": "Kontorstjänster och andra företagstjänster",
        "85": "Utbildning",
        "86": "Hälso- och sjukvård",
        "87": "Vård och omsorg med boende",
        "88": "Öppna sociala insatser",
        "90": "Konstnärlig och kulturell verksamhet samt nöjesverksamhet",
        "93": "Sport-, fritids- och nöjesverksamhet",
        "94": "Intressebevakning; religiös verksamhet",
        "95": "Reparation av datorer, hushållsartiklar och personliga artiklar",
        "96": "Andra konsumenttjänster",
    }
    
    @classmethod
    def get_description(cls, sni_code: str) -> Optional[str]:
        """Get description for an SNI code."""
        if not sni_code:
            return None
        
        # Try full code first
        if sni_code in cls.SNI_CODES:
            return cls.SNI_CODES[sni_code]
        
        # Try first 2 digits
        prefix = sni_code[:2]
        return cls.SNI_CODES.get(prefix)
    
    @classmethod
    def match_keywords(cls, keywords: list[str]) -> list[str]:
        """Find SNI codes matching keywords."""
        matches = []
        for code, desc in cls.SNI_CODES.items():
            if any(kw.lower() in desc.lower() for kw in keywords):
                matches.append(code)
        return matches
