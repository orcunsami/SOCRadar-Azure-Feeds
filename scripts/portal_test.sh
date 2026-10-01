#!/bin/bash
# SOCRadar Feeds Function App - Azure Test Script
# Tests Function App end-to-end: trigger, check TI indicators, checkpoint, dedup
# Run state comes from SOCRadar_Feeds_Audit_CL and indicator counts from
# ThreatIntelIndicators, both read through Log Analytics (exact distinct, no
# page limit). Ingestion lags by minutes, so every read polls with a ceiling.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

ENV_RESOURCE_GROUP="${RESOURCE_GROUP:-}"
ENV_WORKSPACE_NAME="${WORKSPACE_NAME:-}"
ENV_SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-}"

# Load config
if [ -f "$SCRIPT_DIR/test.config" ]; then
    source "$SCRIPT_DIR/test.config"
fi

RESOURCE_GROUP="${ENV_RESOURCE_GROUP:-${RESOURCE_GROUP:-}}"
WORKSPACE_NAME="${ENV_WORKSPACE_NAME:-${WORKSPACE_NAME:-}}"
SUBSCRIPTION_ID="${ENV_SUBSCRIPTION_ID:-${SUBSCRIPTION_ID:-}}"
if [ -z "$SUBSCRIPTION_ID" ] || [ -z "$RESOURCE_GROUP" ] || [ -z "$WORKSPACE_NAME" ]; then
    echo "ERROR: set SUBSCRIPTION_ID, RESOURCE_GROUP and WORKSPACE_NAME (scripts/test.config or environment)"
    exit 1
fi

# Find Function App name
FUNC_APP_NAME=$(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv 2>/dev/null | head -1)
if [ -z "$FUNC_APP_NAME" ]; then
    echo "ERROR: No Function App found. Run portal_setup.sh first."
    exit 1
fi

# CRITICAL: Stop Function App on exit (cost control!)
cleanup() {
    echo ""
    echo "=== CLEANUP: Stopping Function App ==="
    az functionapp stop --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" 2>/dev/null || true
    FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv 2>/dev/null || echo "UNKNOWN")
    echo "  $FUNC_APP_NAME state: $FA_STATE"
}
trap cleanup EXIT

# Helper: trigger function via admin endpoint
trigger_function() {
    local MASTER_KEY=$(az functionapp keys list --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "masterKey" -o tsv 2>/dev/null)
    if [ -z "$MASTER_KEY" ]; then
        echo "ERROR: Could not get master key"
        return 1
    fi

    curl -s -o /dev/null -w "%{http_code}" \
        -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/socradar_feeds_import" \
        -H "x-functions-key: $MASTER_KEY" \
        -H "Content-Type: application/json" \
        -d '{}'
}

# Log Analytics read through ARM. Prints the first row's cells, tab separated;
# NA when the call failed, EMPTY when the query returned no rows. The live
# endpoint answers Tables/Rows; lower case is accepted too.
LAW_URL="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/api/query?api-version=2017-01-01-preview"
law() {
    local body out
    body=$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}))' "$1")
    out=$(az rest --method POST --url "$LAW_URL" --body "$body" -o json 2>/dev/null) || { echo NA; return 0; }
    printf '%s' "$out" | python3 -c '
import sys, json
try:
    j = json.load(sys.stdin)
    t = (j["Tables"] if "Tables" in j else j["tables"])[0]
    rows = t["Rows"] if "Rows" in t else t["rows"]
    print("\t".join(str(c) for c in rows[0]) if rows else "EMPTY")
except Exception:
    print("NA")'
}
is_num() { [[ "$1" =~ ^[0-9]+$ ]]; }

TI_FILTER="ThreatIntelIndicators | where SourceSystem == 'SOCRadar Threat Feeds'"
audit_count() { law "SOCRadar_Feeds_Audit_CL | count"; }
ti_rows()     { law "$TI_FILTER | count"; }
# Exact: distinct, not dcount (dcount is approximate).
ti_distinct() { law "$TI_FILTER | distinct Id | count"; }

