"""risk_detector_agent — self-reliant risk pattern namer.

@pre_compose  load_risk_context — pull the account's prior plays + its
              last health snapshot from the store so the LLM reasons over
              what's already been tried and the trend, not just the
              inbound verdict. (A second save-play after a no-response is
              a very different call from a first touch.)
@chain        enforce_strategic_review — deterministic floors the play
              matrix on this agent's edges relies on and the LLM cannot
              waive: strategic accounts (ARR >= $250K) with real severity
              ALWAYS require a human before a play fires; exec_turnover
              ALWAYS sets requires_stakeholder_map; pattern names are
              validated against the allowed vocabulary (an invalid name
              routes to hold, never a wrong play).

The pattern string this returns selects the play (the matrix lives on
the YAML can_call edges), so these floors are load-bearing.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import extract, store

logger = LeafMeshLogger(__name__)

# ARR >= $250K — strategic drift always gets a human before a play fires.
STRATEGIC_ARR_CENTS = 25_000_000
STRATEGIC_SEVERITY = 0.3

VALID_PATTERNS = frozenset({
    "usage_decay", "feature_abandonment", "support_drag", "billing_friction",
    "exec_turnover", "silent_disengagement", "product_misfit",
    "competitor_in_account", "approaching_renewal",
})

_DEFAULTS: dict[str, Any] = {
    "patterns": [], "primary_pattern": "", "primary_severity": 0.0,
    "requires_stakeholder_map": False, "requires_human_review": False,
    "customer_id": "", "arr_cents": 0, "segment": "smb", "csm_briefing": "",
}


def _clamp01(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


# ── @pre_compose: what's already been tried + the trend ──────────────
def load_risk_context(data: Any, ctx: Any) -> dict[str, Any]:
    customer_id = str(extract.pick(data, "customer_id", ""))
    return {
        "customer_id": customer_id,
        "prior_plays": store.prior_plays(customer_id) if customer_id else [],
        "last_health": store.get_health_snapshot(customer_id) if customer_id else {},
        "arr_cents": store.get_account(customer_id).get("arr_cents", 0) if customer_id else 0,
    }


# ── @chain: strategic-review + stakeholder + pattern-validation floors ─
async def enforce_strategic_review(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    for k, v in _DEFAULTS.items():
        result.setdefault(k, v)

    # Validate the primary pattern against the vocabulary — an invalid
    # name must NOT fire a play; collapse it so the matrix routes to hold.
    primary = str(result.get("primary_pattern", ""))
    if primary and primary not in VALID_PATTERNS:
        logger.warning(f"[risk] invalid pattern {primary!r} — collapsing to hold")
        primary = ""
        result["primary_severity"] = 0.0
    result["primary_pattern"] = primary
    result["primary_severity"] = _clamp01(result.get("primary_severity"))

    # exec_turnover anywhere → stakeholder map is mandatory.
    pats = result.get("patterns") or []
    names = {str(p.get("pattern", "")) for p in pats if isinstance(p, dict)}
    if primary == "exec_turnover" or "exec_turnover" in names:
        result["requires_stakeholder_map"] = True

    # STRATEGIC FLOOR: ARR >= $250K with real severity → human first.
    try:
        arr = float(result.get("arr_cents", 0) or 0)
    except (TypeError, ValueError):
        arr = 0.0
    if arr >= STRATEGIC_ARR_CENTS and result["primary_severity"] >= STRATEGIC_SEVERITY:
        result["requires_human_review"] = True

    if primary and not str(result.get("csm_briefing", "")).strip():
        result["csm_briefing"] = (
            f"{primary} (severity {result['primary_severity']:.2f}) on a "
            f"${int(arr)//100000/10:g}K-ARR {result.get('segment','')} account; "
            f"{'human review recommended' if result['requires_human_review'] else 'standard play'}."
        )
    logger.info(
        f"[risk] {result.get('customer_id','?')} pattern={primary or '(hold)'} "
        f"sev={result['primary_severity']} human_review={result['requires_human_review']}"
    )
    return result


@pre_compose(context_processor=load_risk_context)
@chain(enforce_strategic_review)
async def risk_detector_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    llm = extract.parse_json(llm_response)
    ctx = extract.prepared_context(context)

    out = {**_DEFAULTS}
    out["patterns"] = list(llm.get("patterns") or [])
    out["primary_pattern"] = str(llm.get("primary_pattern", ""))
    out["primary_severity"] = _clamp01(llm.get("primary_severity"))
    out["requires_stakeholder_map"] = bool(llm.get("requires_stakeholder_map"))
    out["requires_human_review"] = bool(llm.get("requires_human_review"))
    out["csm_briefing"] = str(llm.get("csm_briefing", ""))
    out["customer_id"] = str(llm.get("customer_id") or ctx.get("customer_id") or
                             extract.pick(input_data, "customer_id", ""))
    out["arr_cents"] = llm.get("arr_cents") or ctx.get("arr_cents") or \
        extract.pick(input_data, "arr_cents", 0)
    out["segment"] = str(llm.get("segment") or extract.pick(input_data, "segment", "smb")) or "smb"
    return out
