#!/bin/bash
# SOCRadar Feeds Function App - Azure Test Script
# Tests Function App end-to-end: trigger, check TI indicators, checkpoint, dedup

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load config
if [ -f "$SCRIPT_DIR/test.config" ]; then
    source "$SCRIPT_DIR/test.config"
fi

RESOURCE_GROUP="${RESOURCE_GROUP:-RSS-app-RG}"
WORKSPACE_NAME="${WORKSPACE_NAME:-socradar-feeds-func-mar03}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-b622bfd9-6b2b-45b3-b7d2-7b5d3114b96b}"

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

    echo "  Waiting for function to complete (max ${MAX_WAIT}s)..."

    while [ $ELAPSED -lt $MAX_WAIT ]; do
        # Check recent invocations via app insights or just wait
        printf "\r  [%3ds] Running...   " $ELAPSED
        sleep $INTERVAL
        ELAPSED=$((ELAPSED + INTERVAL))

        # Check if function is idle (no active executions)
        local STATUS=$(curl -s \
            -H "x-functions-key: $(az functionapp keys list --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "masterKey" -o tsv 2>/dev/null)" \
            "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/socradar_feeds_import/status" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('is_running', 'unknown'))" 2>/dev/null || echo "unknown")

        if [ "$STATUS" = "false" ] || [ "$STATUS" = "False" ]; then
            echo ""
            echo "  Completed (${ELAPSED}s)"
            return 0
        fi
    done

    echo ""
    echo "  Timeout after ${MAX_WAIT}s (function may still be running)"
    return 0
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
FA_PRINCIPAL=$(az functionapp show --name "$FUNC_APP_NAME" -g "$RESOURCE_GROUP" --query "identity.principalId" -o tsv 2>/dev/null || echo "")
if [ -n "$FA_PRINCIPAL" ]; then
    SENTINEL_ROLE=$(az role assignment list --assignee "$FA_PRINCIPAL" \
        --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
        --query "[?roleDefinitionName=='Microsoft Sentinel Contributor'].roleDefinitionName" -o tsv 2>/dev/null)
    [ -n "$SENTINEL_ROLE" ] && echo "  Sentinel Contributor: OK" || echo "  Sentinel Contributor: MISSING (may fail)"
fi

# Pre-test TI indicator count
TI_URL="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/providers/Microsoft.SecurityInsights/threatIntelligence/main/indicators?api-version=2024-03-01&\$top=1000"
INDICATOR_COUNT_BEFORE=$(az rest --method GET --url "$TI_URL" --query "value | length(@)" -o tsv 2>/dev/null || echo "0")
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

# Wait for completion
wait_for_completion 300

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

INDICATOR_COUNT_AFTER=$(az rest --method GET --url "$TI_URL" --query "value | length(@)" -o tsv 2>/dev/null || echo "0")
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
echo "=== Test 3: Checking Storage Checkpoint ==="
STORAGE_ACCOUNT=$(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv 2>/dev/null | head -1)
if [ -n "$STORAGE_ACCOUNT" ]; then
    echo "  Storage Account: $STORAGE_ACCOUNT"
    TABLE_EXISTS=$(az storage table list --account-name "$STORAGE_ACCOUNT" --query "[?name=='FeedState'].name" -o tsv 2>/dev/null || echo "")
    if [ -n "$TABLE_EXISTS" ]; then
        echo "  FeedState Table: OK"
        ENTITY_COUNT=$(az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" --query "items | length(@)" -o tsv 2>/dev/null || echo "0")
        echo "  Checkpoint entries: $ENTITY_COUNT"
        # Show checkpoint details
        az storage entity query --table-name "FeedState" --account-name "$STORAGE_ACCOUNT" \
            --query "items[].{Collection:CollectionName, LastRun:LastRun, New:NewIndicators}" -o table 2>/dev/null || true
    else
        echo "  FeedState Table: NOT FOUND"
    fi
else
    echo "  Storage Account: NOT FOUND"
fi
echo ""

# ===========================================
# TEST 4: Second Run (checkpoint/dedup test)
# ===========================================
echo "=== Test 4: Second Run (Checkpoint Test) ==="
echo "  Triggering second run..."
HTTP_CODE=$(trigger_function)
echo "  Triggered (HTTP $HTTP_CODE)"

wait_for_completion 300

INDICATOR_COUNT_FINAL=$(az rest --method GET --url "$TI_URL" --query "value | length(@)" -o tsv 2>/dev/null || echo "0")
DUPLICATE_INDICATORS=$((INDICATOR_COUNT_FINAL - INDICATOR_COUNT_AFTER))

echo "  Indicators after 2nd run: $INDICATOR_COUNT_FINAL (new: $DUPLICATE_INDICATORS)"

if [ "$DUPLICATE_INDICATORS" -le 0 ] 2>/dev/null; then
    echo "  Checkpoint working - no duplicate indicators"
    CHECKPOINT_OK="PASS"
else
    echo "  New indicators on 2nd run: $DUPLICATE_INDICATORS (may be new data)"
    CHECKPOINT_OK="WARN"
fi
echo ""

# ===========================================
# SUMMARY
# ===========================================
echo "==========================================="
echo "            TEST SUMMARY"
echo "==========================================="
echo ""
echo "| Test                  | Result          |"
echo "|-----------------------|-----------------|"
[ "$NEW_INDICATORS" -gt 0 ] 2>/dev/null && echo "| Import Run            | PASS            |" || echo "| Import Run            | WARN (0 new)    |"
[ "$NEW_INDICATORS" -gt 0 ] 2>/dev/null && echo "| TI Indicators         | PASS ($NEW_INDICATORS new) |" || echo "| TI Indicators         | WARN (0 new)    |"
[ -n "$TABLE_EXISTS" ] && echo "| Storage Checkpoint    | PASS            |" || echo "| Storage Checkpoint    | WARN            |"
echo "| Checkpoint Dedup      | $CHECKPOINT_OK            |"
echo ""
echo "Indicators: $INDICATOR_COUNT_BEFORE -> $INDICATOR_COUNT_AFTER -> $INDICATOR_COUNT_FINAL"
echo ""
echo "Function App will be STOPPED by cleanup trap."
