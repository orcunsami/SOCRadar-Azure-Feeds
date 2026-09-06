#!/usr/bin/env python3
"""Every column the code writes to the audit table is declared in the template.

A DCR silently drops columns that are not in its stream declaration, and the
Log Analytics table drops columns that are not in its schema. A counter added
to the code but not to both places would be written and never seen.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: F401
from _harness import FakeCredential
import dcr_logger

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
template = json.load(open(os.path.join(REPO, "azuredeploy.json"), encoding="utf-8"))


def columns(resource_type, name_contains, path):
    for r in template["resources"]:
        if r["type"] == resource_type and name_contains in r["name"]:
            node = r
            for key in path:
                node = node[key]
            return {c["name"] for c in node}
    return None


class Capture:
    def __init__(self):
        self.rows = []

    def post(self, url, **kw):
        self.rows.extend(kw["json"])
        return _harness.Response(204)


for table_var, stream_var, method, sample in (
    ("AuditTableName", "AuditStreamName", "log_audit", {"collections_processed": 1}),
    ("FeedsTableName", "FeedsStreamName", "log_feeds", [{"TimeGenerated": "", "CollectionName": "", "CollectionUUID": "",
                                                         "IndicatorValue": "", "IndicatorType": "", "LatestSeenDate": "", "Source": ""}]),
):
    table_cols = columns("Microsoft.OperationalInsights/workspaces/tables", table_var, ("properties", "schema", "columns"))
    dcr = None
    for r in template["resources"]:
        if r["type"] == "Microsoft.Insights/dataCollectionRules" and stream_var.replace("StreamName", "DcrName") in r["name"]:
            decl = r["properties"]["streamDeclarations"]
            dcr = {c["name"] for c in list(decl.values())[0]["columns"]}
    check(table_cols and dcr, "table or DCR for %s not found in template" % table_var)

    cap = Capture()
    dcr_logger.requests = cap
    logger = dcr_logger.DcrLogger(FakeCredential(), feeds_endpoint="e", feeds_dcr_id="d", feeds_stream="s",
                                  audit_endpoint="e", audit_dcr_id="d", audit_stream="s")
    getattr(logger, method)(sample)
    written = set(cap.rows[0].keys())
    check(written <= (table_cols or set()), "%s: code writes columns the table lacks: %r" % (table_var, sorted(written - (table_cols or set()))))
    check(written <= (dcr or set()), "%s: code writes columns the DCR stream lacks: %r" % (table_var, sorted(written - (dcr or set()))))
    check((table_cols or set()) == (dcr or set()), "%s: table and DCR columns differ: %r" % (table_var, sorted((table_cols or set()) ^ (dcr or set()))))

status_desc = ""
for r in template["resources"]:
    if r["type"] == "Microsoft.OperationalInsights/workspaces/tables" and "AuditTableName" in r["name"]:
        status_desc = next(c.get("description", "") for c in r["properties"]["schema"]["columns"] if c["name"] == "Status")
check("PartialSuccess" in status_desc, "Status column description does not list PartialSuccess: %r" % status_desc)

if failures:
    for line in failures:
        print("FAIL " + line)
    sys.exit(1)
print("audit schema matches the code: OK (%d checks)" % 7)
