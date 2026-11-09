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

## Retained API-age findings

These are explicit compatibility exceptions, not claims that age checks pass or
that another approved solution justifies the findings. Azure provider metadata
read on 9 November 2026 still advertises the versions below. The existing
template and package were exercised together; this change keeps that API
contract while fixing endpoints and test coverage. Adopting newer resource
contracts requires a separate comparison of existing-workspace preservation,
redeploy and service defaults. No API is being declared permanently frozen.

| ARM-TTK finding | Version / surface | Specific reason for retaining it |
|---|---|---|
| apiVersions Should Be Recent | deployments 2021-04-01 | Empty inner-scope workspace precheck; preserve reference evaluation and failure-before-resource-creation semantics. |
| apiVersions Should Be Recent | workspaces 2023-09-01 | Existing-workspace conditional PUT intentionally avoids SKU/retention/cap writes. Avoid changing that resource contract in an endpoint correction. |
| apiVersions Should Be Recent | storageAccounts, tableServices, tables 2023-05-01 | Keep the checkpoint storage and shared-key connection contract aligned with the running package. |
| apiVersions Should Be Recent | dataCollectionEndpoints/dataCollectionRules 2023-03-11 | Preserve the current custom stream/table transform and endpoint binding; no new collection feature is needed. |
| apiVersions Should Be Recent | workspaces/tables 2022-10-01 | Preserve the deployed column/type schema and existing table lifecycle. |
| apiVersions Should Be Recent | userAssignedIdentities 2023-01-31 | Preserve the UAMI client/principal reference contract; no identity or federation migration is part of this change. |
| apiVersions Should Be Recent | serverfarms/sites 2023-12-01 | Preserve Consumption/Linux create and app-settings behavior, especially the initial package pointer and subsequent blob staging. |
| apiVersions Should Be Recent In Reference Functions | storage listKeys 2023-05-01 | Same supported contract as storage creation; only the connection-string DNS suffix changes. |
| apiVersions Should Be Recent In Reference Functions | UAMI reference 2023-01-31 | Same contract as UAMI creation; the client/principal fields and identity selection are unchanged. |
| apiVersions Should Be Recent In Reference Functions | DCE/DCR references 2023-03-11 | Same contract as collection-resource creation; ingestion endpoint and immutable-id fields remain aligned. |

Owner: SOCRadar Feeds maintainer / release owner. Revisit on the next API-contract
upgrade, a provider deprecation notice, or a failing create/redeploy. A successful
new greenfield validates this branch's public-cloud create/import path only;
it does not establish every existing-workspace or sovereign-cloud scenario.

## Sources and evidence

- [Environment endpoints](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/template-functions-deployment#environment)
- [GroupingConfiguration fields](https://learn.microsoft.com/en-us/azure/templates/microsoft.securityinsights/2024-03-01/alertrules#groupingconfiguration)
- [Storage 2023-05-01 schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.storage/2023-05-01/storageaccounts)
- [Data collection rules 2023-03-11 schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/2023-03-11/datacollectionrules)

The release handoff records provider API support, the complete before/after
ARM-TTK failure lists, exact branch/template SHA, CI and mutant results, and the
independent greenfield/import and cleanup evidence. Raw age failures remain
visible there; an exception document does not turn them into absolute PASS.
