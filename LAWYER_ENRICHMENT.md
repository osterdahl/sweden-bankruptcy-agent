# Lawyer Contact Enrichment Feature

## Overview

This feature automatically enriches bankruptcy records with lawyer (konkursförvaltare) contact information by scraping the official Bolagsverket POIT system.

## How It Works

1. **Source**: Scrapes lawyer contact details from https://poit.bolagsverket.se/poit-app/sok
2. **Process**:
   - Searches for company by organization number
   - Looks for "konkursbeslut" (bankruptcy decision) announcements
   - Extracts lawyer email and phone number from the announcement details
3. **Integration**: Automatically runs as part of the enrichment pipeline after scraping

## Files Added/Modified

### New Files

- **src/lawyer_enrichment.py**: Main enrichment module with `BolagsverketLawyerEnricher` class
- **tests/test_lawyer_enrichment.py**: Comprehensive test suite (11 tests)
- **test_live_lawyer_enrichment.py**: Manual test script for verifying real scraping

### Modified Files

- **src/agent.py**: Integrated lawyer enrichment into the main workflow
- **src/__init__.py**: Exported new `BolagsverketLawyerEnricher` class
- **src/models.py**: Already had `email` and `phone` fields in `BankruptcyAdministrator` (no changes needed)

## Usage

### Automatic Usage

The lawyer enrichment runs automatically when you run the agent:

```bash
python main.py --no-email  # Lawyer contact info will be scraped automatically
```

### Manual Usage

You can also use the enricher directly:

```python
from src.lawyer_enrichment import BolagsverketLawyerEnricher
from src.models import BankruptcyRecord

# Create enricher
enricher = BolagsverketLawyerEnricher(
    headless=True,
    timeout=30.0,
    request_delay=2.0
)

# Enrich single record
async with enricher:
    enriched_record = await enricher.enrich_record(record)

# Enrich batch
enriched_records = await enricher.enrich_batch(records, concurrency=1)
```

## Features

### Email Extraction
- Automatically finds email addresses in bankruptcy announcements
- Filters out invalid emails (noreply@, test@, example@)
- Returns first valid lawyer email found

### Phone Extraction
- Supports multiple Swedish phone formats:
  - International: `+46 XX XXX XX XX`
  - Local: `0XX-XXX XX XX`
  - Mobile: `070-XXXXXXX`

### Intelligent Enrichment
- **Preserves existing data**: Won't overwrite manually entered contact info
- **Graceful degradation**: If scraping fails, continues without errors
- **Rate limiting**: Respects Bolagsverket's servers with configurable delays
- **Batch processing**: Can process multiple records efficiently

## Testing

### Run Unit Tests

```bash
# Run all tests
pytest tests/test_lawyer_enrichment.py -v

# Run specific test
pytest tests/test_lawyer_enrichment.py::TestBolagsverketLawyerEnricher::test_extract_email_valid -v
```

### Test with Real Data

```bash
# Interactive test with browser visible
python test_live_lawyer_enrichment.py
```

This will:
- Open a browser window
- Navigate to Bolagsverket POIT
- Try to find lawyer contact info for a real bankruptcy case
- Show results

## Configuration

The enricher can be configured with:

```python
enricher = BolagsverketLawyerEnricher(
    headless=True,        # Run browser in background (default: True)
    timeout=30.0,         # Request timeout in seconds (default: 30)
    request_delay=2.0     # Delay between requests in seconds (default: 1.0)
)
```

## Data Model

The lawyer contact information is stored in the `BankruptcyAdministrator` model:

```python
@dataclass
class BankruptcyAdministrator:
    name: str
    title: Optional[str] = None         # e.g., "advokat"
    law_firm: Optional[str] = None
    email: Optional[str] = None         # ← Added by enrichment
    phone: Optional[str] = None         # ← Added by enrichment
    address: Optional[str] = None
```

## Test Coverage

The test suite includes 11 comprehensive tests:

1. ✓ Email extraction (valid formats)
2. ✓ Phone extraction (Swedish formats)
3. ✓ Enriching record with existing administrator
4. ✓ Enriching record without administrator
5. ✓ Handling missing contact information
6. ✓ Batch enrichment
7. ✓ Preserving existing contact data
8. ✓ Context manager functionality
9. ✓ Various email format extraction
10. ✓ Filtering invalid emails
11. ✓ Various phone format extraction

All tests pass ✓

## Notes

### Bolagsverket Site Structure

The scraper uses Playwright to automate browser interactions with the Bolagsverket POIT system. The current implementation:

- Searches by organization number
- Looks for input fields with common patterns (`name*="org"`, `id*="org"`, etc.)
- Searches for links/sections containing "konkursbeslut"
- Extracts contact info using regex patterns

**Important**: If the Bolagsverket site structure changes, the selectors in `_fetch_lawyer_contact()` may need adjustment.

### Rate Limiting

To be respectful to Bolagsverket's servers:
- Default delay: 1-2 seconds between requests
- Concurrency: Limited to 1 by default for batch processing
- Graceful error handling: Won't crash if a request fails

### Privacy & Ethics

This feature scrapes publicly available information from official Swedish government announcements. All data is:
- Publicly accessible on Bolagsverket POIT
- Part of official bankruptcy proceedings
- Already published by Swedish courts

## Troubleshooting

### "Could not find search input field"

This warning means the scraper couldn't locate the search field on Bolagsverket. Possible causes:
- Site structure has changed (selectors need updating)
- Page didn't load fully (increase timeout)
- Network issues

### No contact information found

Even when scraping succeeds, contact info might not be available if:
- The bankruptcy announcement doesn't include contact details
- The announcement is very recent and not yet in POIT
- The specific announcement type doesn't include lawyer contacts

### Tests failing

If tests fail:
1. Check internet connection (integration test requires network)
2. Ensure pytest-asyncio is installed: `pip install pytest-asyncio`
3. Run with verbose output: `pytest -v --tb=short`

## Future Improvements

Potential enhancements:
- [ ] Add retry logic for failed scraping attempts
- [ ] Cache results to avoid re-scraping same companies
- [ ] Support additional sources beyond Bolagsverket
- [ ] OCR for contact info in PDF announcements
- [ ] Machine learning to improve extraction accuracy

## Support

For issues or questions:
1. Check test suite: `pytest tests/test_lawyer_enrichment.py -v`
2. Run manual test: `python test_live_lawyer_enrichment.py`
3. Check logs: Enable verbose logging with `--verbose` flag
