"""
Test cases for lawyer contact enrichment from Bolagsverket POIT.
"""
import pytest
import asyncio
from datetime import datetime

from src.models import BankruptcyRecord, CompanyInfo, BankruptcyAdministrator, BankruptcyStatus
from src.lawyer_enrichment import BolagsverketLawyerEnricher


class TestBolagsverketLawyerEnricher:
    """Test suite for BolagsverketLawyerEnricher."""

    @pytest.fixture
    def sample_record(self):
        """Create a sample bankruptcy record for testing."""
        company = CompanyInfo(
            org_number="559305-2573",
            name="Visvall bilteknik AB",
            region="Gävleborgs län"
        )

        administrator = BankruptcyAdministrator(
            name="Test Lawyer",
            law_firm="Test Law Firm"
        )

        record = BankruptcyRecord(
            company=company,
            status=BankruptcyStatus.INITIATED,
            declaration_date=datetime(2025, 12, 23),
            administrator=administrator
        )

        return record

    @pytest.fixture
    def sample_record_no_admin(self):
        """Create a sample bankruptcy record without administrator."""
        company = CompanyInfo(
            org_number="559202-2049",
            name="The Brighthouse AB",
            region="Stockholms län"
        )

        record = BankruptcyRecord(
            company=company,
            status=BankruptcyStatus.INITIATED,
            declaration_date=datetime(2025, 12, 23)
        )

        return record

    def test_extract_email_valid(self):
        """Test email extraction from HTML content."""
        enricher = BolagsverketLawyerEnricher()

        # Test valid email
        html = '<p>Contact: john.doe@lawfirm.se for more info</p>'
        email = enricher._extract_email(html)
        assert email == "john.doe@lawfirm.se"

        # Test multiple emails - should return first valid one
        html = '<p>noreply@example.com, contact@lawfirm.com</p>'
        email = enricher._extract_email(html)
        assert email == "contact@lawfirm.com"  # Skip noreply

        # Test no email
        html = '<p>No contact information available</p>'
        email = enricher._extract_email(html)
        assert email is None

    def test_extract_phone_valid(self):
        """Test phone number extraction from HTML content."""
        enricher = BolagsverketLawyerEnricher()

        # Test Swedish phone number formats
        test_cases = [
            ('<p>Tel: +46 8 123 45 67</p>', '+46 8 123 45 67'),
            ('<p>Phone: 08-123 45 67</p>', '08-123 45 67'),
            ('<p>Contact: 070-1234567</p>', '070-1234567'),
            ('<p>No phone</p>', None),
        ]

        for html, expected in test_cases:
            phone = enricher._extract_phone(html)
            if expected:
                assert phone is not None
                # Normalize whitespace for comparison
                assert phone.replace(' ', '').replace('-', '') == expected.replace(' ', '').replace('-', '')
            else:
                assert phone is None

    @pytest.mark.asyncio
    async def test_enrich_record_with_admin(self, sample_record):
        """Test enriching a record that already has an administrator."""
        enricher = BolagsverketLawyerEnricher(headless=True)

        # The administrator should already exist
        assert sample_record.administrator is not None
        assert sample_record.administrator.name == "Test Lawyer"

        # Mock the fetch method to return test data
        async def mock_fetch(org_number):
            return ("lawyer@lawfirm.se", "08-123 45 67")

        enricher._fetch_lawyer_contact = mock_fetch

        # Enrich the record
        enriched = await enricher.enrich_record(sample_record)

        # Check that contact info was added
        assert enriched.administrator.email == "lawyer@lawfirm.se"
        assert enriched.administrator.phone == "08-123 45 67"
        assert enriched.administrator.name == "Test Lawyer"  # Original name preserved

    @pytest.mark.asyncio
    async def test_enrich_record_no_admin(self, sample_record_no_admin):
        """Test enriching a record without an administrator."""
        enricher = BolagsverketLawyerEnricher(headless=True)

        # No administrator initially
        assert sample_record_no_admin.administrator is None

        # Mock the fetch method
        async def mock_fetch(org_number):
            return ("contact@firm.se", "+46 70 123 45 67")

        enricher._fetch_lawyer_contact = mock_fetch

        # Enrich the record
        enriched = await enricher.enrich_record(sample_record_no_admin)

        # Administrator should still be None (we only add contact to existing admin)
        # This is by design - we don't create admin without a name
        assert enriched.administrator is None

    @pytest.mark.asyncio
    async def test_enrich_record_no_contact_found(self, sample_record):
        """Test enriching when no contact information is found."""
        enricher = BolagsverketLawyerEnricher(headless=True)

        # Mock the fetch method to return None
        async def mock_fetch(org_number):
            return None

        enricher._fetch_lawyer_contact = mock_fetch

        original_admin = sample_record.administrator
        enriched = await enricher.enrich_record(sample_record)

        # Administrator should be unchanged
        assert enriched.administrator.email is None
        assert enriched.administrator.phone is None
        assert enriched.administrator.name == original_admin.name

    @pytest.mark.asyncio
    async def test_enrich_batch(self):
        """Test batch enrichment of multiple records."""
        enricher = BolagsverketLawyerEnricher(headless=True)

        # Create multiple test records
        records = []
        for i in range(3):
            company = CompanyInfo(
                org_number=f"55930{i}-{i}573",
                name=f"Test Company {i} AB"
            )
            admin = BankruptcyAdministrator(
                name=f"Lawyer {i}",
                law_firm="Test Firm"
            )
            record = BankruptcyRecord(
                company=company,
                administrator=admin,
                status=BankruptcyStatus.INITIATED
            )
            records.append(record)

        # Mock the fetch method to return different data for each
        call_count = 0
        async def mock_fetch(org_number):
            nonlocal call_count
            call_count += 1
            return (f"lawyer{call_count}@firm.se", f"08-12{call_count} 45 67")

        enricher._fetch_lawyer_contact = mock_fetch

        # Enrich batch
        enriched_records = await enricher.enrich_batch(records, concurrency=2)

        # Check all records were enriched
        assert len(enriched_records) == 3
        for i, record in enumerate(enriched_records):
            assert record.administrator.email is not None
            assert record.administrator.phone is not None

    @pytest.mark.asyncio
    async def test_enrich_record_preserves_existing_contact(self, sample_record):
        """Test that existing contact information is not overwritten."""
        enricher = BolagsverketLawyerEnricher(headless=True)

        # Set existing contact info
        sample_record.administrator.email = "existing@email.com"
        sample_record.administrator.phone = "existing-phone"

        # Mock the fetch method to return different data
        async def mock_fetch(org_number):
            return ("new@email.com", "new-phone")

        enricher._fetch_lawyer_contact = mock_fetch

        enriched = await enricher.enrich_record(sample_record)

        # Existing contact should be preserved (not overwritten)
        assert enriched.administrator.email == "existing@email.com"
        assert enriched.administrator.phone == "existing-phone"

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test that enricher works as async context manager."""
        async with BolagsverketLawyerEnricher(headless=True) as enricher:
            assert enricher is not None
            # Browser should be initialized
            # Note: We don't test actual browser because it requires full setup


class TestEmailExtraction:
    """Test email extraction edge cases."""

    def test_extract_various_email_formats(self):
        """Test extraction of various email formats."""
        enricher = BolagsverketLawyerEnricher()

        test_cases = [
            ('Email: simple@lawfirm.com', 'simple@lawfirm.com'),
            ('Contact: name.surname@law-firm.se', 'name.surname@law-firm.se'),
            ('Write to: info+legal@company.org', 'info+legal@company.org'),
            ('<a href="mailto:contact@firm.com">Email us</a>', 'contact@firm.com'),
            ('Multiple: first@lawyer.se, second@legal.se', 'first@lawyer.se'),
            ('Invalid email format', None),
        ]

        for html, expected in test_cases:
            result = enricher._extract_email(html)
            assert result == expected, f"Failed for: {html}"

    def test_extract_email_filters_invalid(self):
        """Test that invalid/system emails are filtered out."""
        enricher = BolagsverketLawyerEnricher()

        # Should skip noreply, example, test emails
        invalid_cases = [
            'noreply@example.com',
            'test@test.com',
            'example@example.com',
        ]

        for invalid_email in invalid_cases:
            html = f'Contact: {invalid_email}'
            result = enricher._extract_email(html)
            # Should return None or skip to next valid email
            assert result != invalid_email


class TestPhoneExtraction:
    """Test phone number extraction edge cases."""

    def test_extract_various_phone_formats(self):
        """Test extraction of various Swedish phone formats."""
        enricher = BolagsverketLawyerEnricher()

        test_cases = [
            'Tel: +46 8 123 45 67',
            'Phone: 08-123 45 67',
            'Mobile: 070-123 45 67',
            'Contact: 0771-123456',
            '+46701234567',
        ]

        for html in test_cases:
            result = enricher._extract_phone(html)
            assert result is not None, f"Failed to extract from: {html}"
            # Check that result contains digits
            assert any(c.isdigit() for c in result)


@pytest.mark.integration
class TestLawyerEnrichmentIntegration:
    """Integration tests for real Bolagsverket scraping."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Integration test - runs against live site")
    async def test_real_bolagsverket_scraping(self):
        """
        Test real scraping from Bolagsverket POIT.

        This test is skipped by default as it hits the real website.
        Run with: pytest -m integration --run-integration
        """
        enricher = BolagsverketLawyerEnricher(headless=True, timeout=60.0)

        # Use a real organization number from our data
        company = CompanyInfo(
            org_number="559305-2573",
            name="Visvall bilteknik AB"
        )

        admin = BankruptcyAdministrator(
            name="Test Administrator",
            law_firm="Test Firm"
        )

        record = BankruptcyRecord(
            company=company,
            administrator=admin,
            status=BankruptcyStatus.INITIATED,
            declaration_date=datetime(2025, 12, 23)
        )

        # Try to enrich
        async with enricher:
            enriched = await enricher.enrich_record(record)

            # Log what we found (may be None if not available)
            print(f"Email found: {enriched.administrator.email}")
            print(f"Phone found: {enriched.administrator.phone}")

            # At least check that the record is returned
            assert enriched is not None
            assert enriched.company.org_number == "559305-2573"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
