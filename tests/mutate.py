#!/usr/bin/env python3
"""Break the code on purpose and prove the tests notice.

Each entry below breaks one invariant, runs the test that should catch it, and
restores the file. A mutation that survives is BLIND and the run exits non-zero.

Bytecode is disabled for the child runs: a same-size mutation applied and
reverted inside one second leaves a .pyc that still looks current, and the next
run would execute the mutated bytecode.

    python3 tests/mutate.py
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRASH_MARKERS = ("TimeoutExpired", "ModuleNotFoundError", "ImportError", "SyntaxError", "IndentationError")
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
 ("harness: reader drops live Tables/Rows keys", "scripts/portal_test.sh",
  '''(j["Tables"] if "Tables" in j else j["tables"])[0]''', '''j["tables"][0]''',
  "tests/test_portal_harness.py"),
 ("harness: reader drops lower-case keys", "scripts/portal_test.sh",
  '''t["Rows"] if "Rows" in t else t["rows"]''', '''t["Rows"]''',
  "tests/test_portal_harness.py"),
 ("harness: reader key renamed", "scripts/portal_test.sh",
  '''j["Tables"] if "Tables" in j''', '''j["TablesX"] if "TablesX" in j''',
  "tests/test_portal_harness.py"),
 ("harness: dedup judged on a silent run", "scripts/portal_test.sh",
  '''if [ "$RUN2_A_CREATED" -gt 0 ]; then''', '''if true; then''',
  "tests/test_portal_harness.py"),
 ("harness: table list failure exits silently", "scripts/portal_test.sh",
  '''    if ! TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key \\
        --query "[?name=='FeedState'].name" -o tsv 2>"$AZERR"); then''',
  '''    TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key \\
        --query "[?name=='FeedState'].name" -o tsv 2>"$AZERR")
    if [ $? -ne 0 ]; then''',
  "tests/test_portal_harness.py"),
 ("harness: entity query failure exits silently", "scripts/portal_test.sh",
  '''        if ! ENTITY_COUNT=$(az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" \\
            --auth-mode key --query "items | length(@)" -o tsv 2>"$AZERR"); then''',
  '''        ENTITY_COUNT=$(az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" \\
            --auth-mode key --query "items | length(@)" -o tsv 2>"$AZERR")
        if [ $? -ne 0 ]; then''',
  "tests/test_portal_harness.py"),
 ("harness: trigger failure exits silently (run 1)", "scripts/portal_test.sh",
  '''if ! HTTP_CODE=$(trigger_function); then
    echo "  Trigger: CANNOT MEASURE ($HTTP_CODE)"
    TRIG1="CANNOT-MEASURE"
elif''', '''HTTP_CODE=$(trigger_function)
if false; then
    TRIG1="CANNOT-MEASURE"
elif''',
  "tests/test_portal_harness.py"),
 ("harness: trigger failure exits silently (run 2)", "scripts/portal_test.sh",
  '''if HTTP_CODE=$(trigger_function) && case "$HTTP_CODE" in 200|202|204) true ;; *) false ;; esac; then
    echo "  Triggered (HTTP $HTTP_CODE)"''', '''HTTP_CODE=$(trigger_function)
if true; then
    echo "  Triggered (HTTP $HTTP_CODE)"''',
  "tests/test_portal_harness.py"),
 ("harness: curl failure reads as an answer", "scripts/portal_test.sh",
  '''if [ "$rc" -ne 0 ]; then echo "curl failed (rc=$rc, http $code)"; return 1; fi''', '''if false; then return 1; fi''',
  "tests/test_portal_harness.py"),
 ("harness: account show failure exits silently", "scripts/portal_test.sh",
  '''if ! ACCOUNT=$(az account show --query "user.name" -o tsv) || [ -z "$ACCOUNT" ]; then''',
  '''ACCOUNT=$(az account show --query "user.name" -o tsv)
if [ -z "$ACCOUNT" ]; then''',
  "tests/test_portal_harness.py"),
 ("harness: active subscription failure exits silently", "scripts/portal_test.sh",
  '''if ! ACTIVE_SUB=$(az account show --query id -o tsv); then''',
  '''ACTIVE_SUB=$(az account show --query id -o tsv)
if false; then''',
  "tests/test_portal_harness.py"),
 ("harness: role lookup failure exits silently", "scripts/portal_test.sh",
  '''    if ! SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \\
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" \\
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv); then
        echo "  Sentinel Contributor: CANNOT MEASURE"
    elif''',
  '''    SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \\
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" \\
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv)
    if false; then
        echo "  Sentinel Contributor: CANNOT MEASURE"
    elif''',
  "tests/test_portal_harness.py"),
 ("harness: function app list pipes into head", "scripts/portal_test.sh",
  '''if ! FUNC_APP_NAME=$(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv); then''',
  '''if ! FUNC_APP_NAME=$(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv 2>/dev/null | head -1); then''',
  "tests/test_portal_harness.py"),
 ("harness: storage account list pipes into head", "scripts/portal_test.sh",
  '''if ! SA_OUT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv); then''',
  '''if ! SA_OUT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv 2>/dev/null | head -1); then''',
  "tests/test_portal_harness.py"),
 ("harness: storage account lookup failure reads as absent", "scripts/portal_test.sh",
  '''    SA_ERR=1''', '''    SA_ERR=''',
  "tests/test_portal_harness.py"),
 ("harness: failed table list reads as absent", "scripts/portal_test.sh",
  '''        STORAGE_STATE="CANNOT-MEASURE"; TABLE_EXISTS=""''', '''        STORAGE_STATE="MISSING"; TABLE_EXISTS=""''',
  "tests/test_portal_harness.py"),
 ("harness: failed entity query reads as present", "scripts/portal_test.sh",
  '''            echo "  Checkpoint entries: CANNOT MEASURE ($(az_err))"
            STORAGE_STATE="CANNOT-MEASURE"''', '''            echo "  Checkpoint entries: CANNOT MEASURE ($(az_err))"
            STORAGE_STATE="OK"''',
  "tests/test_portal_harness.py"),
 ("harness: function app start failure is silent", "scripts/portal_test.sh",
  '''    if ! az functionapp start --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" -o none; then''',
  '''    az functionapp start --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" -o none 2>/dev/null
    if false; then''',
  "tests/test_portal_harness.py"),
 ("harness: subscription guard removed", "scripts/portal_test.sh",
  '''if [ "$ACTIVE_SUB" != "$SUBSCRIPTION_ID" ]; then''', '''if false; then''',
  "tests/test_portal_harness.py"),
 ("harness: app lookup before the subscription guard", "scripts/portal_test.sh",
  '''# Check login and subscription before anything''',
  '''FUNC_APP_NAME=$(az functionapp list -g "$RESOURCE_GROUP" --query "[].name" -o tsv)
# Check login and subscription before anything''',
  "tests/test_portal_harness.py"),
 ("harness: show failure reads as absent", "scripts/portal_test.sh",
  '''if [ "$FA_RC" -eq 3 ]; then''', '''if [ "$FA_RC" -ne 0 ]; then''',
  "tests/test_portal_harness.py"),
 ("harness: absent app reads as could-not-look", "scripts/portal_test.sh",
  '''if [ "$FA_RC" -eq 3 ]; then''', '''if false; then''',
  "tests/test_portal_harness.py"),
 ("harness: empty state is fine", "scripts/portal_test.sh",
  '''elif [ "$FA_RC" -ne 0 ] || [ -z "$FA_STATE" ]; then''', '''elif [ "$FA_RC" -ne 0 ]; then''',
  "tests/test_portal_harness.py"),
 ("harness: identity failure skips the role check", "scripts/portal_test.sh",
  '''if ! FA_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "SOCRadar-Feeds-MI" --query principalId -o tsv) || [ -z "$FA_PRINCIPAL" ]; then
    echo "  Sentinel Contributor: CANNOT MEASURE (identity SOCRadar-Feeds-MI unreadable)"
else''',
  '''FA_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "SOCRadar-Feeds-MI" --query principalId -o tsv 2>/dev/null || echo "")
if [ -z "$FA_PRINCIPAL" ]; then
    :
else''',
  "tests/test_portal_harness.py"),
 ("harness: rows not compared with created", "scripts/portal_test.sh",
  '''[ "$TI1_ROWS" -ge "$RUN1_A_CREATED" ]; }''', '''true; }''',
  "tests/test_portal_harness.py"),
 ("harness: rows judged after run 2", "scripts/portal_test.sh",
  '''[ "$TI1_ROWS" -ge "$RUN1_A_CREATED" ]; }''', '''[ "$TI_ROWS_SEEN" -ge "$RUN1_A_CREATED" ]; }''',
  "tests/test_portal_harness.py"),
 ("harness: Success with failed>0 passes", "scripts/portal_test.sh",
  '''[ "$A_STATUS" = "Success" ] && [ "$A_FAILED" = "0" ]''', '''[ "$A_STATUS" = "Success" ]''',
  "tests/test_portal_harness.py"),
 ("setup: api key on the az command line", "scripts/portal_setup.sh",
  "--parameters @\"$SECRETS_FILE\" \\", "--parameters SocradarApiKey=\"$SOCRADAR_API_KEY\" \\",
  "tests/test_setup_secrets.py"),
 ("setup: package SAS on the az command line", "scripts/portal_setup.sh",
  "        WorkspaceName=\"$WORKSPACE_NAME\" \\", "        PackageUri=\"$PACKAGE_URI\" WorkspaceName=\"$WORKSPACE_NAME\" \\",
  "tests/test_setup_secrets.py"),
 ("setup: secrets file left behind", "scripts/portal_setup.sh",
  "trap 'rm -f \"$SECRETS_FILE\"' EXIT", "trap : EXIT",
  "tests/test_setup_secrets.py"),
 ("setup: secrets file world-readable", "scripts/portal_setup.sh",
  "SECRETS_FILE=$(mktemp)", "SECRETS_FILE=$(mktemp); chmod 644 \"$SECRETS_FILE\"",
  "tests/test_setup_secrets.py"),
 ('harness: cleanup never stops the app', 'scripts/portal_test.sh',
  '    if ! az functionapp stop --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP"; then',
  '    if ! true; then',
  'tests/test_portal_harness.py'),
 ('harness: cleanup trap removed', 'scripts/portal_test.sh',
  'trap cleanup EXIT',
  ':',
  'tests/test_portal_harness.py'),
 ('harness: failed stop is not an error', 'scripts/portal_test.sh',
  'may still be billing"; bad=1',
  'may still be billing"; bad=0',
  'tests/test_portal_harness.py'),
 ('harness: app still Running is accepted', 'scripts/portal_test.sh',
  '    if [ "$FA_STATE" != "Stopped" ]; then',
  '    if false; then',
  'tests/test_portal_harness.py'),
 ('harness: cleanup keeps exit 0', 'scripts/portal_test.sh',
  '    [ "$rc" -eq 0 ] && [ "$bad" -ne 0 ] && exit 1',
  '    true',
  'tests/test_portal_harness.py'),
 ('harness: unreadable rows read as FAIL (no rows)', 'scripts/portal_test.sh',
  'is_num "$TI_ROWS_SEEN"; then\n                echo "  The second',
  'true; then\n                echo "  The second',
  'tests/test_portal_harness.py'),
 ('harness: missing Sentinel Contributor passes', 'scripts/portal_test.sh',
  'ROLE_STATE="MISSING"; fi',
  'ROLE_STATE="OK"; fi',
  'tests/test_portal_harness.py'),
 ('harness: role row never fails', 'scripts/portal_test.sh',
  'MISSING) row "Sentinel Contributor" "FAIL (missing)" ;;',
  'MISSING) printf "| %-24s | %-15s |\\\\n" "Sentinel Contributor" "FAIL (missing)" ;;',
  'tests/test_portal_harness.py'),
 ('setup: subscription guard removed', 'scripts/portal_setup.sh',
  'if [ "$ACTIVE_SUB" != "$SUBSCRIPTION_ID" ]; then',
  'if false; then',
  'tests/test_setup_secrets.py'),
 ('setup: unreadable subscription goes on', 'scripts/portal_setup.sh',
  'if ! ACTIVE_SUB=$(az account show --query id -o tsv); then',
  'ACTIVE_SUB=$(az account show --query id -o tsv 2>/dev/null) || true\nif false; then',
  'tests/test_setup_secrets.py'),
 ('setup: failed functionapp show goes on', 'scripts/portal_setup.sh',
  'elif [ "$FA_RC" -ne 0 ] || [ -z "$FA_STATE" ]; then',
  'elif false; then',
  'tests/test_setup_secrets.py'),
 ('setup: absent app reads as could-not-look', 'scripts/portal_setup.sh',
  'if [ "$FA_RC" -eq 3 ]; then',
  'if false; then',
  'tests/test_setup_secrets.py'),
 ('setup: failed identity look goes on', 'scripts/portal_setup.sh',
  'elif [ "$ID_RC" -ne 0 ] || [ -z "$FA_PRINCIPAL" ]; then',
  'elif false; then',
  'tests/test_setup_secrets.py'),
 ('setup: failed table list reads as absent', 'scripts/portal_setup.sh',
  'echo "  FeedState Table: CANNOT MEASURE (az storage table list failed)"; exit 1',
  'TABLE_EXISTS=""',
  'tests/test_setup_secrets.py'),
 ('setup: xtrace on at the script start', 'scripts/portal_setup.sh',
  '{ set +x; } 2>/dev/null\nset -e',
  'set -x\nset -e',
  'tests/test_setup_secrets.py'),
 ('setup: xtrace on at the secrets file', 'scripts/portal_setup.sh',
  '{ set +x; } 2>/dev/null\nSK=',
  'set -x\nSK=',
  'tests/test_setup_secrets.py'),
 ('paths: subscription guard removed', 'scripts/test_deploy_paths.sh',
  'if [ -z "${TEST_SUBSCRIPTION_ID:-}" ] || [ "$ACTIVE_SUB" != "$TEST_SUBSCRIPTION_ID" ]; then',
  'if false; then',
  'tests/test_deploy_paths_guards.py'),
 ('paths: subscription read hides its reason', 'scripts/test_deploy_paths.sh',
  'if ! ACTIVE_SUB=$(az account show --query id -o tsv); then',
  'if ! ACTIVE_SUB=$(az account show --query id -o tsv 2>/dev/null); then',
  'tests/test_deploy_paths_guards.py'),
 ('paths: api key on the az command line', 'scripts/test_deploy_paths.sh',
  '--parameters @"$SECRETS_FILE" WorkspaceLocation',
  '--parameters SocradarApiKey="$API_KEY" WorkspaceLocation',
  'tests/test_deploy_paths_guards.py'),
 ('paths: api key echoed from an az error', 'scripts/test_deploy_paths.sh',
  '${msg//"$API_KEY"/***}',
  '$msg',
  'tests/test_deploy_paths_guards.py'),
 ('paths: key file left behind', 'scripts/test_deploy_paths.sh',
  '    rm -f "$SECRETS_FILE"\n    if [ "$KEEP"',
  '    true\n    if [ "$KEEP"',
  'tests/test_deploy_paths_guards.py'),
 ('paths: key file world-readable', 'scripts/test_deploy_paths.sh',
  'SECRETS_FILE=$(mktemp)\nSK=',
  'SECRETS_FILE=$(mktemp); chmod 644 "$SECRETS_FILE"\nSK=',
  'tests/test_deploy_paths_guards.py'),
 ('harness: table list without --auth-mode key', 'scripts/portal_test.sh',
  'az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key \\\n',
  'az storage table list --account-name "$STORAGE_ACCOUNT" \\\n',
  'tests/test_portal_harness.py'),
 ('harness: entity count without --auth-mode key', 'scripts/portal_test.sh',
  '            --auth-mode key --query "items | length(@)"',
  '            --query "items | length(@)"',
  'tests/test_portal_harness.py'),
 ('harness: entity display without --auth-mode key', 'scripts/portal_test.sh',
  '"$STORAGE_ACCOUNT" --auth-mode key \\\n                --query "items[]',
  '"$STORAGE_ACCOUNT" \\\n                --query "items[]',
  'tests/test_portal_harness.py'),
 ('harness: table list stderr merged into the answer', 'scripts/portal_test.sh',
  '-o tsv 2>"$AZERR"); then\n        echo "  FeedState Table: CANNOT',
  '-o tsv 2>&1); then\n        echo "  FeedState Table: CANNOT',
  'tests/test_portal_harness.py'),
 ('harness: entity count stderr merged into the answer', 'scripts/portal_test.sh',
  '-o tsv 2>"$AZERR"); then\n            echo "  Checkpoint entries: CANNOT MEASURE ($(az_err))"',
  '-o tsv 2>&1); then\n            echo "  Checkpoint entries: CANNOT MEASURE ($(az_err))"',
  'tests/test_portal_harness.py'),
 ('harness: unreadable TI counts read FAIL (NA)', 'scripts/portal_test.sh',
  '    row "TI Indicators" "CANNOT-MEASURE"',
  '    row "TI Indicators" "FAIL (NA)"',
  'tests/test_portal_harness.py'),
 ('harness: pre-test role text says may fail', 'scripts/portal_test.sh',
  'echo "  Sentinel Contributor: MISSING"; ROLE_STATE',
  'echo "  Sentinel Contributor: MISSING (may fail)"; ROLE_STATE',
  'tests/test_portal_harness.py'),
 ('harness: run 2 takes any HTTP code', 'scripts/portal_test.sh',
  '&& case "$HTTP_CODE" in 200|202|204) true ;; *) false ;; esac; then',
  '; then',
  'tests/test_portal_harness.py'),
 ('harness: run 1 rejected trigger still waits', 'scripts/portal_test.sh',
  'TRIG1="FAIL (HTTP $HTTP_CODE)"',
  'TRIG1="OK"',
  'tests/test_portal_harness.py'),
 ('harness: zero checkpoint entries pass', 'scripts/portal_test.sh',
  'elif [ "$ENTITY_COUNT" -eq 0 ]; then',
  'elif false; then',
  'tests/test_portal_harness.py'),
 ('harness: non-numeric checkpoint count passes', 'scripts/portal_test.sh',
  'elif ! is_num "$ENTITY_COUNT"; then',
  'elif false; then',
  'tests/test_portal_harness.py'),
 ('harness: empty checkpoint row passes', 'scripts/portal_test.sh',
  'EMPTY)   row "Storage Checkpoint" "FAIL (empty)" ;;',
  'EMPTY)   row "Storage Checkpoint" "PASS" ;;',
  'tests/test_portal_harness.py'),
 ('harness: unreadable audit row reads FAIL (NA)', 'scripts/portal_test.sh',
  'elif [ "$A_STATUS" = "NA" ] || [ "$A_STATUS" = "EMPTY" ]; then echo "CANNOT-MEASURE"',
  'elif false; then echo "CANNOT-MEASURE"',
  'tests/test_portal_harness.py'),
 ('harness: unreadable audit count waits as TIMEOUT', 'scripts/portal_test.sh',
  'is_num "$before" || { echo "  CANNOT MEASURE: audit table unreadable"; return 2; }',
  'true',
  'tests/test_portal_harness.py'),
 ('harness: law failure reason hidden', 'scripts/portal_test.sh',
  '|| { echo "  law: $(az_err)" >&2; echo NA; return 0; }',
  '|| { echo NA; return 0; }',
  'tests/test_portal_harness.py'),
 ('harness: missing config goes on', 'scripts/portal_test.sh',
  'if [ -z "$SUBSCRIPTION_ID" ] || [ -z "$RESOURCE_GROUP" ] || [ -z "$WORKSPACE_NAME" ]; then',
  'if false; then',
  'tests/test_portal_harness.py'),
 ('setup: missing storage role goes on', 'scripts/portal_setup.sh',
  'echo "  Storage Table Data Contributor: MISSING"; exit 1;',
  'echo "  Storage Table Data Contributor: MISSING"; true;',
  'tests/test_setup_secrets.py'),
 ('setup: storage role looked up subscription-wide', 'scripts/portal_setup.sh',
  '--assignee "$FA_PRINCIPAL" --scope "$STORAGE_ID"',
  '--assignee "$FA_PRINCIPAL" --all',
  'tests/test_setup_secrets.py'),
 ('setup: absent storage account goes on', 'scripts/portal_setup.sh',
  'if [ -z "$STORAGE_ID" ]; then',
  'if false; then',
  'tests/test_setup_secrets.py'),
 ('setup: failed storage account list goes on', 'scripts/portal_setup.sh',
  'echo "ERROR: CANNOT MEASURE: az storage account list failed (reason above)"; exit 1',
  'true',
  'tests/test_setup_secrets.py'),
 ('setup: failed role list reads MISSING', 'scripts/portal_setup.sh',
  'echo "ERROR: CANNOT MEASURE: az role assignment list failed (workspace scope)"; exit 1',
  'SENTINEL_ROLE=""',
  'tests/test_setup_secrets.py'),
 ('setup: table list without --auth-mode key', 'scripts/portal_setup.sh',
  'az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key --query',
  'az storage table list --account-name "$STORAGE_ACCOUNT" --query',
  'tests/test_setup_secrets.py'),
 ('setup: unreadable deployment outputs exit silently', 'scripts/portal_setup.sh',
  'echo "  deployment outputs unreadable (reason above); looking in the resource group"',
  'exit 1',
  'tests/test_setup_secrets.py'),
 ('setup: failed functionapp list reads absent', 'scripts/portal_setup.sh',
  'echo "ERROR: CANNOT MEASURE: az functionapp list failed"; exit 1',
  'FA_OUT=""',
  'tests/test_setup_secrets.py'),
 ('paths: failed restart still passes', 'scripts/test_deploy_paths.sh',
  '|| { echo restart-failed; return; }',
  '|| true',
  'tests/test_deploy_paths_guards.py'),
 ('paths: failed group delete reads as queued', 'scripts/test_deploy_paths.sh',
  'if ! az group delete -n "$rg" --yes --no-wait -o none; then',
  'az group delete -n "$rg" --yes --no-wait -o none || true\n        if false; then',
  'tests/test_deploy_paths_guards.py'),
 ('paths: failed read reads as FAIL', 'scripts/test_deploy_paths.sh',
  'row "$1" CANNOT "the az read failed (reason above)"',
  'row "$1" FAIL "the az read failed (reason above)"',
  'tests/test_deploy_paths_guards.py'),
 ('paths: failed read reads as empty', 'scripts/test_deploy_paths.sh',
  '        out="$UNREAD"\n',
  '        out=""\n',
  'tests/test_deploy_paths_guards.py'),
 ('paths: unreadable function list reads as zero', 'scripts/test_deploy_paths.sh',
  '[ "$read_ok" -eq 1 ] && echo "${n:-0}" || echo unreadable',
  'echo "${n:-0}"',
  'tests/test_deploy_paths_guards.py'),
 ('paths: failed workspace create is ignored', 'scripts/test_deploy_paths.sh',
  'if ! az monitor log-analytics workspace create -g "$RG_C" -n "$WS" -l "$LOCATION" --retention-time 30 -o none; then',
  'if az monitor log-analytics workspace create -g "$RG_C" -n "$WS" -l "$LOCATION" --retention-time 30 -o none; false; then',
  'tests/test_deploy_paths_guards.py'),
 ('paths: failed group create is ignored', 'scripts/test_deploy_paths.sh',
  '|| { echo "ERROR: CANNOT MEASURE: could not create $rg"; exit 1; }',
  '|| true',
  'tests/test_deploy_paths_guards.py'),
 ('paths: unreadable pointer reads as missing', 'scripts/test_deploy_paths.sh',
  '"") [ "$rc" = 0 ] && echo missing || echo unreadable ;;',
  '"") echo missing ;;',
  'tests/test_deploy_paths_guards.py'),
 ('portal_test: xtrace on', 'scripts/portal_test.sh',
  '{ set +x; } 2>/dev/null\nset -e',
  ':\nset -e',
  'tests/test_portal_harness.py'),
 ('portal_test: key on curl argv', 'scripts/portal_test.sh',
  '-H @"$HDRFILE" \\',
  '-H "x-functions-key: $MASTER_KEY" \\',
  'tests/test_portal_harness.py'),
 ('portal_test: empty key goes on', 'scripts/portal_test.sh',
  ' || [ -z "$MASTER_KEY" ]',
  '',
  'tests/test_portal_harness.py'),
 ('portal_test: one trigger try', 'scripts/portal_test.sh',
  'for attempt in 1 2 3; do',
  'for attempt in 1; do',
  'tests/test_portal_harness.py'),
 ('portal_test: 5xx not retried', 'scripts/portal_test.sh',
  '[ "$rc" -eq 0 ] && [ "${code:0:1}" != 5 ]',
  '[ "$rc" -eq 0 ]',
  'tests/test_portal_harness.py'),
 ('portal_test: 4xx retried', 'scripts/portal_test.sh',
  '"${code:0:1}" != 5 ]',
  '"${code:0:1}" != 2 ]',
  'tests/test_portal_harness.py'),
 ('portal_test: no retry wait', 'scripts/portal_test.sh',
  'sleep 15; }',
  ':; }',
  'tests/test_portal_harness.py'),
 ('portal_test: curl no max-time', 'scripts/portal_test.sh',
  '--max-time 60 ',
  '',
  'tests/test_portal_harness.py'),
 ('paths: xtrace on', 'scripts/test_deploy_paths.sh',
  '{ set +x; } 2>/dev/null\nset -uo pipefail',
  ':\nset -uo pipefail',
  'tests/test_deploy_paths_guards.py'),
 ('paths: RG_C not deleted', 'scripts/test_deploy_paths.sh',
  'for rg in "$RG_APP" "$RG_C"; do\n        # A group',
  'for rg in "$RG_APP"; do\n        # A group',
  'tests/test_deploy_paths_guards.py'),
 ('paths: RG_APP not deleted', 'scripts/test_deploy_paths.sh',
  'for rg in "$RG_APP" "$RG_C"; do\n        # A group',
  'for rg in "$RG_C"; do\n        # A group',
  'tests/test_deploy_paths_guards.py'),
 ('paths: never-created RG alarms', 'scripts/test_deploy_paths.sh',
  '[ "$exists" = false ] && continue',
  ':',
  'tests/test_deploy_paths_guards.py'),
 ('paths: unreadable exists skips', 'scripts/test_deploy_paths.sh',
  '|| exists=unknown',
  '|| exists=false',
  'tests/test_deploy_paths_guards.py'),
 ('setup: .env error silent', 'scripts/portal_setup.sh',
  'if ! bash -n "$SCRIPT_DIR/.env" 2>"$ENVERR" || ! source "$SCRIPT_DIR/.env" 2>>"$ENVERR"; then',
  'if ! source "$SCRIPT_DIR/.env" 2>/dev/null; then true; fi; if false; then',
  'tests/test_setup_secrets.py'),
 ('.env error shows the value', 'scripts/portal_setup.sh',
  '$(grep -o \'line [0-9]*\' "$ENVERR" | sort -u | tr \'\\n\' \' \')',
  '$(cat "$ENVERR")',
  'tests/test_setup_secrets.py'),
]


def syntax_error(path, text):
    """None when the file still parses, else the reason. A mutant that does not
    parse is not a caught mutant: the test fails on the typo, not on the logic."""
    if path.endswith(".sh"):
        r = subprocess.run(["bash", "-n", "/dev/stdin"], input=text, capture_output=True, text=True)
        return r.stderr.strip()[:200] if r.returncode else None
    if path.endswith(".py"):
        try:
            compile(text, path, "exec")   # in-process: py_compile would write a .pyc
        except SyntaxError as e:
            return str(e)
    if path.endswith(".json"):
        try:
            json.loads(text)
        except ValueError as e:
            return str(e)
    return None


blind, fake = [], []
for name, path, old, new, test in MUTATIONS:   # the originals must parse, or every mutant is a fake catch
    err = syntax_error(path, open(os.path.join(REPO, path), encoding="utf-8").read())
    if err:
        print("ABORT %s does not parse: %s" % (path, err)); sys.exit(2)
for name, path, old, new, test in MUTATIONS:
    full = os.path.join(REPO, path)
    orig = open(full, encoding="utf-8").read()
    if old not in orig:
        print("SKIP  %-32s anchor not found in %s" % (name, path)); blind.append(name); continue
    mutant = orig.replace(old, new, 1)
    err = syntax_error(path, mutant)
    if err:
        print("FAKE  %-32s mutant does not parse: %s" % (name, err)); fake.append(name); continue
    try:
        open(full, "w", encoding="utf-8").write(mutant)
        try:
            r = subprocess.run([sys.executable, test], cwd=REPO, capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            rc = r.returncode
            # a crash under load (timeout inside the test, import error) is not a catch
            if rc and any(m in (r.stdout + r.stderr) for m in CRASH_MARKERS):
                rc = None
        except subprocess.TimeoutExpired:
            rc = None
    finally:
        open(full, "w", encoding="utf-8").write(orig)
    if rc is None:
        print("ERROR %-32s %s (timeout or crash is not a catch)" % (name, test)); fake.append(name)
    elif rc == 0:
        print("BLIND %-32s %s still passed" % (name, test)); blind.append(name)
    else:
        print("caught %-31s %s" % (name, test))
print("\nblind:", len(blind), "fake:", len(fake), "of", len(MUTATIONS))
sys.exit(1 if blind or fake else 0)