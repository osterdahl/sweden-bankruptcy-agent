"""
Live scraper test - Tests individual scrapers with real data.

This test verifies that scrapers can collect data from live sources without errors.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.aggregator_scraper import AllabolagScraper, KonkurslistanScraper, BolagsfaktaScraper


async def test_konkurslistan_scraper():
    """Test Konkurslistan scraper - should collect the most complete data."""
    print("\n=== Testing Konkurslistan Scraper ===")

    async with KonkurslistanScraper(headless=True, timeout=30000) as scraper:
        count = 0
        admin_found = 0
        court_found = 0

        print("Scraping up to 5 records from Konkurslistan...")
        async for record in scraper.scrape_bankruptcies(2025, 12):
            count += 1

            # Verify basic fields
            assert record.company.name, "Missing company name"
            assert record.company.org_number, "Missing org number"

            print(f"\n[{count}] {record.company.name}")
            print(f"    Org: {record.company.org_number}")
            print(f"    City: {record.company.city or 'N/A'}")
            print(f"    Business: {record.company.business_type or 'N/A'}")
            print(f"    Employees: {record.company.employees or 'N/A'}")
            print(f"    Revenue: {record.company.revenue or 'N/A'}")
            print(f"    Court: {record.court or 'N/A'}")

            if record.administrator:
                admin_found += 1
                print(f"    Admin: {record.administrator.name}")
                if record.administrator.law_firm:
                    print(f"    Firm: {record.administrator.law_firm}")

            if record.court:
                court_found += 1

            if count >= 5:
                break

        print(f"\n✓ Successfully scraped {count} records")
        print(f"  - {admin_found}/{count} have administrator info")
        print(f"  - {court_found}/{count} have court info")

        # At least some should have administrator and court (from detail pages)
        if admin_found == 0:
            print("⚠ WARNING: No administrator info found - detail page enrichment may have failed")
        if court_found == 0:
            print("⚠ WARNING: No court info found - detail page enrichment may have failed")

        return count > 0


async def test_allabolag_scraper():
    """Test Allabolag scraper - should collect basic data."""
    print("\n\n=== Testing Allabolag Scraper ===")

    async with AllabolagScraper(headless=True, timeout=30000) as scraper:
        count = 0

        print("Scraping up to 5 records from Allabolag...")
        async for record in scraper.scrape_bankruptcies(2025, 12):
            count += 1

            assert record.company.name, "Missing company name"
            assert record.company.org_number, "Missing org number"

            print(f"\n[{count}] {record.company.name}")
            print(f"    Org: {record.company.org_number}")
            print(f"    Region: {record.company.region or 'N/A'}")
            print(f"    Business: {record.company.business_type or 'N/A'}")

            if count >= 5:
                break

        print(f"\n✓ Successfully scraped {count} records")
        return count > 0


async def test_bolagsfakta_scraper():
    """Test Bolagsfakta scraper - should collect minimal data."""
    print("\n\n=== Testing Bolagsfakta Scraper ===")

    async with BolagsfaktaScraper(headless=True, timeout=30000) as scraper:
        count = 0

        print("Scraping up to 5 records from Bolagsfakta...")
        async for record in scraper.scrape_bankruptcies(2025, 12):
            count += 1

            assert record.company.name, "Missing company name"
            assert record.company.org_number, "Missing org number"

            print(f"\n[{count}] {record.company.name}")
            print(f"    Org: {record.company.org_number}")
            print(f"    Date: {record.declaration_date or 'N/A'}")

            if count >= 5:
                break

        print(f"\n✓ Successfully scraped {count} records")
        return count > 0


async def main():
    """Run all scraper tests."""
    print("=" * 60)
    print("LIVE SCRAPER COMPONENT TEST")
    print("Testing scrapers with real bankruptcy data from Dec 2025")
    print("=" * 60)

    results = {
        "Konkurslistan": False,
        "Allabolag": False,
        "Bolagsfakta": False,
    }

    try:
        # Test Konkurslistan (most important - has detail pages)
        results["Konkurslistan"] = await test_konkurslistan_scraper()
    except Exception as e:
        print(f"\n❌ Konkurslistan test FAILED: {e}")

    try:
        # Test Allabolag
        results["Allabolag"] = await test_allabolag_scraper()
    except Exception as e:
        print(f"\n❌ Allabolag test FAILED: {e}")

    try:
        # Test Bolagsfakta
        results["Bolagsfakta"] = await test_bolagsfakta_scraper()
    except Exception as e:
        print(f"\n❌ Bolagsfakta test FAILED: {e}")

    # Summary
    print("\n\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for scraper, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{scraper:15} {status}")

    print(f"\nPassed: {passed}/{total}")

    if passed == total:
        print("\n🎉 All scraper tests PASSED!")
        return 0
    else:
        print("\n⚠ Some scraper tests FAILED!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
