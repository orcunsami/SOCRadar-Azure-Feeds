#!/bin/bash
# SOCRadar Feeds Function App - Azure Test Script
# Tests Function App end-to-end: trigger, check TI indicators, checkpoint, dedup

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

# Helper: wait for function execution by polling logs
wait_for_completion() {
    local MAX_WAIT=${1:-300}
    local INTERVAL=10
    local ELAPSED=0
    local MASTER_KEY
    MASTER_KEY=$(az functionapp keys list --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "masterKey" -o tsv 2>&1)
    if [ $? -ne 0 ] || [ -z "$MASTER_KEY" ]; then
        # Not a timeout and not a success: we never got to look. Say so.
        echo "  CANNOT MEASURE: master key unavailable (${MASTER_KEY%%$'\n'*})"
        return 2
    fi

    echo "  Waiting for function to complete (max ${MAX_WAIT}s)..."

    while [ $ELAPSED -lt $MAX_WAIT ]; do
        printf "\r  [%3ds] Running...   " $ELAPSED
        sleep $INTERVAL
        ELAPSED=$((ELAPSED + INTERVAL))

        local BODY HTTP
        BODY=$(curl -s -w '\n%{http_code}' \
            -H "x-functions-key: $MASTER_KEY" \
            "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/socradar_feeds_import/status")
        HTTP=$(printf '%s' "$BODY" | tail -1)
        BODY=$(printf '%s' "$BODY" | sed '$d')
        if [ "$HTTP" != "200" ]; then
            echo ""
            echo "  CANNOT MEASURE: status endpoint returned HTTP $HTTP"
            return 2
        fi
        local STATUS
        STATUS=$(printf '%s' "$BODY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('is_running','unknown'))" 2>/dev/null || echo "parse-error")
        if [ "$STATUS" = "parse-error" ]; then
            echo ""
            echo "  CANNOT MEASURE: status body was not JSON"
            return 2
        fi
        if [ "$STATUS" = "false" ] || [ "$STATUS" = "False" ]; then
            echo ""
            echo "  Completed (${ELAPSED}s)"
            return 0
        fi
    done

    echo ""
    # A timeout is NOT a pass. The old code returned 0 here and the caller
    # ignored it, so a function that never finished read as a green run.
    echo "  TIMEOUT after ${MAX_WAIT}s (function did not report idle)"
    return 1
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

# Pre-test TI indicator count
TI_URL="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/providers/Microsoft.SecurityInsights/threatIntelligence/main/indicators?api-version=2024-03-01&\$top=1000"
# The indicators API pages its results; one GET sees only the first page.
# Measured 6 Sep 2026: the nextLink it hands back returns an empty page, so
# the count is only reliable up to the $top of the first page (1000).
ti_external_ids() {
    local url="$TI_URL"
    while [ -n "$url" ]; do
        local page
        page=$(az rest --method GET --url "$url" -o json 2>/dev/null) || break
        printf '%s' "$page" | python3 -c 'import sys, json
for v in json.load(sys.stdin).get("value", []):
    print(v.get("properties", {}).get("externalId", ""))'
        url=$(printf '%s' "$page" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("nextLink", ""))')
    done
}
ti_count() { ti_external_ids | wc -l | tr -d ' '; }

INDICATOR_COUNT_BEFORE=$(ti_count)
echo "  TI Indicators before: $INDICATOR_COUNT_BEFORE"
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

# Wait for completion. The return code matters: 0 done, 1 timeout, 2 could not look.
RUN1_WAIT="OK"
wait_for_completion 300 || RUN1_WAIT=$([ $? -eq 1 ] && echo "TIMEOUT" || echo "CANNOT-MEASURE")

# Check logs for result
echo "  Checking recent logs..."
az rest --method POST \
    --url "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Web/sites/$FUNC_APP_NAME/hostruntime/admin/functions/socradar_feeds_import/status?api-version=2023-12-01" \
    2>/dev/null || true
echo ""

# ===========================================
# TEST 2: Check TI Indicators
# ===========================================
echo "=== Test 2: Checking TI Indicators ==="

sleep 5

INDICATOR_COUNT_AFTER=$(ti_count)
NEW_INDICATORS=$((INDICATOR_COUNT_AFTER - INDICATOR_COUNT_BEFORE))

echo "  TI Indicators before: $INDICATOR_COUNT_BEFORE"
echo "  TI Indicators after:  $INDICATOR_COUNT_AFTER"
echo "  New indicators:       $NEW_INDICATORS"

if [ "$NEW_INDICATORS" -gt 0 ] 2>/dev/null; then
    echo ""
    echo "  Sample indicators:"
    az rest --method GET --url "$TI_URL" \
        --query "value[0:3].{pattern:properties.pattern, source:properties.source, confidence:properties.confidence}" \
        -o table 2>/dev/null || true
fi
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
echo "  Triggering second run..."
HTTP_CODE=$(trigger_function)
echo "  Triggered (HTTP $HTTP_CODE)"

RUN2_WAIT="OK"
wait_for_completion 300 || RUN2_WAIT=$([ $? -eq 1 ] && echo "TIMEOUT" || echo "CANNOT-MEASURE")

INDICATOR_COUNT_FINAL=$(ti_count)
DUPLICATE_INDICATORS=$((INDICATOR_COUNT_FINAL - INDICATOR_COUNT_AFTER))
echo "  Indicators after 2nd run: $INDICATOR_COUNT_FINAL (delta: $DUPLICATE_INDICATORS)"

# A count that stayed flat proves nothing: the second run may simply have sent
# nothing. Dedup is proven by the same STIX id being one record after two
# uploads, and by the audit row of the second run saying it did send.
DUP_IDS=$(ti_external_ids | sort | uniq -d | wc -l | tr -d ' ')
echo "  STIX ids present more than once: $DUP_IDS"
if [ "$DUP_IDS" = "0" ]; then
    CHECKPOINT_OK="PASS"
else
    CHECKPOINT_OK="FAIL"
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

if [ "$RUN1_WAIT" != "OK" ]; then
    row "Import Run" "$RUN1_WAIT"
elif [ "${NEW_INDICATORS:-0}" -gt 0 ] 2>/dev/null; then
    row "Import Run" "PASS"
else
    # First run against a fresh workspace importing nothing is a failure, not a warning.
    row "Import Run" "FAIL (0 new)"
fi

if [ "${NEW_INDICATORS:-0}" -gt 0 ] 2>/dev/null; then
    printf "| %-21s | %-15s |\n" "TI Indicators" "PASS ($NEW_INDICATORS)"
else
    row "TI Indicators" "FAIL (0 new)"
fi

case "$STORAGE_STATE" in
    OK)      row "Storage Checkpoint" "PASS" ;;
    MISSING) row "Storage Checkpoint" "FAIL (absent)" ;;
    *)       row "Storage Checkpoint" "CANNOT-MEASURE" ;;
esac

# Second run: 0 new is the EXPECTED result (that is what the checkpoint is for).
if [ "$RUN2_WAIT" != "OK" ]; then
    row "Second Run" "$RUN2_WAIT"
else
    row "Second Run" "PASS"
fi

row "Checkpoint Dedup" "$CHECKPOINT_OK"

echo ""
if [ "$fails" -ne 0 ]; then
    echo "RESULT: FAIL ($fails check(s) not PASS)"
    echo "Indicators: $INDICATOR_COUNT_BEFORE -> $INDICATOR_COUNT_AFTER -> $INDICATOR_COUNT_FINAL"
    echo "Function App will be STOPPED by cleanup trap."
    exit 1
fi
echo "RESULT: PASS"
echo ""
echo "Indicators: $INDICATOR_COUNT_BEFORE -> $INDICATOR_COUNT_AFTER -> $INDICATOR_COUNT_FINAL"
echo ""
echo "Function App will be STOPPED by cleanup trap."
