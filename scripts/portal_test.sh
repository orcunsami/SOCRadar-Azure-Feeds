#!/bin/bash
# SOCRadar Feeds Function App - Azure Test Script
# Tests Function App end-to-end: trigger, check TI indicators, checkpoint, re-sent ids
# Run state comes from SOCRadar_Feeds_Audit_CL and indicator counts from
# ThreatIntelIndicators, both read through Log Analytics (exact distinct, no
# page limit). Ingestion lags by minutes, so every read polls with a ceiling.

# `bash -x` would print the Functions master key; no xtrace in this script.
{ set +x; } 2>/dev/null
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

# Check login and subscription before anything is read or stopped: the cleanup
# trap below stops a Function App, and it must never do that in the wrong subscription.
if ! ACCOUNT=$(az account show --query "user.name" -o tsv) || [ -z "$ACCOUNT" ]; then
    echo "Not logged in. Run: az login --use-device-code"
    exit 1
fi
echo "Logged in as: $ACCOUNT"
if ! ACTIVE_SUB=$(az account show --query id -o tsv); then
    echo "ERROR: CANNOT MEASURE: az account show failed"
    exit 1
fi
if [ "$ACTIVE_SUB" != "$SUBSCRIPTION_ID" ]; then
    echo "ERROR: az is on subscription '${ACTIVE_SUB:-none}' but SUBSCRIPTION_ID is '$SUBSCRIPTION_ID'"
    exit 1
fi
echo ""

# Find Function App name
# Under set -e a bare X=$(...) or a pipe into head hides an az failure: every
# az read below is `if ! X=$(...)` so "could not look" never reads as "absent".
if ! FUNC_APP_NAME=$(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv); then
    echo "ERROR: CANNOT MEASURE: az functionapp list failed"
    exit 1
fi
FUNC_APP_NAME=${FUNC_APP_NAME%%$'\n'*}
if [ -z "$FUNC_APP_NAME" ]; then
    echo "ERROR: No Function App found. Run portal_setup.sh first."
    exit 1
fi

# CRITICAL: Stop Function App on exit (cost control!)
# A stop that failed, or an app not read back as Stopped, makes the exit code non-zero.
cleanup() {
    local rc=$? bad=0
    rm -f "${AZERR:-}" "${HDRFILE:-}"
    echo ""
    echo "=== CLEANUP: Stopping Function App ==="
    if ! az functionapp stop --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP"; then
        echo "ERROR: functionapp stop failed: $FUNC_APP_NAME may still be billing"; bad=1
    fi
    if ! FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv); then
        FA_STATE=UNKNOWN
    fi
    echo "  $FUNC_APP_NAME state: $FA_STATE"
    if [ "$FA_STATE" != "Stopped" ]; then
        echo "ERROR: $FUNC_APP_NAME is '$FA_STATE', not Stopped: stop it by hand"; bad=1
    fi
    [ "$rc" -eq 0 ] && [ "$bad" -ne 0 ] && exit 1
    return 0
}
# az writes its stderr here, never into the answer: az 2.84 prints a WARNING on
# every call that uses --auth-mode key, and that text is not data.
AZERR=$(mktemp)
az_err() { local m; m=$(grep -m1 '^ERROR' "$AZERR"); [ -n "$m" ] || m=$(head -c 200 "$AZERR" | tr '\n' ' '); printf '%s' "${m:0:200}"; }
# The master key goes to curl through this 0600 file, not argv (ps shows argv).
HDRFILE=$(mktemp)
trap cleanup EXIT