# Wait until the audit table holds more rows than $1 (a run finished and wrote
# its row). 0 done, 1 timeout, 2 never able to read. A run that was still in
# flight when we triggered is indistinguishable from ours; either is a finished
# import, and the row it writes is what we judge.
wait_for_audit_row() {
    local before=$1 max=${2:-900} waited=0 n read_ok=0
    is_num "$before" || { echo "  CANNOT MEASURE: audit table unreadable"; return 2; }
    echo "  Waiting for the run's audit row (max ${max}s)..."
    while [ $waited -lt $max ]; do
        sleep 30
        waited=$((waited + 30))
        n=$(audit_count)
        if is_num "$n"; then
            read_ok=1
            if [ "$n" -gt "$before" ]; then
                echo "  Audit row seen (${waited}s)"
                return 0
            fi
        fi
    done
    if [ $read_ok -eq 0 ]; then
        echo "  CANNOT MEASURE: audit table never answered"
        return 2
    fi
    # A timeout is NOT a pass.
    echo "  TIMEOUT after ${max}s (no new audit row)"
    return 1
}

# Newest audit row -> A_STATUS A_CREATED A_FAILED (NA when unreadable).
read_last_audit() {
    local row
    row=$(law "SOCRadar_Feeds_Audit_CL | top 1 by TimeGenerated desc | project Status, IndicatorsCreated, IndicatorsFailed")
    A_STATUS=NA; A_CREATED=NA; A_FAILED=NA
    case "$row" in NA|EMPTY) ;; *) IFS=$'\t' read -r A_STATUS A_CREATED A_FAILED <<< "$row" ;; esac
}

# The TI table is an append log: wait until it holds at least $1 rows. Sets
# TI_ROWS_SEEN. Without this a count read right after a run can predate its rows.
wait_ti_rows() {
    local want=$1 max=$2 waited=0
    while :; do
        TI_ROWS_SEEN=$(ti_rows)
        if is_num "$TI_ROWS_SEEN" && [ "$TI_ROWS_SEEN" -ge "$want" ]; then return 0; fi
        [ $waited -ge "$max" ] && return 1
        sleep 30
        waited=$((waited + 30))
    done
}

# How one finished run reads in the summary.
run_verdict() {
    if [ "$1" != "OK" ]; then echo "$1"
    elif [ "$A_STATUS" = "NA" ] || [ "$A_STATUS" = "EMPTY" ]; then echo "CANNOT-MEASURE"
    elif [ "$A_STATUS" = "Success" ] && [ "$A_FAILED" = "0" ]; then echo "PASS"
    else echo "FAIL ($A_STATUS)"; fi
}

echo "=== SOCRadar Feeds Function App - Test ==="
echo ""
echo "Config:"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Workspace:      $WORKSPACE_NAME"
echo "  Function App:   $FUNC_APP_NAME"
echo ""

# Check login
ACCOUNT=$(az account show --query "user.name" -o tsv 2>/dev/null)
if [ -z "$ACCOUNT" ]; then
    echo "Not logged in. Run: az login --use-device-code"
    exit 1
fi
echo "Logged in as: $ACCOUNT"
ACTIVE_SUB=$(az account show --query id -o tsv 2>/dev/null)
if [ "$ACTIVE_SUB" != "$SUBSCRIPTION_ID" ]; then
    echo "ERROR: az is on subscription '${ACTIVE_SUB:-none}' but SUBSCRIPTION_ID is '$SUBSCRIPTION_ID'"
    exit 1
fi
echo ""

# ===========================================
# PRE-TEST: Check Function App exists and running
# ===========================================
echo "=== Pre-Test: Checking Resources ==="
FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv 2>/dev/null || echo "NOT_FOUND")
if [ "$FA_STATE" = "NOT_FOUND" ]; then
    echo "ERROR: Function App not found. Run portal_setup.sh first."
    exit 1
fi

# Start if stopped
if [ "$FA_STATE" != "Running" ]; then
    echo "  Starting Function App..."
    az functionapp start --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" 2>/dev/null
    sleep 10
fi
echo "  Function App: $FUNC_APP_NAME ($FA_STATE)"

# Check role assignments
FA_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "SOCRadar-Feeds-MI" --query principalId -o tsv 2>/dev/null || echo "")
if [ -n "$FA_PRINCIPAL" ]; then
    SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" \
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv 2>/dev/null)
    [ -n "$SENTINEL_ROLE" ] && echo "  Sentinel Contributor: OK" || echo "  Sentinel Contributor: MISSING (may fail)"
fi

AUDIT_BEFORE=$(audit_count)
echo "  Audit rows before: $AUDIT_BEFORE"
echo ""

