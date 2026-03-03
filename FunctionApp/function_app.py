"""
SOCRadar Feeds Import - Azure Function
Timer-triggered function to import SOCRadar threat intelligence feeds into Microsoft Sentinel.
"""

import logging
import time
import azure.functions as func

from feeds_processor import FeedsProcessor

app = func.FunctionApp()

logger = logging.getLogger(__name__)


@app.timer_trigger(
    schedule="%POLLING_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False
)
def socradar_feeds_import(timer: func.TimerRequest) -> None:
    start_time = time.time()
    logger.info("SOCRadar Feeds Import started")

    if timer.past_due:
        logger.warning("Timer is past due, running anyway")

    processor = None
    try:
        processor = FeedsProcessor.from_env()
        result = processor.run()

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Completed: %d collections, %d created, %d skipped, %dms",
            result["collections_processed"],
            result["indicators_created"],
            result["indicators_skipped"],
            elapsed_ms,
        )

        processor.log_audit(
            collections_processed=result["collections_processed"],
            indicators_created=result["indicators_created"],
            indicators_skipped=result["indicators_skipped"],
            duration_ms=elapsed_ms,
            status="Success",
            error_message="",
        )

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.error("Feed import failed: %s", e)
        if processor:
            try:
                processor.log_audit(
                    collections_processed=0,
                    indicators_created=0,
                    indicators_skipped=0,
                    duration_ms=elapsed_ms,
                    status="Failed",
                    error_message=str(e),
                )
            except Exception:
                pass
        raise