# Helper: trigger function via admin endpoint
trigger_function() {
    # Prints the HTTP code, or the reason and return 1. Callers: `if ! X=$(trigger_function)`.
    local MASTER_KEY code rc attempt
    if ! MASTER_KEY=$(az functionapp keys list --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "masterKey" -o tsv) || [ -z "$MASTER_KEY" ]; then
        echo "Could not get master key"
        return 1
    fi
    printf 'x-functions-key: %s\n' "$MASTER_KEY" > "$HDRFILE"
    # A freshly started app answers 502/503 on the admin endpoint while it cold-starts:
    # 5xx or no answer is tried 3 times, 15 s apart; 4xx is a real answer and is not retried.
    for attempt in 1 2 3; do
        code=$(curl -s --max-time 60 -o /dev/null -w "%{http_code}" \
            -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/socradar_feeds_import" \
            -H @"$HDRFILE" \
            -H "Content-Type: application/json" \
            -d '{}') && rc=0 || rc=$?
        if [ "$rc" -eq 0 ] && [ "${code:0:1}" != 5 ]; then break; fi
        [ "$attempt" -lt 3 ] && { echo "  trigger attempt $attempt: rc=$rc http $code, retrying in 15s" >&2; sleep 15; }
    done
    if [ "$rc" -ne 0 ]; then echo "curl failed (rc=$rc, http $code)"; return 1; fi
    echo "$code"
}

# Log Analytics read through ARM. Prints the first row's cells, tab separated;
# NA when the call failed, EMPTY when the query returned no rows. The live
# endpoint answers Tables/Rows; lower case is accepted too.
LAW_URL="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/api/query?api-version=2017-01-01-preview"
law() {
    local body out
    body=$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}))' "$1") || { echo NA; return 0; }
    out=$(az rest --method POST --url "$LAW_URL" --body "$body" -o json 2>"$AZERR") || { echo "  law: $(az_err)" >&2; echo NA; return 0; }
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
    # The product only writes Success with failed=0; the second test is a guard.
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

# ===========================================
# PRE-TEST: Check Function App exists and running
# ===========================================
echo "=== Pre-Test: Checking Resources ==="
# az exits 3 for a resource that is not there (measured live); any other failure
# is a look that did not happen and must not read as "absent".
FA_STATE=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "state" -o tsv 2>/dev/null) && FA_RC=0 || FA_RC=$?
if [ "$FA_RC" -eq 3 ]; then
    echo "ERROR: FAIL (absent): Function App $FUNC_APP_NAME not found. Run portal_setup.sh first."
    exit 1
elif [ "$FA_RC" -ne 0 ] || [ -z "$FA_STATE" ]; then
    echo "ERROR: CANNOT MEASURE: az functionapp show failed (rc=$FA_RC)"
    exit 1
fi

# Start if stopped
if [ "$FA_STATE" != "Running" ]; then
    echo "  Starting Function App..."
    if ! az functionapp start --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" -o none; then
        echo "ERROR: CANNOT MEASURE: could not start Function App"
        exit 1
    fi
    sleep 10
fi
echo "  Function App: $FUNC_APP_NAME ($FA_STATE)"

# Check role assignments (the template assigns it; without it TI upload is refused, so MISSING is a FAIL in the summary)
ROLE_STATE="CANNOT-MEASURE"
if ! FA_PRINCIPAL=$(az identity show -g "$RESOURCE_GROUP" -n "SOCRadar-Feeds-MI" --query principalId -o tsv) || [ -z "$FA_PRINCIPAL" ]; then
    echo "  Sentinel Contributor: CANNOT MEASURE (identity SOCRadar-Feeds-MI unreadable)"
else
    if ! SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" \
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv); then
        echo "  Sentinel Contributor: CANNOT MEASURE"
    elif [ -n "$SENTINEL_ROLE" ]; then echo "  Sentinel Contributor: OK"; ROLE_STATE="OK"
    else echo "  Sentinel Contributor: MISSING"; ROLE_STATE="MISSING"; fi
fi

AUDIT_BEFORE=$(audit_count)
echo "  Audit rows before: $AUDIT_BEFORE"
echo ""

# ===========================================
# TEST 1: Trigger Function App
# ===========================================
echo "=== Test 1: Trigger Import ==="

