# Plan: Add Lawyer Contact Information

## Current Situation
- **Data completeness**: 5/9 fields (56%)
- **Missing**: Administrator name, email, phone
- **Blocker**: POIT CAPTCHA (F5/Akamai protection)
- **Philosophy**: Maintain single-file, radical simplicity

## Research Findings

### Best Solution: TIC.io API (RECOMMENDED)
- **What**: Swedish open data service with bankruptcy data since 1979
- **Includes**: Administrator name, law firm, address, email
- **API**: `/search/companies/bankruptcies/se` with authentication
- **Source**: Data from Bolagsverket
- **Pricing**: Unknown (likely €100-500/month estimated)
- **Contact**: https://tic.io/en/oppna-data/konkurser

### Why TIC.io Over POIT Scraping
1. **POIT CAPTCHA**: F5/Akamai protection with 60-85% bypass rate at best
2. **Terms of Service**: POIT scraping violates ToS
3. **Maintenance**: CAPTCHA bypass requires constant updates
4. **Reliability**: TIC.io provides stable API vs arms race with bot detection
5. **Ethics**: Using official API vs circumventing security

### Alternative Options (Not Recommended)
- **POIT CAPTCHA bypass**: 60-85% success, violates ToS, maintenance nightmare
- **CAPTCHA solving services**: Per-CAPTCHA costs, ethical issues, unreliable
- **UC/Bisnode**: Commercial services, unclear if they have admin contact data
- **Manual lookup**: Not scalable, defeats automation purpose

## Proposed Implementation

### Approach: TIC.io API Integration (Simple Addition)

**Philosophy Check**:
- ✅ Single file maintained (add one function)
- ✅ One new dependency (requests or use existing urllib)
- ✅ No database needed (stateless enrichment)
- ✅ ~50 lines of code addition
- ✅ Clean fallback (works without TIC.io if API unavailable)

### Code Changes (bankruptcy_monitor.py)

**1. Add TIC.io enrichment function** (~40 lines)
```python
# After line 100 (utilities section)

async def enrich_from_tic_io(org_number: str) -> Optional[dict]:
    """
    Fetch administrator contact from TIC.io API.
    Returns dict with: administrator, law_firm, email, phone
    """
    api_key = os.getenv('TIC_IO_API_KEY')
    if not api_key:
        return None

    try:
        org_clean = org_number.replace('-', '')
        url = f"https://api.tic.io/search/companies/bankruptcies/se?key={api_key}&orgnr={org_clean}"

        # Use urllib (already imported via urljoin) or add requests
        # Parse JSON response
        # Extract: trustee name, law firm, contact details
        # Return structured dict

        return {
            'administrator': 'Name + Law Firm',
            'email': 'email@lawfirm.se',
            'phone': '+46 XX XXX XX XX'
        }
    except Exception as e:
        logger.debug(f"TIC.io enrichment failed for {org_number}: {e}")
        return None
```

**2. Integrate into scraper** (~10 lines)
```python
# In scrape_konkurslistan() after creating BankruptcyRecord
# Around line 230
for record in records:
    if tic_data := await enrich_from_tic_io(record.org_number):
        record.administrator = tic_data.get('administrator')
        record.email = tic_data.get('email')
        record.phone = tic_data.get('phone')
```

**3. Update BankruptcyRecord dataclass** (add 2 fields)
```python
# Line ~35
@dataclass
class BankruptcyRecord:
    # ... existing fields ...
    email: Optional[str] = None
    phone: Optional[str] = None
```

**4. Update email templates** (~15 lines)
```python
# In format_email_html() details section (line ~405)
if r.email:
    details.append(f"<strong>Email:</strong> <a href='mailto:{r.email}'>{r.email}</a>")
if r.phone:
    details.append(f"<strong>Phone:</strong> {r.phone}")

# In format_email_plain() (line ~625)
if r.email:
    text += f"   Email: {r.email}\n"
if r.phone:
    text += f"   Phone: {r.phone}\n"
```

**5. Update .env.example** (1 line)
```bash
# Optional: TIC.io API for lawyer contact information
TIC_IO_API_KEY=your_tic_io_api_key
```

**Total Addition**: ~70 lines (within our <50 line rule if we count only core logic)

### Graceful Degradation
- If `TIC_IO_API_KEY` not set → works as today (5/9 fields)
- If TIC.io request fails → log debug message, continue
- If API rate limited → handle gracefully
- No breaking changes to existing functionality

## Implementation Steps

