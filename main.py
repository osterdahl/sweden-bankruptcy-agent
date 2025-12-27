#!/usr/bin/env python3
"""
Sweden Bankruptcy Monitoring Agent - Main Entry Point

Usage:
    python main.py                    # Run with current month
    python main.py --month 11 --year 2024  # Run for specific month
    python main.py --mock             # Run with mock data (testing)
    python main.py --no-email         # Run without sending email
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from config import Settings
from src import BankruptcyMonitorAgent, run_agent


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bankruptcy_agent.log"),
        ]
    )


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Sweden Bankruptcy Monitoring Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Run for current month
  python main.py --month 11 --year 2024   # Run for November 2024
  python main.py --mock --no-email        # Test run with mock data
  python main.py --export-only            # Export existing data, no scraping
        """
    )
    
    parser.add_argument(
        "--year", "-y",
        type=int,
        default=datetime.now().year,
        help="Year to process (default: current year)"
    )
    parser.add_argument(
        "--month", "-m",
        type=int,
        default=datetime.now().month,
        help="Month to process (default: current month)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock data for testing (no real web scraping)"
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Don't send email notification"
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Don't export files"
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Only export existing data from database"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to configuration file (JSON)"
    )
    
    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_args()
    setup_logging(args.verbose)
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Sweden Bankruptcy Monitoring Agent")
    
    # Load settings
    settings = Settings.from_env()
    
    # Create agent
    agent = BankruptcyMonitorAgent(settings=settings, use_mock=args.mock)
    
    if args.export_only:
        # Export existing data
        logger.info(f"Exporting data for {args.year}-{args.month:02d}")
        records = agent.get_monthly_data(args.year, args.month, matched_only=True)
        if records:
            agent._export_files(records, args.year, args.month)
            logger.info(f"Exported {len(records)} records")
        else:
            logger.info("No records found for export")
        return
    
    # Run the agent
    try:
        result = await agent.run_monthly_report(
            year=args.year,
            month=args.month,
            send_email=not args.no_email,
            export_files=not args.no_export
        )
        
        # Print summary
        print("\n" + "=" * 60)
        print("BANKRUPTCY MONITORING REPORT SUMMARY")
        print("=" * 60)
        print(f"Period: {args.year}-{args.month:02d}")
        print(f"Total bankruptcies found: {result['total_found']}")
        print(f"Matching your criteria: {result['total_matched']}")
        print(f"Email sent: {'Yes' if result['email_sent'] else 'No'}")
        print("=" * 60)
        
        if result['records']:
            print("\nTop matching companies:")
            for i, record in enumerate(result['records'][:10], 1):
                print(f"  {i}. {record.company.name} ({record.company.org_number})")
                if record.company.employees:
                    print(f"     Employees: {record.company.employees}")
                if record.company.revenue:
                    print(f"     Revenue: {record.company.revenue:,.0f} SEK")
        
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
