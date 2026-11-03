"""Dev-mode store for the Customer Success mesh.

Stands in for the CS data platform — health-score history (the rolling
30-day signal window), product/usage adoption signals, prior save-plays,
and renewal/ARR records — so the whole chain (health → risk → play →
renewal) runs end-to-end BEFORE any connector is wired.

In production these reads/writes are the customer's own systems, reached
through the n8n connector agents in config.yaml:
  * health history + signals  → Gainsight / Pendo / Amplitude / Segment
  * renewal / ARR records     → Salesforce / your CRM
  * prior plays               → your CS playbook ledger / Gainsight CTAs
Until those connectors are wired (their vault records exist and the
workflows return JSON), this module is the day-0 stand-in. The function
signatures are the contract — swap the body, keep the shape.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DIR = Path("./data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Short opaque id for a recorded artifact (sent message / booked meeting)."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _days_from_now(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ─────────────────────────────────────────────────────────────────────
# SEED — realistic accounts with the data the agents reason over.
#   * arr_cents drives every tier/strategic gate
#   * signals = the rolling window health_score aggregates over
#   * usage   = the adoption signals account_intel / renewals read
#   * stakeholders = the map account_intel(stakeholder) rebuilds
#   * renewal_date drives the renewal-window date gate
# ─────────────────────────────────────────────────────────────────────
_SEED_ACCOUNTS: list[dict[str, Any]] = [
    {
        "customer_id": "cust-acme-001",
        "account_id": "acc-acme",
        "name": "ACME Corp",
        "segment": "enterprise",
        "tier": "named_account",
        "arr_cents": 30_000_000,          # $300K — strategic
        "renewal_date": _days_from_now(58),
        "csm": "maya",
        # rolling signal window — newest first
        "signals": [
            {"at": _days_ago_iso(2),  "source": "telemetry", "type": "usage_drop",
             "value": -0.42, "note": "DAU down 42% over 30d after the March API change"},
            {"at": _days_ago_iso(9),  "source": "support",   "type": "ticket_spike",
             "value": 6,     "note": "6 tickets in 7 days — integration breakage"},
            {"at": _days_ago_iso(15), "source": "telemetry", "type": "feature_drop",
             "value": -0.30, "note": "core workflow usage down 30% wow"},
            {"at": _days_ago_iso(22), "source": "billing",   "type": "on_time_pay",
             "value": 1,     "note": "paid on time"},
        ],
        "usage": {
            "dau_7d_avg": 41, "dau_28d_avg": 70, "dau_delta_pct": -0.41,
            "feature_breadth": 4, "longest_quiet_run_days": 3,
            "seat_utilisation": 0.62, "api_utilisation": 0.48,
            "premium_features_touched": [], "nps": 6, "csat": 3.4,
        },
        # stakeholder map — who holds the relationship + buying authority.
        # account_intel(stakeholder) rebuilds this on a champion-change signal.
        "stakeholders": [
            {"name": "Sarah Lin", "title": "VP Engineering", "role": "champion",
             "status": "in_role", "buying_authority": True, "last_verified": _days_ago_iso(20)},
            {"name": "Tom Reyes", "title": "Director Platform", "role": "user_lead",
             "status": "in_role", "buying_authority": False, "last_verified": _days_ago_iso(20)},
        ],
        "prior_plays": [
            {"play": "qbr", "at": _days_ago_iso(95), "outcome": "attended", "pattern": "feature_abandonment"},
        ],
    },
    {
        "customer_id": "cust-globex-002",
        "account_id": "acc-globex",
        "name": "Globex",
        "segment": "mid_market",
        "tier": "standard",
        "arr_cents": 4_200_000,           # $42K — standard
        "renewal_date": _days_from_now(142),
        "csm": "ravi",
        "signals": [
            {"at": _days_ago_iso(1),  "source": "telemetry", "type": "usage_drop",
             "value": -0.18, "note": "mild engagement dip"},
            {"at": _days_ago_iso(11), "source": "support",   "type": "csat_drop",
             "value": -0.5,  "note": "CSAT down half a point QoQ"},
            {"at": _days_ago_iso(20), "source": "billing",   "type": "on_time_pay",
             "value": 1,     "note": "paid on time"},
            {"at": _days_ago_iso(27), "source": "telemetry", "type": "feature_use",
             "value": 0.10,  "note": "tried a new feature"},
        ],
        "usage": {
            "dau_7d_avg": 58, "dau_28d_avg": 64, "dau_delta_pct": -0.09,
            "feature_breadth": 7, "longest_quiet_run_days": 2,
            "seat_utilisation": 0.88, "api_utilisation": 0.55,
            "premium_features_touched": ["sso"], "nps": 8, "csat": 4.1,
        },
        "stakeholders": [
            {"name": "Dana Okoro", "title": "Head of Ops", "role": "champion",
             "status": "in_role", "buying_authority": True, "last_verified": _days_ago_iso(35)},
        ],
        "prior_plays": [],
    },
    {
        "customer_id": "cust-initech-003",
        "account_id": "acc-initech",
        "name": "Initech",
        "segment": "enterprise",
        "tier": "named_account",
        "arr_cents": 60_000_000,          # $600K — exec-signature territory
        "renewal_date": _days_from_now(24),
        "csm": "maya",
        "signals": [
            {"at": _days_ago_iso(3),  "source": "exec_change", "type": "champion_left",
             "value": 1,     "note": "CRM: VP Eng (our champion) left; LinkedIn confirms"},
            {"at": _days_ago_iso(4),  "source": "support",     "type": "email_bounce",
             "value": 1,     "note": "champion email bounced"},
            {"at": _days_ago_iso(18), "source": "telemetry",   "type": "usage_flat",
             "value": 0.0,   "note": "usage flat, healthy"},
        ],
        "usage": {
            "dau_7d_avg": 210, "dau_28d_avg": 205, "dau_delta_pct": 0.02,
            "feature_breadth": 11, "longest_quiet_run_days": 1,
            "seat_utilisation": 0.91, "api_utilisation": 0.83,
            "premium_features_touched": ["sso", "audit_log", "priority_support"],
            "nps": 9, "csat": 4.6,
        },
        # champion just left (see signals: champion_left + email_bounce) and
        # no successor named — stakeholder mode should surface an authority gap.
        "stakeholders": [
            {"name": "Priya Nadar", "title": "VP Engineering", "role": "champion",
             "status": "departed", "buying_authority": True, "last_verified": _days_ago_iso(40),
             "departure_signal": "crm_update+linkedin"},
            {"name": "Carl Voss", "title": "Eng Manager", "role": "user_lead",
             "status": "in_role", "buying_authority": False, "last_verified": _days_ago_iso(5)},
        ],
        "prior_plays": [
            {"play": "save", "at": _days_ago_iso(40), "outcome": "responded", "pattern": "billing_friction"},
            {"play": "save", "at": _days_ago_iso(12), "outcome": "no_response", "pattern": "exec_turnover"},
        ],
    },
    {
        "customer_id": "cust-hooli-004",
        "account_id": "acc-hooli",
        "name": "Hooli",
        "segment": "smb",
        "tier": "tech_touch",
        "arr_cents": 1_800_000,           # $18K — below the usage-decay save floor
        "renewal_date": _days_from_now(88),
        "csm": "auto",
        "signals": [
            {"at": _days_ago_iso(2),  "source": "telemetry", "type": "usage_drop",
             "value": -0.65, "note": "deep usage decay — went quiet"},
            {"at": _days_ago_iso(30), "source": "telemetry", "type": "usage_drop",
             "value": -0.40, "note": "decline began a month ago"},
        ],
        "usage": {
            "dau_7d_avg": 3, "dau_28d_avg": 12, "dau_delta_pct": -0.75,
            "feature_breadth": 2, "longest_quiet_run_days": 9,
            "seat_utilisation": 0.30, "api_utilisation": 0.10,
            "premium_features_touched": [], "nps": 5, "csat": 3.0,
        },
        "stakeholders": [],   # tech-touch — no mapped relationship
        "prior_plays": [],
    },
    # ── The four accounts the examples/ payloads drive (same ids + ARR) ──
    {
        # examples/01 — DAU down ~35% week-over-week; two features abandoned.
        "customer_id": "cust-bytewing-021",
        "account_id": "acc-bytewing",
        "name": "Bytewing Labs",
        "segment": "mid_market",
        "tier": "standard",
        "arr_cents": 8_400_000,           # $84K — above the $50K usage-decay save floor
        "renewal_date": _days_from_now(131),
        "csm": "ravi",
        "signals": [
            {"at": _days_ago_iso(1),  "source": "telemetry", "type": "usage_drop",
             "value": -0.35, "note": "DAU 142 vs 218 baseline (-35% wow)"},
            {"at": _days_ago_iso(6),  "source": "telemetry", "type": "feature_drop",
             "value": -0.50, "note": "analytics_dashboard + api_explorer unused for 6 days"},
            {"at": _days_ago_iso(19), "source": "billing",   "type": "on_time_pay",
             "value": 1,     "note": "Q3 invoice paid on time"},
        ],
        "usage": {
            "dau_7d_avg": 142, "dau_28d_avg": 218, "dau_delta_pct": -0.35,
            "feature_breadth": 5, "longest_quiet_run_days": 4,
            "seat_utilisation": 0.58, "api_utilisation": 0.22,
            "premium_features_touched": [], "nps": 6, "csat": 3.8,
        },
        "stakeholders": [
            {"name": "Elena Varga", "title": "Head of Data", "role": "champion",
             "status": "in_role", "buying_authority": True, "last_verified": _days_ago_iso(28)},
        ],
        "prior_plays": [],
    },
    {
        # examples/02 — renewal charge failed twice (card declined).
        "customer_id": "cust-orbital-systems-008",
        "account_id": "acc-orbital",
        "name": "Orbital Systems",
        "segment": "enterprise",
        "tier": "named_account",
        "arr_cents": 14_200_000,          # $142K; monthly invoice $11,833.33
        "renewal_date": _days_from_now(12),
        "csm": "maya",
        "signals": [
            {"at": _days_ago_iso(1),  "source": "billing",   "type": "failed_payment",
             "value": -1,    "note": "renewal charge $11,833.33 declined (retry 2 of 3)"},
            {"at": _days_ago_iso(4),  "source": "billing",   "type": "failed_payment",
             "value": -1,    "note": "renewal charge $11,833.33 declined (retry 1 of 3)"},
            {"at": _days_ago_iso(16), "source": "telemetry", "type": "usage_flat",
             "value": 0.0,   "note": "usage steady"},
        ],
        "usage": {
            "dau_7d_avg": 96, "dau_28d_avg": 99, "dau_delta_pct": -0.03,
            "feature_breadth": 8, "longest_quiet_run_days": 1,
            "seat_utilisation": 0.79, "api_utilisation": 0.61,
            "premium_features_touched": ["sso"], "nps": 7, "csat": 4.2,
        },
        "stakeholders": [
            {"name": "Marcus Oyelaran", "title": "Director of IT", "role": "champion",
             "status": "in_role", "buying_authority": True, "last_verified": _days_ago_iso(9)},
            {"name": "Hana Ito", "title": "AP Manager", "role": "billing_contact",
             "status": "in_role", "buying_authority": False, "last_verified": _days_ago_iso(9)},
        ],
        "prior_plays": [],
    },
    {
        # examples/03 — 60 days from renewal; 122 of 145 seats active.
        "customer_id": "cust-northgate-analytics-003",
        "account_id": "acc-northgate",
        "name": "Northgate Analytics",
        "segment": "enterprise",
        "tier": "named_account",
        "arr_cents": 32_500_000,          # $325K — strategic: never auto_renew
        "renewal_date": _days_from_now(60),
        "csm": "maya",
        "signals": [
            {"at": _days_ago_iso(5),  "source": "support",   "type": "ticket_volume",
             "value": 2,     "note": "2 tickets this month (8 in 90 days)"},
            {"at": _days_ago_iso(14), "source": "telemetry", "type": "usage_flat",
             "value": 0.0,   "note": "122 of 145 seats active in 30 days"},
            {"at": _days_ago_iso(25), "source": "survey",    "type": "nps",
             "value": 7,     "note": "NPS 7 from the champion"},
        ],
        "usage": {
            "dau_7d_avg": 118, "dau_28d_avg": 121, "dau_delta_pct": -0.02,
            "feature_breadth": 10, "longest_quiet_run_days": 1,
            "seat_utilisation": 0.84, "api_utilisation": 0.70,
            "premium_features_touched": ["audit_log"], "nps": 7, "csat": 4.3,
        },
        "stakeholders": [
            {"name": "Lia Chen", "title": "VP Analytics", "role": "champion",
             "status": "in_role", "buying_authority": True, "last_verified": _days_ago_iso(7)},
            {"name": "Owen Brandt", "title": "Procurement Lead", "role": "procurement",
             "status": "in_role", "buying_authority": False, "last_verified": _days_ago_iso(30)},
        ],
        "prior_plays": [
            {"play": "qbr", "at": _days_ago_iso(88), "outcome": "attended", "pattern": ""},
        ],
    },
    {
        # examples/04 — champion (VP Operations) left; a director is the new contact.
        "customer_id": "cust-pacific-shipping-014",
        "account_id": "acc-pacship",
        "name": "Pacific Coast Shipping",
        "segment": "smb",
        "tier": "standard",
        "arr_cents": 5_600_000,           # $56K
        "renewal_date": _days_from_now(203),
        "csm": "ravi",
        "signals": [
            {"at": _days_ago_iso(2),  "source": "exec_change", "type": "champion_left",
             "value": 1,     "note": "CRM: VP Operations J. Morales left the company"},
            {"at": _days_ago_iso(3),  "source": "support",     "type": "email_bounce",
             "value": 1,     "note": "champion email bounced"},
        ],
        "usage": {
            "dau_7d_avg": 34, "dau_28d_avg": 37, "dau_delta_pct": -0.08,
            "feature_breadth": 6, "longest_quiet_run_days": 2,
            "seat_utilisation": 0.71, "api_utilisation": 0.15,
            "premium_features_touched": [], "nps": 8, "csat": 4.0,
        },
        "stakeholders": [
            {"name": "Javier Morales", "title": "VP Operations", "role": "champion",
             "status": "departed", "buying_authority": True, "last_verified": _days_ago_iso(2),
             "departure_signal": "crm_update+email_bounce"},
            {"name": "Kiran Singh", "title": "Director Logistics", "role": "user_lead",
             "status": "in_role", "buying_authority": False, "last_verified": _days_ago_iso(2)},
        ],
        "prior_plays": [],
    },
]

# Index by customer_id for O(1) reads.
_SEED_BY_ID = {a["customer_id"]: a for a in _SEED_ACCOUNTS}


# ── persistence helpers (dev only; prod = the customer's systems) ─────
def _path(k: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / f"{k}.json"


def _load(k: str) -> dict[str, Any]:
    p = _path(k)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}


def _save(k: str, d: dict[str, Any]) -> None:
    p = _path(k)
    t = p.with_suffix(".json.tmp")
    t.write_text(json.dumps(d, indent=2, default=str))
    t.replace(p)


def _accounts() -> dict[str, Any]:
    """Seed-on-first-read; subsequent reads pick up any persisted plays."""
    s = _load("accounts")
    if not s:
        s = {cid: dict(a) for cid, a in _SEED_BY_ID.items()}
        _save("accounts", s)
    return s


# ─────────────────────────────────────────────────────────────────────
# READS — what the @pre_compose context-pulls call.
# ─────────────────────────────────────────────────────────────────────
def get_account(customer_id: str) -> dict[str, Any]:
    """Full account record, or {} when unknown (connector path / new acct)."""
    if not customer_id:
        return {}
    return dict(_accounts().get(customer_id, {}))


def signal_window(customer_id: str, days: int = 30) -> list[dict[str, Any]]:
    """The rolling N-day signal window health_score aggregates over.
    PROD: query your CS data platform for this account's recent events."""
    acct = get_account(customer_id)
    sigs = acct.get("signals") or []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for s in sigs:
        try:
            at = datetime.fromisoformat(str(s.get("at")))
        except (ValueError, TypeError):
            out.append(s)
            continue
        if at >= cutoff:
            out.append(s)
    return out