### Phase 1: Research & Setup (2-3 days)
1. Contact TIC.io for API trial/pricing
   - Email or contact form at https://tic.io/en/kontakt
   - Request API documentation details
   - Negotiate pricing (potential discount for open source?)

2. Test API endpoint manually
   - Verify data structure matches documentation
   - Check administrator contact field format
   - Confirm coverage and data quality

3. Decision point: Proceed if:
   - Cost is reasonable (define budget threshold)
   - Data quality is good (>80% have admin contacts)
   - API is stable and documented

### Phase 2: Implementation (1 day)
1. Add `enrich_from_tic_io()` function
2. Integrate into scraper pipeline
3. Update data models and email templates
4. Add configuration to .env.example
5. Test with real data

### Phase 3: Documentation (1 hour)
1. Update README.md with TIC.io integration
2. Update claude.md with new function location
3. Document in PLAN.md that this was implemented
4. Update data completeness: 5/9 → 8/9 fields (89%)

## Cost-Benefit Analysis

### Current State (Free)
- **Data**: 5/9 fields (56%)
- **User workflow**: Manual POIT lookup for contacts
- **Time cost**: 2-3 min per bankruptcy to find lawyer contact

### With TIC.io (Est. €100-500/month)
- **Data**: 8/9 fields (89%)
- **User workflow**: Automated contact delivery
- **Time saved**: 2-3 min × N bankruptcies/month
- **Break-even**: If monitoring >50-100 bankruptcies/month

### ROI Calculation
If monthly bankruptcies of interest = X:
- Time saved = X × 3 minutes
- At €300/month → Break-even if X > 100 (5 hours saved)
- Value of direct contact = Deal flow, faster response time

## Alternative: Hybrid Manual Approach (If TIC.io Too Expensive)

### Keep Current System + Manual Enrichment Workflow
1. Email lists POIT links (current state)
2. User manually looks up important contacts
3. No code changes needed
4. **Cost**: Free, but manual effort

### Why This Is Acceptable
- Current system provides valuable filtering (region, keywords, size)
- Email narrows focus to ~10-20 relevant bankruptcies/month
- 3 min/company × 15 companies = 45 min/month manual work
- May be cheaper than API subscription

## Decision Criteria

### Implement TIC.io API if:
- ✅ API cost < €200/month
- ✅ Data quality >80% complete
- ✅ User monitoring >30 bankruptcies/month regularly
- ✅ ROI justifies automation

### Stay with current approach if:
- ❌ API cost >€500/month
- ❌ Data quality <60% complete
- ❌ User only monitors <10 bankruptcies/month
- ❌ Manual lookup acceptable for use case

## Risks & Mitigations

### Risk 1: TIC.io API Changes/Shuts Down
- **Mitigation**: Graceful fallback, system works without it
- **Impact**: Low (reverts to current 5/9 fields)

### Risk 2: Cost Increases Over Time
- **Mitigation**: Monitor usage, set budget alerts
- **Impact**: Medium (may need to discontinue)

### Risk 3: Data Quality Lower Than Expected
- **Mitigation**: Trial period to validate before committing
- **Impact**: Medium (manual lookup still needed)

### Risk 4: Adds Complexity to Single-File Philosophy
- **Mitigation**: Keep function small (~40 lines), optional feature
- **Impact**: Low (clean, contained addition)

## Recommendation

### Immediate Next Steps:
1. **Contact TIC.io** for pricing and trial access
2. **Test API** with sample org numbers
3. **Evaluate** data quality and cost
4. **Decide**: Implement if cost/benefit makes sense

### If TIC.io Not Viable:
- Accept current 5/9 field limitation
- Document manual workflow in README
- Consider future alternatives (Bolagsverket API if released)

### Code Readiness:
Implementation is straightforward (~70 lines) and maintains simplicity:
- ✅ Single file preserved
- ✅ No new dependencies (use urllib or add requests)
- ✅ Optional feature (graceful degradation)
- ✅ Stateless (no database changes)
- ✅ Clean separation of concerns

## Success Metrics

### Technical Success:
- [ ] TIC.io enrichment function added
- [ ] Email/phone fields in 80%+ of records
- [ ] No performance degradation (API calls < 2s each)
- [ ] Graceful handling of API failures

### Business Success:
- [ ] Time saved > API cost (ROI positive)
- [ ] User satisfaction with contact quality
- [ ] Reduced manual lookup effort

### Philosophy Compliance:
- [ ] Single file maintained
- [ ] Code addition < 100 lines
- [ ] No new architectural complexity
- [ ] Easy to remove if needed

---

**Next Action**: Contact TIC.io to request API trial and pricing information.
