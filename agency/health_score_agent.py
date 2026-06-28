"""health_score_agent — self-reliant health scorer.

@pre_compose  load_account_context — pull the rolling 30-day signal
              window + the usage/adoption snapshot from the dev store
              (PROD: Gainsight / Pendo) so the LLM scores over REAL
              history, not a single inbound signal. Also computes a
              deterministic baseline (health_math) the LLM refines.
@chain        enforce_band_floors — deterministic policy the LLM cannot
              waive: clamp ranges, derive risk_band from the score, the
              low-confidence safety floor, and the indefensible-score
              quality floor. Persists the snapshot for trend reads.

auto_discover binds this module's `health_score_agent` function to the
YAML agent of the same name; the YAML prompt/yields are the contract.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import extract, health_math, store

logger = LeafMeshLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "health_score": 0, "score_delta_30d": 0, "risk_band": "watch",
    "contributing_factors": [], "confidence": 0.0,
    "customer_id": "", "arr_cents": 0, "segment": "smb",
    "executive_summary": "", "top_3_contributors": [],
    "what_changed_30d": [], "if_we_did_nothing": "", "quotable_one_liner": "",
}


# ── @pre_compose: pull this account's real history before the LLM runs ──
def load_account_context(data: Any, ctx: Any) -> dict[str, Any]:
    customer_id = str(extract.pick(data, "customer_id", ""))
    window = store.signal_window(customer_id, days=30) if customer_id else []
    usage = store.usage_signals(customer_id) if customer_id else {}
    acct = store.get_account(customer_id) if customer_id else {}
    baseline = health_math.signals_to_score(window, usage)
    return {
        "customer_id": customer_id,
        "signal_window_30d": window,
        "signal_count": len(window),
        "usage_snapshot": usage,
        "arr_cents": acct.get("arr_cents", 0),
        "segment": acct.get("segment", ""),
        "baseline_health": baseline,   # deterministic floor the LLM refines
    }


# ── @chain: enforce the band / safety / quality floors deterministically ─
async def enforce_band_floors(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    for k, v in _DEFAULTS.items():
        result.setdefault(k, v)
    has_explanation = bool(str(result.get("executive_summary", "")).strip()) and bool(
        result.get("top_3_contributors")
    )
    floored = health_math.enforce_floor(
        result.get("health_score"), result.get("score_delta_30d"),
        result.get("confidence"), has_explanation,
    )
    result.update(floored)
    result["top_3_contributors"] = (result.get("top_3_contributors") or [])[:3]

    if result.get("customer_id"):
        store.set_health_snapshot(
            result["customer_id"], result["health_score"],
            result["risk_band"], result["score_delta_30d"],
        )
    logger.info(
        f"[health] {result.get('customer_id','?')} score={result['health_score']} "
        f"band={result['risk_band']} conf={result['confidence']}"
    )
    return result


@pre_compose(context_processor=load_account_context)
@chain(enforce_band_floors)
async def health_score_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    llm = extract.parse_json(llm_response)
    ctx = extract.prepared_context(context)
    baseline = ctx.get("baseline_health", {}) if isinstance(ctx, dict) else {}

    out = {**_DEFAULTS}
    # Identity passthrough — downstream tier routing gates on these.
    out["customer_id"] = str(llm.get("customer_id") or ctx.get("customer_id") or
                             extract.pick(input_data, "customer_id", ""))
    out["arr_cents"] = llm.get("arr_cents") or ctx.get("arr_cents") or \
        extract.pick(input_data, "arr_cents", 0)
    out["segment"] = str(llm.get("segment") or ctx.get("segment") or
                         extract.pick(input_data, "segment", "smb")) or "smb"

    # LLM verdict, falling back to the deterministic baseline.
    out["health_score"] = llm.get("health_score", baseline.get("health_score", 0))
    out["score_delta_30d"] = llm.get("score_delta_30d", baseline.get("score_delta_30d", 0))
    out["confidence"] = llm.get("confidence", baseline.get("confidence", 0.3))
    out["contributing_factors"] = list(llm.get("contributing_factors") or [])
    out["executive_summary"] = str(llm.get("executive_summary", ""))
    out["top_3_contributors"] = list(llm.get("top_3_contributors") or [])
    out["what_changed_30d"] = list(llm.get("what_changed_30d") or [])
    out["if_we_did_nothing"] = str(llm.get("if_we_did_nothing", ""))
    out["quotable_one_liner"] = str(llm.get("quotable_one_liner", ""))
    # risk_band is derived in the @chain — never trust the LLM's band.
    return out