def usage_signals(customer_id: str) -> dict[str, Any]:
    """Adoption / utilisation snapshot (DAU, seat/API util, NPS, CSAT)."""
    return dict(get_account(customer_id).get("usage", {}))


def prior_plays(customer_id: str) -> list[dict[str, Any]]:
    """Plays already run against this account (the save-play history)."""
    return list(get_account(customer_id).get("prior_plays", []))


def stakeholder_map(customer_id: str) -> list[dict[str, Any]]:
    """The account's current stakeholder map — name/title/role, whether
    they're still in-role and who holds buying authority. What
    account_intel(stakeholder) reasons over to confirm a champion change
    and find the authority gap. PROD: your CRM contact roles."""
    return list(get_account(customer_id).get("stakeholders", []))


def expansion_signals(customer_id: str) -> dict[str, Any]:
    """Adoption signals an upsell rides on — seat/API utilisation and the
    premium-tier features already being touched — lifted out of the usage
    snapshot so account_intel(expansion) reasons over the real numbers,
    not whatever the inbound event happened to carry. PROD: your
    usage/billing platform (Pendo/Amplitude + the entitlement system)."""
    u = usage_signals(customer_id)
    if not u:
        return {}
    return {
        "seat_utilisation": u.get("seat_utilisation", 0.0),
        "api_utilisation": u.get("api_utilisation", 0.0),
        "premium_features_touched": list(u.get("premium_features_touched", [])),
        "feature_breadth": u.get("feature_breadth", 0),
        "nps": u.get("nps", 0),
    }


