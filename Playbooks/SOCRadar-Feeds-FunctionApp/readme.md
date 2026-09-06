# SOCRadar Feeds Function App

Deploys an Azure Function App that imports threat intelligence indicators from SOCRadar feed collections into Microsoft Sentinel TI.

## Features

- 1 default recommended feed (APT Block Hash) + custom collections
- Batch upload to Microsoft Sentinel TI (uploadIndicators API)
- Checkpoint-based deduplication via Azure Table Storage
- User-Assigned Managed Identity (stable principal, survives restarts)
- Application Insights monitoring with step-by-step logging
- Automatic first run after deployment (deployment script restarts function)
- Optional feeds and audit table logging (requires Infrastructure playbook)

## Prerequisites

- Microsoft Sentinel workspace
- SOCRadar Platform API Key
- Infrastructure playbook deployed first (for custom table logging)

## Deployment

Deploy the **SOCRadar-Feeds-Infrastructure** playbook first, then deploy this playbook.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FAzure%2FAzure-Sentinel%2Fmaster%2FSolutions%2FSOCRadar%2520Threat%2520Feeds%2FPlaybooks%2FSOCRadar-Feeds-FunctionApp%2Fazuredeploy.json)

You can also install this playbook via **Microsoft Sentinel Content Hub**.

## Post-Deployment

The function automatically runs after deployment. Check Application Insights for execution logs and the Invocations tab for status.

## Resources Created

- Azure Function App (Python 3.11, Consumption Plan)
- Application Insights (workspace-based, 30 day retention)
- Storage Account + FeedState table (checkpoint tracking)
- User-Assigned Managed Identity
- Role assignments: Microsoft Sentinel Contributor, Storage Table Data Contributor, Website Contributor
- Deployment script for automatic first run
