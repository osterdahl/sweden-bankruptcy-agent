"""
Tests for Sweden Bankruptcy Monitoring Agent.
Run with: pytest tests/ -v
"""

import pytest
import asyncio
from datetime import datetime

# Add parent directory to path for imports
import sys
sys.path.insert(0, "..")

from src.models import (
    BankruptcyRecord, 
    CompanyInfo, 
    BankruptcyAdministrator,
    BankruptcyStatus
)
from src.filter import BankruptcyFilter
from src.database import BankruptcyDatabase
from config import FilterCriteria


class TestModels:
    """Test data models."""
    
    def test_company_info_creation(self):
        company = CompanyInfo(
            org_number="123456-7890",
            name="Test AB",
            employees=50,
            revenue=5000000.0
        )
        assert company.org_number == "123456-7890"
        assert company.name == "Test AB"
        assert company.employees == 50
    
    def test_bankruptcy_record_serialization(self):
        company = CompanyInfo("123456-7890", "Test AB")
        admin = BankruptcyAdministrator(
            name="Test Lawyer",
            title="advokat",
            law_firm="Test Law Firm"
        )
        record = BankruptcyRecord(
            company=company,
            administrator=admin,
            status=BankruptcyStatus.INITIATED,
            declaration_date=datetime(2024, 11, 15)
        )
        
        # Test serialization
        data = record.to_dict()
        assert data["company"]["org_number"] == "123456-7890"
        assert data["administrator"]["name"] == "Test Lawyer"
        
        # Test JSON round-trip
        json_str = record.to_json()
        restored = BankruptcyRecord.from_json(json_str)
        assert restored.company.org_number == record.company.org_number
        assert restored.administrator.name == admin.name


class TestFilter:
    """Test filtering logic."""
    
    def create_record(
        self,
        employees=None,
        revenue=None,
        business_type=None,
        region=None
    ):
        company = CompanyInfo(
            org_number="123456-7890",
            name="Test Company AB",
            employees=employees,
            revenue=revenue,
            business_type=business_type,
            region=region
        )
        return BankruptcyRecord(company=company)
    
    def test_filter_by_min_employees(self):
        criteria = FilterCriteria(min_employees=10)
        filter_ = BankruptcyFilter(criteria)
        
        # Should match: 50 employees >= 10
        record1 = self.create_record(employees=50)
        matches, _ = filter_.matches(record1)
        assert matches is True
        
        # Should not match: 5 employees < 10
        record2 = self.create_record(employees=5)
        matches, _ = filter_.matches(record2)
        assert matches is False
    
    def test_filter_by_min_revenue(self):
        criteria = FilterCriteria(min_revenue=1000000)
        filter_ = BankruptcyFilter(criteria)
        
        # Should match
        record1 = self.create_record(revenue=5000000)
        matches, _ = filter_.matches(record1)
        assert matches is True
        
        # Should not match
        record2 = self.create_record(revenue=500000)
        matches, _ = filter_.matches(record2)
        assert matches is False
    
    def test_filter_by_business_type(self):
        criteria = FilterCriteria(business_types=["Bygg", "IT"])
        filter_ = BankruptcyFilter(criteria)
        
        # Should match
        record1 = self.create_record(business_type="Byggverksamhet")
        matches, _ = filter_.matches(record1)
        assert matches is True
        
        # Should not match
        record2 = self.create_record(business_type="Restaurang")
        matches, _ = filter_.matches(record2)
        assert matches is False
    
    def test_filter_no_criteria_matches_all(self):
        criteria = FilterCriteria()  # No filters
        filter_ = BankruptcyFilter(criteria)
        
        record = self.create_record()
        matches, reason = filter_.matches(record)
        assert matches is True
        assert "no filter criteria" in reason.lower()
    
    def test_exclude_keywords(self):
        criteria = FilterCriteria(exclude_keywords=["enskild firma"])
        filter_ = BankruptcyFilter(criteria)
        
        # Should not match (contains excluded keyword)
        company = CompanyInfo(
            org_number="123456-7890",
            name="Test Enskild Firma",
            description="En enskild firma"
        )
        record = BankruptcyRecord(company=company)
        matches, _ = filter_.matches(record)
        assert matches is False


class TestDatabase:
    """Test database operations."""
    
    @pytest.fixture
    def db(self, tmp_path):
        db_path = tmp_path / "test.db"
        return BankruptcyDatabase(str(db_path))
    
    def test_upsert_and_retrieve(self, db):
        company = CompanyInfo(
            org_number="123456-7890",
            name="Test AB",
            city="Stockholm"
        )
        record = BankruptcyRecord(
            company=company,
            declaration_date=datetime(2024, 11, 15),
            court="Stockholms tingsrätt"
        )
        
        # Insert
        record_id = db.upsert_record(record)
        assert record_id > 0
        
        # Retrieve
        records = db.get_by_org_number("123456-7890")
        assert len(records) == 1
        assert records[0].company.name == "Test AB"
        assert records[0].court == "Stockholms tingsrätt"
    
    def test_monthly_query(self, db):
        # Insert records for different months
        for month in [10, 11, 12]:
            company = CompanyInfo(
                org_number=f"123456-789{month}",
                name=f"Company {month}"
            )
            record = BankruptcyRecord(
                company=company,
                declaration_date=datetime(2024, month, 15)
            )
            db.upsert_record(record)
        
        # Query specific month
        nov_records = db.get_for_month(2024, 11)
        assert len(nov_records) == 1
        assert nov_records[0].company.name == "Company 11"


@pytest.mark.asyncio
class TestScraper:
    """Test scraper with mock data."""
    
    async def test_mock_scraper(self):
        from src.scraper import MockBankruptcyScraper
        
        async with MockBankruptcyScraper() as scraper:
            records = []
            async for record in scraper.search_bankruptcies(year=2024, month=11):
                records.append(record)
        
        assert len(records) > 0
        assert all(r.company.org_number for r in records)
        assert all(r.company.name for r in records)


class TestIntegration:
    """Integration tests."""
    
    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self, tmp_path):
        from src.agent import BankruptcyMonitorAgent
        from config import Settings
        
        settings = Settings(
            database_path=str(tmp_path / "test.db"),
            export_path=str(tmp_path / "exports")
        )
        
        agent = BankruptcyMonitorAgent(settings=settings, use_mock=True)
        result = await agent.run_monthly_report(
            year=2024,
            month=11,
            send_email=False,
            export_files=True
        )
        
        assert result["total_found"] > 0
        assert "records" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
