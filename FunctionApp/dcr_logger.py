"""
DCR (Data Collection Rule) ingestion for SOCRadar Feeds custom tables.
Logs to SOCRadar_Feeds_CL and SOCRadar_Feeds_Audit_CL via Azure Monitor Ingestion API.
"""

import os
import logging
import time
from datetime import datetime, timezone
from typing import List

import requests

logger = logging.getLogger(__name__)

DCR_BATCH_SIZE = 500  # DCR ingestion max batch
TOKEN_REFRESH_MARGIN_SECONDS = 300


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
        self._monitor_token_expires = 0

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
        now = time.time()
        if not self._monitor_token or self._monitor_token_expires - now < TOKEN_REFRESH_MARGIN_SECONDS:
            token = self.credential.get_token("https://monitor.azure.com/.default")
            self._monitor_token = token.token
            self._monitor_token_expires = getattr(token, "expires_on", now + 3600)
        return self._monitor_token

    def _ingest(self, endpoint: str, dcr_id: str, stream: str, data: list) -> bool:
        """Send one batch. False means the rows did not land; the caller decides
        whether that is worth an error. A missing endpoint is a configuration
        gap, not a transport failure, so it is logged once and reported False.
        """
        if not endpoint or not dcr_id:
            logger.warning("DCR ingestion skipped for %s: endpoint or DCR id not configured", stream)
            return False
        url = f"{endpoint}/dataCollectionRules/{dcr_id}/streams/{stream}?api-version=2023-01-01"
        try:
            token = self._get_monitor_token()
        except Exception as e:
            logger.error("DCR token acquisition failed: %s", e)
            return False
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=30)
        except Exception as e:
            logger.error("DCR ingestion request failed for %s: %s", stream, e)
            return False
        if resp.status_code not in (200, 204):
            logger.error("DCR ingestion failed for %s: %d %s", stream, resp.status_code, resp.text[:200])
            return False
        return True

    def log_feeds(self, records: List[dict]) -> bool:
        if not records:
            return True
        ok = True
        for i in range(0, len(records), DCR_BATCH_SIZE):
            batch = records[i:i + DCR_BATCH_SIZE]
            ok = self._ingest(self.feeds_endpoint, self.feeds_dcr_id, self.feeds_stream, batch) and ok
        return ok

    def log_audit(self, data: dict) -> bool:
        record = {
            "TimeGenerated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "CollectionsProcessed": data.get("collections_processed", 0),
            "CollectionsFailed": data.get("collections_failed", 0),
            "IndicatorsCreated": data.get("indicators_created", 0),
            "IndicatorsSkipped": data.get("indicators_skipped", 0),
            "IndicatorsFailed": data.get("indicators_failed", 0),
            "DurationMs": data.get("duration_ms", 0),
            "Status": data.get("status", ""),
            "ErrorMessage": data.get("error_message", ""),
        }
        return self._ingest(self.audit_endpoint, self.audit_dcr_id, self.audit_stream, [record])
