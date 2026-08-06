"""
Azure Function — Sale Schedule Timer

Timer trigger that runs every 5 minutes to check for vendor sales
that need to be automatically activated or reverted based on their
scheduled start_at and end_at dates.

Deployment:
  Add this as a new function in the existing shopify-automation-func
  Azure Function App, or create a standalone function app.

  Required environment variables (same as the existing function app):
    TC_PLANNER_URL    — e.g. https://tc-planner-app.azurewebsites.net
    TC_PLANNER_TOKEN  — the auth token for the TC Planner API

  function.json (place alongside this file):
  {
    "scriptFile": "__init__.py",
    "bindings": [
      {
        "name": "timer",
        "type": "timerTrigger",
        "direction": "in",
        "schedule": "0 */5 * * * *"
      }
    ]
  }

  host.json should already exist in the function app root.

File structure:
  shopify-automation-func/
    host.json
    requirements.txt (add 'httpx' if not already present)
    check_sale_schedule/
      __init__.py        <-- THIS FILE
      function.json
"""
import logging
import os
import httpx
import azure.functions as func

logger = logging.getLogger(__name__)

TC_PLANNER_URL = os.environ.get("TC_PLANNER_URL", "https://tc-planner-app.azurewebsites.net")
TC_PLANNER_TOKEN = os.environ.get("TC_PLANNER_TOKEN", "")


def main(timer: func.TimerRequest) -> None:
    """
    Timer trigger — runs every 5 minutes.
    Calls the TC Planner API to check for sales that need
    automatic activation or revert based on their schedule.
    """
    if timer.past_due:
        logger.info("Sale schedule timer is past due — running now")

    if not TC_PLANNER_TOKEN:
        logger.warning("TC_PLANNER_TOKEN not set — skipping sale schedule check")
        return

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{TC_PLANNER_URL}/api/sales/check-schedule",
                headers={
                    "Authorization": f"Bearer {TC_PLANNER_TOKEN}",
                    "Content-Type": "application/json",
                },
            )

            if resp.status_code == 200:
                result = resp.json()
                actions = result.get("actions_processed", 0)
                if actions > 0:
                    logger.info(
                        "Sale schedule check: %d actions processed — %s",
                        actions,
                        "; ".join(
                            f"{r.get('action', '?')} sale '{r.get('name', '?')}' ({r.get('vendor', '?')})"
                            for r in result.get("results", [])
                        ),
                    )
                else:
                    logger.debug("Sale schedule check: no actions needed")
            else:
                logger.error(
                    "Sale schedule check failed: HTTP %d — %s",
                    resp.status_code,
                    resp.text[:200],
                )

    except Exception as e:
        logger.error("Sale schedule check error: %s", e, exc_info=True)
