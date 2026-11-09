#!/usr/bin/env python3
"""Keep the Function App's DCR settings and Feeds schema one exact contract.

The ingestion endpoint accepts arbitrary stream text, while a DCR can silently
drop unknown fields.  This test therefore follows the ARM expressions, rather
than merely checking that similarly named strings exist in the template.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness  # noqa: F401
import dcr_logger
from stix_builder import StixBuilder


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(REPO, "azuredeploy.json"), encoding="utf-8") as handle:
    TEMPLATE = json.load(handle)

FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)


def resources(resource_type):
    return [r for r in TEMPLATE["resources"] if r.get("type") == resource_type]


def app_settings():
    apps = resources("Microsoft.Web/sites")
    check(len(apps) == 1, "exactly one Function App is required")
    if not apps:
        return {}
    settings = apps[0].get("properties", {}).get("siteConfig", {}).get("appSettings", [])
    result = {}
    for setting in settings:
        name = setting.get("name")
        check(bool(name), "Function App has an app setting without a name")
        check(name not in result, "Function App repeats app setting %r" % name)
        result[name] = setting.get("value")
    return result


def arm_variable(expression):
    match = re.fullmatch(r"\[variables\('([^']+)'\)\]", expression or "")
    check(match is not None, "expected a direct ARM variable expression, got %r" % expression)
    if not match:
        return None
    name = match.group(1)
    value = TEMPLATE.get("variables", {}).get(name)
    check(isinstance(value, str) and bool(value), "ARM variable %s is missing or empty" % name)
    return name, value


def dcr_for(stream_expression):
    matches = []
    for dcr in resources("Microsoft.Insights/dataCollectionRules"):
        declarations = dcr.get("properties", {}).get("streamDeclarations", {})
        if stream_expression in declarations:
            matches.append(dcr)
    check(len(matches) == 1, "stream %r must have exactly one DCR declaration" % stream_expression)
    return matches[0] if len(matches) == 1 else None


def column_contract(columns, owner):
    fields = [(column.get("name"), column.get("type")) for column in columns]
    names = [name for name, _ in fields]
    check(all(isinstance(name, str) and name for name in names), "%s has an empty column name" % owner)
    check(len(names) == len(set(names)), "%s repeats a column name" % owner)
    check(all(isinstance(field_type, str) and field_type for _, field_type in fields), "%s has an empty column type" % owner)
    return fields


def columns(table_name_variable, stream_expression, dcr):
    table_expression = "variables('%s')" % table_name_variable
    matching_tables = [r for r in resources("Microsoft.OperationalInsights/workspaces/tables")
                       if table_expression in r.get("name", "")]
    check(len(matching_tables) == 1, "table resource for %s must exist exactly once" % table_name_variable)
    table_columns = []
    if len(matching_tables) == 1:
        table_columns = column_contract(
            matching_tables[0].get("properties", {}).get("schema", {}).get("columns", []),
            "%s table" % table_name_variable,
        )

    dcr_columns = []
    if dcr:
        declaration = dcr.get("properties", {}).get("streamDeclarations", {}).get(stream_expression, {})
        dcr_columns = column_contract(declaration.get("columns", []), "%s stream declaration" % table_name_variable)
    return table_columns, dcr_columns


def check_stream(setting_name, table_name_variable):
    settings = app_settings()
    expression = settings.get(setting_name)
    variable = arm_variable(expression)
    if not variable:
        return [], []
    variable_name, stream_name = variable
    check(stream_name.startswith("Custom-"), "%s resolves to a non-custom stream %r" % (setting_name, stream_name))
    if setting_name == "FEEDS_STREAM_NAME":
        feeds_table_name = TEMPLATE.get("variables", {}).get("FeedsTableName")
        check(feeds_table_name == "SOCRadar_Feeds_CL", "FeedsTableName must equal 'SOCRadar_Feeds_CL', got %r" % feeds_table_name)
        check(stream_name == "Custom-" + str(feeds_table_name), "FeedsStreamName must equal 'Custom-' + FeedsTableName, got %r" % stream_name)
    dcr = dcr_for(expression)
    if not dcr:
        return [], []
    declarations = dcr["properties"]["streamDeclarations"]
    check(set(declarations) == {expression}, "%s DCR has stray or missing stream declarations" % setting_name)
    flows = dcr["properties"].get("dataFlows", [])
    check(len(flows) == 1, "%s DCR must have one data flow" % setting_name)
    if flows:
        flow = flows[0]
        check(flow.get("streams") == [expression], "%s data flow input stream differs" % setting_name)
        check(flow.get("outputStream") == expression, "%s data flow output stream differs" % setting_name)
        if setting_name == "FEEDS_STREAM_NAME":
            transform = flow.get("transformKql")
            check(isinstance(transform, str) and transform.strip() == "source",
                  "Feeds data flow transformKql must be identity 'source', got %r" % transform)
    check(variable_name.endswith("StreamName"), "%s does not resolve through a StreamName variable" % setting_name)
    return columns(table_name_variable, expression, dcr)


feeds_table, feeds_dcr = check_stream("FEEDS_STREAM_NAME", "FeedsTableName")
audit_table, audit_dcr = check_stream("AUDIT_STREAM_NAME", "AuditTableName")

sample_log = StixBuilder.build_feed_log(
    {"feed": "198.51.100.8", "feed_type": "ip", "latest_seen_date": "2026-11-09"},
    {"id": "collection-id", "name": "Collection"},
)
expected_feeds = set(sample_log)
check(len(expected_feeds) == 7, "build_feed_log must emit exactly seven fields, got %r" % sorted(expected_feeds))
check(expected_feeds == {name for name, _ in feeds_table}, "build_feed_log fields differ from SOCRadar_Feeds_CL: %r" % sorted(expected_feeds ^ {name for name, _ in feeds_table}))
check(expected_feeds == {name for name, _ in feeds_dcr}, "build_feed_log fields differ from Feeds DCR: %r" % sorted(expected_feeds ^ {name for name, _ in feeds_dcr}))
check(feeds_table == feeds_dcr and bool(feeds_table), "Feeds table/DCR name and type columns must be nonempty and exactly equal")
check(audit_table == audit_dcr and bool(audit_table), "Audit table/DCR name and type columns must be nonempty and exactly equal")

# This proves the runtime uses the exact two environment keys, rather than a
# fallback setting that happens to share the same literal stream name.
old_environment = dict(os.environ)
try:
    os.environ.clear()
    os.environ.update({"FEEDS_STREAM_NAME": "feeds-from-env", "AUDIT_STREAM_NAME": "audit-from-env"})
    logger = dcr_logger.DcrLogger.from_env(object())
    check(logger.feeds_stream == "feeds-from-env", "DcrLogger.from_env does not consume FEEDS_STREAM_NAME")
    check(logger.audit_stream == "audit-from-env", "DcrLogger.from_env does not consume AUDIT_STREAM_NAME")
finally:
    os.environ.clear()
    os.environ.update(old_environment)


class PostedRows:
    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs.get("json")))
        return type("Response", (), {"status_code": 204, "text": ""})()


# A correct from_env assignment is insufficient if log_feeds later posts through
# audit_stream. Use intentionally distinct routes and stream names to prove the
# value reaches the actual ingestion URL with its matching payload.
posted = PostedRows()
dcr_logger.requests = posted
runtime_logger = dcr_logger.DcrLogger(
    _harness.FakeCredential(),
    feeds_endpoint="https://feeds.example",
    feeds_dcr_id="feeds-dcr",
    feeds_stream="Custom-Feeds-Sentinel",
    audit_endpoint="https://audit.example",
    audit_dcr_id="audit-dcr",
    audit_stream="Custom-Audit-Sentinel",
)
check(runtime_logger.log_feeds([sample_log]), "log_feeds did not report a successful POST")
check(runtime_logger.log_audit({"collections_processed": 1}), "log_audit did not report a successful POST")
check(len(posted.posts) == 2, "expected one Feeds and one audit ingestion POST")
if len(posted.posts) == 2:
    feeds_url, feeds_payload = posted.posts[0]
    audit_url, audit_payload = posted.posts[1]
    check(feeds_url == "https://feeds.example/dataCollectionRules/feeds-dcr/streams/Custom-Feeds-Sentinel?api-version=2023-01-01",
          "log_feeds POST URL used wrong route")
    check(feeds_payload == [sample_log], "log_feeds POST payload differs from build_feed_log output")
    check(audit_url == "https://audit.example/dataCollectionRules/audit-dcr/streams/Custom-Audit-Sentinel?api-version=2023-01-01",
          "log_audit POST URL used wrong route")
    check(isinstance(audit_payload, list) and len(audit_payload) == 1 and "CollectionsProcessed" in audit_payload[0],
          "log_audit POST payload is not an audit record")

settings = app_settings()
client_id = settings.get("AZURE_CLIENT_ID")
check(client_id is not None, "AZURE_CLIENT_ID app setting is missing")
check(isinstance(client_id, str) and re.fullmatch(
    r"\[reference\(resourceId\('Microsoft\.ManagedIdentity/userAssignedIdentities', variables\('managedIdentityName'\)\), '[-0-9]+'\)\.clientId\]",
    client_id or "",
) is not None, "AZURE_CLIENT_ID must reference the UAMI clientId, got %r" % client_id)
apps = resources("Microsoft.Web/sites")
if apps:
    identities = apps[0].get("identity", {})
    check(identities.get("type") == "UserAssigned", "Function App identity type must be UserAssigned")
    expected_uami = "[resourceId('Microsoft.ManagedIdentity/userAssignedIdentities', variables('managedIdentityName'))]"
    check(set(identities.get("userAssignedIdentities", {})) == {expected_uami}, "Function App must attach exactly the AZURE_CLIENT_ID UAMI")

if FAILURES:
    print("FAIL test_arm_stream_contract")
    for failure in FAILURES:
        print("  - " + failure)
    sys.exit(1)
print("ok test_arm_stream_contract (Feeds stream, audit stream POST routes, UAMI, 7 exact Feed fields)")
