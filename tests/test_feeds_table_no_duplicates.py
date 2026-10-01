#!/usr/bin/env python3
"""SOCRadar_Feeds_CL gets an indicator once, not once per run.

The overlap window re-sends recent indicators to Microsoft Sentinel on purpose
(same STIX id = update). The custom table has no such idempotency: every row
written is another row, so the dashboard counts inflated every hour.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import FakeDcrLogger, FakeRequests, FakeTable, Response, item, make_processor, ts, utc

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


t0 = utc(2026, 3, 1, 12, 0, 0)
items = [item("203.0.113.%d" % n, ts(t0)) for n in (1, 2, 3)]
table, dcr = FakeTable(), FakeDcrLogger()


def run(feed):
    stub = FakeRequests(get_responses=[Response(200, feed)], post_responses=[Response(200, {"errors": []})])
    result = make_processor(stub, table=table, sleeps=[], dcr=dcr).run()
    return result, stub


rows = []
for n in (1, 2, 3):
    result, stub = run(items)
    rows.append(len(dcr.feeds))
    check(result["collections_processed"] == 1, "run %d not clean: %r" % (n, result))
    # The overlap still goes to Sentinel TI: only the table must not repeat it.
    if n > 1:
        check(len(stub.post_calls) == 1 and len(stub.post_calls[0][1]) == 3,
              "run %d no longer re-sent the overlap to Sentinel TI: %r" % (n, stub.post_calls))
check(rows == [3, 3, 3], "the feeds table grew on re-sent indicators: rows after runs 1-3 = %r" % rows)

# A genuinely new indicator still gets its row.
newer = items + [item("203.0.113.9", ts(t0 + timedelta(hours=1)))]
run(newer)
check(len(dcr.feeds) == 4, "a new indicator got no row: %d" % len(dcr.feeds))
check(dcr.feeds[-1]["IndicatorValue"] == "203.0.113.9", "wrong row written: %r" % dcr.feeds[-1])

# A late arrival (last seen before the checkpoint, first time in the feed) is
# still sent to Sentinel TI but gets no table row. Documented in the README.
table3, dcr3 = FakeTable(), FakeDcrLogger()
base = [item("198.51.100.1", ts(t0))]
stub = FakeRequests(get_responses=[Response(200, base)], post_responses=[Response(200, {"errors": []})])
make_processor(stub, table=table3, sleeps=[], dcr=dcr3).run()
late = item("198.51.100.2", ts(t0 - timedelta(hours=1)))
stub = FakeRequests(get_responses=[Response(200, base + [late])], post_responses=[Response(200, {"errors": []})])
make_processor(stub, table=table3, sleeps=[], dcr=dcr3).run()
sent = [i["pattern"] for i in stub.post_calls[0][1]]
check(any("198.51.100.2" in p for p in sent), "the late arrival was not sent to Sentinel TI: %r" % sent)
check([r["IndicatorValue"] for r in dcr3.feeds] == ["198.51.100.1"],
      "the late arrival got a table row (README says it does not): %r" % [r["IndicatorValue"] for r in dcr3.feeds])

# Rows stay aligned with their batch: 50 old, 120 new, 100 old = 3 batches
# (100, 100, 70). The second batch fails, so only the new indicators of the
# first batch (fresh 0-49) were delivered and may have rows.
table2, dcr2 = FakeTable(), FakeDcrLogger()
old = [item("10.0.%d.%d" % (n // 250, n % 250), ts(t0)) for n in range(150)]
stub = FakeRequests(get_responses=[Response(200, old)], post_responses=[Response(200, {"errors": []})])
make_processor(stub, table=table2, sleeps=[], dcr=dcr2).run()
fresh = [item("172.16.0.%d" % n, ts(t0 + timedelta(hours=1))) for n in range(120)]
mixed = old[:50] + fresh + old[50:]
stub = FakeRequests(get_responses=[Response(200, mixed)],
                    post_responses=[Response(200, {"errors": []}), Response(500)])
result = make_processor(stub, table=table2, sleeps=[], dcr=dcr2).run()
second = [r["IndicatorValue"] for r in dcr2.feeds[150:]]
check(result["collections_partial"] == 1, "the failed second batch was not reported: %r" % result)
check(second == [i["feed"] for i in fresh[:50]],
      "table rows are not the new indicators of the delivered batch: %d rows, first %r" % (len(second), second[:2]))

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("feeds table gets each indicator once: OK")
