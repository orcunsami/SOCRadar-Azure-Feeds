"""
DCR (Data Collection Rule) ingestion for SOCRadar Feeds custom tables.
Logs to SOCRadar_Feeds_CL and SOCRadar_Feeds_Audit_CL via Azure Monitor Ingestion API.
"""

import os
import logging
from datetime import datetime, timezone
from typing import List

import requests

logger = logging.getLogger(__name__)

DCR_BATCH_SIZE = 500  # DCR ingestion max batch


class DcrLogger:

    def __init__(self, credential, feeds_endpoint="", feeds_dcr_id="",
                 feeds_stream="", audit_endpoint="", audit_dcr_id="",
                 audit_stream=""):
        self.credential = credential
        self.feeds_endpoint = feeds_endpoint
        self.feeds_dcr_id = feeds_dcr_id
        self.feeds_stream = feeds_stream
        self.audit_endpoint = audit_endpoint
        self.audit_dcr_id = audit_dcr_id
        self.audit_stream = audit_stream
        self._monitor_token = None

    @classmethod
    def from_env(cls, credential) -> "DcrLogger":
        return cls(
            credential=credential,
            feeds_endpoint=os.environ.get("FEEDS_DCR_ENDPOINT", ""),
            feeds_dcr_id=os.environ.get("FEEDS_DCR_IMMUTABLE_ID", ""),
            feeds_stream=os.environ.get("FEEDS_STREAM_NAME", "Custom-SOCRadar_Feeds_CL"),
            audit_endpoint=os.environ.get("AUDIT_DCR_ENDPOINT", ""),
            audit_dcr_id=os.environ.get("AUDIT_DCR_IMMUTABLE_ID", ""),
            audit_stream=os.environ.get("AUDIT_STREAM_NAME", "Custom-SOCRadar_Feeds_Audit_CL"),
        )

    def _get_monitor_token(self) -> str:
        if not self._monitor_token:
            token = self.credential.get_token("https://monitor.azure.com/.default")
            self._monitor_token = token.token
        return self._monitor_token

    def _ingest(self, endpoint: str, dcr_id: str, stream: str, data: list):
        if not endpoint or not dcr_id:
            return
        url = f"{endpoint}/dataCollectionRules/{dcr_id}/streams/{stream}?api-version=2023-01-01"
        headers = {
            "Authorization": f"Bearer {self._get_monitor_token()}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, headers=headers, json=data, timeout=30)
        if resp.status_code not in (200, 204):
            logger.warning("DCR ingestion failed: %d %s", resp.status_code, resp.text[:200])

    def log_feeds(self, records: List[dict]):
        if not records:
            return
        for i in range(0, len(records), DCR_BATCH_SIZE):
            batch = records[i:i + DCR_BATCH_SIZE]
            self._ingest(self.feeds_endpoint, self.feeds_dcr_id,
                         self.feeds_stream, batch)

    def log_audit(self, data: dict):
        record = {
            "TimeGenerated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "CollectionsProcessed": data.get("collections_processed", 0),
            "IndicatorsCreated": data.get("indicators_created", 0),
            "IndicatorsSkipped": data.get("indicators_skipped", 0),
            "DurationMs": data.get("duration_ms", 0),
            "Status": data.get("status", ""),
            "ErrorMessage": data.get("error_message", ""),
        }
        self._ingest(self.audit_endpoint, self.audit_dcr_id,
                     self.audit_stream, [record])
