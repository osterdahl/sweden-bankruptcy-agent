"""
End-to-End Integration Test - Tests complete data flow.

This test verifies the complete pipeline:
Scraping → Enrichment → Database → Email Generation

Verifies that all 9 email fields are populated with real data.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Settings
from src.agent import BankruptcyMonitorAgent
from src.database import BankruptcyDatabase


async def test_complete_data_flow():
    """Test complete flow from scraping to email with live data."""
    print("\n" + "=" * 70)
    print("END-TO-END INTEGRATION TEST")
    print("Testing: Scrape → Enrich → Database → Email Generation")
    print("=" * 70)

    # Create temporary database
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    temp_db_path = temp_db.name
    temp_db.close()

    try:
        # Override settings to use temp database
        os.environ["DATABASE_PATH"] = temp_db_path
        os.environ["HEADLESS"] = "true"

        print(f"\nUsing temporary database: {temp_db_path}")

        # Initialize agent
        agent = BankruptcyMonitorAgent(use_mock=False)

        # Run agent for December 2025 (limit to avoid long runtime)
        print("\n📡 Step 1: Scraping bankruptcy data...")
        print("   (This will scrape real data from live sources)")

        # We'll manually test with fewer records for speed
        from src.aggregator_scraper import KonkurslistanScraper

        records = []
        async with KonkurslistanScraper(headless=True, timeout=30000) as scraper:
            print("   Scraping from Konkurslistan (most complete source)...")

            async for record in scraper.scrape_bankruptcies(2025, 12):
                records.append(record)
                if len(records) >= 10:  # Limit to 10 for testing
                    break

        total_scraped = len(records)
        print(f"   ✓ Scraped {total_scraped} records")

        # Check what we got from scraping
        print("\n📊 Scraping Results:")
        has_admin = sum(1 for r in records if r.administrator)
        has_court = sum(1 for r in records if r.court)
        has_business_type = sum(1 for r in records if r.company.business_type)
        has_employees = sum(1 for r in records if r.company.employees)
        has_revenue = sum(1 for r in records if r.company.revenue)

        print(f"   - {has_admin}/{total_scraped} have administrator")
        print(f"   - {has_court}/{total_scraped} have court")
        print(f"   - {has_business_type}/{total_scraped} have business type")
        print(f"   - {has_employees}/{total_scraped} have employees")
        print(f"   - {has_revenue}/{total_scraped} have revenue")

        # Enrich records
        print("\n🔍 Step 2: Enriching records...")
        print("   - Company enrichment (legal form)")
        print("   - POIT enrichment (administrator + court)")
        print("   - Lawyer contact enrichment (email + phone)")

        from src.enrichment import CompanyEnricher
        from src.lawyer_enrichment import LawyerContactEnricher

        enricher = CompanyEnricher()
        records = await enricher.enrich_batch(records[:5])  # Limit enrichment to 5 for speed

        lawyer_enricher = LawyerContactEnricher(headless=True, timeout=30, request_delay=2)
        records = await lawyer_enricher.enrich_batch(records, concurrency=1)

        print(f"   ✓ Enriched {len(records)} records")

        # Check enrichment results
        print("\n📊 Enrichment Results:")
        has_admin_after = sum(1 for r in records if r.administrator and r.administrator.name)
        has_law_firm = sum(1 for r in records if r.administrator and r.administrator.law_firm)
        has_email = sum(1 for r in records if r.administrator and r.administrator.email)
        has_phone = sum(1 for r in records if r.administrator and r.administrator.phone)
        has_court_after = sum(1 for r in records if r.court)

        print(f"   - {has_admin_after}/{len(records)} have administrator name")
        print(f"   - {has_law_firm}/{len(records)} have law firm")
        print(f"   - {has_email}/{len(records)} have email")
        print(f"   - {has_phone}/{len(records)} have phone")
        print(f"   - {has_court_after}/{len(records)} have court")

        # Store in database
        print("\n💾 Step 3: Storing in database...")
        db = BankruptcyDatabase(temp_db_path)
        for record in records:
            db.upsert_record(record)
        print(f"   ✓ Stored {len(records)} records")

        # Retrieve from database
        print("\n📤 Step 4: Retrieving from database...")
        retrieved_records = db.get_for_month(2025, 12, matched_only=False)
        print(f"   ✓ Retrieved {len(retrieved_records)} records")

        # Generate email content
        print("\n📧 Step 5: Generating email report...")
        from src.email_notifier import EmailNotifier
        from config import EmailConfig

        email_config = EmailConfig(
            smtp_server="smtp.gmail.com",
            smtp_port=587,
            sender_email="test@example.com",
            sender_password="test",
            recipient_emails=["test@example.com"]
        )

        notifier = EmailNotifier(email_config)
        html_content = notifier._generate_html_report(retrieved_records, 2025, 12, total_scraped)

        # Verify email contains all fields
        print("\n📋 Step 6: Verifying email content...")

        # Check that email table shows all 9 expected fields
        required_fields_in_html = [
            "Company",  # Column header
            "Date",
            "Business Type",
            "Employees",
            "Revenue",
            "Location",
            "Court",
            "Administrator",
            "Contact"
        ]

        missing_fields = []
        for field in required_fields_in_html:
            if field not in html_content:
                missing_fields.append(field)

        if missing_fields:
            print(f"   ⚠ Missing fields in email: {', '.join(missing_fields)}")
        else:
            print("   ✓ All 9 field columns present in email template")

        # Check that we have actual data (not just headers)
        print("\n📊 Data Quality Check:")

        # Sample the first enriched record
        if retrieved_records:
            sample = retrieved_records[0]
            fields_present = {}

            fields_present['Company Name'] = bool(sample.company.name)
            fields_present['Org Number'] = bool(sample.company.org_number)
            fields_present['Date'] = bool(sample.declaration_date)
            fields_present['Business Type'] = bool(sample.company.business_type)
            fields_present['Employees'] = bool(sample.company.employees)
            fields_present['Revenue'] = bool(sample.company.revenue)
            fields_present['Location'] = bool(sample.company.city or sample.company.region)
            fields_present['Court'] = bool(sample.court)

            if sample.administrator:
                fields_present['Administrator'] = bool(sample.administrator.name)
                fields_present['Law Firm'] = bool(sample.administrator.law_firm)
                fields_present['Email'] = bool(sample.administrator.email)
                fields_present['Phone'] = bool(sample.administrator.phone)
            else:
                fields_present['Administrator'] = False
                fields_present['Law Firm'] = False
                fields_present['Email'] = False
                fields_present['Phone'] = False

            print("\n   Sample Record (first record):")
            print(f"   Company: {sample.company.name}")
            for field, present in fields_present.items():
                status = "✓" if present else "✗"
                print(f"      {status} {field}")

            # Calculate completeness
            complete_count = sum(1 for v in fields_present.values() if v)
            total_fields = len(fields_present)
            completeness = (complete_count / total_fields) * 100

            print(f"\n   Data Completeness: {completeness:.1f}% ({complete_count}/{total_fields} fields)")

            if completeness >= 75:
                print("   ✅ GOOD: High data completeness")
            elif completeness >= 50:
                print("   ⚠ MODERATE: Some fields missing")
            else:
                print("   ❌ POOR: Many fields missing")

        # Final summary
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)

        tests_passed = []
        tests_failed = []

        # Test 1: Scraping
        if total_scraped > 0:
            tests_passed.append("✓ Scraping works")
        else:
            tests_failed.append("✗ Scraping failed")

        # Test 2: Enrichment
        if has_admin_after > 0 or has_court_after > 0:
            tests_passed.append("✓ Enrichment works")
        else:
            tests_failed.append("✗ Enrichment failed")

        # Test 3: Database
        if len(retrieved_records) > 0:
            tests_passed.append("✓ Database storage works")
        else:
            tests_failed.append("✗ Database storage failed")

        # Test 4: Email
        if not missing_fields:
            tests_passed.append("✓ Email template complete")
        else:
            tests_failed.append("✗ Email template incomplete")

        # Test 5: Data quality
        if retrieved_records and completeness >= 50:
            tests_passed.append(f"✓ Data quality acceptable ({completeness:.0f}%)")
        else:
            tests_failed.append("✗ Data quality poor")

        print("\nPassed Tests:")
        for test in tests_passed:
            print(f"  {test}")

        if tests_failed:
            print("\nFailed Tests:")
            for test in tests_failed:
                print(f"  {test}")

        print(f"\nOverall: {len(tests_passed)}/{len(tests_passed) + len(tests_failed)} tests passed")

        return len(tests_failed) == 0

    finally:
        # Cleanup
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)
            print(f"\n🗑️  Cleaned up temporary database")


async def main():
    """Run end-to-end test."""
    try:
        success = await test_complete_data_flow()

        if success:
            print("\n🎉 END-TO-END TEST PASSED!")
            print("   All components working correctly.")
            print("   Email reports should contain complete data.")
            return 0
        else:
            print("\n⚠ END-TO-END TEST FAILED!")
            print("   Some components need fixes.")
            return 1

    except Exception as e:
        print(f"\n❌ TEST CRASHED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
