#!/bin/bash
# SOCRadar Feeds Function App - Azure FAST RESET
# Deletes everything without confirmation - for dev/test only!
# Usage: ./portal_reset.sh [workspace_name]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load config
if [ -f "$SCRIPT_DIR/test.config" ]; then
    source "$SCRIPT_DIR/test.config"
fi

# Override workspace from CLI arg if provided
if [ -n "$1" ]; then
    WORKSPACE_NAME="$1"
fi

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-b622bfd9-6b2b-45b3-b7d2-7b5d3114b96b}"
RESOURCE_GROUP="${RESOURCE_GROUP:-RSS-app-RG}"
WORKSPACE_NAME="${WORKSPACE_NAME:-socradar-feeds-func-mar03}"

echo "=== FEEDS FUNCTION APP - FAST RESET ==="
echo "  Workspace:      $WORKSPACE_NAME"
echo "  Resource Group:  $RESOURCE_GROUP"
echo ""

# 1. Stop & Delete Function App
echo "[1/8] Deleting Function App..."
for fa in $(az functionapp list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'socradar-feeds-')].name" -o tsv 2>/dev/null); do
    echo "  Stopping: $fa"
    az functionapp stop --name "$fa" -g "$RESOURCE_GROUP" 2>/dev/null || true
    echo "  Deleting: $fa"
    az functionapp delete --name "$fa" -g "$RESOURCE_GROUP" --keep-empty-plan 2>/dev/null || true
done
echo "  Done"

# 2. Delete Hosting Plan
echo "[2/8] Deleting Hosting Plan..."
az appservice plan delete --name "SOCRadar-Feeds-Plan" -g "$RESOURCE_GROUP" --yes 2>/dev/null || true
echo "  Done"

# 3. Delete Role Assignments
echo "[3/8] Deleting Role Assignments..."
for id in $(az role assignment list --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" --query "[?principalType=='ServicePrincipal'].id" -o tsv 2>/dev/null); do
    az role assignment delete --ids "$id" 2>/dev/null || true
done
for id in $(az role assignment list --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME" --query "[?principalType=='ServicePrincipal'].id" -o tsv 2>/dev/null); do
    az role assignment delete --ids "$id" 2>/dev/null || true
done
echo "  Done"

# 4. Delete Storage Account (starts with 'srfeeds')
echo "[4/8] Deleting Storage Account..."
for sa in $(az storage account list -g "$RESOURCE_GROUP" --query "[?starts_with(name, 'srfeeds')].name" -o tsv 2>/dev/null); do
    echo "  Deleting: $sa"
    az storage account delete --name "$sa" -g "$RESOURCE_GROUP" --yes 2>/dev/null || true
done
echo "  Done"

# 5. Delete Audit Infrastructure (DCR, DCE, custom tables)
echo "[5/8] Deleting Audit Infrastructure..."
# DCRs first (depend on DCE)
az monitor data-collection rule delete --name "SOCRadar-Feeds-DCR" -g "$RESOURCE_GROUP" --yes 2>/dev/null &
az monitor data-collection rule delete --name "SOCRadar-Feeds-Audit-DCR" -g "$RESOURCE_GROUP" --yes 2>/dev/null &
wait
# DCE (after DCRs)
az monitor data-collection endpoint delete --name "SOCRadar-Feeds-DCE" -g "$RESOURCE_GROUP" --yes 2>/dev/null || true
# Custom tables
az monitor log-analytics workspace table delete --workspace-name "$WORKSPACE_NAME" -g "$RESOURCE_GROUP" --name "SOCRadar_Feeds_CL" --yes 2>/dev/null || true
az monitor log-analytics workspace table delete --workspace-name "$WORKSPACE_NAME" -g "$RESOURCE_GROUP" --name "SOCRadar_Feeds_Audit_CL" --yes 2>/dev/null || true
echo "  Done"

# 6. Delete Workbook
echo "[6/8] Deleting Workbook..."
for wb in $(az resource list -g "$RESOURCE_GROUP" --resource-type "Microsoft.Insights/workbooks" --query "[?contains(name, 'SOCRadar') || contains(tags.displayName, 'SOCRadar')].id" -o tsv 2>/dev/null); do
    az resource delete --ids "$wb" 2>/dev/null || true
done
echo "  Done"

# 7. Delete TI Indicators
echo "[7/8] Deleting TI Indicators..."
TI_URL="https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/providers/Microsoft.SecurityInsights/threatIntelligence/main/indicators?api-version=2024-03-01&\$top=1000"
TI_IDS=$(az rest --method GET --url "$TI_URL" --query "value[].name" -o tsv 2>/dev/null || true)

if [ -n "$TI_IDS" ]; then
    deleted=0
    total=$(echo "$TI_IDS" | wc -l | tr -d ' ')
    echo "  Found $total indicators to delete..."
    while IFS= read -r id; do
        [ -z "$id" ] && continue
        az rest --method DELETE \
            --url "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/providers/Microsoft.SecurityInsights/threatIntelligence/main/indicators/$id?api-version=2024-03-01" \
            >/dev/null 2>&1 &
        deleted=$((deleted + 1))
        if [ $((deleted % 25)) -eq 0 ]; then
            wait
            echo "  Deleted: $deleted / $total"
        fi
    done <<< "$TI_IDS"
    wait
    echo "  $deleted indicators deleted"
else
    echo "  No indicators found"
fi

# 8. Delete Sentinel + Workspace
echo "[8/8] Deleting Sentinel + Workspace..."
az rest --method DELETE \
    --url "https://management.azure.com/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationalInsights/workspaces/$WORKSPACE_NAME/providers/Microsoft.SecurityInsights/onboardingStates/default?api-version=2024-03-01" \
    2>/dev/null || true
az resource delete --ids "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.OperationsManagement/solutions/SecurityInsights($WORKSPACE_NAME)" 2>/dev/null || true
az monitor log-analytics workspace delete --workspace-name "$WORKSPACE_NAME" -g "$RESOURCE_GROUP" --force --yes 2>/dev/null || true
echo "  Done"

echo ""
echo "=== RESET COMPLETE ==="
echo ""

# Print remaining resources
ALL_RESOURCES=$(az resource list -g "$RESOURCE_GROUP" --query "length(@)" -o tsv 2>/dev/null || echo "0")
ROLE_COUNT=$(az role assignment list --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" --query "[?principalType=='ServicePrincipal'] | length(@)" -o tsv 2>/dev/null || echo "0")
echo "  Resources in RG: $ALL_RESOURCES"
echo "  Role Assignments: $ROLE_COUNT"

if [ "$ALL_RESOURCES" = "0" ] && [ "$ROLE_COUNT" = "0" ]; then
    echo ""
    echo "  RG is CLEAN - ready for fresh deploy!"
else
    echo ""
    echo "  Remaining resources:"
    az resource list -g "$RESOURCE_GROUP" --query "[].{name:name, type:type}" -o table 2>/dev/null
fi
echo ""
echo "NOTE: Use a NEW workspace name (soft-delete recovers old data for 14 days)"