# ===========================================
# TEST 1: Trigger Function App
# ===========================================
echo "=== Test 1: Trigger Import ==="

echo "  Triggering via admin endpoint..."
HTTP_CODE=$(trigger_function)
if [ "$HTTP_CODE" = "202" ] || [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "204" ]; then
    echo "  Triggered (HTTP $HTTP_CODE)"
else
    echo "  Trigger response: HTTP $HTTP_CODE"
    echo "  Checking function logs..."
    az functionapp log tail --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --timeout 5 2>/dev/null || true
fi

# Wait for the run's audit row. The return code matters: 0 done, 1 timeout, 2 could not look.
RUN1_WAIT="OK"
wait_for_audit_row "$AUDIT_BEFORE" 900 || RUN1_WAIT=$([ $? -eq 1 ] && echo "TIMEOUT" || echo "CANNOT-MEASURE")
read_last_audit
RUN1_A_STATUS=$A_STATUS; RUN1_A_CREATED=$A_CREATED
echo "  Audit: status=$A_STATUS created=$A_CREATED failed=$A_FAILED"
RUN1_VERDICT=$(run_verdict "$RUN1_WAIT")
echo ""

# ===========================================
# TEST 2: Check TI Indicators
# ===========================================
echo "=== Test 2: Checking TI Indicators ==="

# Absolute, not a delta: the first import usually finished during deployment
# (RunOnStartup), before any "before" count could be taken.
if is_num "$RUN1_A_CREATED" && [ "$RUN1_A_CREATED" -gt 0 ]; then
    wait_ti_rows "$RUN1_A_CREATED" 600 || true
else
    wait_ti_rows 1 0 || true
fi
TI_DISTINCT_AFTER=$(ti_distinct)
echo "  TI rows:         $TI_ROWS_SEEN"
echo "  TI distinct Ids: $TI_DISTINCT_AFTER"
echo ""

