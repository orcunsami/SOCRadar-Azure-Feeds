# ARM-TTK review: 9 November 2026

The template supports Azure public cloud. Python authentication scopes and the
release package are unchanged. ARM-TTK runs all tests; no test is skipped.

## Fixed findings

- **DeploymentTemplate Must Not Contain Hardcoded Uri:** both storage connection
  strings use `environment().suffixes.storage`. The deployment script receives
  `RESOURCE_MANAGER` and `STORAGE_SUFFIX` from `environment()` and uses those
  values for its ARM request and staged blob URL. The ARM endpoint's trailing
  slash is trimmed before adding `/subscriptions`.
- **Location Should Not Be Hardcoded:** the six application/support resources
  use `parameters('location')`, whose default is `resourceGroup().location`.
  `WorkspaceLocation` remains independent for existing/cross-region workspaces.
- **Template Should Not Contain Blanks:** thirteen optional empty grouping arrays
  were omitted. AllEntities does not use these selectors; Selected retains its
  nonempty entity and alert-detail selectors. Required empty UAMI objects and the
  empty precheck resource list remain. Grouping, lookback and reopen behavior
  are unchanged.

## API-age findings closed by task 0116

The former API-age exceptions are closed. Resource declarations and matching
reference/listKeys calls now use supported newer API versions. No ARM-TTK
checks are excluded. The full template tree is tested by `tests/run_all.py`;
CI installs a pinned ARM-TTK checkout before running it.

| Resource surface | API version |
|---|---|
| Nested deployments | 2025-03-01 |
| Workspaces and custom tables | 2025-02-01 |
| Storage account, table service and tables | 2025-01-01 |
| Data collection endpoints and rules | 2024-03-11 |
| User-assigned identity | 2024-11-30 |
| App Service plan and Function App | 2025-03-01 |
| Workbooks | 2023-06-01 |
| Deployment scripts | 2023-08-01 |

Resource names, IDs, role GUID inputs, stream schemas, application settings,
package staging script and runtime code are unchanged. Existing-workspace
creation remains conditional and does not write SKU, retention or cap fields.
New greenfield and identical-parameter redeploy proof is required before release.

Some providers have no recent stable version. ARM-TTK accepts its cached latest
stable API (including OperationsManagement solutions 2015-11-01-preview,
Insights components 2020-02-02 and Authorization roleAssignments 2022-04-01).
These are tool-native passes, with no template skip metadata.

## Sources and evidence

- [Environment endpoints](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/template-functions-deployment#environment)
- [GroupingConfiguration fields](https://learn.microsoft.com/en-us/azure/templates/microsoft.securityinsights/2024-03-01/alertrules#groupingconfiguration)
- [Storage 2025-01-01 schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.storage/2025-01-01/storageaccounts)
- [Data collection rules 2024-03-11 schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/2024-03-11/datacollectionrules)

Task 0116 records provider support, exact baseline/final results, semantic diff,
CI and mutation evidence. Static PASS does not establish live create/redeploy,
existing-workspace preservation or sovereign-cloud compatibility.
