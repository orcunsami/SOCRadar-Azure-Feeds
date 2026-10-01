#!/usr/bin/env python3
"""A run with nothing to import is a failed run, not a successful one.

IncludeAPTBlockHash=false with no custom collection id deploys without error.
The run used to return empty totals and the audit row said Success.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import FakeRequests, make_processor

failures = []

stub = FakeRequests()
processor = make_processor(stub, sleeps=[], collections=[])
raised = None
try:
    processor.run()
except RuntimeError as e:
    raised = e
if raised is None:
    failures.append("run() with no collections returned instead of raising")
elif "No collections configured" not in str(raised):
    failures.append("the error does not say what is wrong: %r" % raised)
if stub.get_calls or stub.post_calls:
    failures.append("requests were made with nothing configured")

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("no collections is a failure: OK (3 checks)")