echo "  Triggering via admin endpoint..."
TRIG1="OK"
if ! HTTP_CODE=$(trigger_function); then
    echo "  Trigger: CANNOT MEASURE ($HTTP_CODE)"
    TRIG1="CANNOT-MEASURE"
elif [ "$HTTP_CODE" = "202" ] || [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "204" ]; then
    echo "  Triggered (HTTP $HTTP_CODE)"
else
    # A rejected trigger is a failed run: waiting would let an unrelated run's audit row pass for ours.
    echo "  Trigger response: HTTP $HTTP_CODE"
    TRIG1="FAIL (HTTP $HTTP_CODE)"
    echo "  Checking function logs..."
    az functionapp log tail --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --timeout 5 2>/dev/null || true
fi

# Wait for the run's audit row. The return code matters: 0 done, 1 timeout, 2 could not look.
RUN1_WAIT="$TRIG1"
if [ "$TRIG1" = "OK" ]; then
    wait_for_audit_row "$AUDIT_BEFORE" 900 || RUN1_WAIT=$([ $? -eq 1 ] && echo "TIMEOUT" || echo "CANNOT-MEASURE")
fi
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
TI1_ROWS=$TI_ROWS_SEEN   # Test 4 overwrites TI_ROWS_SEEN; the summary judges run 1 on this
echo "  TI rows:         $TI_ROWS_SEEN"
echo "  TI distinct Ids: $TI_DISTINCT_AFTER"
echo ""

# ===========================================
# TEST 3: Check Storage Table (Checkpoint)
# ===========================================
STORAGE_STATE="CANNOT-MEASURE"
echo "=== Test 3: Checking Storage Checkpoint ==="
STORAGE_ACCOUNT=""; SA_ERR=""
if ! SA_OUT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv); then
    echo "  Storage Account: CANNOT MEASURE (az storage account list failed)"
    SA_ERR=1
else
    STORAGE_ACCOUNT=${SA_OUT%%$'\n'*}
fi
if [ -n "$STORAGE_ACCOUNT" ]; then
    echo "  Storage Account: $STORAGE_ACCOUNT"
    # --auth-mode key is required: without it recent az CLI tries AAD against the
    # table endpoint and fails. stdout is the answer, stderr goes to $AZERR (az 2.84
    # prints a WARNING there on every call), shown only when the call failed.
    # `if ! X=$(...)`: under set -e a bare X=$(...) exits before any `$?` test.
    if ! TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --auth-mode key \
        --query "[?name=='FeedState'].name" -o tsv 2>"$AZERR"); then
        echo "  FeedState Table: CANNOT MEASURE ($(az_err))"
        STORAGE_STATE="CANNOT-MEASURE"; TABLE_EXISTS=""
    elif [ -n "$TABLE_EXISTS" ]; then
        echo "  FeedState Table: OK"
        if ! ENTITY_COUNT=$(az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" \
            --auth-mode key --query "items | length(@)" -o tsv 2>"$AZERR"); then
            echo "  Checkpoint entries: CANNOT MEASURE ($(az_err))"
            STORAGE_STATE="CANNOT-MEASURE"
        elif ! is_num "$ENTITY_COUNT"; then
            echo "  Checkpoint entries: CANNOT MEASURE (not a number: '${ENTITY_COUNT:0:40}')"
            STORAGE_STATE="CANNOT-MEASURE"
        elif [ "$ENTITY_COUNT" -eq 0 ]; then
            echo "  Checkpoint entries: 0 (an import ran and wrote no checkpoint)"
            STORAGE_STATE="EMPTY"
        else
            echo "  Checkpoint entries: $ENTITY_COUNT"
            STORAGE_STATE="OK"
            # Display only; the verdict is the count above.
            az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" --auth-mode key \
                --query "items[].{Collection:CollectionName, Processed:LastProcessedDate, LastRun:LastRun, New:NewIndicators}" -o table 2>/dev/null | head -20
        fi
    else
        echo "  FeedState Table: NOT FOUND (query succeeded, table absent)"
        STORAGE_STATE="MISSING"
    fi
elif [ -z "$SA_ERR" ]; then
    echo "  Storage Account: NOT FOUND"
    STORAGE_STATE="MISSING"
fi
echo ""

# ===========================================
# TEST 4: Second Run (re-sent ids must not multiply)
# ===========================================
echo "=== Test 4: Second Run (overlap re-send) ==="
AUDIT_BEFORE2=$(audit_count)
TI_ROWS_BEFORE2=$(ti_rows)
echo "  Triggering second run..."
RUN2_WAIT="OK"
if HTTP_CODE=$(trigger_function) && case "$HTTP_CODE" in 200|202|204) true ;; *) false ;; esac; then
    echo "  Triggered (HTTP $HTTP_CODE)"
    wait_for_audit_row "$AUDIT_BEFORE2" 900 || RUN2_WAIT=$([ $? -eq 1 ] && echo "TIMEOUT" || echo "CANNOT-MEASURE")
