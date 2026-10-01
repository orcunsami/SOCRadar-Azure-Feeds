#!/usr/bin/env python3
"""The collection's time budget also bounds the feed fetch.

Only the retry sleep and the upload loop used to look at the deadline. The host
kills the function at its own timeout without writing an audit row, so a fetch
that starts after the budget is gone must fail loudly instead.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import COLLECTION, FakeRequests, Response, make_processor

failures = []

stub = FakeRequests(get_responses=[Response(200, [])])
processor = make_processor(stub, sleeps=[])
processor._deadline = time.time() - 1
raised = None
try:
    processor.fetch_feed(COLLECTION["id"])
except RuntimeError as e:
    raised = e
if raised is None:
    failures.append("fetch_feed started a request after the budget was spent")
elif "budget" not in str(raised):
    failures.append("the error does not name the budget: %r" % raised)
if stub.get_calls:
    failures.append("a request was sent with no budget left: %r" % stub.get_calls)

# With budget left the fetch works as before.
stub = FakeRequests(get_responses=[Response(200, [])])
processor = make_processor(stub, sleeps=[])
processor._deadline = time.time() + 60
if processor.fetch_feed(COLLECTION["id"]) != [] or len(stub.get_calls) != 1:
    failures.append("fetch_feed broke with budget left")

# The request timeout never outlasts what is left of the budget.
stub = FakeRequests(get_responses=[Response(200, [])])
processor = make_processor(stub, sleeps=[])
processor._deadline = time.time() + 5
processor.fetch_feed(COLLECTION["id"])
if not stub.get_timeouts or not (0 < stub.get_timeouts[0] <= 5):
    failures.append("request timeout exceeds the remaining 5s budget: %r" % stub.get_timeouts)

# With no budget set the old 60s timeout applies.
stub = FakeRequests(get_responses=[Response(200, [])])
processor = make_processor(stub, sleeps=[])
processor._deadline = None
processor.fetch_feed(COLLECTION["id"])
if stub.get_timeouts != [60]:
    failures.append("no budget should keep the 60s timeout: %r" % stub.get_timeouts)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("fetch honours the time budget: OK")
