#!/usr/bin/env python3
"""STIX ids are stable, unsupported types are refused, literals are escaped."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: F401
from _harness import COLLECTION, item
from stix_builder import StixBuilder, parse_feed_datetime

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


a = StixBuilder.build_indicator(item("1.2.3.4", "2026-03-01 10:00:00"), COLLECTION)
b = StixBuilder.build_indicator(item("1.2.3.4", "2026-03-02 10:00:00"), COLLECTION)
check(a["id"] == b["id"], "same IOC produced different ids across runs: %s vs %s" % (a["id"], b["id"]))
c = StixBuilder.build_indicator(item("1.2.3.5", "2026-03-01 10:00:00"), COLLECTION)
check(a["id"] != c["id"], "different IOCs share an id")
other = dict(COLLECTION, id="ffffffffffffffffffffffffffffffff")
check(a["id"] != StixBuilder.build_indicator(item("1.2.3.4", ""), other)["id"], "same value in another collection shares an id")

now = datetime.now(timezone.utc)
check(parse_feed_datetime(a["valid_until"]) > now, "valid_until is not in the future: %s" % a["valid_until"])
check(parse_feed_datetime(a["valid_from"]) <= now, "valid_from is in the future")
old = StixBuilder.build_indicator(item("9.9.9.9", "2020-01-01 00:00:00"), COLLECTION)
check(parse_feed_datetime(old["valid_until"]) > now, "an old sighting expired on upload: %s" % old["valid_until"])

check(StixBuilder.build_indicator(item("x", "", feed_type="cidr"), COLLECTION) is None, "unknown feed type was mapped to a pattern")
check(StixBuilder.build_indicator(item("abc", "", feed_type="hash"), COLLECTION) is None, "hash of unknown length produced a pattern")
v6 = StixBuilder.build_indicator(item("2001:db8::1", "", feed_type="ipv6"), COLLECTION)
check(v6 and v6["pattern"] == "[ipv6-addr:value = '2001:db8::1']", "ipv6 not supported: %r" % v6)
sha1 = StixBuilder.build_indicator(item("a" * 40, "", feed_type="hash"), COLLECTION)
check(sha1["pattern"].startswith("[file:hashes.'SHA-1'"), "sha1 pattern wrong: %s" % sha1["pattern"])

url = StixBuilder.build_indicator(item("http://x/it's\\a", "", feed_type="url"), COLLECTION)
check(url["pattern"] == "[url:value = 'http://x/it\\'s\\\\a']", "pattern literal not escaped: %s" % url["pattern"])

check(StixBuilder.build_indicator({"feed": " ", "feed_type": "ip"}, COLLECTION) is None, "empty value produced an indicator")

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("stix builder: OK (%d checks)" % 12)
