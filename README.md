# SOCRadar Threat Feeds for Microsoft Sentinel

Ingests threat intelligence indicators from SOCRadar feeds into Microsoft Sentinel TI.

## Deployment

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Feeds%2Fmaster%2Fazuredeploy.json)

Click the **Deploy to Azure** button above. Fill in the parameters and click **Create**. The function app and code are deployed automatically.

Or via CLI:

```bash
az deployment group create \
  --resource-group <YOUR_RG> \
  --template-file azuredeploy.json \
  --parameters \
    WorkspaceName=<YOUR_WORKSPACE> \
    SocradarApiKey=<API_KEY>
```

## Prerequisites

- Microsoft Sentinel workspace **in the same resource group** you deploy to
- SOCRadar Platform API Key

## Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `WorkspaceName` | Yes | - | Microsoft Sentinel workspace name |
| `DeployNewWorkspace` | No | `false` | Create `WorkspaceName` instead of using an existing one. Set `true` for a greenfield deploy into an empty resource group; leave `false` to attach to your existing workspace without touching its pricing tier, retention or daily cap. |
| `WorkspaceLocation` | No | RG location | Region of the workspace |
| `SocradarApiKey` | Yes | - | SOCRadar Platform API key |
| `IncludeAPTBlockHash` | No | true | Include APT Recommended Block Hash feed (~500 indicators) |
| `CustomCollectionIds` | No | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | No | "" | Comma-separated custom collection names |
| `InitialLookbackDays` | No | 30 | First run of a collection sends only indicators last seen within this many days. `0` sends the whole feed. |
| `PollingIntervalMinutes` | No | 60 | Polling interval (5-1440 minutes). 60 and above is rounded down to whole hours. |
| `EnableFeedsTable` | No | true | Store indicators in SOCRadar_Feeds_CL |
| `EnableAuditLogging` | No | true | Log runs to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | No | true | Deploy the dashboard (needs `EnableFeedsTable`) |

## Existing installations

Deployments made before `DeployNewWorkspace` existed stated a pricing tier on the workspace
resource, and a template overwrites every field it states. If the target workspace was on a
**commitment tier**, redeploying reset it to `PerGB2018` (pay-as-you-go).

Check the current tier:

```bash
az monitor log-analytics workspace show -g <resource-group> -n <workspace> \
  --query "{sku:sku.name, lastSkuUpdate:sku.lastSkuUpdate}" -o json
```

If `lastSkuUpdate` lines up with when you deployed this integration and the tier isn't the one
you picked, reset your commitment tier from **Log Analytics workspaces > Usage and estimated
costs > Pricing tier**. The current template states no workspace-level settings at all, so
redeploying -- even with `DeployNewWorkspace=true` set by mistake -- cannot change its pricing
tier, retention or daily cap.

Redeploy the template over an existing installation to pick up the `IndicatorsFailed` and `CollectionsFailed` audit columns; until then the data collection rule drops those two columns silently.

## What Gets Deployed

- **Azure Function App** (Python 3.11, Consumption plan) - Polls SOCRadar feeds on schedule
- **Application Insights** - Step-by-step logging (workspace-based, 30 day retention)
- **User-Assigned Managed Identity** - Access to Microsoft Sentinel and Storage, no stored Azure credentials
- **Storage Account** - `FeedState` table holding one checkpoint per collection
- **DCE + DCR + Custom Tables** (optional) - SOCRadar_Feeds_CL and SOCRadar_Feeds_Audit_CL
- **Workbook** (optional) - SOCRadar Threat Feeds Dashboard
- **Deployment Script** - Checks the package loaded and triggers the first import

## How a run works

1. Each configured collection is fetched from the SOCRadar feed API.
2. Indicators last seen after the collection's checkpoint (minus a 48 hour overlap) are sent to Microsoft Sentinel TI in batches of 100. On the first run the window is `InitialLookbackDays`.
3. The checkpoint moves to the newest `latest_seen_date` that was delivered. If a batch never reaches Microsoft Sentinel the checkpoint stays where it was and the run is recorded as `PartialSuccess`; the next run sends those indicators again.
4. Indicator ids are stable (derived from type, value and collection), so re-sending an indicator updates the existing record instead of creating a copy.

Indicators with an unsupported type or hash length are counted and skipped, not sent as a guessed type.

## Indicator Types

| Feed type | Pattern |
|------|---------|
| ip | `[ipv4-addr:value = '...']` |
| ipv6 | `[ipv6-addr:value = '...']` |
| domain, hostname | `[domain-name:value = '...']` |
| url | `[url:value = '...']` |
| hash (32 / 40 / 64 chars) | `[file:hashes.MD5 = '...']`, `SHA-1`, `SHA-256` |
| email | `[email-addr:value = '...']` |

## Post-Deployment

The deployment script verifies that the package URL answers and that the function was indexed, then restarts the app so the first import runs. Custom tables receive their first rows 10-15 minutes after the first run; that delay is normal for a newly created table.

### Audit table

`SOCRadar_Feeds_Audit_CL` gets one row per run:

| Status | Meaning |
|--------|---------|
| `Success` | every collection was delivered completely |
| `PartialSuccess` | at least one collection failed or lost indicators; `ErrorMessage` says which. Lost indicators are sent again on the next run |
| `Failed` | the run itself failed before any collection completed |

### Managing Collections

1. Go to your **Function App** in Azure Portal
2. Open **Settings > Environment variables**
3. Edit:
   - `CUSTOM_COLLECTION_IDS` — comma-separated collection UUIDs
   - `CUSTOM_COLLECTION_NAMES` — comma-separated names (same order as IDs)
   - `INCLUDE_0cb06558728b4dc296019c93b78360d1` — `True` or `False` for the APT Block Hash feed
4. Click **Apply** — the Function App restarts and, because the timer runs on startup, imports immediately

New collections start from `InitialLookbackDays`. Removed collections leave harmless orphan checkpoints in Table Storage.

### Monitoring Logs

1. Go to your **Function App** in Azure Portal
2. **Monitoring > Log stream** for real-time logs
3. Or **Application Insights > Logs**:

```kql
traces
| where timestamp > ago(1h)
| where message has "Step"
| order by timestamp desc
```

## Development

```bash
python3 tests/run_all.py        # unit tests, no Azure needed
python3 tests/mutate.py         # proves the tests catch the bugs they exist for
python3 scripts/build_package.py --out dist/FunctionApp.zip --deps-from <released FunctionApp.zip>
```

Build the package with the script, not `zip -r`: archives from the macOS zip tool can carry entries the Linux worker cannot read, and the host then indexes zero functions without reporting an error.

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform. Learn more at [socradar.io](https://socradar.io)

## Support

integration@socradar.io
