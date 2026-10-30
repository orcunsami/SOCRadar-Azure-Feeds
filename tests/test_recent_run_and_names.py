#!/usr/bin/env python3
"""A host start must not repeat an import that just finished, and a collection
name left at its default must not label a different feed."""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import FakeRequests, FakeTable, Response, item, make_processor, ts
import feeds_processor
from feeds_processor import resolve_collection_name

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


HASH = "0cb06558728b4dc296019c93b78360d1"
IP = "4d7a69ce6e7c49ff8c916da5d7343916"
HASH_NAME = "SOCRadar-APT-Recommended-Block-Hash"
IP_NAME = "SOCRadar-APT-Recommended-Block-IP"

# names: a default left behind while the ID changed, blank, own, custom, unknown
check(resolve_collection_name(HASH, HASH_NAME, 1) == HASH_NAME, "hash id with its own name")
check(resolve_collection_name(IP, HASH_NAME, 1) == IP_NAME, "ip id kept the hash name: %r" % resolve_collection_name(IP, HASH_NAME, 1))
check(resolve_collection_name(IP, "", 1) == IP_NAME, "blank name for a known id")
check(resolve_collection_name(IP, "My IP feed", 1) == "My IP feed", "a custom name for a known id must win")
check(resolve_collection_name("f" * 32, HASH_NAME, 3) == "Custom-Feed-3", "unknown id borrowed a recommended name")
check(resolve_collection_name("f" * 32, "Mine", 3) == "Mine", "custom name for an unknown id")
check(resolve_collection_name("f" * 32, "", 3) == "Custom-Feed-3", "blank name for an unknown id")


def state(minutes_ago=None, failed=0, last_run=None):
    now = datetime.now(timezone.utc)
    e = {"LastProcessedDate": "2026-10-30T00:00:00Z", "IndicatorsFailed": failed}
    if minutes_ago is not None:
        e["LastRun"] = (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if last_run is not None:
        e["LastRun"] = last_run
    return e


def run(entity, minutes=15):
    table = FakeTable(entity=entity)
    stub = FakeRequests(get_responses=[Response(200, [item("1.1.1.1", ts(datetime.now(timezone.utc)))])],
                        post_responses=[Response(200, {"errors": []})])
    processor = make_processor(stub, table=table, sleeps=[], recent_run_minutes=minutes)
    totals = processor.run()
    return totals, table, stub


totals, table, stub = run(state(2))
check(not stub.post_calls, "a collection that finished 2 minutes ago was sent again")
check(not table.upserts, "the skipped run wrote the checkpoint row")
check(totals["collections_processed"] == 1 and totals["collections_failed"] == 0, "a skipped run is not a failure: %r" % totals)
check(totals["indicators_created"] == 0, "a skipped run created indicators")

_, _, stub = run(state(20))
check(stub.post_calls, "a collection that finished 20 minutes ago was skipped")
totals, _, stub = run(state(8))
check(not stub.post_calls, "the restart Azure adds 8 minutes after a deployment was imported again")
_, _, stub = run(state(2, failed=3))
check(stub.post_calls, "a run that lost indicators must be retried at once")
_, _, stub = run(state())
check(stub.post_calls, "a row without LastRun was skipped")
_, _, stub = run(state(-30))
check(stub.post_calls, "a LastRun in the future (clock skew) was skipped")
_, _, stub = run(state(2), minutes=0)
check(stub.post_calls, "the guard cannot be switched off")
check(feeds_processor.RECENT_RUN_MINUTES == 15, "default window changed: %r" % feeds_processor.RECENT_RUN_MINUTES)

import json
tmpl = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "azuredeploy.json")))
minimum = tmpl["parameters"]["PollingIntervalMinutes"]["minValue"]
check(minimum >= 2 * feeds_processor.RECENT_RUN_MINUTES,
      "polling minimum %d is below twice the guard window: scheduled runs would be skipped" % minimum)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("recent-run guard and name resolution: OK (17 checks)")
