"""Global dev-store tools for the Customer Success pod.

These are the on-demand tool surface the LLM specialists (health_score,
risk_detector, account_intel, save_play, renewals) reach for during a turn —
deep reads BEYOND the deterministic baseline their `@pre_compose` already pulls
— plus the atomic backlog claim the fanned `account_sweep_agent` uses to split
the base across its parallel copies.

Every read **announces itself**: when the account is unknown or no real source
is wired it returns `available: false` and says why — NEVER a synthetic success
that looks like a real read (doctrine A4/A5). Each reads the dev store so the
pod runs day-0 with no connector; to go live, wire the CRM / CS-platform MCP
block in the configuration (or swap the store bodies) — these are the
fallback contract, and the function signatures are what you swap into.

"Is a real source wired?" is answered from the vault, never from .env: a
source counts as wired when its vault record exists (crm_mcp,
cs_operations_n8n or customer_onboarding_n8n — the records the real
connectors in the configuration use).

main.py imports this module BEFORE from_store so the @global_tool decorators
register the tools ahead of start().
"""
from __future__ import annotations

import os

from leafmesh import LeafMeshLogger, SecretNotAllowed, connection_rows, global_tool
from leafmesh.vault.refs import UnresolvedVaultRef

try:
    from ._shared import store
except ImportError:  # pragma: no cover — direct-run fallback
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _shared import store  # type: ignore

logger = LeafMeshLogger(__name__)


# The vault records the real CRM / CS-platform connectors use. A record that
# exists = that source is wired (Infrastructure → Integrations → MCP / n8n).
_SOURCE_RECORDS = ("crm_mcp", "cs_operations_n8n", "customer_onboarding_n8n")


async def _record_exists(name: str) -> bool:
    try:
        await connection_rows(name)
        return True
    except UnresolvedVaultRef:
        return False
    except SecretNotAllowed:
        return True    # the record exists; it just limits which agents read it


async def _connector_wired() -> bool:
    """True if a real CS-platform / CRM source has its vault record."""
    for name in _SOURCE_RECORDS:
        if await _record_exists(name):
            return True
    return False


@global_tool(category="cs_context")
async def get_account_detail(customer_id: str) -> dict:
    """Read one account's full record — segment, tier, ARR, the stakeholder
    map, the usage/adoption snapshot, and the renewal date.

    Returns `available: false` with a reason when the account is unknown (a new
    account, or a connector path with nothing for this id) — never an empty
    record dressed as real.

    Args:
        customer_id: The account to read (e.g. "cust-acme-001").
    """
    cid = str(customer_id or "").strip()
    acct = store.get_account(cid) if cid else {}
    if not acct:
        reason = (
            "connector wired but returned nothing for this account"
            if await _connector_wired()
            else (f"no seed account for {cid!r}; no CRM / CS-platform source is wired "
                  f"(vault records: {', '.join(_SOURCE_RECORDS)})")
        )
        return {"available": False, "customer_id": cid, "reason": reason}
    return {
        "available": True,
        "customer_id": cid,
        "segment": acct.get("segment", ""),
        "tier": acct.get("tier", ""),
        "arr_cents": acct.get("arr_cents", 0),
        "renewal_date": acct.get("renewal_date", ""),
        "usage": acct.get("usage", {}),
        "stakeholders": acct.get("stakeholders", []),
        "signals": acct.get("signals", []),
    }


@global_tool(category="cs_context")
def list_executed_plays(customer_id: str) -> dict:
    """List the plays already EXECUTED against an account (save-messages sent,
    QBRs booked) — the actuator ledger, so a draft knows what's been tried.

    Returns `available: false` when the account is unknown; an empty list with
    `available: true` is a real "we looked, nothing yet" (distinct from
    could-not-tell) — doctrine A5.

    Args:
        customer_id: The account whose executed-play history to read.
    """
    cid = str(customer_id or "").strip()
    if not cid or not store.get_account(cid):
        return {"available": False, "customer_id": cid,
                "reason": f"unknown account {cid!r} — nothing to read"}
    plays = store.executed_plays(cid)
    return {"available": True, "customer_id": cid,
            "executed_plays": plays, "count": len(plays)}


@global_tool(category="backlog")
def claim_account_batch(count: int = 5) -> dict:
    """Atomically claim up to `count` accounts from the shared sweep backlog so
    parallel `account_sweep_agent` copies pull DISJOINT batches (LPOP-style,
    thread-locked) instead of all scanning the same base.

    Returns `available: false` when the run's backlog is exhausted — the honest
    "nothing left to claim", never a fabricated batch. The programmatic sweep
    module calls the same store primitive directly (passing the session id as
    the run generation); this tool is the same claim exposed for an LLM sweep
    variant.

    Args:
        count: Maximum number of accounts to claim in this batch.
    """
    result = store.claim_account_batch(count)
    logger.info(f"claim_account_batch count={count} available={result.get('available')} "
                f"claimed={len(result.get('claimed', []))} remaining={result.get('remaining', 0)}")
    return result
