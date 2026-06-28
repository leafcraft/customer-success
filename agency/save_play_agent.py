"""save_play_agent — self-reliant save-play drafter.

@pre_compose  load_play_history — pull the account's prior plays + the
              running touch count from the session stash so the LLM
              doesn't re-send the same angle and KNOWS this is touch N.
@chain        enforce_exec_signature_and_cap — deterministic floors:
              * EXEC-SIGNATURE: ARR >= $500K AND immediate urgency →
                requires_executive_signature (VP CS personal-note).
              * QUALITY: a thin/short draft is bumped to exec_sponsor so
                a senior CSM reviews it — never goes out on the standard
                path.
              * BOUNDED TOUCH LOOP (Rule 3): each save-play touch on an
                account increments a session counter; past the cap the
                play is forced to exec_sponsor and an escalation_reason is
                set — the Manager's "no response after 3 touches" rule,
                enforced, not hoped for. Records the play in the store.
@compose      per-tier payloads — csm_human / senior_csm_human (the
              draft to review) vs vp_cs_human (the escalation reason +
              draft for a personal note). Different recipients need
              different briefs.

The drafted text is NEVER sent here — it always routes to a human tier.
"""
from __future__ import annotations

import os
from typing import Any

from leafmesh import LeafMeshLogger, chain, compose, pre_compose

from agency._shared import extract, session_stash, store

logger = LeafMeshLogger(__name__)

# ARR >= $500K + immediate → VP CS personal-note territory.
EXEC_SIGNATURE_ARR_CENTS = 50_000_000
# Strategic accounts (ARR > $250K) for the urgency uplift.
STRATEGIC_ARR_CENTS = 25_000_000
# A save-play with no customer response must stop after this many touches.
_MAX_TOUCHES = int(os.getenv("CS_SAVE_PLAY_MAX_TOUCHES", "3") or "3")

VALID_CHANNELS = frozenset({"email", "slack", "call", "inperson"})
VALID_ROLES = frozenset({"champion", "exec_sponsor", "new_contact"})

_DEFAULTS: dict[str, Any] = {
    "primary_draft": "", "alternative_angles": [], "proposed_channel": "email",
    "urgency": "this_week", "recipient_role": "champion",
    "requires_executive_signature": False, "customer_id": "",
    "draft": "", "escalation_reason": "",
}


# ── @pre_compose: prior plays + this account's touch count ───────────
def load_play_history(data: Any, ctx: Any) -> dict[str, Any]:
    customer_id = str(extract.pick(data, "customer_id", ""))
    sid = extract.session_id(data, ctx)
    touch_no = session_stash.peek(sid, f"touch:{customer_id}") if customer_id else 0
    return {
        "customer_id": customer_id,
        "prior_plays": store.prior_plays(customer_id) if customer_id else [],
        "touch_number": touch_no + 1,   # this would be the Nth touch
        "arr_cents": store.get_account(customer_id).get("arr_cents", 0) if customer_id else 0,
    }


# ── @chain: exec-signature, quality, and the bounded touch loop ──────
async def enforce_exec_signature_and_cap(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    # Be robust to partial inputs (smoke tests / connector edge cases): the
    # @chain normally receives the agent's _DEFAULTS-merged output, but a
    # missing key must never KeyError the floor.
    for k, v in _DEFAULTS.items():
        result.setdefault(k, v)

    if result.get("proposed_channel") not in VALID_CHANNELS:
        result["proposed_channel"] = "email"
    if result.get("recipient_role") not in VALID_ROLES:
        result["recipient_role"] = "champion"

    customer_id = str(result.get("customer_id", ""))
    try:
        arr = float(result.get("arr_cents", 0) or 0)
    except (TypeError, ValueError):
        arr = 0.0

    # Strategic urgency uplift: this_month → this_week for big accounts.
    if arr > STRATEGIC_ARR_CENTS and result.get("urgency") == "this_month":
        result["urgency"] = "this_week"

    # EXEC-SIGNATURE FLOOR: $500K + immediate → VP personal note.
    if arr >= EXEC_SIGNATURE_ARR_CENTS and result.get("urgency") == "immediate":
        result["requires_executive_signature"] = True

    # QUALITY FLOOR: a thin draft must get a senior review, not go standard.
    draft = str(result.get("primary_draft", "")).strip()
    if len(draft) < 60:
        result["recipient_role"] = "exec_sponsor"
        logger.warning("[save_play] thin draft — routing to senior review")

    # BOUNDED TOUCH LOOP (Rule 3): count touches; escalate past the cap.
    sid = extract.session_id(result, ctx)
    if customer_id:
        touches = session_stash.bump(sid, f"touch:{customer_id}")
        if touches > _MAX_TOUCHES:
            result["recipient_role"] = "exec_sponsor"
            result["escalation_reason"] = (
                f"{touches} save-play touches with no resolution — escalate "
                f"to a senior CSM (touch cap {_MAX_TOUCHES} exceeded)."
            )
            logger.warning(f"[save_play] touch cap hit ({touches}) for {customer_id} — escalating")
        store.record_play(customer_id, "save",
                          pattern=str(extract.pick(result, "primary_pattern", "")),
                          outcome="drafted")

    # draft mirrors primary_draft — the CSM inbox reads `draft`.
    result["draft"] = draft
    logger.info(
        f"[save_play] {customer_id or '?'} role={result['recipient_role']} "
        f"urgency={result['urgency']} exec_sig={result['requires_executive_signature']}"
    )
    return result


# ── @compose: per-tier payloads ──────────────────────────────────────
def _to_csm(result: dict, ctx: dict) -> dict[str, Any]:
    return {"customer_id": result.get("customer_id", ""), "draft": result.get("draft", "")}


def _to_vp(result: dict, ctx: dict) -> dict[str, Any]:
    return {
        "customer_id": result.get("customer_id", ""),
        "escalation_reason": result.get("escalation_reason", "")
        or "High-ARR save-play needs a VP personal note before send.",
    }


@pre_compose(context_processor=load_play_history)
@compose(csm_human=_to_csm, senior_csm_human=_to_csm, vp_cs_human=_to_vp)
@chain(enforce_exec_signature_and_cap)
async def save_play_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    llm = extract.parse_json(llm_response)
    ctx = extract.prepared_context(context)

    out = {**_DEFAULTS}
    out["primary_draft"] = str(llm.get("primary_draft", ""))
    out["alternative_angles"] = list(llm.get("alternative_angles") or [])
    out["proposed_channel"] = str(llm.get("proposed_channel", "email")) or "email"
    out["urgency"] = str(llm.get("urgency", "this_week")) or "this_week"
    out["recipient_role"] = str(llm.get("recipient_role", "champion")) or "champion"
    out["requires_executive_signature"] = bool(llm.get("requires_executive_signature"))
    out["escalation_reason"] = str(llm.get("escalation_reason", ""))
    out["customer_id"] = str(llm.get("customer_id") or ctx.get("customer_id") or
                             extract.pick(input_data, "customer_id", ""))
    out["arr_cents"] = ctx.get("arr_cents") or extract.pick(input_data, "arr_cents", 0)
    out["draft"] = out["primary_draft"]
    return out