def renewal_record(customer_id: str) -> dict[str, Any]:
    """ARR + renewal_date — what the renewal-window date gate reads."""
    a = get_account(customer_id)
    if not a:
        return {}
    return {
        "customer_id": customer_id,
        "arr_cents": a.get("arr_cents", 0),
        "segment": a.get("segment", ""),
        "tier": a.get("tier", ""),
        "renewal_date": a.get("renewal_date", ""),
    }


def days_to_renewal(customer_id: str) -> int | None:
    """Whole days until renewal_date, or None if unknown. Negative = past."""
    rec = renewal_record(customer_id)
    rd = rec.get("renewal_date")
    if not rd:
        return None
    try:
        d = datetime.fromisoformat(str(rd)).date()
    except (ValueError, TypeError):
        return None
    return (d - datetime.now(timezone.utc).date()).days


def accounts_in_renewal_window(windows: tuple[int, ...] = (30, 60, 90)) -> dict[str, Any]:
    """Daily-cron helper: every seeded account entering a renewal window,
    counted in the SMALLEST window it fits. PROD: Salesforce date query."""
    by_window: dict[str, list[str]] = {f"{w}d": [] for w in windows}
    accts: list[dict[str, Any]] = []
    for cid in _accounts():
        d = days_to_renewal(cid)
        if d is None or d < 0:
            continue
        for w in sorted(windows):
            if d <= w:
                by_window[f"{w}d"].append(cid)
                accts.append({"customer_id": cid, "days_to_renewal": d, "window": f"{w}d"})
                break
    counts = {k: len(v) for k, v in by_window.items()}
    return {"accounts": accts, "windows_triggered": counts}


