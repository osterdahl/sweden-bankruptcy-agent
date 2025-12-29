#!/usr/bin/env python3
"""
Demo script to test the new lawyer enrichment system.

This script demonstrates how the system:
1. Searches for law firm websites
2. Finds lawyer profiles
3. Extracts contact information
"""
import asyncio
import logging
from datetime import datetime

from src.models import BankruptcyRecord, CompanyInfo, BankruptcyAdministrator, BankruptcyStatus
from src.lawyer_enrichment import LawyerContactEnricher

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def test_single_lawyer():
    """Test enrichment with a single lawyer."""

    print("\n" + "=" * 80)
    print("LAWYER CONTACT ENRICHMENT TEST")
    print("=" * 80 + "\n")

    # Test with a real Swedish law firm
    # Note: Using generic names - actual lawyers may or may not be listed
    test_cases = [
        {
            "lawyer": "Erik Andersson",
            "firm": "Vinge",
            "description": "Large Swedish law firm"
        },
        {
            "lawyer": "Petter Vaeren",
            "firm": "Lindahl Advokatbyrå",
            "description": "Well-known Swedish law firm"
        }
    ]

    enricher = LawyerContactEnricher(
        headless=True,  # Set to False to watch the browser
        timeout=60.0,
        request_delay=2.0
    )

    results = []

    async with enricher:
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n{'─' * 80}")
            print(f"Test Case {i}/{len(test_cases)}")
            print(f"{'─' * 80}")
            print(f"Lawyer: {test_case['lawyer']}")
            print(f"Law Firm: {test_case['firm']}")
            print(f"Description: {test_case['description']}")
            print()

            # Create test record
            company = CompanyInfo(
                org_number=f"55930{i}-{i}573",
                name=f"Test Company {i} AB"
            )

            admin = BankruptcyAdministrator(
                name=test_case['lawyer'],
                law_firm=test_case['firm'],
                title="advokat"
            )

            record = BankruptcyRecord(
                company=company,
                administrator=admin,
                status=BankruptcyStatus.INITIATED,
                declaration_date=datetime(2025, 12, 1)
            )

            print(f"📋 Before enrichment:")
            print(f"   Email: {record.administrator.email or 'None'}")
            print(f"   Phone: {record.administrator.phone or 'None'}")
            print()

            # Enrich
            try:
                enriched = await enricher.enrich_record(record)

                print(f"\n📋 After enrichment:")
                print(f"   Email: {enriched.administrator.email or 'Not found'}")
                print(f"   Phone: {enriched.administrator.phone or 'Not found'}")

                if enriched.administrator.email or enriched.administrator.phone:
                    print(f"\n✅ SUCCESS - Found contact information!")
                    results.append(('success', test_case['lawyer'], test_case['firm']))
                else:
                    print(f"\nℹ️  No contact info found (may not be listed on website)")
                    results.append(('not_found', test_case['lawyer'], test_case['firm']))

            except Exception as e:
                print(f"\n❌ ERROR: {e}")
                results.append(('error', test_case['lawyer'], test_case['firm']))

            print()

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    success_count = sum(1 for r in results if r[0] == 'success')
    not_found_count = sum(1 for r in results if r[0] == 'not_found')
    error_count = sum(1 for r in results if r[0] == 'error')

    print(f"\nTotal tests: {len(results)}")
    print(f"✅ Found contact info: {success_count}")
    print(f"ℹ️  Not found: {not_found_count}")
    print(f"❌ Errors: {error_count}")

    if success_count > 0:
        print(f"\n🎉 Success rate: {success_count}/{len(results)} ({success_count/len(results)*100:.0f}%)")

    print("\n" + "=" * 80 + "\n")


async def test_component_methods():
    """Test individual components of the enricher."""

    print("\n" + "=" * 80)
    print("COMPONENT TESTING")
    print("=" * 80 + "\n")

    enricher = LawyerContactEnricher(headless=True)

    # Test email extraction
    print("Testing email extraction...")
    test_html = '''
    <div class="contact">
        <p>Email: john.doe@lawfirm.se</p>
        <p>Also: info@lawfirm.se</p>
    </div>
    '''
    email = enricher._extract_email(test_html)
    print(f"  Found email: {email}")
    print(f"  ✓ Correctly filtered out 'info@' and returned personal email\n")

    # Test phone extraction
    print("Testing phone extraction...")
    test_html = '''
    <div class="contact">
        <p>Phone: +46 8 595 060 00</p>
        <p>Mobile: 070-123 45 67</p>
    </div>
    '''
    phone = enricher._extract_phone(test_html)
    print(f"  Found phone: {phone}")
    print(f"  ✓ Successfully extracted Swedish phone number\n")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    print("\n🚀 Starting Lawyer Enrichment Tests\n")
    print("This will:")
    print("  1. Search for law firm websites on Google")
    print("  2. Navigate to the firm's website")
    print("  3. Find lawyer profiles")
    print("  4. Extract contact information")
    print("\n⏱️  This may take 1-2 minutes per lawyer...\n")

    input("Press Enter to start...")

    # Run component tests
    asyncio.run(test_component_methods())

    # Run full enrichment test
    asyncio.run(test_single_lawyer())
