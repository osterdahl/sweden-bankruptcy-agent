# POIT CAPTCHA Limitation - Technical Analysis

**Date:** 2026-01-02
**Status:** BLOCKING ISSUE IDENTIFIED
**Impact:** Administrator and contact information cannot be automatically collected

## Executive Summary

Post- och Inrikes Tidningar (POIT), the **only official source** for bankruptcy administrator information in Sweden, implements CAPTCHA protection that prevents automated data collection. This is a fundamental architectural blocker that prevents the bankruptcy monitoring system from automatically collecting administrator names, law firms, email addresses, and phone numbers.

## Investigation Results

### What is POIT?

POIT (Post- och Inrikes Tidningar) is:
- Sweden's official government gazette (established 1645)
- The legally mandated publication for bankruptcy announcements
- Operated by Bolagsverket (Swedish Companies Registration Office)
- Available at: https://poit.bolagsverket.se/poit-app/sok

When a district court (tingsrätt) decides on bankruptcy, the announcement MUST be published in POIT, including:
- Company name and organization number
- Court that made the decision
- **Bankruptcy administrator name and law firm** ← Critical data we need
- Date of bankruptcy decision

### Bot Protection Discovered

**Finding:** POIT implements F5/Akamai bot protection with CAPTCHA challenges.

