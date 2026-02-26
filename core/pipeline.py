"""
Pipeline orchestrator for the Nordic Bankruptcy Monitor.

Provides run_country() and run_all() as the main entry points for
running the bankruptcy monitoring pipeline across one or more countries.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from core.scoring import score_bankruptcies
from core.email_lookup import lookup_trustee_emails
from core.reporting import format_email_html, format_email_plain, send_email
from countries import get_active_countries
from countries.protocol import CountryPlugin

logger = logging.getLogger(__name__)


def _determine_target_month() -> tuple:
    """Determine year/month from env vars, with auto-previous-month logic."""
    year_env = os.getenv('YEAR')
    month_env = os.getenv('MONTH')

    year = int(year_env) if year_env else datetime.now().year
    month = int(month_env) if month_env else datetime.now().month

    # Auto-select previous month if in first 7 days (only when not explicitly set)
    if not month_env and not year_env and datetime.now().day <= 7:
        month = month - 1 if month > 1 else 12
        year = year - 1 if month == 12 else year

    return year, month


def _filter_records(records, country_plugin=None):
    """Filter records based on environment variables.

    Extracted from bankruptcy_monitor.filter_records() — works for any country.
    """
    filter_regions = [r.strip() for r in os.getenv("FILTER_REGIONS", "").split(",") if r.strip()]
    filter_keywords = [k.strip().lower() for k in os.getenv("FILTER_INCLUDE_KEYWORDS", "").split(",") if k.strip()]
    min_employees = int(os.getenv("FILTER_MIN_EMPLOYEES", "5") or "5")
    min_revenue = int(os.getenv("FILTER_MIN_REVENUE", "1000000") or "1000000")

    filtered = []

    for record in records:
        # Region filter
        if filter_regions and record.region:
            if not any(region.lower() in record.region.lower() for region in filter_regions):
                continue

        # Keyword filter
        if filter_keywords:
            searchable = f"{record.company_name} {record.industry_name}".lower()
            if not any(kw in searchable for kw in filter_keywords):
                continue

        # Employee filter
        if min_employees > 0:
            if record.employees is None or record.employees < min_employees:
                continue

        # Revenue filter
        if min_revenue > 0:
            if record.net_sales is None or record.net_sales < min_revenue:
                continue

        filtered.append(record)

    return filtered


def run_country(country_plugin: CountryPlugin, year: int, month: int) -> None:
    """Full pipeline for one country: scrape -> dedup -> score -> lookup -> stage.

    Args:
        country_plugin: A CountryPlugin implementation for the target country.
        year: Target year.
        month: Target month.
    """
    code = country_plugin.code
    name = country_plugin.name
    logger.info(f"=== {name} ({code.upper()}) Bankruptcy Monitor ===")
    logger.info(f"Processing: {year}-{month:02d}")

    # Step 1: Scrape
    from scheduler import get_cached_keys
    cached = get_cached_keys()
    records = country_plugin.scrape_bankruptcies(year, month, cached)
    logger.info(f"[{code.upper()}] Scraped {len(records)} bankruptcies for {year}-{month:02d}")

    if not records:
        logger.warning(f"[{code.upper()}] No bankruptcies found for {year}-{month:02d}")
        return

    # Step 2: Deduplicate
    from scheduler import deduplicate, update_scores
    records = deduplicate(records)

    if not records:
        logger.info(f"[{code.upper()}] No new bankruptcies after deduplication.")
        return

    # Step 3: Score
    records = score_bankruptcies(records, country_plugin=country_plugin)
    update_scores(records)

    # Step 4: Email lookup and outreach for HIGH/MEDIUM candidates
    candidates = [r for r in records if r.priority in ('HIGH', 'MEDIUM')]
    if candidates:
        lookup_trustee_emails(candidates, country_plugin=country_plugin)
        from outreach import stage_outreach
        stage_outreach(candidates)
        logger.info(f"[{code.upper()}] Staged outreach for {len(candidates)} HIGH/MEDIUM candidates")

    # Step 5: Filter for email report
    filtered = _filter_records(records, country_plugin=country_plugin)
    logger.info(f"[{code.upper()}] Filtered to {len(filtered)} matching bankruptcies")

    if not filtered:
        logger.warning(
            f"[{code.upper()}] All {len(records)} bankruptcies were filtered out. "
            "Check filter settings."
        )
        return

    # Step 6: Generate and send email report
    month_name = datetime(year, month, 1).strftime("%B %Y")
    subject = f"{name} Bankruptcy Report - {month_name} ({len(filtered)} bankruptcies)"
    html_body = format_email_html(filtered, year, month, country_name=name)
    plain_body = format_email_plain(filtered, year, month, country_name=name)

    if os.getenv('NO_EMAIL', '').lower() == 'true':
        logger.info("Email sending skipped (NO_EMAIL=true)")
        print(plain_body)
        html_path = '/tmp/bankruptcy_email_sample.html'
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_body)
        logger.info(f"HTML preview saved to {html_path}")
    else:
        send_email(subject, html_body, plain_body)


def run_all(year: Optional[int] = None, month: Optional[int] = None) -> None:
    """Run pipeline for all active countries (from COUNTRIES env var).

    COUNTRIES=se,no  -> runs Sweden then Norway
    COUNTRIES=no     -> runs Norway only
    Default: 'se'    -> runs Sweden only

    If year/month not provided, determines from env vars / auto-detect logic.
    """
    if year is None or month is None:
        year, month = _determine_target_month()

    plugins = get_active_countries()

    if not plugins:
        logger.error(
            "No country plugins registered. Ensure country modules are imported "
            "(e.g. countries.sweden) and COUNTRIES env var is set correctly."
        )
        return

    logger.info(
        f"Running pipeline for {len(plugins)} countries: "
        f"{', '.join(p.name for p in plugins)}"
    )

    for plugin in plugins:
        try:
            run_country(plugin, year, month)
        except Exception as e:
            logger.error(f"Pipeline failed for {plugin.name}: {e}", exc_info=True)
            # Continue with next country
