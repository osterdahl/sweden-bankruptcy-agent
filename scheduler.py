#!/usr/bin/env python3
"""
Scheduler for running bankruptcy monitoring on a schedule.
Supports both cron-style scheduling and simple interval-based scheduling.

For cloud deployment:
- AWS Lambda: Use CloudWatch Events/EventBridge to trigger
- Google Cloud: Use Cloud Scheduler + Cloud Functions
- Azure: Use Azure Functions with Timer trigger
- Kubernetes: Use CronJob resource

This scheduler is for standalone deployment (Docker, VM, etc.)
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from config import Settings
from src import BankruptcyMonitorAgent

logger = logging.getLogger(__name__)


class BankruptcyScheduler:
    """
    Scheduler for running bankruptcy monitoring agent.
    
    Supports:
    - Monthly runs on specified day
    - Interval-based runs for testing
    - One-off runs
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        use_mock: bool = False
    ):
        self.settings = settings or Settings.from_env()
        self.use_mock = use_mock
        self.agent = BankruptcyMonitorAgent(settings=self.settings, use_mock=use_mock)
        self._running = False
    
    async def run_once(self, year: Optional[int] = None, month: Optional[int] = None):
        """Run a single report."""
        logger.info("Running single bankruptcy report")
        try:
            result = await self.agent.run_monthly_report(
                year=year,
                month=month,
                send_email=True,
                export_files=True
            )
            logger.info(f"Report completed: {result['total_found']} found, {result['total_matched']} matched")
            return result
        except Exception as e:
            logger.error(f"Report failed: {e}")
            raise
    
    async def run_scheduled(self):
        """
        Run continuously, executing reports on schedule.
        
        Default: Run on the 1st of each month at 06:00 UTC
        """
        self._running = True
        run_day = self.settings.run_day_of_month
        
        logger.info(f"Scheduler started. Will run on day {run_day} of each month.")
        
        while self._running:
            now = datetime.utcnow()
            
            # Calculate next run time
            if now.day == run_day and now.hour == 6:
                # Run for previous month's data
                if now.month == 1:
                    target_year = now.year - 1
                    target_month = 12
                else:
                    target_year = now.year
                    target_month = now.month - 1
                
                logger.info(f"Scheduled run triggered for {target_year}-{target_month:02d}")
                
                try:
                    await self.run_once(year=target_year, month=target_month)
                except Exception as e:
                    logger.error(f"Scheduled run failed: {e}")
                
                # Wait until next hour to avoid re-running
                await asyncio.sleep(3600)
            else:
                # Check every 5 minutes
                await asyncio.sleep(300)
    
    def stop(self):
        """Stop the scheduler."""
        self._running = False


async def run_scheduler():
    """Main scheduler entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Check if we should run immediately (for testing)
    run_now = os.getenv("RUN_NOW", "false").lower() == "true"
    use_mock = os.getenv("USE_MOCK", "false").lower() == "true"
    
    scheduler = BankruptcyScheduler(use_mock=use_mock)
    
    if run_now:
        logger.info("RUN_NOW enabled - running immediately")
        await scheduler.run_once()
    else:
        logger.info("Starting scheduled mode")
        await scheduler.run_scheduled()


# Lambda handler for AWS Lambda deployment
def lambda_handler(event, context):
    """AWS Lambda handler."""
    import json
    
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        # Parse event for optional year/month override
        year = event.get("year")
        month = event.get("month")
        
        # If not specified, use previous month
        if not year or not month:
            now = datetime.utcnow()
            if now.month == 1:
                year = now.year - 1
                month = 12
            else:
                year = now.year
                month = now.month - 1
        
        # Run synchronously for Lambda
        result = asyncio.run(
            BankruptcyScheduler().run_once(year=year, month=month)
        )
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Bankruptcy report completed",
                "year": year,
                "month": month,
                "total_found": result["total_found"],
                "total_matched": result["total_matched"],
                "email_sent": result["email_sent"],
            })
        }
    except Exception as e:
        logger.error(f"Lambda execution failed: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


# Google Cloud Function handler
def cloud_function_handler(request):
    """Google Cloud Function handler."""
    import json
    
    logging.basicConfig(level=logging.INFO)
    
    try:
        data = request.get_json() or {}
        year = data.get("year")
        month = data.get("month")
        
        if not year or not month:
            now = datetime.utcnow()
            if now.month == 1:
                year = now.year - 1
                month = 12
            else:
                year = now.year
                month = now.month - 1
        
        result = asyncio.run(
            BankruptcyScheduler().run_once(year=year, month=month)
        )
        
        return json.dumps({
            "status": "success",
            "year": year,
            "month": month,
            "total_found": result["total_found"],
            "total_matched": result["total_matched"],
        }), 200
        
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    asyncio.run(run_scheduler())