# ─────────────────────────────────────────────────────────────────────
# WRITES — the @chain steps persist the decisions taken (dev lifecycle).
# ─────────────────────────────────────────────────────────────────────
def record_play(customer_id: str, play: str, pattern: str = "",
                outcome: str = "drafted") -> dict[str, Any]:
    """Append a play to the account's history (PROD: create a Gainsight CTA)."""
    rec = {"play": play, "pattern": pattern, "outcome": outcome, "at": _now()}
    with _LOCK:
        s = _accounts()
        acct = s.get(customer_id)
        if acct is not None:
            acct.setdefault("prior_plays", []).append(rec)
            _save("accounts", s)
    return rec


def save_touches(customer_id: str, days: int = 30) -> int:
    """Save-messages actually SENT to this account in the last `days` days —
    the touch count the save_play @chain caps. Drafts don't count (only a
    message the customer received is a touch). PROD: your comms ledger /
    Gainsight CTA history."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = 0
    for p in prior_plays(customer_id):
        if p.get("play") != "save" or p.get("outcome") == "drafted":
            continue
        try:
            at = datetime.fromisoformat(str(p.get("at")))
        except (ValueError, TypeError):
            continue
        if at >= cutoff:
            n += 1
    return n


def set_health_snapshot(customer_id: str, health_score: float,
                        risk_band: str, score_delta_30d: float) -> None:
    """Persist the latest computed health snapshot for trend reads."""
    with _LOCK:
        s = _load("health_snapshots")
        s[customer_id] = {"health_score": health_score, "risk_band": risk_band,
                          "score_delta_30d": score_delta_30d, "at": _now()}
        _save("health_snapshots", s)


def get_health_snapshot(customer_id: str) -> dict[str, Any]:
    return _load("health_snapshots").get(customer_id, {})


# ─────────────────────────────────────────────────────────────────────
# EXECUTED PLAYS — the play_runner_agent's actuator ledger. When the
# comms / calendar connector is NOT wired (day-0), play_runner records the
# sent message / booked meeting HERE so the team still FINISHES the job and
# produces a real artifact. PROD: these are the message id your comms
# platform returns and the event id your calendar returns; this ledger is
# the dev stand-in + the frequency-cap source of truth.
# ─────────────────────────────────────────────────────────────────────
def record_sent_play(customer_id: str, channel: str, recipient_role: str,
                     body: str, message_id: str = "") -> dict[str, Any]:
    """Record an approved save-message as SENT. Returns the artifact record
    (carrying the message id). PROD: your comms platform's send ack."""
    mid = message_id or new_id("msg")
    rec = {
        "artifact_type": "sent_message", "artifact_id": mid,
        "customer_id": customer_id, "channel": channel,
        "recipient_role": recipient_role, "body_preview": str(body)[:160],
        "at": _now(),
    }
    with _LOCK:
        s = _load("executed_plays")
        s.setdefault(customer_id, []).append(rec)
        _save("executed_plays", s)
    # mirror onto the account's play history as an executed outcome
    record_play(customer_id, "save", outcome="sent")
    return rec


