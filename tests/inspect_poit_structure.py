"""
Script to inspect actual POIT website structure.
Identifies correct selectors for form elements.
"""

import asyncio
from playwright.async_api import async_playwright


async def inspect_poit_structure():
    """Inspect POIT website to find correct selectors."""
    print("=" * 70)
    print("INSPECTING POIT WEBSITE STRUCTURE")
    print("=" * 70)

    async with async_playwright() as p:
        # Launch with stealth settings to avoid bot detection
        browser = await p.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='sv-SE',
            timezone_id='Europe/Stockholm',
            extra_http_headers={
                'Accept-Language': 'sv-SE,sv;q=0.9,en;q=0.8',
            }
        )

        # Remove automation indicators
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {}
            };
        """)

        page = await context.new_page()

        try:
            print("\n📡 Navigating to POIT search page...")
            await page.goto('https://poit.bolagsverket.se/poit-app/sok', timeout=60000)

            print("⏳ Waiting for page to load (bypassing bot detection)...")
            await asyncio.sleep(5)  # Wait for bot detection to complete

            # Check if we hit bot protection
            page_content = await page.content()
            if 'Please enable JavaScript' in page_content or 'support ID' in page_content:
                print("⚠️  Bot protection detected, waiting longer...")
                await asyncio.sleep(10)
                await page.reload()
                await asyncio.sleep(5)

            await page.wait_for_load_state('networkidle', timeout=60000)
            await asyncio.sleep(3)  # Extra time for JavaScript to render

            print("\n🔍 Inspecting form elements...\n")

            # Try to find org number input by various selectors
            print("Looking for Organization Number Input:")
            org_selectors = [
                'input#orgnr',
                'input[name="orgnr"]',
                'input[placeholder*="organisationsnummer"]',
                'input[type="text"]',
                'input[aria-label*="organisationsnummer"]',
            ]

            for selector in org_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        attrs = await element.evaluate('''el => ({
                            id: el.id,
                            name: el.name,
                            type: el.type,
                            placeholder: el.placeholder,
                            className: el.className,
                            ariaLabel: el.getAttribute('aria-label')
                        })''')
                        print(f"  ✓ FOUND with selector: {selector}")
                        print(f"    Attributes: {attrs}")
                except Exception as e:
                    print(f"  ✗ Not found: {selector}")

            # Try to find announcement type select
            print("\nLooking for Announcement Type Select/Dropdown:")
            select_selectors = [
                'select#kungtyp',
                'select[name="kungtyp"]',
                'select[aria-label*="typ"]',
                'select',
                'div[role="combobox"]',
                'input[role="combobox"]',
            ]

            for selector in select_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        attrs = await element.evaluate('''el => ({
                            tagName: el.tagName,
                            id: el.id,
                            name: el.name,
                            className: el.className,
                            role: el.getAttribute('role'),
                            ariaLabel: el.getAttribute('aria-label')
                        })''')
                        print(f"  ✓ FOUND with selector: {selector}")
                        print(f"    Attributes: {attrs}")
                except Exception as e:
                    print(f"  ✗ Not found: {selector}")

            # Get all input elements
            print("\nAll INPUT elements on page:")
            inputs = await page.query_selector_all('input')
            for i, input_el in enumerate(inputs[:10]):  # Limit to first 10
                attrs = await input_el.evaluate('''el => ({
                    id: el.id,
                    name: el.name,
                    type: el.type,
                    placeholder: el.placeholder,
                    className: el.className
                })''')
                if attrs['type'] in ['text', 'search', 'tel']:  # Likely candidates
                    print(f"  [{i+1}] {attrs}")

            # Get all select elements
            print("\nAll SELECT elements on page:")
            selects = await page.query_selector_all('select')
            for i, select_el in enumerate(selects):
                attrs = await select_el.evaluate('''el => ({
                    id: el.id,
                    name: el.name,
                    className: el.className
                })''')
                print(f"  [{i+1}] {attrs}")

            # Check for custom dropdowns (divs with role="combobox")
            print("\nCustom dropdowns (role=combobox):")
            comboboxes = await page.query_selector_all('[role="combobox"]')
            for i, combo in enumerate(comboboxes):
                attrs = await combo.evaluate('''el => ({
                    tagName: el.tagName,
                    id: el.id,
                    className: el.className,
                    ariaLabel: el.getAttribute('aria-label')
                })''')
                print(f"  [{i+1}] {attrs}")

            # Take a screenshot for manual inspection
            screenshot_path = '/tmp/poit_page_structure.png'
            await page.screenshot(path=screenshot_path, full_page=True)
            print(f"\n📸 Screenshot saved to: {screenshot_path}")

            # Get the full page HTML for inspection
            html_content = await page.content()
            html_path = '/tmp/poit_page.html'
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f"📄 HTML saved to: {html_path}")

            print("\n✅ Inspection complete!")
            print("   Review the output above to identify correct selectors.")

        except Exception as e:
            print(f"\n❌ Error during inspection: {e}")
            import traceback
            traceback.print_exc()

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(inspect_poit_structure())
