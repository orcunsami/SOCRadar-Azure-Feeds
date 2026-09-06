"""
SOCRadar Feeds Import - Azure Function
Timer-triggered function to import SOCRadar threat intelligence feeds into Microsoft Sentinel.
"""

import logging
import time
import azure.functions as func

from feeds_processor import FeedsProcessor, redact

app = func.FunctionApp()

logger = logging.getLogger(__name__)


def audit_status(result: dict) -> str:
    """Status follows the counters, not the absence of an exception. A run
    that lost indicators or a collection is not a success even though it
    raised nothing; reporting it as one is what hid that class of loss."""
    if result["collections_failed"] or result["collections_partial"] or result["indicators_failed"]:
        return "PartialSuccess"
    return "Success"


@app.timer_trigger(
    schedule="%POLLING_SCHEDULE%",
    arg_name="timer",
    run_on_startup=True
)
def socradar_feeds_import(timer: func.TimerRequest) -> None:
    start_time = time.time()
    logger.info("=== SOCRadar Feeds Import started ===")

    if timer.past_due:
        logger.warning("Timer is past due, running anyway")

    processor = None
    try:
        logger.info("Step 1: Initializing processor from environment")
        processor = FeedsProcessor.from_env()
        logger.info("Step 1: Done - %d collections configured", len(processor.collections))

        logger.info("Step 2: Running feed import")
        result = processor.run()

        elapsed_ms = int((time.time() - start_time) * 1000)
        status = audit_status(result)
        logger.info(
            "Step 3: Import %s - %d collections ok, %d partial, %d failed, %d created, %d skipped, %d failed, %dms",
            status, result["collections_processed"], result["collections_partial"],
            result["collections_failed"], result["indicators_created"],
            result["indicators_skipped"], result["indicators_failed"], elapsed_ms,
        )

        logger.info("Step 4: Sending audit log")
        written = processor.log_audit(
            collections_processed=result["collections_processed"],
            collections_failed=result["collections_failed"] + result["collections_partial"],
            indicators_created=result["indicators_created"],
            indicators_skipped=result["indicators_skipped"],
            indicators_failed=result["indicators_failed"],
            duration_ms=elapsed_ms,
            status=status,
            error_message="; ".join(result["errors"])[:1000],
        )
        if not written:
            logger.error("Step 4: Audit row was not written; the run itself finished with status %s", status)
        else:
            logger.info("Step 4: Done")
        logger.info("=== SOCRadar Feeds Import finished (%s, %dms) ===", status, elapsed_ms)

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        message = redact(e)
        logger.error("=== SOCRadar Feeds Import FAILED after %dms: %s ===", elapsed_ms, message)
        if processor:
            try:
                processor.log_audit(
                    collections_processed=0,
                    collections_failed=len(processor.collections),
                    indicators_created=0,
                    indicators_skipped=0,
                    indicators_failed=0,
                    duration_ms=elapsed_ms,
                    status="Failed",
                    error_message=message[:1000],
                )
            except Exception:
                pass
        raise RuntimeError(message) from None