def record_booked_meeting(customer_id: str, slot: str, attendee_role: str,
                          meeting_id: str = "") -> dict[str, Any]:
    """Record an approved QBR as BOOKED. Returns the artifact record
    (carrying the meeting id). PROD: your calendar's event id."""
    eid = meeting_id or new_id("mtg")
    rec = {
        "artifact_type": "booked_meeting", "artifact_id": eid,
        "customer_id": customer_id, "slot": slot,
        "attendee_role": attendee_role, "at": _now(),
    }
    with _LOCK:
        s = _load("executed_plays")
        s.setdefault(customer_id, []).append(rec)
        _save("executed_plays", s)
    record_play(customer_id, "qbr", outcome="booked")
    return rec


def executed_plays(customer_id: str) -> list[dict[str, Any]]:
    """Every play already EXECUTED against this account (sent / booked)."""
    return list(_load("executed_plays").get(customer_id, []))


def executions_today(customer_id: str) -> int:
    """How many plays were executed against this account in the last 24h —
    the frequency-cap source of truth the play_runner @chain reads so we
    never over-contact a customer. PROD: a windowed query on this ledger."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    n = 0
    for rec in executed_plays(customer_id):
        try:
            at = datetime.fromisoformat(str(rec.get("at")))
        except (ValueError, TypeError):
            continue
        if at >= cutoff:
            n += 1
    return n


def _placeholder_slot() -> str:
    """A plausible near-future ISO slot for a booked QBR (dev fallback)."""
    slot = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    return slot.isoformat()


# ─────────────────────────────────────────────────────────────────────
# BACKLOG FAN-OUT QUEUE — the atomic claim the account_sweep_agent's
# parallel copies use to SPLIT the account base instead of duplicating
# it. Thread-locked, LPOP-style: each claim atomically removes up to
# `count` ids. Keyed by `gen` (the sweep's session id) so the copies in
# ONE wake share a drained queue (a drained gen returns available:false —
# no re-seed mid-run, so no duplication) while the NEXT day's wake, a new
# gen, gets a fresh full backlog. PROD: `SELECT ... FOR UPDATE SKIP LOCKED`
# on your CRM, or a Redis `LPOP key count`.
# ─────────────────────────────────────────────────────────────────────
_BACKLOG_LOCK = threading.Lock()
_BACKLOG: list[str] = []
_BACKLOG_GEN: str | None = None


def reset_backlog() -> None:
    """Force the next claim to re-seed (dev/test helper)."""
    global _BACKLOG_GEN
    with _BACKLOG_LOCK:
        _BACKLOG.clear()
        _BACKLOG_GEN = None


def claim_account_batch(count: int = 5, gen: str = "") -> dict[str, Any]:
    """Atomically claim up to `count` account ids from the shared sweep
    backlog so parallel sweep copies pull DISJOINT batches.

    `gen` scopes a sweep run (pass the session id): a new gen re-seeds the
    backlog from the account base; the same gen drains it and then returns
    available:false — never re-seeding mid-run, so copies never duplicate.
    Returns available:false when the run's backlog is exhausted."""
    global _BACKLOG_GEN
    try:
        n = max(1, int(count))
    except (TypeError, ValueError):
        n = 5
    key = str(gen or "default")
    with _BACKLOG_LOCK:
        if key != _BACKLOG_GEN:
            # New sweep run → fresh full backlog for this generation.
            _BACKLOG.clear()
            _BACKLOG.extend(sorted(_accounts().keys()))
            _BACKLOG_GEN = key
        if not _BACKLOG:
            return {"available": False,
                    "reason": "backlog exhausted for this sweep run — nothing left to claim",
                    "claimed": [], "count": 0, "remaining": 0}
        claimed = _BACKLOG[:n]
        del _BACKLOG[:n]
        return {"available": True, "claimed": claimed,
                "count": len(claimed), "remaining": len(_BACKLOG)}