elif [ -n "${HTTP_CODE//[0-9]/}" ]; then
    echo "  Trigger: CANNOT MEASURE ($HTTP_CODE)"
    RUN2_WAIT="CANNOT-MEASURE"
else
    echo "  Trigger response: HTTP $HTTP_CODE"
    RUN2_WAIT="FAIL (HTTP $HTTP_CODE)"
fi
read_last_audit
RUN2_A_CREATED=$A_CREATED
echo "  Audit: status=$A_STATUS created=$A_CREATED failed=$A_FAILED"
RUN2_VERDICT=$(run_verdict "$RUN2_WAIT")

# Ids: the checkpoint does not narrow the window (48 h overlap), so the second run
# sends indicators again: created repeats, rows grow, the distinct id count must not move. That only means something once the second
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
            if is_num "$TI_ROWS_SEEN"; then
                echo "  The second run's rows never appeared in ThreatIntelIndicators ($TI_ROWS_SEEN rows)"
                CHECKPOINT_OK="FAIL (no rows)"
            else
                echo "  CANNOT MEASURE: ThreatIntelIndicators unreadable ($TI_ROWS_SEEN)"
            fi
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
row() { printf "| %-24s | %-15s |\n" "$1" "$2"; [ "$2" = "PASS" ] || fails=$((fails+1)); }

echo "| Test                     | Result          |"
echo "|--------------------------|-----------------|"

row "Import Run" "$RUN1_VERDICT"

# Rows must cover what the run reports sending, and at least one id must exist:
# a run that imported nothing is a failure, not a warning.
if ! is_num "$TI_DISTINCT_AFTER" || ! is_num "$TI1_ROWS"; then
    row "TI Indicators" "CANNOT-MEASURE"
elif [ "$TI_DISTINCT_AFTER" -gt 0 ] && { ! is_num "$RUN1_A_CREATED" || [ "$TI1_ROWS" -ge "$RUN1_A_CREATED" ]; }; then
    printf "| %-24s | %-15s |\n" "TI Indicators" "PASS ($TI_DISTINCT_AFTER ids)"
else
    row "TI Indicators" "FAIL ($TI_DISTINCT_AFTER ids)"
fi

case "$STORAGE_STATE" in
    OK)      row "Storage Checkpoint" "PASS" ;;
    MISSING) row "Storage Checkpoint" "FAIL (absent)" ;;
    EMPTY)   row "Storage Checkpoint" "FAIL (empty)" ;;
    *)       row "Storage Checkpoint" "CANNOT-MEASURE" ;;
esac

case "$ROLE_STATE" in
    OK)      row "Sentinel Contributor" "PASS" ;;
    MISSING) row "Sentinel Contributor" "FAIL (missing)" ;;
    *)       row "Sentinel Contributor" "CANNOT-MEASURE" ;;
esac

row "Second Run" "$RUN2_VERDICT"

row "Same IDs not duplicated" "$CHECKPOINT_OK"

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
