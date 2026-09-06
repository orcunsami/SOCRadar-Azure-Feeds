#!/usr/bin/env python3
"""An upload that never reached Microsoft Sentinel must not move the checkpoint.

Re-uploading a batch that already landed is safe (Sentinel updates the
indicator). Advancing past one that did not land is not recoverable.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import FakeRequests, FakeTable, Response, item, make_processor, ts, utc
from stix_builder import format_checkpoint

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


cp = utc(2026, 3, 1, 12, 0, 0)
newer = ts(cp + timedelta(hours=1))


def run(post_responses, table=None, items=None, **kwargs):
    table = table if table is not None else FakeTable(entity={"LastProcessedDate": format_checkpoint(cp)})
    stub = FakeRequests(get_responses=[Response(200, items or [item("1.1.1.1", newer)])],
                        post_responses=post_responses)
    sleeps = []
    processor = make_processor(stub, table=table, sleeps=sleeps, **kwargs)
    return processor.run(), table, stub, sleeps


# 1. Upload keeps failing: the checkpoint is written again at its old value,
#    the loss is counted, and the run is partial rather than a success.
result, table, stub, sleeps = run([Response(503, headers={"Retry-After": "0"})])
check(len(table.upserts) == 1, "the failed run left no checkpoint row: %r" % table.upserts)
check(table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp),
      "the checkpoint moved past an indicator that never landed: %r" % table.upserts)
check(result["indicators_failed"] == 1, "failed indicators were not counted: %r" % result)
check(result["indicators_created"] == 0, "indicators were reported as created after a failed upload")
check(result["collections_partial"] == 1 and result["collections_processed"] == 0,
      "a run that lost indicators was counted as processed: %r" % result)
check(len(stub.post_calls) == 3, "expected 3 attempts, got %d" % len(stub.post_calls))

# 2. First run failure pins the window instead of leaving no row.
from datetime import datetime, timezone
result, table, _, _ = run([Response(500)], table=FakeTable(), initial_lookback_days=30,
                          items=[item("1.1.1.1", ts(datetime.now(timezone.utc)))])
check(table.upserts and table.upserts[-1]["LastProcessedDate"] > "2020",
      "a failed first run did not pin its window: %r" % table.upserts)
check(result["indicators_failed"] == 1, "first-run failure not counted")

# 3. Upload succeeds: the checkpoint must move.
result, table, _, _ = run([Response(200, {"errors": []})])
check(table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp + timedelta(hours=1)),
      "a clean run did not advance the checkpoint: %r" % table.upserts)
check(result["collections_processed"] == 1 and result["indicators_failed"] == 0, "clean run miscounted: %r" % result)

# 4. A 200 that rejects indicators is different: rejected means delivered and
#    refused, so the checkpoint still moves and they count as skipped.
result, table, _, _ = run([Response(200, {"errors": [{"recordIndex": 0}]})])
check(table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp + timedelta(hours=1)),
      "a rejected-but-delivered indicator blocked the checkpoint")
check(result["indicators_skipped"] == 1 and result["indicators_failed"] == 0,
      "rejected indicators were miscounted: %r" % result)

# 5. Mid-run failure: batch 1 lands, batch 2 does not. The checkpoint stays,
#    the rest is counted as failed, and no further batch is attempted.
items = [item("10.0.0.%d" % i, newer) for i in range(250)]
result, table, stub, _ = run([Response(200, {"errors": []}), Response(500)], items=items)
check(len(stub.post_calls) == 4, "expected 1 ok + 3 attempts then stop, got %d posts" % len(stub.post_calls))
check(result["indicators_created"] == 100 and result["indicators_failed"] == 150,
      "mid-run failure miscounted: %r" % result)
check(table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp),
      "checkpoint moved after a partial delivery: %r" % table.upserts)

# 6. A transport exception on upload is a failure, not a crash, and the
#    checkpoint stays.
result, table, _, _ = run([RuntimeError("boom")])
check(result["indicators_failed"] == 1, "exception on upload was not counted as failed: %r" % result)
check(table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp), "checkpoint moved after upload exception")

# 7. Retry honours Retry-After and stops when it would overrun the budget.
result, _, stub, sleeps = run([Response(429, headers={"Retry-After": "7"}), Response(200, {"errors": []})])
check(sleeps == [7.0], "Retry-After was not honoured: %r" % sleeps)
check(result["indicators_created"] == 1, "retry did not recover: %r" % result)
result, _, stub, sleeps = run([Response(503, headers={"Retry-After": "600"})], time_budget_seconds=60)
check(sleeps == [] and len(stub.post_calls) == 1, "retry slept past the time budget: %r %d" % (sleeps, len(stub.post_calls)))

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("upload failure never advances the checkpoint: OK (%d checks)" % 19)
