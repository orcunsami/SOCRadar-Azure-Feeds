#!/usr/bin/env python3
"""What the feed API can send, and what must never leak out of the function.

The key travels as a query parameter, so any text that came near a URL is
redacted before it is logged, raised or written to a table. A 200 with an
error object is not an empty feed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import FakeRequests, FakeTable, Response, fixture_items, item, make_processor
from stix_builder import parse_feed_datetime

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


# 1. Fixture format: the real feed stamps "YYYY-MM-DD HH:MM:SS", and it parses.
for entry in fixture_items():
    seen = entry.get("latest_seen_date")
    if seen:
        check(" " in seen and parse_feed_datetime(seen) is not None, "fixture date not parsed: %r" % seen)

# 2. Transport exception: requests puts the full URL, key included, in its
#    message. The processor must not.
class ConnErr(Exception):
    pass


stub = FakeRequests(get_responses=[ConnErr("Max retries exceeded with url: /feed_list/x.json?key=secret-key-value&v=2")])
result = make_processor(stub, table=FakeTable(), sleeps=[]).run() if False else None
try:
    make_processor(stub, table=FakeTable(), sleeps=[]).run()
    raised = None
except RuntimeError as e:
    raised = str(e)
check(raised is not None and "secret-key-value" not in raised and "key=***" in raised,
      "key leaked through a transport error: %r" % raised)

# 3. Non-200 with the key echoed in the body: redacted as well.
stub = FakeRequests(get_responses=[Response(401, text="invalid key=secret-key-value")])
try:
    make_processor(stub, table=FakeTable(), sleeps=[]).run()
    raised = None
except RuntimeError as e:
    raised = str(e)
check(raised is not None and "secret-key-value" not in raised, "key leaked through an HTTP error body: %r" % raised)

# 4. A 200 whose body is an error object must not read as an empty feed.
table = FakeTable()
stub = FakeRequests(get_responses=[Response(200, {"error": "No custom collection found"})])
try:
    make_processor(stub, table=table, sleeps=[]).run()
    raised = None
except RuntimeError as e:
    raised = str(e)
check(raised is not None, "200 + error object was treated as an empty feed")
check(table.upserts == [], "checkpoint was written after an unusable feed body: %r" % table.upserts)

# 5. Non-JSON 200 likewise.
stub = FakeRequests(get_responses=[Response(200, ValueError("not json"))])
try:
    make_processor(stub, table=FakeTable(), sleeps=[]).run()
    raised = None
except RuntimeError as e:
    raised = str(e)
check(raised is not None, "non-JSON 200 was not an error")

# 6. Both accepted shapes work: bare list and {"data": [...]}.
for payload in ([item("1.1.1.1", "2026-03-01 10:00:00")], {"data": [item("1.1.1.1", "2026-03-01 10:00:00")]}):
    stub = FakeRequests(get_responses=[Response(200, payload)], post_responses=[Response(200, {"errors": []})])
    r = make_processor(stub, table=FakeTable(), sleeps=[]).run()
    check(r["indicators_created"] == 1, "accepted feed shape not processed: %r" % (payload,))

# 7. Feed fetch retries on 503 and gives up on 401.
stub = FakeRequests(get_responses=[Response(503, headers={"Retry-After": "0"}), Response(200, [])])
r = make_processor(stub, table=FakeTable(), sleeps=[]).run()
check(len(stub.get_calls) == 2 and r["collections_failed"] == 0, "503 on fetch was not retried")
stub = FakeRequests(get_responses=[Response(401)])
try:
    make_processor(stub, table=FakeTable(), sleeps=[]).run()
except RuntimeError:
    pass
check(len(stub.get_calls) == 1, "401 on fetch was retried")

# 8. A checkpoint read error that is not "no row" surfaces instead of posing
#    as a first run.
stub = FakeRequests(get_responses=[Response(200, [item("1.1.1.1", "2026-03-01 10:00:00")])])
try:
    make_processor(stub, table=FakeTable(entity=PermissionError("403 forbidden")), sleeps=[]).run()
    raised = None
except RuntimeError as e:
    raised = str(e)
check(raised is not None and "Checkpoint read failed" in raised, "table permission error posed as a first run: %r" % raised)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("feed contract and redaction: OK (%d checks)" % 12)
