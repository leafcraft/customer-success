"""customer-success — Customer Success (proactive retention).

Watches the customer base for risk signals BEFORE customers escalate
or churn. A peer-to-peer TEAM of agents (Rule 1) — the Manager hovers
above and intervenes only on narration / escalation / contract
violations.

Deep AND usable. The play matrix lives on risk_detector's conditional
edges; the self-reliant LLM agents (health_score, risk_detector,
save_play, renewals) ship agency/*_agent.py modules that pull their own
context (@pre_compose) from a dev store seeded with realistic accounts
and enforce deterministic floors the LLM can't waive (@chain) — band /
safety floors, the strategic-review gate, exec-signature, the bounded
save-play touch loop, the renewal safeguard + window date gate. Those
modules AUTO-BIND by filename (auto_discover), so nothing here registers
them. The dev store stands in for your CRM / telemetry / intel systems
(behind n8n connectors in prod) so the full chain runs day-0 with NO
connectors set. The only Python registered explicitly here is the
hash-chained WORM audit sink (a custom external agent).

For full architecture: see README.md and PRD.md.
"""
import asyncio
import signal
import sys

from dotenv import load_dotenv
from leafmesh import EventType, LeafMesh, LeafMeshLogger

import agency.tools  # noqa: F401 — registers @global_tool dev-store tools BEFORE start()
from agency._shared import audit_logger as _audit_logger

load_dotenv()
logger = LeafMeshLogger(__name__)

leafmesh = LeafMesh.from_yaml("configs/config.yaml")

# Register the WORM audit sink as the intelligence function for the
# custom external cs_audit_logger_agent (SDK 2.4+ API).
_audit_logger.register(leafmesh, "cs_audit_logger_agent")


# ── Event-bus subscribers — CS audit + ops surface ────────────────
async def _on_agent_error(event):
    logger.warning(
        f"[cs-audit] AGENT_ERROR session={event.session_id} "
        f"data={(event.data or {})!r}"
    )

async def _on_manager_intervention(event):
    logger.warning(
        f"[cs-ops] MANAGER_INTERVENTION session={event.session_id} "
        f"data={(event.data or {})!r}"
    )

async def _on_intervention_needed(event):
    logger.error(
        f"[cs-ops] INTERVENTION_NEEDED session={event.session_id} "
        f"data={(event.data or {})!r}"
    )

async def _on_human_input_timeout(event):
    logger.warning(
        f"[cs-audit] HITL TIMEOUT session={event.session_id} "
        f"data={(event.data or {})!r}"
    )


async def main():
    await leafmesh.start()

    leafmesh.event_bus.subscribe(EventType.AGENT_ERROR,           _on_agent_error)
    leafmesh.event_bus.subscribe(EventType.MANAGER_INTERVENTION,  _on_manager_intervention)
    leafmesh.event_bus.subscribe(EventType.INTERVENTION_NEEDED,   _on_intervention_needed)
    leafmesh.event_bus.subscribe(EventType.HUMAN_INPUT_TIMEOUT,   _on_human_input_timeout)

    try:
        await leafmesh.enable_self_healing()
        logger.info("Self-healing enabled (6 graduated recovery actions)")
    except AttributeError:
        pass

    agents = (
        leafmesh.registry.list_agents()
        if hasattr(leafmesh, "registry") and leafmesh.registry
        else []
    )
    if agents:
        summary = ", ".join(f"{a.name} ({a.agent_type})" for a in agents)
        logger.info(f"Registered agents: {summary}")

    logger.info("customer-success — Customer Success mesh running")
    logger.info("API docs (synthetic testing): http://127.0.0.1:18820/docs")

    # ── Synthetic trigger — telemetry signal ───────────────────────
    # result = await leafmesh.mesh_call(
    #     "product_telemetry",
    #     input_data={
    #         "customer_event": {
    #             "customer_id": "cust-acme-001",
    #             "account_id": "acc-acme",
    #             "signal_source": "telemetry",
    #             "signal_type": "usage_drop",
    #             "arr_cents": 30_000_000,
    #             "segment": "enterprise",
    #             "tier": "named_account",
    #             "raw_payload": {"dau_delta_30d": -0.42},
    #         },
    #     },
    # )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass

    logger.info("Shutting down (draining in-flight CS workflows)...")
    await leafmesh.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
