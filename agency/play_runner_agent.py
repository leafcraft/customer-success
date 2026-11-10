"""play_runner_agent — the executor that FINISHES the retention play.

The rest of the CS team drafts and routes; this agent is the one set of
HANDS that actually RUNS the approved play after a human said go:

  * SAVE plays  → SEND the approved save-message through your comms
                  platform (vault record 'cs_comms_webhook'); returns the
                  message id the platform answers with.
  * QBR plays   → BOOK the QBR through your calendar (vault record
                  'cs_calendar_webhook'); returns the meeting id.

Each record is an "Add custom" record on Studio's Infrastructure page,
service type 'Outbound webhook' (base URL = the address; header rows, with
the token as a secret row). The address and token live only in the vault;
this module reads them with `leafmesh.connection_rows`. With no record
(day 0), the dev-store fallback RECORDS the sent message / booked meeting
in agency/_shared/store and says so (source = dev_store), so the team still
produces a real, inspectable artifact. The POST payload + the store writes
are the contract you swap your real comms / calendar systems into.

@pre_compose  load_run_context — resolve the play type (send vs book),
              read the human approval signal, and pull the account's
              executions-in-last-24h for the frequency cap, BEFORE the
              actuator runs. Pure context — no side-effects.
@chain        enforce_approval_and_cap — the FAIL-CLOSED floor the LLM /
              router cannot waive:
                * NEVER run a play that wasn't human-approved.
                * NEVER exceed the per-account daily frequency cap.
                * NEVER send a save-message with an empty body.
              When blocked it forces executed=False, drops any artifact,
              and sets a reason — even if the actuator above were bypassed.

The mesh boundary holds: this agent talks to an external comms / calendar
SYSTEM (egress connector), never to the customer directly.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from leafmesh import LeafMeshLogger, SecretNotAllowed, chain, connection_rows, pre_compose
from leafmesh.vault.refs import UnresolvedVaultRef

from agency._shared import extract, store

logger = LeafMeshLogger(__name__)

# Egress connectors — vault records (Infrastructure → "Add custom" →
# 'Outbound webhook'). Inert until the record exists. Send vs book is chosen
# from the play type, not the record, so either or both can be wired.
COMMS_RECORD = "cs_comms_webhook"
CALENDAR_RECORD = "cs_calendar_webhook"
# Never contact one customer more than this many times in 24h (sends and
# bookings together): one save-message and one QBR booking a day at most.
_MAX_PLAYS_PER_DAY = int(os.getenv("CS_MAX_PLAYS_PER_DAY", "2") or "2")

# Approve/deny pair (reference finisher doctrine). A human gate go-token is
# the ONLY thing that runs a play; an explicit block token records WHY it was
# refused; ANY unknown verdict falls through to fail-closed (not approved).
_APPROVAL_TOKENS = frozenset({
    "approve", "approved", "send", "book", "approved_to_send",
    "edit_and_send", "forecast_approved",
})
_BLOCK_TOKENS = frozenset({
    "reject", "rejected", "deny", "denied", "no",
    "hold", "held", "held_for_review",
})
# QBR / book vs save / send classification.
_BOOK_PATTERNS = frozenset({"qbr", "book", "meeting", "review"})

# The real artifact this executor renders — the inspectable record of the play
# it actually ran. Deleted on a blocked floor (never a half-run artifact).
_OUT_DIR = Path("./out")

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
    "artifact_path": "",     # the ./out record of the run (dropped on block)
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


def _message_body(data: Any) -> str:
    """The text to send: the reviewer's own edit (edited_draft on the seat's
    reply) when they wrote one, else the draft they approved."""
    return str(extract.pick(data, "edited_draft", "")
               or extract.pick(data, "draft", "")
               or extract.pick(data, "primary_draft", "")).strip()


# ── @pre_compose: play type + approval + frequency-cap usage ─────────
def load_run_context(data: Any, ctx: Any) -> dict[str, Any]:
    customer_id = str(extract.pick(data, "customer_id", ""))
    signal = _approval_signal(data)
    # The reviewer's own word decides send vs book when it is explicit:
    # 'book' books the QBR; otherwise the play type decides.
    action = "book" if signal == "book" else _resolve_action(data)
    body = _message_body(data)
    return {
        "customer_id": customer_id,
        "action": action,
        # A save-message with no text is never sent (an approval of a
        # strategic-drop alert or a stakeholder brief carries no draft).
        "empty_send": action == "send" and not body,
        "approval_signal": signal,
        # Fail closed: only an explicit approve token runs; an explicit block
        # token is recorded distinctly; anything else is not-approved.
        "approved": signal in _APPROVAL_TOKENS,
        "explicitly_blocked": signal in _BLOCK_TOKENS,
        "executions_today": store.executions_today(customer_id) if customer_id else 0,
    }


# ── real artifact render / delete (reference finisher doctrine) ──────
def _render_artifact(out: dict[str, Any]) -> str:
    """Write the inspectable record of the play we ACTUALLY ran to ./out and
    return its path. This is the finisher's real artifact — the audit sink
    logs the decision, this file is the thing produced."""
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    cid = str(out.get("customer_id") or "account")
    art_id = str(out.get("artifact_id") or "play")
    path = _OUT_DIR / f"{cid}_{out.get('artifact_type') or 'play'}_{art_id}.md"
    if out.get("action") == "book":
        lines = [
            f"# QBR booked — {cid}",
            f"Meeting id: {art_id}",
            f"Slot: {out.get('slot','')}",
            f"Source: {out.get('source','')}",
            "",
            "_Human-approved. Booked via the calendar record (or the dev-store fallback)._",
        ]
    else:
        lines = [
            f"# Save-message sent — {cid}",
            f"Message id: {art_id}",
            f"Channel: {out.get('channel','')}",
            f"Source: {out.get('source','')}",
            "",
            "_Human-approved. Sent via the comms record (or the dev-store fallback)._",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _delete_partial(out: dict[str, Any]) -> None:
    """Drop any ./out artifact when the floor blocks — never a half-run file."""
    p = out.get("artifact_path")
    if p:
        Path(p).unlink(missing_ok=True)
    out["artifact_path"] = ""


# ── connector dispatch (inert until the vault record exists) ─────────
async def _rows(record: str) -> dict[str, Any] | None:
    """The vault record's rows ({headers, query, body, base_url}), or None
    when it is not wired (no record, no vault, or no address on it)."""
    try:
        rows = await connection_rows(record)
    except UnresolvedVaultRef:
        return None
    except SecretNotAllowed as e:
        logger.warning(f"[play_runner] vault record {record!r} refuses this agent ({e}) "
                       f"— dev-store fallback")
        return None
    if not str(rows.get("base_url") or "").strip():
        logger.warning(f"[play_runner] vault record {record!r} has no base URL — dev-store fallback")
        return None
    return rows


def _post_sync(rows: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    url = str(rows["base_url"])
    query = rows.get("query") or {}
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    body = json.dumps({**(rows.get("body") or {}), **payload}).encode("utf-8")
    headers = {"Content-Type": "application/json", **(rows.get("headers") or {})}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (operator-set URL)
        raw = resp.read().decode("utf-8") or "{}"
    try:
        out = json.loads(raw)
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


async def _post(rows: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_post_sync, rows, payload)


async def _send_message(customer_id: str, channel: str, recipient_role: str,
                        body: str) -> dict[str, Any]:
    """SEND the approved save-message. Comms record if wired, else dev store."""
    rows = await _rows(COMMS_RECORD)
    if rows is not None:
        try:
            raw = await _post(rows, {
                "customer_id": customer_id, "channel": channel,
                "recipient_role": recipient_role, "body": body,
            })
            mid = str(raw.get("message_id") or raw.get("id") or store.new_id("msg"))
            store.record_sent_play(customer_id, channel, recipient_role, body, mid)
            return {"artifact_id": mid, "source": "comms_connector"}
        except (urllib.error.URLError, OSError) as e:
            # A real send that failed is a failure, never a dev-store "sent":
            # nothing is recorded as sent and nothing counts toward the cap.
            logger.warning(f"[play_runner] comms connector failed ({e}) — nothing sent")
            return {"failed": True, "error": f"comms send failed: {type(e).__name__}",
                    "source": "comms_connector"}
    rec = store.record_sent_play(customer_id, channel, recipient_role, body)
    return {"artifact_id": rec["artifact_id"], "source": "dev_store"}


async def _book_meeting(customer_id: str, attendee_role: str, slot: str) -> dict[str, Any]:
    """BOOK the QBR. Calendar record if wired, else dev store."""
    slot = slot or store._placeholder_slot()
    rows = await _rows(CALENDAR_RECORD)
    if rows is not None:
        try:
            raw = await _post(rows, {
                "customer_id": customer_id, "attendee_role": attendee_role,
                "slot": slot,
            })
            eid = str(raw.get("meeting_id") or raw.get("event_id")
                      or raw.get("id") or store.new_id("mtg"))
            slot = str(raw.get("slot") or raw.get("start") or slot)
            store.record_booked_meeting(customer_id, slot, attendee_role, eid)
            return {"artifact_id": eid, "slot": slot, "source": "calendar_connector"}
        except (urllib.error.URLError, OSError) as e:
            logger.warning(f"[play_runner] calendar connector failed ({e}) — nothing booked")
            return {"failed": True, "error": f"calendar booking failed: {type(e).__name__}",
                    "source": "calendar_connector", "slot": slot}
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
    explicitly_blocked = bool(rc.get("explicitly_blocked"))
    over_cap = int(rc.get("executions_today", 0)) >= _MAX_PLAYS_PER_DAY
    empty_send = bool(rc.get("empty_send"))

    if not approved or over_cap or empty_send:
        if over_cap and approved:
            reason = f"frequency cap hit ({_MAX_PLAYS_PER_DAY}/day) — refusing to execute"
        elif empty_send and approved:
            reason = "no save-message text to send — refusing to execute"
        elif explicitly_blocked:
            reason = "approver rejected / held the play — refusing to execute"
        else:
            reason = "play not human-approved — refusing to execute (fail closed)"
        logger.warning(f"[play_runner] BLOCKED: {reason} "
                       f"(customer={result.get('customer_id') or '?'})")
        # Fail closed: no artifact survives a blocked run — drop the ./out file too.
        _delete_partial(result)
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
    empty_send = bool(rc.get("empty_send"))

    out = {**_DEFAULTS, "customer_id": customer_id, "action": action}

    # Fail closed here too — the actuator never fires without approval / room / text.
    if not approved or over_cap or empty_send:
        return out  # @chain stamps the block reason deterministically

    if action == "book":
        attendee_role = str(extract.pick(input_data, "recipient_role", "") or "exec_sponsor")
        slot = str(extract.pick(input_data, "slot", ""))
        res = await _book_meeting(customer_id, attendee_role, slot)
        if res.get("failed"):
            out.update({"executed": False, "source": res["source"],
                        "summary": f"Booking for {customer_id} FAILED ({res['error']}); nothing booked."})
            return out
        out.update({
            "executed": True, "artifact_type": "booked_meeting",
            "artifact_id": res["artifact_id"], "slot": res["slot"],
            "source": res["source"],
            "summary": f"Booked QBR {res['artifact_id']} for {customer_id} "
                       f"at {res['slot']} ({res['source']}).",
        })
        out["artifact_path"] = _render_artifact(out)
    else:
        channel = str(extract.pick(input_data, "proposed_channel", "")
                      or extract.pick(input_data, "channel", "") or "email")
        recipient_role = str(extract.pick(input_data, "recipient_role", "") or "champion")
        body = _message_body(input_data)
        res = await _send_message(customer_id, channel, recipient_role, body)
        if res.get("failed"):
            out.update({"executed": False, "channel": channel, "source": res["source"],
                        "summary": f"Save-message to {customer_id} FAILED ({res['error']}); nothing sent."})
            return out
        out.update({
            "executed": True, "artifact_type": "sent_message",
            "artifact_id": res["artifact_id"], "channel": channel,
            "source": res["source"],
            "summary": f"Sent save-message {res['artifact_id']} to {customer_id} "
                       f"via {channel} ({res['source']}).",
        })
        out["artifact_path"] = _render_artifact(out)
    return out
