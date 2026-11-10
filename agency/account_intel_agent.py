"""account_intel_agent — self-reliant, mode-dispatched account analyst.

One role, two responsibilities (expansion scanning + stakeholder
alignment); the input decides which. The expansion floors + the
two-signal stakeholder rule are deterministic PROMPT rules (see the
agent's prompt in the configuration) — by design they are not re-enforced
here. What this module adds is the missing hand:

@pre_compose  load_intel_context — pull the RIGHT slice of the account
              from the store for the mode this request is in, so the
              analyst reasons over the real account record rather than
              whatever the inbound event happened to carry:
                * expansion  → seat/API utilisation + premium features
                               touched (store.expansion_signals)
                * stakeholder → the current stakeholder map: who's in
                               role, who departed, who holds buying
                               authority (store.stakeholder_map)
              The mode is resolved with the SAME precedence the prompt
              uses (explicit mode > requires_stakeholder_map flag >
              exec-change hints > expansion), so the pull always lines
              up with the responsibility the LLM will run.

There is no @chain: this agent's floors are intentionally prompt rules
(the 2.3.x consolidation note in the configuration). This module is a pure
context-pull, matching renewals/risk_detector's @pre_compose style.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, pre_compose

from agency._shared import extract, store

logger = LeafMeshLogger(__name__)

# Exec-change hints (lowercased substring match) that flip a request to
# stakeholder mode — mirrors the prompt's MODE SELECTION step 3.
_EXEC_HINTS = ("champion", "exec", "stakeholder", "exec_change")


def resolve_intel_mode(data: Any) -> str:
    """Decide expansion vs stakeholder with the prompt's exact precedence:
      1. explicit mode / intel_mode field anywhere in the input
      2. an upstream requires_stakeholder_map=true flag
      3. exec-change hints in signal_source / signal_type / event_type
      4. otherwise expansion
    Standalone + pure so the pull can be asserted in isolation."""
    explicit = str(extract.pick(data, "intel_mode", "") or extract.pick(data, "mode", "")).lower()
    if explicit in ("expansion", "stakeholder"):
        return explicit

    flag = extract.pick(data, "requires_stakeholder_map", False)
    if flag in (True, "true", "True", 1):
        return "stakeholder"

    for field in ("signal_source", "signal_type", "event_type", "signal"):
        val = str(extract.pick(data, field, "")).lower()
        if any(h in val for h in _EXEC_HINTS):
            return "stakeholder"

    return "expansion"


# ── @pre_compose: pull the slice the resolved mode reasons over ──────
def load_intel_context(data: Any, ctx: Any) -> dict[str, Any]:
    customer_id = str(extract.pick(data, "customer_id", ""))
    mode = resolve_intel_mode(data)

    out: dict[str, Any] = {
        "intel_mode": mode,
        "customer_id": customer_id,
        "account_known": bool(customer_id and store.get_account(customer_id)),
    }
    if mode == "stakeholder":
        out["stakeholder_map"] = store.stakeholder_map(customer_id) if customer_id else []
        # the rolling signals are the corroboration the two-signal rule
        # weighs (CRM update + LinkedIn + email bounce live here).
        out["recent_signals"] = store.signal_window(customer_id) if customer_id else []
    else:
        out["expansion_signals"] = store.expansion_signals(customer_id) if customer_id else {}

    logger.info(
        f"[account_intel] mode={mode} customer={customer_id or '?'} "
        f"known={out['account_known']}"
    )
    return out


@pre_compose(context_processor=load_intel_context)
async def account_intel_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    """Shape the LLM's JSON into the declared yields. The expansion floor
    and the two-signal stakeholder rule are enforced in the prompt; here
    we only parse, default, and echo customer_id/intel_mode through."""
    llm = extract.parse_json(llm_response)
    ctx = extract.prepared_context(context)

    intel_mode = str(llm.get("intel_mode") or ctx.get("intel_mode") or "")
    customer_id = str(llm.get("customer_id") or ctx.get("customer_id") or
                      extract.pick(input_data, "customer_id", ""))

    return {
        "intel_mode": intel_mode,
        "customer_id": customer_id,
        # expansion responsibility
        "expansion_score": llm.get("expansion_score", 0),
        "upsell_signals": list(llm.get("upsell_signals") or []),
        "cross_sell_signals": list(llm.get("cross_sell_signals") or []),
        "ae_action_required": bool(llm.get("ae_action_required", False)),
        "expansion_briefing": str(llm.get("expansion_briefing", "")),
        # stakeholder responsibility
        "change_confirmed": bool(llm.get("change_confirmed", False)),
        "former_champion": str(llm.get("former_champion", "")),
        "new_champion_candidate": str(llm.get("new_champion_candidate", "")),
        "authority_gap": bool(llm.get("authority_gap", False)),
        "recommendation": str(llm.get("recommendation", "")),
    }
