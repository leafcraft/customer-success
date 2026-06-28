"""play_runner_agent — the executor that FINISHES the retention play.

The rest of the CS team drafts and routes; this agent is the one set of
HANDS that actually RUNS the approved play after a human said go:

  * SAVE plays  → SEND the approved save-message via the comms connector
                  (${CS_COMMS_WEBHOOK_URL:}); returns the message id.
  * QBR plays   → BOOK the QBR via the calendar connector
                  (${CALENDAR_WEBHOOK_URL:}); returns the meeting id.

Connectors are INERT until their webhook env var is set. With neither
set (day-0), the dev-store fallback RECORDS the sent message / booked
meeting in agency/_shared/store so the team still produces a real,
inspectable artifact out of the box. The function signatures (the POST
payload + the store writes) are the contract you swap your real comms /
calendar systems into.

@pre_compose  load_run_context — resolve the play type (send vs book),
              read the human approval signal, and pull the account's
              executions-in-last-24h for the frequency cap, BEFORE the
              actuator runs. Pure context — no side-effects.
@chain        enforce_approval_and_cap — the FAIL-CLOSED floor the LLM /
              router cannot waive:
                * NEVER run a play that wasn't human-approved.
                * NEVER exceed the per-account daily frequency cap.
              When blocked it forces executed=False, drops any artifact,
              and sets a reason — even if the actuator above were bypassed.

The mesh boundary holds: this agent talks to an external comms / calendar
SYSTEM (egress connector), never to the customer directly.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import extract, store

logger = LeafMeshLogger(__name__)

# Egress connectors — inert until set (3-tier config). Send vs book is
# chosen from the play type, not the URL, so either or both can be wired.
COMMS_WEBHOOK_URL = os.getenv("CS_COMMS_WEBHOOK_URL", "") or ""
CALENDAR_WEBHOOK_URL = os.getenv("CALENDAR_WEBHOOK_URL", "") or ""
# Never contact one customer more than this many times in 24h.
_MAX_PLAYS_PER_DAY = int(os.getenv("CS_MAX_PLAYS_PER_DAY", "3") or "3")

# Tokens a human gate (csm/senior/vp) emits that mean "go".
_APPROVAL_TOKENS = frozenset({
    "approve", "approved", "send", "book", "approved_to_send",
    "edit_and_send", "forecast_approved",
})
# QBR / book vs save / send classification.
_BOOK_PATTERNS = frozenset({"qbr", "book", "meeting", "review"})

_DEFAULTS: dict[str, Any] = {
    "executed": False,
    "action": "",            # send | book
    "artifact_type": "",     # sent_message | booked_meeting
    "artifact_id": "",
    "channel": "",
    "slot": "",
    "customer_id": "",
    "blocked": False,
    "block_reason": "",
    "summary": "",
    "source": "",            # comms_connector | calendar_connector | dev_store
}


def _approval_signal(data: Any) -> str:
    """The human gate's go/no-go token, wherever the conductor put it."""
    for key in ("human_message", "human_decision", "decision"):
        v = extract.pick(data, key, "")
        if v:
            return str(v).strip().lower()
    human = data.get("human_data") if isinstance(data, dict) else None
    if isinstance(human, dict):
        for key in ("message", "decision"):
            if human.get(key):
                return str(human[key]).strip().lower()
    return ""


def _resolve_action(data: Any) -> str:
    """send (save-message) vs book (QBR) — from the play type / pattern."""
    play = str(
        extract.pick(data, "play", "")
        or extract.pick(data, "play_type", "")
        or extract.pick(data, "primary_pattern", "")
    ).strip().lower()
    if any(tok in play for tok in _BOOK_PATTERNS):
        return "book"
    # an explicit channel of "call/inperson/meeting" with no draft also books
    if not extract.pick(data, "draft", "") and extract.pick(data, "slot", ""):
        return "book"
    return "send"


# ── @pre_compose: play type + approval + frequency-cap usage ─────────
def load_run_context(data: Any, ctx: Any) -> dict[str, Any]:
    customer_id = str(extract.pick(data, "customer_id", ""))
    signal = _approval_signal(data)
    return {
        "customer_id": customer_id,
        "action": _resolve_action(data),
        "approval_signal": signal,
        "approved": signal in _APPROVAL_TOKENS,
        "executions_today": store.executions_today(customer_id) if customer_id else 0,
    }


# ── connector dispatch (inert until the webhook env var is set) ──────
def _post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (operator-set URL)
        raw = resp.read().decode("utf-8") or "{}"
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _send_message(customer_id: str, channel: str, recipient_role: str,
                  body: str) -> dict[str, Any]:
    """SEND the approved save-message. Connector if wired, else dev store."""
    if COMMS_WEBHOOK_URL:
        try:
            raw = _post(COMMS_WEBHOOK_URL, {
                "customer_id": customer_id, "channel": channel,
                "recipient_role": recipient_role, "body": body,
            })
            mid = str(raw.get("message_id") or raw.get("id") or store.new_id("msg"))
            store.record_sent_play(customer_id, channel, recipient_role, body, mid)
            return {"artifact_id": mid, "source": "comms_connector"}
        except (urllib.error.URLError, OSError) as e:
            logger.warning(f"[play_runner] comms connector failed ({e}) — dev-store fallback")
    rec = store.record_sent_play(customer_id, channel, recipient_role, body)
    return {"artifact_id": rec["artifact_id"], "source": "dev_store"}


