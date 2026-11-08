#!/usr/bin/env python3
"""The STIX upload wire contract fails closed on ambiguous 200 responses."""

import os
import sys
import time
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (FakeDcrLogger, FakeRequests, FakeTable, Response, item,
                      make_processor, ts, utc)
from stix_builder import format_checkpoint

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def records(count=3):
    return [{"id": "indicator--%d" % n, "type": "indicator"} for n in range(count)]


def upload(responses, batch=None, **kwargs):
    stub = FakeRequests(post_responses=responses)
    sleeps = []
    processor = make_processor(stub, sleeps=sleeps, **kwargs)
    return processor.upload_batch(batch or records()), stub, sleeps, processor


# The endpoint, API version, source system and envelope must change together.
(created, skipped, failed, rejected), stub, _, _ = upload([Response(200, {"errors": []})])
check((created, skipped, failed, rejected) == (3, 0, 0, set()), "clean upload miscounted")
check(len(stub.post_calls) == 1, "clean upload did not post exactly once")
url = stub.post_calls[0][0] if stub.post_calls else ""
body = stub.post_bodies[0] if stub.post_bodies else {}
check(url == "https://api.ti.sentinel.azure.com/workspaces/workspace/threat-intelligence-stix-objects:upload?api-version=2024-02-01-preview",
      "wrong STIX upload URL: %r" % url)
check(set(body) == {"sourcesystem", "stixobjects"}, "wrong upload envelope: %r" % body)
check(body.get("sourcesystem") == "SOCRadar Threat Feeds" and body.get("sourcesystem") != "Microsoft Sentinel",
      "source system drifted: %r" % body.get("sourcesystem"))
check(body.get("stixobjects") == records(), "STIX records were not sent intact: %r" % body)

# The service documents no body and whitespace as a successful all-created
# response. Any non-empty body must instead be parseable and validated.
for label, response in (
    ("empty", Response(200, ValueError("must not parse"), text="")),
    ("whitespace", Response(200, ValueError("must not parse"), text=" \t\n")),
):
    result, stub, _, _ = upload([response])
    check(result == (3, 0, 0, set()) and len(stub.post_calls) == 1,
          "%s 200 was not accepted as all-created: %r" % (label, result))

# recordIndex is zero-based. Duplicate error rows are one rejected object, not
# two, and their set determines the arithmetic exactly.
result, _, _, _ = upload([Response(200, {"errors": [
    {"recordIndex": 1, "errorMessages": ["bad"]},
    {"recordIndex": 1, "errorMessages": ["duplicate"]},
]})])
check(result == (2, 1, 0, {1}), "duplicate rejection indexes changed arithmetic: %r" % (result,))

# The live service currently uses validationErrorMessages, rather than the
# documented errorMessages spelling. Only recordIndex decides batch outcome.
result, _, _, _ = upload([Response(200, {"errors": [{
    "recordIndex": 1,
    "validationErrorMessages": ["Error for Property=id: Required property is missing. Actual value: NULL."],
}]})])
check(result == (2, 1, 0, {1}),
      "live validationErrorMessages rejection was not accepted: %r" % (result,))

# A 200 that cannot prove which records landed is a failed batch. Exercise all
# accepted JSON failure shapes plus a JSON decoder exception without allowing a
# transport-shaped crash to count as a partial success.
invalid = [
    ("boolean response", True),
    ("array response", []),
    ("missing errors", {"status": "unknown"}),
    ("non-list errors", {"errors": {}}),
    ("non-dict error", {"errors": ["bad"]}),
    ("boolean index", {"errors": [{"recordIndex": True}]}),
    ("negative index", {"errors": [{"recordIndex": -1}]}),
    ("out-of-range index", {"errors": [{"recordIndex": 3}]}),
    ("missing index", {"errors": [{"errorMessages": ["bad"]}]}),
    ("non-json response", ValueError("not json")),
]
for label, payload in invalid:
    try:
        result, stub, _, _ = upload([Response(200, payload)])
        check(result == (0, 0, 3, set()), "%s was not failed closed: %r" % (label, result))
        check(len(stub.post_calls) == 1, "%s retried a 200 response" % label)
    except Exception as exc:
        failures.append("%s escaped as %r" % (label, exc))

# More than 100 objects must be rejected before the first HTTP call.
result, stub, _, _ = upload([], records(101))
check(result == (0, 0, 101, set()) and not stub.post_calls,
      "101-object batch made HTTP calls or was miscounted: %r %r" % (result, stub.post_calls))

# Retry policy is bounded at three attempts and must not sleep into the run
# budget. The direct method shares this policy with the checkpointed run.
result, stub, sleeps, _ = upload([Response(503, headers={"Retry-After": "0"})])
check(result == (0, 0, 3, set()) and len(stub.post_calls) == 3 and sleeps == [0.0, 0.0],
      "retry cap changed: result=%r calls=%d sleeps=%r" % (result, len(stub.post_calls), sleeps))
# Apply the deadline before the request.
stub = FakeRequests(post_responses=[Response(429, headers={"Retry-After": "30"})])
sleeps = []
processor = make_processor(stub, sleeps=sleeps)
processor._deadline = time.time() + 5
result = processor.upload_batch(records())
check(result == (0, 0, 3, set()) and len(stub.post_calls) == 1 and sleeps == [],
      "retry crossed the time budget: %r %d %r" % (result, len(stub.post_calls), sleeps))

# A malformed 200 during a real collection run is not a successful delivery:
# it preserves the pre-existing checkpoint and reports one failed indicator.
cp = utc(2026, 3, 1, 12, 0, 0)
table = FakeTable(entity={"LastProcessedDate": format_checkpoint(cp)})
stub = FakeRequests(
    get_responses=[Response(200, [item("203.0.113.8", ts(cp + timedelta(hours=1)))])],
    post_responses=[Response(200, {"errors": [{"recordIndex": True}]})],
)
result = make_processor(stub, table=table, sleeps=[]).run()
check(result["indicators_failed"] == 1 and result["collections_partial"] == 1,
      "malformed 200 was reported as delivered: %r" % result)
check(table.upserts and table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp),
      "malformed 200 advanced the checkpoint: %r" % table.upserts)

# Sentinel can reject a valid middle record in an otherwise delivered batch.
# It is skipped and checkpointed, but its corresponding SOCRadar_Feeds_CL row
# must not claim delivery; the first and last records still belong in the table.
dcr = FakeDcrLogger()
middle_feed = [
    item("198.51.100.10", ts(cp + timedelta(hours=1))),
    item("198.51.100.11", ts(cp + timedelta(hours=1))),
    item("198.51.100.12", ts(cp + timedelta(hours=1))),
]
stub = FakeRequests(
    get_responses=[Response(200, middle_feed)],
    post_responses=[Response(200, {"errors": [{"recordIndex": 1, "errorMessages": ["rejected"]}]})],
)
result = make_processor(stub, table=FakeTable(), sleeps=[], dcr=dcr).run()
check(result["indicators_created"] == 2 and result["indicators_skipped"] == 1 and result["indicators_failed"] == 0,
      "partial 200 counters are wrong: %r" % result)
check([row["IndicatorValue"] for row in dcr.feeds] == ["198.51.100.10", "198.51.100.12"],
      "rejected middle record was logged as delivered: %r" % dcr.feeds)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("STIX upload contract: OK")