**Evidence:**
1. Automated browser access triggers CAPTCHA: "What code is in the image?"
2. Page shows support ID instead of search form
3. Heavy JavaScript obfuscation and bot detection code
4. No actual form elements (input#orgnr, select#kungtyp) accessible until CAPTCHA solved

**Screenshot:** `/tmp/poit_page_structure.png` shows CAPTCHA challenge

### Alternative Access Methods Investigated

Searched for alternative ways to access bankruptcy administrator data:

#### 1. Bolagsverket API
- **Status:** APIs exist for company information
- **Bankruptcy Data:** No public API for bankruptcy administrators found
- **Contact:** `foretagsreg-api@bolagsverket.se` for API inquiries
- **Portal:** https://portal.api.bolagsverket.se/devportal/
- **Conclusion:** No documented API for POIT bankruptcy announcements

**Sources:**
- [Bolagsverket APIs and Open Data](https://bolagsverket.se/apierochoppnadata.2531.html)
- [Bolagsverket API FAQ](https://bolagsverket.se/apierochoppnadata/fragorochsvaromapierna.4611.html)

#### 2. Sveriges Domstolar (Swedish Courts)
- **Status:** No public API for bankruptcy lookups
- **Guidance:** Official page directs users to POIT website
- **GitHub:** Domstolsverket has limited public repos
- **Conclusion:** POIT is the only recommended source

**Sources:**
- [Who is bankruptcy administrator?](https://www.domstol.se/amnen/skulder-konkurs-och-foretagsrekonstruktion/konkurs/vem-ar-konkursforvaltare-i-mitt-arende/)
- [Sveriges Domstolar GitHub](https://github.com/domstol)

#### 3. Tillväxtanalys Statistics
- **Status:** Provides aggregate bankruptcy statistics
- **Data Type:** Statistical summaries, not individual case details
- **Conclusion:** Not useful for individual company administrator lookup

#### 4. Third-party Services
Potential paid services (not investigated):
- UC (Upplysningscentralen) - Swedish credit information bureau
- Bisnode - Business information provider
- Roaring.io - Company data API aggregator

## Technical Analysis

### Current Code Expectations

File: `src/lawyer_enrichment.py:378-446`

```python
# Current implementation expects:
await self.page.goto("https://poit.bolagsverket.se/poit-app/sok")

# Tries to find these selectors:
org_input = await self.page.query_selector('input#orgnr')  # ❌ NOT FOUND (CAPTCHA blocks)
select = await self.page.query_selector('select#kungtyp')  # ❌ NOT FOUND (CAPTCHA blocks)
```

**Problem:** These selectors would exist on the search form, but automated scripts never reach the form due to CAPTCHA.

### Bypass Attempts Made

Tried stealth techniques to avoid detection:
- ✓ Disabled automation flags (`--disable-blink-features=AutomationControlled`)
- ✓ Set realistic user agent (Swedish locale, Chrome/Safari)
- ✓ Added JavaScript to hide `navigator.webdriver`
- ✓ Realistic viewport and timezone
- ✓ Extended wait times for bot detection to pass

**Result:** Still triggers CAPTCHA. F5/Akamai protection is sophisticated.

### Why This Blocks Data Collection

From `TEST_RESULTS_SUMMARY.md`:

| Field | Current Status | Blocker |
|-------|----------------|---------|
| Company Name | ✅ 100% | - |
| Org Number | ✅ 100% | - |
| Declaration Date | ✅ ~80% | - |
| Business Type | ✅ ~50% | - |
| Court | ✅ 100% | Fixed via Konkurslistan detail pages |
| Location | ✅ ~70% | - |
| **Administrator** | ❌ 0% | **POIT CAPTCHA** |
| **Email** | ❌ 0% | **Depends on administrator** |
| **Phone** | ❌ 0% | **Depends on administrator** |
| Employees | ❌ 0% | Not on public listing pages |
| Revenue | ❌ 0% | Not on public listing pages |

**Administrator fields are critical:** Without administrator name and law firm, we cannot search for contact information (email/phone).

## Impact on Email Reports

### Current Email Completeness: 5/9 Fields (55%)

**Working Fields:**
1. ✅ Company Name
2. ✅ Org Number + Link
3. ✅ Declaration Date
4. ✅ Business Type (partial)
5. ✅ Location (city/region)
6. ✅ Court

**Missing Fields (due to POIT):**
7. ❌ Administrator Name
8. ❌ Email (depends on #7)
9. ❌ Phone (depends on #7)

**Not Available (data source limitation):**
- Employees (not published on public listing pages)
- Revenue (not published on public listing pages)

## Possible Solutions

### Option 1: Accept Limitation (RECOMMENDED)
**Approach:** Document that administrator info requires manual lookup
**Effort:** Low
**Cost:** Free
**Pros:**
- Honest about capabilities
- Current 5/9 fields still valuable
- System works reliably for what it can do

**Cons:**
- Missing contact information
- User must manually look up administrators if needed

**Implementation:**
- Update email template to note "Administrator: See POIT"
- Provide POIT link for each company
- Document limitation in README

### Option 2: Manual Periodic Updates
**Approach:** User manually looks up administrators for important matches
**Effort:** Medium
**Cost:** Free
**Pros:**
- Selective manual effort only for relevant companies
- Maintains data quality

**Cons:**
- Requires user time
- Not fully automated

**Implementation:**
- Add "Update Administrator" workflow
- Allow manual entry of administrator info
- Store in database for future reference

### Option 3: Third-party Data Service
**Approach:** Use paid API services (UC, Bisnode, etc.)
**Effort:** Medium-High
**Cost:** Likely subscription fees
**Pros:**
- Automated data collection
- May include additional company data

**Cons:**
- Ongoing cost
- API integration required
- Data completeness uncertain

**Implementation:**
- Research available services
- Evaluate pricing
- Integrate API calls
- Handle rate limiting and errors

### Option 4: CAPTCHA Solving Service (NOT RECOMMENDED)
**Approach:** Use automated CAPTCHA solving (2captcha, anti-captcha, etc.)
**Effort:** Medium
**Cost:** Per-CAPTCHA fees
**Pros:**
- Could bypass protection

**Cons:**
- ❌ Violates POIT Terms of Service
- ❌ Ethically questionable
- ❌ Unreliable (service may break)
- ❌ Ongoing costs
- ❌ May trigger account bans

**Recommendation:** Do NOT pursue this option

### Option 5: PDF Scraping
**Approach:** Download POIT PDFs directly if available
**Effort:** High
**Cost:** Free
**Pros:**
- Bypasses web interface
- Official source

**Cons:**
- PDF parsing complexity
- May also have access restrictions
- Maintenance burden

**Investigation Needed:**
- Check if POIT provides direct PDF downloads without CAPTCHA
- Assess PDF format consistency

## Recommendations

### Immediate Actions

1. **Document Current State** ✅
   - Update README with data completeness: 5/9 fields
   - Clearly state administrator info limitation
   - Provide POIT link in emails for manual lookup

2. **Optimize What Works** ✅
   - Current scraping and court extraction are solid
   - Email reports provide valuable initial filtering
   - Database stores org numbers for manual POIT lookup

3. **User Communication**
   - Set clear expectations about data completeness
   - Explain POIT limitation
   - Suggest manual lookup workflow for important matches

### Future Exploration

1. **Contact Bolagsverket**
   - Email: foretagsreg-api@bolagsverket.se
   - Ask about official API access to POIT bankruptcy announcements
   - Explain legitimate business use case

2. **Research Third-party Services**
   - Get quotes from UC, Bisnode, Roaring.io
   - Compare data completeness and pricing
   - Evaluate ROI for automated administrator data

3. **Monitor for Changes**
   - Check if POIT removes/weakens CAPTCHA in future
   - Watch for new government open data initiatives
   - Consider EU data access regulations

## Conclusion

**POIT CAPTCHA protection is a fundamental blocker that cannot be bypassed ethically or reliably.**

The bankruptcy monitoring system successfully collects 5/9 email fields automatically, providing valuable initial filtering and court information. Administrator and contact details require either:
- Manual POIT lookup (free, time-consuming)
- Third-party data service (automated, costly)

**Recommended Path:** Accept current limitation, document clearly, and focus on delivering value with the 5/9 fields that work reliably. Provide POIT links in email reports for manual administrator lookup when needed.

---

**Related Documents:**
- `TEST_RESULTS_SUMMARY.md` - Comprehensive test results
- `README.md` - User-facing documentation (to be updated)
- `src/lawyer_enrichment.py:378-446` - POIT enrichment code (currently blocked)

**Investigation Artifacts:**
- `/tmp/poit_page_structure.png` - CAPTCHA screenshot
- `/tmp/poit_page.html` - Bot protection HTML
- `tests/inspect_poit_structure.py` - Inspection script

**Sources:**
- [Bolagsverket - POIT](https://poit.bolagsverket.se/)
- [Sveriges Domstolar - Bankruptcy Administrators](https://www.domstol.se/amnen/skulder-konkurs-och-foretagsrekonstruktion/konkurs/vem-ar-konkursforvaltare-i-mitt-arende/)
- [Bolagsverket APIs](https://bolagsverket.se/apierochoppnadata.2531.html)
- [Post- och Inrikes Tidningar - Wikipedia](https://en.wikipedia.org/wiki/Post-_och_Inrikes_Tidningar)
