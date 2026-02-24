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
| `SOCRadarApiKey` | Your SOCRadar Platform API key |

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `IncludeRecommended` | true | Include 7 recommended SOCRadar threat feed collections |
| `CustomCollectionIds` | "" | Comma-separated custom feed collection UUIDs |
| `CustomCollectionNames` | "" | Comma-separated custom collection names (matching IDs order) |
| `CustomCollectionTypes` | "" | Comma-separated indicator types: ip, domain, hash, url, email |
| `PollingIntervalMinutes` | 60 | How often to poll feeds (5-1440 minutes) |
| `MinConfidence` | 0 | Minimum confidence score (0-100) |
| `MaxCollectionsPerRun` | 50 | Max collections per polling cycle |
| `EnableFeedsTable` | false | Store indicators in SOCRadar_Feeds_CL custom table |
| `EnableAuditLogging` | false | Log operations to SOCRadar_Feeds_Audit_CL |

## What Gets Deployed

- **SOCRadar-Feeds-Import** - Logic App that polls SOCRadar feeds and imports indicators as TI
- **Storage Account** - Checkpoint state for deduplication
- **Sentinel TI Indicators** - Imported as TiIndicators in your workspace
- **Optional: SOCRadar_Feeds_CL** - Custom table for indicators (if EnableFeedsTable=true)
- **Optional: SOCRadar_Feeds_Audit_CL** - Audit log table (if EnableAuditLogging=true)

## Key Features

- Support for 7 recommended threat feed collections (configurable)
- Custom feed collections with flexible indicator types
- STIX pattern generation for Sentinel TI ingestion
- Checkpoint-based deduplication to prevent duplicates
- Configurable confidence filtering
- Collection rotation for large deployments
- Optional audit logging to Log Analytics

## Post-Deployment

Logic App starts 3 minutes after deployment to allow Azure role propagation.

No manual action required.

## About SOCRadar

SOCRadar is an Extended Threat Intelligence (XTI) platform providing actionable threat intelligence, digital risk protection, and external attack surface management.

Learn more at [socradar.io](https://socradar.io)

## Support

- Documentation: [docs.socradar.io](https://docs.socradar.io)
- Support: support@socradar.io
