"""Shape-tolerant input helpers shared by the CS agent modules.

The conductor may deliver inputs flat, nested under upstream_yields
(either directly or keyed by the calling agent), or inside
original_webhook_data / human_data. These helpers keep every agent
runnable regardless of how it was reached, and parse the LLM's JSON.
"""
from __future__ import annotations

import json
import re
from typing import Any


def parse_json(resp: Any) -> dict[str, Any]:
    """Parse an LLM response into a dict; tolerate prose-wrapped JSON."""
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except ValueError:
            m = re.search(r"\{.*\}", resp, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
    return {}


def pick(input_data: Any, key: str, default: Any = "") -> Any:
    """Read `key` from input_data, then upstream_yields (flat or
    agent-keyed), then customer_event / human_data."""
    if not isinstance(input_data, dict):
        return default
    if input_data.get(key) not in (None, ""):
        return input_data[key]
    for blob_key in ("upstream_yields", "customer_event", "health_verdict", "human_data"):
        blob = input_data.get(blob_key)
        if isinstance(blob, dict):
            if blob.get(key) not in (None, ""):
                return blob[key]
            for v in blob.values():
                if isinstance(v, dict) and v.get(key) not in (None, ""):
                    return v[key]
    return default


def session_id(input_data: Any, context: Any) -> str:
    for s in (context, input_data):
        if isinstance(s, dict):
            if s.get("session_id"):
                return str(s["session_id"])
            owd = s.get("original_webhook_data")
            if isinstance(owd, dict) and owd.get("session_id"):
                return str(owd["session_id"])
    return ""


def prepared_context(context: Any) -> dict[str, Any]:
    """The dict a @pre_compose processor returned, injected by the SDK at
    context['prepared_data']['context']."""
    if not isinstance(context, dict):
        return {}
    prepared = context.get("prepared_data") or {}
    ctx = prepared.get("context") if isinstance(prepared, dict) else {}
    return ctx if isinstance(ctx, dict) else {}
