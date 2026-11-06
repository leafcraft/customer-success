"""account_sweep_agent — the proactive base-wide sweep, FANNED OUT over parallel copies.

`scaling.copies.count: 3` in the configuration → one daily wake runs this module 3× in parallel
in the SAME session. Each copy atomically CLAIMS a disjoint batch of account ids
from the shared backlog (store.claim_account_batch, thread-locked, keyed by the
session id so the three copies split one run and the next day's wake re-seeds a
fresh backlog) and flags the at-risk ones with the DETERMINISTIC health baseline
(health_math — no LLM needed for a counting sweep). Under
`scaling.copies.handoff: last` the numeric yields SUM and the list yields CONCAT into
the whole-base digest, routed once to the WORM audit sink.

The atomic claim is ALSO exposed as the `claim_account_batch` @global_tool
(agency/tools.py) for an LLM sweep variant; this module calls the same store
primitive directly. FAILS CLOSED: an exhausted backlog yields available:false →
0 claimed, never a fabricated sweep (doctrine A5).

auto_discover binds this module's `account_sweep_agent` function to the YAML
agent of the same name.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import extract, health_math, store

logger = LeafMeshLogger(__name__)

# Per-copy claim size. Batch >= base means one copy drains the seed base and
# the others honestly report an empty claim — the disjoint-split mechanic; raise
# it (or shrink batches) against a real multi-thousand-account base.
_BATCH = int(os.getenv("CS_SWEEP_BATCH", "5") or "5")
# Health baseline below this flags the account for the daily digest.
_AT_RISK_BELOW = int(os.getenv("CS_SWEEP_AT_RISK_BELOW", "60") or "60")

_DEFAULTS: dict[str, Any] = {
    "accounts_claimed": 0,
    "accounts_flagged": 0,
    "flagged_accounts": [],
    "window_start_utc": "",
    "source": "dev_store",
}


def _evaluate(customer_id: str) -> dict[str, Any]:
    """Deterministic at-risk check for one account (health baseline + band)."""
    window = store.signal_window(customer_id, days=30)
    usage = store.usage_signals(customer_id)
    base = health_math.signals_to_score(window, usage)
    score = base.get("health_score", 100)
    band = health_math.band_for(float(score))
    return {"customer_id": customer_id, "health_score": score,
            "risk_band": band, "at_risk": score < _AT_RISK_BELOW}


async def account_sweep_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    """Claim this copy's disjoint batch and flag its at-risk accounts.
    Deterministic — the model output (if any) is ignored; the sweep is code."""
    # The session id is the run generation: all three copies of one wake share
    # it, so store.claim_account_batch drains ONE backlog across them.
    gen = extract.session_id(input_data, context) or "sweep"
    batch = store.claim_account_batch(_BATCH, gen=gen)
    claimed = list(batch.get("claimed", [])) if batch.get("available") else []

    flagged = [ev for ev in (_evaluate(cid) for cid in claimed) if ev["at_risk"]]

    out = {
        **_DEFAULTS,
        "accounts_claimed": len(claimed),
        "accounts_flagged": len(flagged),
        "flagged_accounts": flagged,
        "window_start_utc": datetime.now(timezone.utc).isoformat(),
        "source": "dev_store",
    }
    logger.info(
        f"[account_sweep] gen={gen} claimed={len(claimed)} flagged={len(flagged)} "
        f"remaining={batch.get('remaining', 0)}"
    )
    return out
