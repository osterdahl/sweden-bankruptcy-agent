"""
Entry point for the Nordic Bankruptcy Monitor.

Works in two modes:
  GitHub Actions / env-var-driven:
      python run.py
      (reads COUNTRIES, YEAR, MONTH, NO_EMAIL, AI_SCORING_ENABLED, etc. from env)

  Local / manual:
      python run.py --countries se,no --year 2026 --month 2 --no-email
      (CLI args override env vars when provided)

Examples:
  python run.py                            # auto-detect month, Sweden only
  python run.py --countries se,no          # Sweden + Norway
  python run.py --year 2026 --month 2 --no-email   # dry run, specific period
  python run.py --countries se --ai        # Sweden with AI scoring
  python run.py --filter-regions Stockholm,Göteborg --min-employees 10
"""

import argparse
import logging
import os
import sys


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Nordic Bankruptcy Monitor — monthly report runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--countries",
        metavar="CODES",
        help="Comma-separated country codes to process (e.g. se,no,dk,fi). "
             "Default: COUNTRIES env var, or 'se'.",
    )
    parser.add_argument(
        "--year",
        type=int,
        metavar="YYYY",
        help="Year to process. Default: auto-detect (previous month logic).",
    )
    parser.add_argument(
        "--month",
        type=int,
        metavar="1-12",
        help="Month to process. Default: auto-detect (previous month logic).",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Dry run — print the report to stdout, skip sending email. "
             "Also saves HTML preview to /tmp/bankruptcy_email_sample.html.",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable AI scoring via Claude API (requires ANTHROPIC_API_KEY).",
    )
    parser.add_argument(
        "--filter-regions",
        metavar="REGIONS",
        help="Comma-separated regions to include (e.g. Stockholm,Oslo). "
             "Default: FILTER_REGIONS env var.",
    )
    parser.add_argument(
        "--filter-keywords",
        metavar="KEYWORDS",
        help="Comma-separated keywords to filter by company/industry name "
             "(e.g. IT,tech,consulting). Default: FILTER_INCLUDE_KEYWORDS env var.",
    )
    parser.add_argument(
        "--min-employees",
        type=int,
        metavar="N",
        help="Minimum employee count to include. Default: FILTER_MIN_EMPLOYEES env var, or 5.",
    )
    parser.add_argument(
        "--min-revenue",
        type=int,
        metavar="N",
        help="Minimum revenue (local currency) to include. "
             "Default: FILTER_MIN_REVENUE env var, or 1000000.",
    )

    return parser.parse_args()


def _apply_args_to_env(args):
    """Push parsed CLI args into env vars so the pipeline picks them up."""
    if args.countries:
        os.environ["COUNTRIES"] = args.countries
    if args.year is not None:
        os.environ["YEAR"] = str(args.year)
    if args.month is not None:
        os.environ["MONTH"] = str(args.month)
    if args.no_email:
        os.environ["NO_EMAIL"] = "true"
    if args.ai:
        os.environ["AI_SCORING_ENABLED"] = "true"
    if args.filter_regions:
        os.environ["FILTER_REGIONS"] = args.filter_regions
    if args.filter_keywords:
        os.environ["FILTER_INCLUDE_KEYWORDS"] = args.filter_keywords
    if args.min_employees is not None:
        os.environ["FILTER_MIN_EMPLOYEES"] = str(args.min_employees)
    if args.min_revenue is not None:
        os.environ["FILTER_MIN_REVENUE"] = str(args.min_revenue)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    args = _parse_args()
    _apply_args_to_env(args)

    # Import country modules to trigger register_country() at module level.
    # The registry in countries/__init__.py is empty until these are imported.
    import countries.sweden    # noqa: F401
    import countries.norway    # noqa: F401
    import countries.denmark   # noqa: F401
    import countries.finland   # noqa: F401

    from core.pipeline import run_all
    run_all()


if __name__ == "__main__":
    main()
