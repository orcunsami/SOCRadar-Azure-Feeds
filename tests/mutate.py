#!/usr/bin/env python3
"""Break the code on purpose and prove the tests notice.

Each entry below breaks one invariant, runs the test that should catch it, and
restores the file. A mutation that survives is BLIND and the run exits non-zero.

Bytecode is disabled for the child runs: a same-size mutation applied and
reverted inside one second leaves a .pyc that still looks current, and the next
run would execute the mutated bytecode.

    python3 tests/mutate.py
"""

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTATIONS = [
 ("string checkpoint compare", "FunctionApp/feeds_processor.py",
  '''            elif window is None or seen >= window:''',
  '''            elif window is None or str(item.get("latest_seen_date")) >= format_checkpoint(window):''',
  "tests/test_checkpoint_window.py"),
 ("no overlap window", "FunctionApp/feeds_processor.py",
  '''CHECKPOINT_OVERLAP_HOURS = 48''', '''CHECKPOINT_OVERLAP_HOURS = 0''',
  "tests/test_checkpoint_window.py"),
 ("checkpoint from clock", "FunctionApp/feeds_processor.py",
  '''        advance_to = max(pinned, newest) if newest else pinned''',
  '''        advance_to = datetime.now(timezone.utc)''',
  "tests/test_checkpoint_window.py"),
 ("checkpoint advances on failure", "FunctionApp/feeds_processor.py",
  '''        if result["failed"]:
            self.save_checkpoint(cid, name, pinned, len(items), result["created"], result["failed"])''',
  '''        if False:
            pass''',
  "tests/test_upload_failure_keeps_checkpoint.py"),
 ("failed counted as skipped", "FunctionApp/feeds_processor.py",
  '''        return 0, 0, len(indicators)

    # ----''', '''        return 0, len(indicators), 0

    # ----''',
  "tests/test_upload_failure_keeps_checkpoint.py"),
 ("keeps uploading after a loss", "FunctionApp/feeds_processor.py",
  '''                             name, batch_num, total_batches, format_checkpoint(pinned))
                break''',
  '''                             name, batch_num, total_batches, format_checkpoint(pinned))
                continue''',
  "tests/test_upload_failure_keeps_checkpoint.py"),
 ("Retry-After ignored", "FunctionApp/feeds_processor.py",
  '''            wait = min(float(retry_after), MAX_RETRY_SLEEP)''',
  '''            wait = min(2 ** attempt, MAX_RETRY_SLEEP)''',
  "tests/test_upload_failure_keeps_checkpoint.py"),
 ("no retry at all", "FunctionApp/feeds_processor.py",
  '''MAX_ATTEMPTS = 3''', '''MAX_ATTEMPTS = 1''',
  "tests/test_upload_failure_keeps_checkpoint.py"),
 ("status always Success", "FunctionApp/function_app.py",
  '''    if result["collections_failed"] or result["collections_partial"] or result["indicators_failed"]:''',
  '''    if False:''',
  "tests/test_audit_status.py"),
 ("key not redacted", "FunctionApp/feeds_processor.py",
  '''    return re.sub(r"(key=)[^&\\s'\\"]+", r"\\1***", str(text))''',
  '''    return str(text)''',
  "tests/test_feed_contract.py"),
 ("200 error body is an empty feed", "FunctionApp/feeds_processor.py",
  '''        raise RuntimeError(
            f"Feed API returned an unexpected body for {collection_id}: {redact(str(data)[:200])}"
        )''',
  '''        return []''',
  "tests/test_feed_contract.py"),
 ("table errors pose as first run", "FunctionApp/feeds_processor.py",
  '''            if self._is_not_found(e):
                return None''',
  '''            return None''',
  "tests/test_feed_contract.py"),
 ("uuid4 ids again", "FunctionApp/stix_builder.py",
  '''        return "indicator--" + str(uuid.uuid5(ID_NAMESPACE, f"{stix_type}|{value}|{collection_id}"))''',
  '''        return "indicator--" + str(uuid.uuid4())''',
  "tests/test_stix_builder.py"),
 ("unknown type maps to domain", "FunctionApp/stix_builder.py",
  '''    return None


def _escape''', '''    return "domain-name"


def _escape''',
  "tests/test_stix_builder.py"),
 ("no pattern escaping", "FunctionApp/stix_builder.py",
  '''    return value.replace("\\\\", "\\\\\\\\").replace("'", "\\\\'")''',
  '''    return value''',
  "tests/test_stix_builder.py"),
 ("feeds table repeats the overlap", "FunctionApp/feeds_processor.py",
  '''                is_new = checkpoint is None or (seen is not None and seen > checkpoint)''',
  '''                is_new = True''',
  "tests/test_feeds_table_no_duplicates.py"),
 ("table rows shift off their batch", "FunctionApp/feeds_processor.py",
  '''                feed_logs.append(StixBuilder.build_feed_log(item, col) if is_new else None)''',
  '''                if is_new:
                    feed_logs.append(StixBuilder.build_feed_log(item, col))''',
  "tests/test_feeds_table_no_duplicates.py"),
 ("fetch timeout ignores the budget", "FunctionApp/feeds_processor.py",
  '''                timeout = 60 if remaining is None else max(1, min(60, remaining))''',
  '''                timeout = 60''',
  "tests/test_fetch_time_budget.py"),
 ("no collections is a success", "FunctionApp/feeds_processor.py",
  '''            raise RuntimeError("No collections configured: enable a recommended collection or set CustomCollectionIds")''',
  '''            return totals''',
  "tests/test_no_collections.py"),
 ("fetch ignores the time budget", "FunctionApp/feeds_processor.py",
  '''            if remaining is not None and remaining <= 0:
                raise RuntimeError(f"Time budget exhausted before fetching''',
  '''            if remaining is not None and remaining <= -1e9:
                raise RuntimeError(f"Time budget exhausted before fetching''',
  "tests/test_fetch_time_budget.py"),
 ("checkpoint advances on table failure", "FunctionApp/feeds_processor.py",
  '''        elif not result["feeds_table_ok"]:''',
  '''        elif False:''',
  "tests/test_feeds_table_failure_holds_checkpoint.py"),
 ("first run pins a checkpoint on table failure", "FunctionApp/feeds_processor.py",
  '''            if checkpoint is not None:
                self.save_checkpoint(cid, name, pinned, len(items), result["created"])''',
  '''            if True:
                self.save_checkpoint(cid, name, pinned, len(items), result["created"])''',
  "tests/test_feeds_table_failure_holds_checkpoint.py"),
 ("DCR column removed", "azuredeploy.json",
  '''                            { "name": "IndicatorsFailed", "type": "int" },
''', '''''',
  "tests/test_audit_schema.py"),
 ("table column removed", "azuredeploy.json",
  '''                        { "name": "IndicatorsFailed", "type": "int", "description": "Indicators that never reached Microsoft Sentinel and will be sent again" },
''', '''''',
  "tests/test_audit_schema.py"),
 ("run table hides the failure counters", "azuredeploy.json",
  '''IndicatorsSkipped, CollectionsFailed, IndicatorsFailed, DurationMs''', '''IndicatorsSkipped, DurationMs''',
  "tests/test_audit_schema.py"),
 ("pointer reading removed", "azuredeploy.json",
  '''staged() { [ \\"$(shape)\\" = blob ]; }; ''', '''''',
  "tests/test_package_push.py"),
 ("pointer echoed with its SAS", "azuredeploy.json",
  '''staged() { [ \\"$(shape)\\" = blob ]; }; ''', '''staged() { echo \\"$(pointer)\\"; [ \\"$(shape)\\" = blob ]; }; ''',
  "tests/test_package_push.py"),
 ("settings read not retried", "azuredeploy.json",
  '''for wait in $(seq 1 8); ''', '''for wait in $(seq 1 1); ''',
  "tests/test_package_push.py"),
 ("single push attempt", "azuredeploy.json",
  '''for attempt in $(seq 1 6); ''', '''for attempt in $(seq 1 1); ''',
  "tests/test_package_push.py"),
 ("index poll cut to one minute", "azuredeploy.json",
  '''for i in $(seq 1 40); ''', '''for i in $(seq 1 4); ''',
  "tests/test_package_push.py"),
 ("no restart after staging", "azuredeploy.json",
  '''az functionapp restart ''', '''true restart ''',
  "tests/test_package_push.py"),
 ("container log deleted on failure", "azuredeploy.json",
  '''"cleanupPreference": "OnSuccess"''', '''"cleanupPreference": "Always"''',
  "tests/test_package_push.py"),
 ("failed run's evidence expires in an hour", "azuredeploy.json",
  '''"retentionInterval": "PT26H"''', '''"retentionInterval": "PT1H"''',
  "tests/test_package_push.py"),
 ("recovery loses rows when a failed run moves the checkpoint", "FunctionApp/feeds_processor.py",
  '''        if result["failed"]:
            self.save_checkpoint(cid, name, pinned, len(items), result["created"], result["failed"])''',
  '''        if False:
            pass''',
  "tests/test_feeds_table_no_duplicates.py"),
 ("harness: dedup assert always true", "scripts/portal_test.sh",
  '''[ "$TI_DISTINCT_FINAL" = "$TI_DISTINCT_AFTER" ]''', '''true''',
  "tests/test_portal_harness.py"),
 ("harness: dedup read before rows land", "scripts/portal_test.sh",
  '''if wait_ti_rows $((TI_ROWS_BEFORE2 + RUN2_A_CREATED)) 600; then''', '''if true; then''',
  "tests/test_portal_harness.py"),
 ("harness: old audit row ends the wait", "scripts/portal_test.sh",
  '''[ "$n" -gt "$before" ]''', '''[ "$n" -ge "$before" ]''',
  "tests/test_portal_harness.py"),
 ("harness: run status ignored", "scripts/portal_test.sh",
  '''[ "$A_STATUS" = "Success" ] && [ "$A_FAILED" = "0" ]''', '''true''',
  "tests/test_portal_harness.py"),
 ("harness: zero ids pass", "scripts/portal_test.sh",
  '''[ "$TI_DISTINCT_AFTER" -gt 0 ] &&''', '''true &&''',
  "tests/test_portal_harness.py"),
 ("harness: dedup judged on a silent run", "scripts/portal_test.sh",
  '''if [ "$RUN2_A_CREATED" -gt 0 ]; then''', '''if true; then''',
  "tests/test_portal_harness.py"),
]
blind = []
for name, path, old, new, test in MUTATIONS:
    full = os.path.join(REPO, path)
    orig = open(full, encoding="utf-8").read()
    if old not in orig:
        print("SKIP  %-32s anchor not found in %s" % (name, path)); blind.append(name); continue
    try:
        open(full, "w", encoding="utf-8").write(orig.replace(old, new, 1))
        try:
            r = subprocess.run([sys.executable, test], cwd=REPO, capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            rc = r.returncode
        except subprocess.TimeoutExpired:
            rc = 124
    finally:
        open(full, "w", encoding="utf-8").write(orig)
    if rc == 0:
        print("BLIND %-32s %s still passed" % (name, test)); blind.append(name)
    else:
        print("caught %-31s %s" % (name, test))
print("\nblind:", len(blind), "of", len(MUTATIONS))
sys.exit(1 if blind else 0)