def _book_meeting(customer_id: str, attendee_role: str, slot: str) -> dict[str, Any]:
    """BOOK the QBR. Connector if wired, else dev store."""
    slot = slot or store._placeholder_slot()
    if CALENDAR_WEBHOOK_URL:
        try:
            raw = _post(CALENDAR_WEBHOOK_URL, {
                "customer_id": customer_id, "attendee_role": attendee_role,
                "slot": slot,
            })
            eid = str(raw.get("meeting_id") or raw.get("event_id")
                      or raw.get("id") or store.new_id("mtg"))
            slot = str(raw.get("slot") or raw.get("start") or slot)
            store.record_booked_meeting(customer_id, slot, attendee_role, eid)
            return {"artifact_id": eid, "slot": slot, "source": "calendar_connector"}
        except (urllib.error.URLError, OSError) as e:
            logger.warning(f"[play_runner] calendar connector failed ({e}) — dev-store fallback")
    rec = store.record_booked_meeting(customer_id, slot, attendee_role)
    return {"artifact_id": rec["artifact_id"], "slot": slot, "source": "dev_store"}


# ── @chain: FAIL-CLOSED approval + frequency-cap floor ──────────────
async def enforce_approval_and_cap(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    for k, v in _DEFAULTS.items():
        result.setdefault(k, v)

    rc = extract.prepared_context(ctx)
    approved = bool(rc.get("approved"))
    over_cap = int(rc.get("executions_today", 0)) >= _MAX_PLAYS_PER_DAY

    if not approved or over_cap:
        reason = ("play not human-approved — refusing to execute"
                  if not approved else
                  f"frequency cap hit ({_MAX_PLAYS_PER_DAY}/day) — refusing to execute")
        logger.warning(f"[play_runner] BLOCKED: {reason} "
                       f"(customer={result.get('customer_id') or '?'})")
        # Fail closed: no artifact survives a blocked run.
        result.update({
            "executed": False, "blocked": True, "block_reason": reason,
            "artifact_id": "", "artifact_type": "",
            "summary": f"Blocked — {reason}.",
        })
        return result

    logger.info(
        f"[play_runner] EXECUTED {result.get('action')} for "
        f"{result.get('customer_id') or '?'} → {result.get('artifact_type')} "
        f"{result.get('artifact_id')} ({result.get('source')})"
    )
    return result


@pre_compose(context_processor=load_run_context)
@chain(enforce_approval_and_cap)
async def play_runner_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    """Run the approved play: SEND the save-message or BOOK the QBR, then
    return the artifact. The @chain floor fails closed on no-approval /
    over-cap; only do the side-effect when the prepared context says go."""
    input_data = input_data or {}
    rc = extract.prepared_context(context)
    customer_id = str(rc.get("customer_id") or extract.pick(input_data, "customer_id", ""))
    action = str(rc.get("action") or "send")
    approved = bool(rc.get("approved"))
    over_cap = int(rc.get("executions_today", 0)) >= _MAX_PLAYS_PER_DAY

    out = {**_DEFAULTS, "customer_id": customer_id, "action": action}

    # Fail closed here too — the actuator never fires without approval / room.
    if not approved or over_cap:
        return out  # @chain stamps the block reason deterministically

    if action == "book":
        attendee_role = str(extract.pick(input_data, "recipient_role", "") or "exec_sponsor")
        slot = str(extract.pick(input_data, "slot", ""))
        res = _book_meeting(customer_id, attendee_role, slot)
        out.update({
            "executed": True, "artifact_type": "booked_meeting",
            "artifact_id": res["artifact_id"], "slot": res["slot"],
            "source": res["source"],
            "summary": f"Booked QBR {res['artifact_id']} for {customer_id} "
                       f"at {res['slot']} ({res['source']}).",
        })
    else:
        channel = str(extract.pick(input_data, "proposed_channel", "")
                      or extract.pick(input_data, "channel", "") or "email")
        recipient_role = str(extract.pick(input_data, "recipient_role", "") or "champion")
        body = str(extract.pick(input_data, "draft", "")
                   or extract.pick(input_data, "primary_draft", ""))
        res = _send_message(customer_id, channel, recipient_role, body)
        out.update({
            "executed": True, "artifact_type": "sent_message",
            "artifact_id": res["artifact_id"], "channel": channel,
            "source": res["source"],
            "summary": f"Sent save-message {res['artifact_id']} to {customer_id} "
                       f"via {channel} ({res['source']}).",
        })
    return out
