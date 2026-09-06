#!/usr/bin/env python3
"""The checkpoint is compared as a time, with an overlap, not as a string.

The feed stamps entries "YYYY-MM-DD HH:MM:SS"; the checkpoint used to be stored
"YYYY-MM-DDTHH:MM:SSZ" and compared with `>`. A space sorts before a T, so every
entry from the checkpoint's own day compared as older and was dropped for good:
after the first run the integration delivered almost nothing while the audit
table said Success. These checks pin the repaired behaviour.
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (COLLECTION, FakeRequests, FakeTable, Response, fixture_items,
                      item, make_processor, ts, utc)
import feeds_processor
from stix_builder import format_checkpoint

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def run(items, table=None, post=None, **kwargs):
    table = table if table is not None else FakeTable()
    stub = FakeRequests(get_responses=[Response(200, items)],
                        post_responses=post or [Response(200, {"errors": []})])
    processor = make_processor(stub, table=table, sleeps=[], **kwargs)
    return processor.run(), table, stub


# 1. The bug itself, with real entries: a checkpoint written earlier the same
#    day must not hide entries stamped later that day.
real = [i for i in fixture_items() if i.get("latest_seen_date")]
check(len(real) >= 10, "fixture lost its dated entries")
day = max(i["latest_seen_date"] for i in real)[:10]
same_day = [i for i in real if i["latest_seen_date"].startswith(day)]
check(len(same_day) >= 3, "fixture has too few same-day entries: %d" % len(same_day))
# The checkpoint sits two days later so that the overlap window starts at
# 00:00 of the entries' own day: that boundary is where a string comparison
# of "YYYY-MM-DD HH:MM:SS" against "YYYY-MM-DDTHH:MM:SSZ" goes wrong.
from datetime import datetime, timezone
cp_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=feeds_processor.CHECKPOINT_OVERLAP_HOURS)
same_day_cp = {"LastProcessedDate": format_checkpoint(cp_dt)}
result, table, stub = run(real, table=FakeTable(entity=same_day_cp))
sent_values = {ind["name"].split(" - ", 1)[1] for call in stub.post_calls for ind in call[1]}
missing = [i["feed"] for i in same_day if i["feed"][:100] not in sent_values]
check(not missing, "same-day entries were dropped: %r" % missing)

# 2. Overlap: an entry stamped 1h before the checkpoint is re-sent (it may
#    have been stamped after the previous run read the feed); one stamped
#    well before the overlap is not.
cp = utc(2026, 3, 1, 12, 0, 0)
table = FakeTable(entity={"LastProcessedDate": format_checkpoint(cp)})
items = [item("1.1.1.1", ts(cp - timedelta(hours=1))),
         item("2.2.2.2", ts(cp - timedelta(hours=feeds_processor.CHECKPOINT_OVERLAP_HOURS + 1))),
         item("3.3.3.3", ts(cp + timedelta(hours=1)))]
result, table, stub = run(items, table=table)
values = [ind["name"].split(" - ")[1] for ind in stub.post_calls[0][1]]
check(values == ["1.1.1.1", "3.3.3.3"], "overlap window wrong, sent %r" % values)

# 3. The checkpoint advances to the newest sighting delivered, not to the clock.
written = table.upserts[-1]["LastProcessedDate"]
check(written == format_checkpoint(cp + timedelta(hours=1)),
      "checkpoint should be the newest delivered sighting, got %r" % written)

# 4. It never moves backwards when everything delivered is older than it.
table = FakeTable(entity={"LastProcessedDate": format_checkpoint(cp)})
result, table, _ = run([item("4.4.4.4", ts(cp - timedelta(hours=2)))], table=table)
check(table.upserts[-1]["LastProcessedDate"] == format_checkpoint(cp),
      "checkpoint moved backwards: %r" % table.upserts[-1]["LastProcessedDate"])

# 5. Undated entries are always sent (a re-send is an update, not a copy).
table = FakeTable(entity={"LastProcessedDate": format_checkpoint(cp)})
result, table, stub = run([item("5.5.5.5", "")], table=table)
check(sum(len(c[1]) for c in stub.post_calls) == 1, "undated entry was not sent")

# 6. First run: no lookback means everything; a lookback bounds it and the
#    checkpoint pins the window start when nothing newer was delivered.
old = utc(2020, 1, 1, 0, 0, 0)
result, table, stub = run([item("6.6.6.6", ts(old))])
check(sum(len(c[1]) for c in stub.post_calls) == 1, "first run without lookback dropped an old entry")
result, table, stub = run([item("7.7.7.7", ts(old))], initial_lookback_days=30)
check(sum(len(c[1]) for c in stub.post_calls) == 0, "lookback did not bound the first run")
check(table.upserts and table.upserts[-1]["LastProcessedDate"] > "2020",
      "first run with lookback did not pin its window: %r" % table.upserts)

# 7. A stored checkpoint in the old T-format is still readable.
table = FakeTable(entity={"LastProcessedDate": "2026-03-01T12:00:00.000Z"})
result, table, stub = run([item("8.8.8.8", "2026-03-01 13:00:00")], table=table)
check(sum(len(c[1]) for c in stub.post_calls) == 1, "old-format checkpoint was not parsed")

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("checkpoint window: OK (%d checks)" % 10)
