#!/usr/bin/env python3
"""A failed SOCRadar_Feeds_CL write must not move the checkpoint.

The table only gets rows for indicators newer than the checkpoint. If the first
write fails (a fresh deploy's DCR role has not propagated yet, so a 403) and the
checkpoint moves anyway, the next run sees nothing new and the rows are gone for
good. Holding it costs only a re-send to Microsoft Sentinel, which is an update.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import FakeDcrLogger, FakeRequests, FakeTable, Response, item, make_processor, ts, utc
from stix_builder import format_checkpoint

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def run(feed, table, dcr):
    dcr.feeds = []
    stub = FakeRequests(get_responses=[Response(200, feed)], post_responses=[Response(200, {"errors": []})])
    result = make_processor(stub, table=table, sleeps=[], dcr=dcr).run()
    return result, stub


cp = utc(2026, 3, 1, 12, 0, 0)
newer = utc(2026, 3, 1, 14, 0, 0)

# 1. First run, table write fails. Undated indicators are in the feed too.
feed = [item("203.0.113.1", ts(newer)), item("203.0.113.2", ts(newer)), item("203.0.113.7", None)]
table, dcr = FakeTable(), FakeDcrLogger(feeds_ok=False)
result, stub = run(feed, table, dcr)
check(len(stub.post_calls) == 1 and len(stub.post_calls[0][1]) == 3, "run 1 did not deliver to Microsoft Sentinel TI")
check(result["collections_partial"] == 1 and result["collections_processed"] == 0,
      "a failed table write was counted as a clean run: %r" % result)
check(table.entity is None, "the first run pinned a checkpoint after a failed table write: %r" % table.upserts)

dcr.feeds_ok = True
result, stub = run(feed, table, dcr)
check(sorted(r["IndicatorValue"] for r in dcr.feeds) == ["203.0.113.1", "203.0.113.2", "203.0.113.7"],
      "run 2 did not write the rows run 1 lost: %r" % [r["IndicatorValue"] for r in dcr.feeds])
check(result["collections_processed"] == 1, "run 2 not clean: %r" % result)
check(table.entity and table.entity["LastProcessedDate"] == format_checkpoint(newer),
      "run 2 did not advance the checkpoint: %r" % table.entity)
run(feed, table, dcr)
check(dcr.feeds == [], "run 3 wrote rows again after the table caught up: %r" % dcr.feeds)

# 2. Existing checkpoint, table write fails: it stays put, the next run writes the rows.
table = FakeTable(entity={"LastProcessedDate": format_checkpoint(cp)})
dcr = FakeDcrLogger(feeds_ok=False)
feed = [item("198.51.100.1", ts(newer))]
result, _ = run(feed, table, dcr)
check(result["collections_partial"] == 1, "failed table write not reported: %r" % result)
check(table.entity["LastProcessedDate"] == format_checkpoint(cp),
      "the checkpoint moved past rows the table never got: %r" % table.entity)
dcr.feeds_ok = True
run(feed, table, dcr)
check([r["IndicatorValue"] for r in dcr.feeds] == ["198.51.100.1"], "the lost row was not written on the next run: %r" % dcr.feeds)
check(table.entity["LastProcessedDate"] == format_checkpoint(newer), "checkpoint did not advance once the table caught up")

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("failed table write holds the checkpoint: OK")