# ===========================================
# TEST 3: Check Storage Table (Checkpoint)
# ===========================================
STORAGE_STATE="CANNOT-MEASURE"
echo "=== Test 3: Checking Storage Checkpoint ==="
STORAGE_ACCOUNT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv 2>/dev/null | head -1)
if [ -n "$STORAGE_ACCOUNT" ]; then
    echo "  Storage Account: $STORAGE_ACCOUNT"
    # --auth-mode key is required: without it recent az CLI tries AAD against the
    # table endpoint and fails. The old code sent that failure to /dev/null and
    # printed "NOT FOUND" / "0 entries", so a broken call read as a real answer.
    TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key \
        --query "[?name=='FeedState'].name" -o tsv 2>&1)
    if [ $? -ne 0 ]; then
        echo "  FeedState Table: CANNOT MEASURE (${TABLE_EXISTS%%$'\n'*})"
        STORAGE_STATE="CANNOT-MEASURE"; TABLE_EXISTS=""
    elif [ -n "$TABLE_EXISTS" ]; then
        echo "  FeedState Table: OK"
        ENTITY_COUNT=$(az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" \
            --auth-mode key --query "items | length(@)" -o tsv 2>&1)
        if [ $? -ne 0 ]; then
            echo "  Checkpoint entries: CANNOT MEASURE (${ENTITY_COUNT%%$'\n'*})"
            STORAGE_STATE="CANNOT-MEASURE"
        else
            echo "  Checkpoint entries: $ENTITY_COUNT"
            STORAGE_STATE="OK"
            az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" --auth-mode key \
                --query "items[].{Collection:CollectionName, Processed:LastProcessedDate, LastRun:LastRun, New:NewIndicators}" -o table 2>&1 | head -20
        fi
    else
        echo "  FeedState Table: NOT FOUND (query succeeded, table absent)"
        STORAGE_STATE="MISSING"
    fi
else
    echo "  Storage Account: NOT FOUND"
    STORAGE_STATE="MISSING"
fi
echo ""

# ===========================================
# TEST 4: Second Run (checkpoint/dedup test)
# ===========================================
echo "=== Test 4: Second Run (Checkpoint Test) ==="
AUDIT_BEFORE2=$(audit_count)
TI_ROWS_BEFORE2=$(ti_rows)
echo "  Triggering second run..."
HTTP_CODE=$(trigger_function)
echo "  Triggered (HTTP $HTTP_CODE)"

RUN2_WAIT="OK"
wait_for_audit_row "$AUDIT_BEFORE2" 900 || RUN2_WAIT=$([ $? -eq 1 ] && echo "TIMEOUT" || echo "CANNOT-MEASURE")
read_last_audit
RUN2_A_CREATED=$A_CREATED
echo "  Audit: status=$A_STATUS created=$A_CREATED failed=$A_FAILED"
RUN2_VERDICT=$(run_verdict "$RUN2_WAIT")

# Dedup: the second run re-sends the overlap under the same STIX ids, so the
# distinct id count must not move. That only means something once the second
# run's rows have landed (the log is append-only) and the run really sent some.
CHECKPOINT_OK="CANNOT-MEASURE"
if [ "$RUN2_WAIT" = "OK" ] && is_num "$RUN2_A_CREATED" && is_num "$TI_ROWS_BEFORE2" && is_num "$TI_DISTINCT_AFTER"; then
    if [ "$RUN2_A_CREATED" -gt 0 ]; then
        if wait_ti_rows $((TI_ROWS_BEFORE2 + RUN2_A_CREATED)) 600; then
            TI_DISTINCT_FINAL=$(ti_distinct)
            echo "  TI rows: $TI_ROWS_BEFORE2 -> $TI_ROWS_SEEN, distinct Ids: $TI_DISTINCT_AFTER -> $TI_DISTINCT_FINAL"
            if is_num "$TI_DISTINCT_FINAL" && [ "$TI_DISTINCT_FINAL" = "$TI_DISTINCT_AFTER" ]; then
                CHECKPOINT_OK="PASS"
            elif is_num "$TI_DISTINCT_FINAL"; then
                CHECKPOINT_OK="FAIL (ids grew)"
            fi
        else
            echo "  The second run's rows never appeared in ThreatIntelIndicators ($TI_ROWS_SEEN rows)"
            CHECKPOINT_OK="FAIL (no rows)"
        fi
    else
        echo "  The second run sent nothing, so dedup cannot be judged"
    fi
fi
echo ""

# ===========================================
# SUMMARY
# ===========================================
echo "==========================================="
echo "            TEST SUMMARY"
echo "==========================================="
echo ""
# Every row is PASS, FAIL or CANNOT-MEASURE. There is no row that can only warn:
# a harness whose worst outcome is a warning cannot fail, and a run that imported
# nothing used to read as green here.
fails=0
row() { printf "| %-21s | %-15s |\n" "$1" "$2"; [ "$2" = "PASS" ] || fails=$((fails+1)); }

echo "| Test                  | Result          |"
echo "|-----------------------|-----------------|"

row "Import Run" "$RUN1_VERDICT"

# Rows must cover what the run reports sending, and at least one id must exist:
# a run that imported nothing is a failure, not a warning.
if ! is_num "$TI_DISTINCT_AFTER" || ! is_num "$TI_ROWS_SEEN"; then
    row "TI Indicators" "CANNOT-MEASURE"
elif [ "$TI_DISTINCT_AFTER" -gt 0 ] && { ! is_num "$RUN1_A_CREATED" || [ "$TI_ROWS_SEEN" -ge "$RUN1_A_CREATED" ]; }; then
    printf "| %-21s | %-15s |\n" "TI Indicators" "PASS ($TI_DISTINCT_AFTER ids)"
else
    row "TI Indicators" "FAIL ($TI_DISTINCT_AFTER ids)"
fi

case "$STORAGE_STATE" in
    OK)      row "Storage Checkpoint" "PASS" ;;
    MISSING) row "Storage Checkpoint" "FAIL (absent)" ;;
    *)       row "Storage Checkpoint" "CANNOT-MEASURE" ;;
esac

row "Second Run" "$RUN2_VERDICT"

row "Checkpoint Dedup" "$CHECKPOINT_OK"

echo ""
if [ "$fails" -ne 0 ]; then
    echo "RESULT: FAIL ($fails check(s) not PASS)"
    echo "TI distinct Ids: ${TI_DISTINCT_AFTER:-NA} -> ${TI_DISTINCT_FINAL:-NA}"
    echo "Function App will be STOPPED by cleanup trap."
    exit 1
fi
echo "RESULT: PASS"
echo ""
echo "TI distinct Ids: ${TI_DISTINCT_AFTER:-NA} -> ${TI_DISTINCT_FINAL:-NA}"
echo ""
echo "Function App will be STOPPED by cleanup trap."
