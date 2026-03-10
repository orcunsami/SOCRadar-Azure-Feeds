# SOCRadar Feeds Infrastructure

Creates the data collection infrastructure for SOCRadar Threat Feeds.

## Resources Created

- Data Collection Endpoint (SOCRadar-Feeds-DCE)
- Custom table: SOCRadar_Feeds_CL (indicator logging)
- Custom table: SOCRadar_Feeds_Audit_CL (audit logging)
- Data Collection Rule for feeds (SOCRadar-Feeds-DCR)
- Data Collection Rule for audit (SOCRadar-Feeds-Audit-DCR)
- SOCRadar Threat Feeds Dashboard workbook (optional)

## Prerequisites

- Microsoft Sentinel workspace

## Deployment

Deploy this playbook first, then deploy the **SOCRadar-Feeds-FunctionApp** playbook.

Use the DCE endpoint and DCR immutable ID values from the deployment outputs as inputs for the Function App playbook.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FAzure%2FAzure-Sentinel%2Fmaster%2FSolutions%2FSOCRadar%2520Threat%2520Feeds%2FPlaybooks%2FSOCRadar-Feeds-Infrastructure%2Fazuredeploy.json)

You can also install this playbook via **Microsoft Sentinel Content Hub**.
