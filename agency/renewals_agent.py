"""renewals_agent — self-reliant renewals analyst + window monitor.

@pre_compose  load_renewal_context — pull the account's ARR + renewal_date
              from the store (and, on a cron wake with no customer, the
              full set of accounts entering the 30/60/90-day window) so
              the forecast reasons over the real renewal record and the
              window scan reports real accounts — never invented ones.
@chain        enforce_renewal_gates — deterministic floors:
              * STRATEGIC SAFEGUARD: ARR >= $250K may NEVER be auto_renew
                — downgraded to high_touch. Too much downside for
                automation alone.
              * RENEWAL-WINDOW DATE GATE: window-scan runs are forced
                informational/terminal (recommendation=window_scan,
                confidence=1.0, expansion=0) and carry the store's real
                in-window accounts + per-window counts, so no forecast
                edge mis-fires off a sweep.

Forecasting (the judgment) is the LLM's; the safeguards + the date math
are policy enforced here.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import extract, store

logger = LeafMeshLogger(__name__)

# ARR >= $250K — never auto_renew.
STRATEGIC_ARR_CENTS = 25_000_000

VALID_RECS = frozenset({
    "auto_renew", "low_touch", "high_touch", "save", "risk_escalate", "window_scan",
})

_DEFAULTS: dict[str, Any] = {
    "renewal_probability": 0.0, "expansion_mrr_cents": 0, "expansion_products": [],
    "top_factors": [], "confidence": 0.0, "recommendation": "high_touch",
    "customer_id": "", "arr_cents": 0,
    "accounts_entering_window": [], "windows_triggered": {"30d": 0, "60d": 0, "90d": 0},
}


def _clamp01(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _is_cron_scan(data: Any) -> bool:
    """A cron wake delivers a trigger and no customer context."""
    if not isinstance(data, dict):
        return False
    has_customer = bool(extract.pick(data, "customer_id", ""))
    has_trigger = bool(data.get("trigger") or data.get("wake_up") or
                       data.get("scheduled") or data.get("cron"))
    return has_trigger and not has_customer


# ── @pre_compose: real renewal record, or the in-window account set ──
def load_renewal_context(data: Any, ctx: Any) -> dict[str, Any]:
    if _is_cron_scan(data):
        scan = store.accounts_in_renewal_window()
        return {"mode": "window_scan", **scan}
    customer_id = str(extract.pick(data, "customer_id", ""))
    rec = store.renewal_record(customer_id) if customer_id else {}
    return {
        "mode": "forecast",
        "customer_id": customer_id,
        "renewal_record": rec,
        "arr_cents": rec.get("arr_cents", 0),
        "days_to_renewal": store.days_to_renewal(customer_id) if customer_id else None,
        "usage_snapshot": store.usage_signals(customer_id) if customer_id else {},
    }


# ── @chain: strategic safeguard + window-scan date gate ──────────────
async def enforce_renewal_gates(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    for k, v in _DEFAULTS.items():
        result.setdefault(k, v)
    pctx = extract.prepared_context(ctx)

    # WINDOW-SCAN RUNS — informational + terminal; carry the real set.
    if pctx.get("mode") == "window_scan":
        accts = pctx.get("accounts") or []
        result.update({
            "recommendation": "window_scan",
            "confidence": 1.0,
            "expansion_mrr_cents": 0,
            "renewal_probability": 0.0,
            "customer_id": "",
            "arr_cents": 0,
            "accounts_entering_window": accts,
            "windows_triggered": pctx.get("windows_triggered", {"30d": 0, "60d": 0, "90d": 0}),
        })
        logger.info(f"[renewals] window scan — {len(accts)} accounts in-window")
        return result

    # FORECAST RUNS — validate + apply the strategic safeguard.
    result["renewal_probability"] = _clamp01(result.get("renewal_probability"))
    result["confidence"] = _clamp01(result.get("confidence"))
    try:
        result["expansion_mrr_cents"] = max(0, int(result.get("expansion_mrr_cents", 0) or 0))
    except (TypeError, ValueError):
        result["expansion_mrr_cents"] = 0
    if result.get("recommendation") not in VALID_RECS:
        result["recommendation"] = "high_touch"

    try:
        arr = float(result.get("arr_cents", 0) or 0)
    except (TypeError, ValueError):
        arr = 0.0
    if arr >= STRATEGIC_ARR_CENTS and result["recommendation"] == "auto_renew":
        result["recommendation"] = "high_touch"
        logger.info("[renewals] strategic safeguard — auto_renew → high_touch")

    # Forecast runs never carry window fields.
    result["accounts_entering_window"] = []
    result["windows_triggered"] = {"30d": 0, "60d": 0, "90d": 0}
    logger.info(
        f"[renewals] {result.get('customer_id','?')} prob={result['renewal_probability']} "
        f"rec={result['recommendation']} conf={result['confidence']}"
    )
    return result


@pre_compose(context_processor=load_renewal_context)
@chain(enforce_renewal_gates)
async def renewals_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    llm = extract.parse_json(llm_response)
    ctx = extract.prepared_context(context)

    out = {**_DEFAULTS}
    out["renewal_probability"] = _clamp01(llm.get("renewal_probability"))
    out["expansion_mrr_cents"] = llm.get("expansion_mrr_cents", 0)
    out["expansion_products"] = list(llm.get("expansion_products") or [])
    out["top_factors"] = list(llm.get("top_factors") or [])
    out["confidence"] = _clamp01(llm.get("confidence"))
    out["recommendation"] = str(llm.get("recommendation", "high_touch")) or "high_touch"
    out["customer_id"] = str(llm.get("customer_id") or ctx.get("customer_id") or
                             extract.pick(input_data, "customer_id", ""))
    out["arr_cents"] = llm.get("arr_cents") or ctx.get("arr_cents") or \
        extract.pick(input_data, "arr_cents", 0)
    return out
