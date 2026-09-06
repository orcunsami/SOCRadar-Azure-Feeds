"""Shared stubs so the Function App code can run outside Azure.

The processor talks to exactly two things: the SOCRadar feed API and the
Sentinel upload API, both through `requests`. Replacing that one module is
enough to drive every path in this directory, including the failure paths that
only appear when a real server returns 429 or 503.
"""

import json
import logging
import os
import sys
import types
from datetime import datetime, timezone

logging.disable(logging.CRITICAL)

# `requests` is a deployment dependency, not a test dependency: every call it
# would make is stubbed below, so a placeholder module keeps the import working
# without pulling the real package into the test environment.
sys.modules.setdefault("requests", types.ModuleType("requests"))

HERE = os.path.dirname(os.path.abspath(__file__))
FUNCTION_APP = os.path.join(os.path.dirname(HERE), "FunctionApp")
if FUNCTION_APP not in sys.path:
    sys.path.insert(0, FUNCTION_APP)


def fixture_items():
    """Real feed entries captured from the API (values only, no key)."""
    with open(os.path.join(HERE, "fixtures", "feed_sample.json"), encoding="utf-8") as f:
        return json.load(f)


class Response:
    def __init__(self, status_code, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text if text is not None else "body"

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeRequests:
    """Serves scripted responses and records what was asked for."""

    MAX_CALLS = 40

    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []

    def _next(self, queue, calls, record):
        calls.append(record)
        if len(self.get_calls) + len(self.post_calls) > self.MAX_CALLS:
            raise AssertionError("the code under test never stopped requesting")
        if not queue:
            raise AssertionError("no scripted response left for %r" % (record,))
        item = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self._next(self.get_responses, self.get_calls, (url, kwargs.get("params")))

    def post(self, url, **kwargs):
        body = kwargs.get("json") or {}
        return self._next(self.post_responses, self.post_calls, (url, list(body.get("indicators", []))))


class FakeTable:
    """Azure Table stand-in that remembers whether the checkpoint moved."""

    def __init__(self, entity=None):
        self.entity = entity
        self.upserts = []

    def get_entity(self, partition_key, row_key):
        if self.entity is None:
            raise KeyError("no checkpoint")
        if isinstance(self.entity, Exception):
            raise self.entity
        return self.entity

    def upsert_entity(self, entity):
        self.upserts.append(dict(entity))
        self.entity = dict(entity)


class FakeCredential:
    def __init__(self):
        self.calls = 0

    def get_token(self, *_scopes):
        self.calls += 1
        return types.SimpleNamespace(token="token-%d" % self.calls, expires_on=9999999999)


class FakeDcrLogger:
    def __init__(self, feeds_ok=True, audit_ok=True):
        self.feeds = []
        self.audits = []
        self.feeds_ok = feeds_ok
        self.audit_ok = audit_ok

    def log_feeds(self, records):
        self.feeds.extend(records)
        return self.feeds_ok

    def log_audit(self, data):
        self.audits.append(dict(data))
        return self.audit_ok


COLLECTION = {"id": "0cb06558728b4dc296019c93b78360d1", "name": "SOCRadar-APT-Recommended-Block-Hash"}


def item(value, seen, feed_type="ip"):
    return {"feed": value, "feed_type": feed_type, "latest_seen_date": seen}


def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def make_processor(requests_stub, table=None, sleeps=None, dcr=None, **kwargs):
    import feeds_processor

    feeds_processor.requests = requests_stub
    if sleeps is not None:
        feeds_processor.time.sleep = sleeps.append

    config = dict(
        socradar_api_key="secret-key-value",
        workspace_id="workspace",
        collections=[COLLECTION],
        enable_feeds_table=dcr is not None,
        enable_audit_logging=dcr is not None,
        credential=FakeCredential(),
        table_client=table if table is not None else FakeTable(),
        dcr_logger=dcr,
    )
    config.update(kwargs)
    return feeds_processor.FeedsProcessor(config)
