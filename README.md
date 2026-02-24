# SOCRadar Threat Feeds for Microsoft Sentinel

Ingests threat intelligence indicators from SOCRadar feeds into Microsoft Sentinel TI.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Forcunsami%2FSOCRadar-Azure-Feeds%2Fmaster%2Fazuredeploy.json)

## Prerequisites

- Microsoft Sentinel workspace
- SOCRadar Platform API Key

## Configuration

### Required Parameters

| Parameter | Description |
|-----------|-------------|
| `WorkspaceName` | Your Sentinel workspace name (e.g., `my-sentinel-workspace`, NOT the Workspace ID/GUID) |
| `WorkspaceLocation` | Region of your workspace (e.g., `centralus`, `northeurope`) |
| `SocradarApiKey` | Your SOCRadar Platform API key |

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `IncludeRecommended` | true | Include 7 recommended SOCRadar threat feed collections |
| `CustomCollectionIds` | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | "" | Comma-separated custom collection names (matching IDs order) |
| `PollingIntervalMinutes` | 60 | How often to poll feeds (5-1440 minutes) |
| `EnableFeedsTable` | true | Store indicators in SOCRadar_Feeds_CL custom table |
| `EnableAuditLogging` | true | Log operations to SOCRadar_Feeds_Audit_CL |
| `EnableWorkbook` | true | Deploy SOCRadar Threat Feeds Analytics Dashboard |

## What Gets Deployed

- **SOCRadar-Feeds-Import** - Logic App that polls SOCRadar feeds and imports indicators as TI
- **Storage Account** - Checkpoint state for deduplication
- **Sentinel TI Indicators** - Imported as TiIndicators in your workspace
- **SOCRadar_Feeds_CL** - Custom table for indicator analytics (if EnableFeedsTable=true)
- **SOCRadar_Feeds_Audit_CL** - Audit log table (if EnableAuditLogging=true)
- **SOCRadar Threat Feeds Dashboard** - Workbook with indicator charts and audit monitoring (if EnableWorkbook=true)

## Key Features

- Support for 7 recommended threat feed collections (configurable)
- Custom feed collections (indicator type auto-detected from feed data)
- STIX pattern generation for Sentinel TI ingestion
- Checkpoint-based deduplication to prevent duplicates
- Collection rotation for large deployments
- Optional audit logging to Log Analytics
- Analytics dashboard with indicator and audit visualizations

## Post-Deployment

Logic Apps are configured to start **3 minutes after deployment** to allow Azure role propagation.

No manual action required - they will start automatically.

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform.

Learn more at [socradar.io](https://socradar.io)

## Support

- **Documentation:** [docs.socradar.io](https://docs.socradar.io)
- **Support:** support@socradar.io
