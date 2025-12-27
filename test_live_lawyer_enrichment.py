#!/usr/bin/env python3
"""
Quick test script to verify lawyer enrichment with a real organization number.

This script tests the Bolagsverket POIT scraping with a real bankruptcy case
from our database to ensure the scraping logic works correctly.
"""
import asyncio
import logging
from datetime import datetime

from src.models import BankruptcyRecord, CompanyInfo, BankruptcyAdministrator, BankruptcyStatus
from src.lawyer_enrichment import BolagsverketLawyerEnricher

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


async def test_real_enrichment():
    """Test lawyer enrichment with a real organization number."""

    print("=" * 60)
    print("Testing Lawyer Contact Enrichment from Bolagsverket POIT")
    print("=" * 60)
    print()

    # Use a real org number from our recent scrape
    # This company had a bankruptcy declaration on 2025-12-23
    company = CompanyInfo(
        org_number="559305-2573",
        name="Visvall bilteknik AB",
        region="Gävleborgs län",
        business_type="Bilar"
    )

    admin = BankruptcyAdministrator(
        name="Unknown",  # We'll try to find this
        law_firm="Unknown"
    )

    record = BankruptcyRecord(
        company=company,
        status=BankruptcyStatus.INITIATED,
        declaration_date=datetime(2025, 12, 23),
        administrator=admin
    )

    print(f"Testing with company: {record.company.name}")
    print(f"Organization number: {record.company.org_number}")
    print()
    print("Before enrichment:")
    print(f"  Administrator name: {record.administrator.name}")
    print(f"  Email: {record.administrator.email}")
    print(f"  Phone: {record.administrator.phone}")
    print()

    # Create enricher
    enricher = BolagsverketLawyerEnricher(
        headless=False,  # Set to False to see the browser in action
        timeout=60.0,
        request_delay=2.0
    )

    print("Starting enrichment (this may take 30-60 seconds)...")
    print()

    try:
        async with enricher:
            enriched = await enricher.enrich_record(record)

            print()
            print("After enrichment:")
            print(f"  Administrator name: {enriched.administrator.name}")
            print(f"  Email: {enriched.administrator.email}")
            print(f"  Phone: {enriched.administrator.phone}")
            print()

            if enriched.administrator.email or enriched.administrator.phone:
                print("✓ Successfully enriched lawyer contact information!")
            else:
                print("⚠ No contact information found (may not be available)")
                print("  This could mean:")
                print("  - The bankruptcy announcement is not yet on Bolagsverket POIT")
                print("  - The selectors need adjustment for the current site structure")
                print("  - Contact info is not included in this particular announcement")

    except Exception as e:
        print(f"Error during enrichment: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)


if __name__ == "__main__":
    print()
    print("NOTE: This script will open a browser window to test the scraping.")
    print("The browser will navigate to poit.bolagsverket.se")
    print()
    input("Press Enter to continue...")
    print()

    asyncio.run(test_real_enrichment())
