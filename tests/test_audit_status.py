#!/usr/bin/env python3
"""A run that lost indicators must not write Success into the audit table.

The audit table is the only place a customer can see what a run did. Status
has to follow the counters, not the absence of an exception.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: F401  (installs the requests stub and the import path)

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


class FakeApp:
    def timer_trigger(self, **_kwargs):
        return lambda fn: fn


def load_function_app():
    functions = types.ModuleType("azure.functions")
    functions.FunctionApp = FakeApp
    functions.TimerRequest = object
    azure = types.ModuleType("azure")
    sys.modules.update({"azure": azure, "azure.functions": functions})
    sys.modules.pop("function_app", None)
    import function_app
    return function_app


def run_with(result=None, error=None):
    module = load_function_app()
    dcr = _harness.FakeDcrLogger()

    class Proc:
        collections = [_harness.COLLECTION]

        def run(self):
            if error:
                raise error
            return result

        def log_audit(self, **kw):
            return dcr.log_audit(kw)

    module.FeedsProcessor.from_env = staticmethod(lambda: Proc())
    raised = None
    try:
        module.socradar_feeds_import(types.SimpleNamespace(past_due=False))
    except Exception as e:  # noqa: BLE001
        raised = e
    return dcr.audits, raised


def totals(**kw):
    base = {"collections_processed": 1, "collections_partial": 0, "collections_failed": 0,
            "indicators_created": 5, "indicators_skipped": 0, "indicators_failed": 0,
            "indicators_unsupported": 0, "errors": []}
    base.update(kw)
    return base


rows, raised = run_with(totals())
check(rows and rows[0]["status"] == "Success", "clean run not Success: %r" % rows)
check(raised is None, "clean run raised: %r" % raised)

rows, raised = run_with(totals(indicators_failed=3, collections_partial=1, collections_processed=0,
                               errors=["c: 3 indicator(s) did not reach Microsoft Sentinel"]))
check(rows and rows[0]["status"] == "PartialSuccess", "lost indicators not PartialSuccess: %r" % rows)
check(rows and rows[0]["indicators_failed"] == 3, "IndicatorsFailed not carried to audit: %r" % rows)
check(rows and "did not reach" in rows[0]["error_message"], "error_message empty on partial run: %r" % rows)

rows, raised = run_with(totals(collections_failed=1, errors=["c (id): Feed API failed"]))
check(rows and rows[0]["status"] == "PartialSuccess", "failed collection not PartialSuccess: %r" % rows)
check(rows and rows[0]["collections_failed"] == 1, "CollectionsFailed not carried: %r" % rows)

rows, raised = run_with(error=RuntimeError("All configured collections failed: x (id): HTTP 401 key=abc"))
check(rows and rows[0]["status"] == "Failed", "raised run not Failed: %r" % rows)
check(rows and "key=***" in rows[0]["error_message"] and "abc" not in rows[0]["error_message"],
      "key leaked into audit error_message: %r" % rows)
check(raised is not None and "abc" not in str(raised), "key leaked into the raised error: %r" % raised)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("audit status follows the counters: OK (%d checks)" % 10)
