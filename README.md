# customer-success — Customer Success (proactive retention)

A LeafMesh deployment that watches the whole customer base for risk
signals **before** customers escalate or churn. A peer-to-peer team of
governed agents (Rule 1) — the Manager hovers above and intervenes only
on narration / escalation / contract violations.

## Story — one signal, end to end

Maya is a CSM with 40 mid-market and enterprise accounts. She can't
watch all of them every day, so the mesh does.

At 02:00, ACME Corp's product telemetry fires a `product_telemetry`
signal — daily active users down 42% over 30 days. The
**health_score_agent** pulls ACME's rolling 30-day signal window from
Redis and the scoring rubric from the `health_rules` knowledge group,
and returns a score of **58 (at_risk), delta −18, confidence 0.82** —
plus a plain-English explanation Maya can quote to sales. Because the
band isn't healthy and ACME isn't a $500K strategic-drop account, it
hands off to the **risk_detector_agent**.

The risk detector grounds itself in the `risk_patterns` group and names
the pattern: `usage_decay`, severity 0.7, on a $300K-ARR account. That
exact cell of the **play matrix** — one conditional edge on the risk
detector itself — fires the **save_play_agent** (usage_decay, sev ≥ 0.6,
ARR ≥ $50K → immediate save). Save_play drafts a warm, specific
outreach from the `save_play_library` ("Hi Sarah — your team's logins
dropped 40% since the March API change…") with two alternative angles
and a clear ask. It is **never auto-sent**. Because ACME is $300K and
the play is urgent, recipient_role is `exec_sponsor`, so the draft
lands in **senior_csm_human**'s inbox. Maya's senior colleague edits
the personalisation, approves, and sends. Every hop — the score, the
pattern, the draft, the human decision — is appended to the
hash-chained **cs_audit_logger_agent** WORM log. If the senior CSM had
not responded within 8 hours, the gate would have escalated to VP CS
(default-deny), never auto-acted.

Maya never opened a dashboard. She got one reviewed draft for the one
account that needed her, with the reasoning attached.

## Why it works in an organization

Customer Success is the SaaS "biggest single prize" in the ROI model —
protecting revenue you already have. This template realises the
**`cs × saas`** cell:

- **Pool**: CS / retention is ~8% of revenue today (SaaS).
- **Routine share**: ~45% of CSM hours are mechanical — health
  monitoring, QBR drafting, renewal forecasting, exec-map refresh — and
  this is exactly the work the mesh sweeps continuously.
- **Revenue protected, not just cost saved**: for a $100M-ARR SaaS
  company, retention is roughly **$8M/yr of CS spend** today AND the
  **~$25-30M of ARR that walks out as churn**. The headline number
  comes from both.
- **Trust posture**: every customer-facing send is HITL-reviewed;
  human gates are default-deny on timeout; strategic accounts carry
  hard safeguards (no `auto_renew` on ARR ≥ $250K; two-signal
  corroboration before confirming a champion change).

The win is *proactivity*: scheduled sweeps and event triggers act on a
slow decline weeks before the customer complains.

## Configuration (three tiers)

Copy `env` → `.env` and fill in. Grounded in the actual `env` +
`configs/config.yaml`.

### Tier 1 — Boot (required to start the mesh)

| Var | Required | Purpose |
|---|---|---|
| `LEAFMESH_LICENSE_KEY` | **required** | SDK license — the mesh refuses to start without it |
| `OPENAI_API_KEY` | **required** | the `gpt-4o-mini` agents + the manager |
| `ANTHROPIC_API_KEY` | **required** | `qbr_drafter_agent` (`claude-sonnet-4-6` + thinking) |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | **required** (host defaults `localhost:6379`) | health-score cache, customer context, save-play history, session store |
| `LEAFMESH_ENV_TOKEN` | optional | environment tag (defaults `development`) |

### Tier 2 — Host prerequisites / escalation channels (optional)

| Var | Required | Purpose |
|---|---|---|
| `PAGER_WEBHOOK` | optional | manager escalation → PagerDuty-style webhook (consumed in `config.yaml`) |
| `SLACK_CS_CHANNEL` | optional | manager escalation Slack channel id (consumed in `config.yaml`) |
| `SLACK_BOT_TOKEN` | optional | **not auto-consumed** by the escalation block — wire into a `channels:` slack adapter to actually post |
| `SLACK_SIGNING_SECRET` | optional | only for an inbound Slack adapter — wire in code/config |

### Tier 3 — Connectors (inert until wired)

The three connector agents are pure-YAML n8n calls. Each webhook is
referenced in `config.yaml`; the agent stays inert (and its `wake_up`
cron stays commented) until you point the URL at a live n8n workflow
that returns JSON in the declared `yields` shape.

| Var | Required | Connector agent → your system |
|---|---|---|
| `N8N_CS_OPERATIONS_WEBHOOK_URL` | optional | `cs_operations_agent` → Pendo / Amplitude / Segment |
| `N8N_CUSTOMER_ONBOARDING_WEBHOOK_URL` | optional | `customer_onboarding_agent` → Salesforce / Gainsight |
| `N8N_COMPETITOR_INTEL_WEBHOOK_URL` | optional | `competitor_intel_agent` → Klue / Crayon |

## Agent table (the department)

Role = the human job title a VP of Customer Success would recognise.
Grounded in each agent's actual type, knowledge group, tools and edges
in `configs/config.yaml`.

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `health_score_agent` | Customer Health Analyst | Aggregates usage/billing/support/engagement into a 0..100 score + 30d velocity, grounded in `health_rules`; explains the score in plain English; applies band + confidence safety floors | Routes non-healthy bands → `risk_detector_agent`; strategic-drop (ARR ≥ $500K, score fell >10/30d) → `senior_csm_human` directly; healthy + confident → audit |
| `risk_detector_agent` | Retention Risk Strategist | Names the specific churn pattern (usage_decay, exec_turnover, billing_friction, …) with evidence, grounded in `risk_patterns`; its outbound edges ARE the play matrix (pattern × severity × ARR) | Fires `save_play_agent` / `qbr_drafter_agent` / `renewals_agent` per matrix row; champion-loss → `account_intel_agent`; strategic / low-confidence → `senior_csm_human`; no-match → audit |
| `account_intel_agent` | Account Intelligence Analyst | Stakeholder mode: confirms a champion change against the two-signal rule and rebuilds the buying-authority map, grounded in `stakeholder_protocol`. Expansion mode: scores upsell/cross-sell signals | Confirmed change + authority gap → `senior_csm_human`; confirmed, no gap → `csm_human`; expansion ≥ threshold → `ae_human`; else audit |
| `save_play_agent` | Retention Copywriter (save-play drafter) | Drafts personalised at-risk outreach from the `save_play_library` with alternatives + a clear ask; enforces channel/role whitelists, the exec-signature rule and a quality floor. Never auto-sends | Standard → `csm_human`; exec-routed → `senior_csm_human`; exec-signature (ARR ≥ $500K immediate) → `vp_cs_human` |
| `qbr_drafter_agent` | QBR Author (executive writer) | Drafts the quarterly business review (Claude + thinking) from the `qbr_template` and real usage data; data-honesty floor forbids invented numbers | Always → `csm_human` for review |
| `renewals_agent` | Renewals Analyst | Forecasts renewal probability + expansion sizing, grounded in `renewal_forecast_model`; strategic safeguard blocks auto-renew on ARR ≥ $250K; daily 09:00 cron detects 30/60/90-day renewal windows | auto_renew + high confidence → audit; expansion → `ae_human`; save/risk → `senior_csm_human`; low-confidence / risk / strategic → `renewals_manager_human` |
| `csm_human` | Customer Success Manager (standard accounts) | Reviews, edits and approves drafts for standard accounts before any send | Approve → audit; escalate → `senior_csm_human`; timeout (4h) → escalate (default-deny) |
| `senior_csm_human` | Senior CSM (named / strategic accounts) | Reviews exec-sponsor plays, strategic-drop alerts and confirmed champion changes | Approve → audit; escalate → `vp_cs_human`; timeout (8h) → escalate |
| `renewals_manager_human` | Renewals Manager | Owns the renewal window; reviews every strategic-account forecast and negotiates terms | Approve / renegotiate → audit; escalate → `vp_cs_human`; timeout (6h) → escalate |
| `ae_human` | Account Executive | Owns expansion deals + competitor responses (expansion is a sales motion, never customer-facing CS) | Approve → audit; timeout (4h) → hold for manual review |
| `vp_cs_human` | VP Customer Success | Board-visibility accounts, strategic plays, final escalation tier | → audit; timeout (12h) → halt for personal review (default-deny) |
| `cs_audit_logger_agent` | Compliance Audit Officer | Appends every CS decision and customer touch to a SHA-256 hash-chained WORM log | Terminal sink |

The four self-reliant analysts (`health_score_agent`, `risk_detector_agent`,
`save_play_agent`, `renewals_agent`) ship an `agency/<name>_agent.py`
module that auto-binds by filename: a `@pre_compose` that pulls the
account's real history from the dev store, and a `@chain` that enforces
the deterministic floors the LLM cannot waive (see Trust mechanisms).
The three connector agents stay pure YAML (n8n reaches your systems);
the audit sink is a custom external intelligence function.
| `cs_operations_agent` | CS Operations Analyst (telemetry) | Sweeps the whole base (mode=sweep) and pulls DAU/feature-breadth/quiet-runs for one account (mode=analytics) from your Pendo/Amplitude/Segment stack via n8n | analytics result → `health_score_agent` as a clean signal |
| `customer_onboarding_agent` | Onboarding Manager | Tracks post-sale milestones + TTFV in your Salesforce/Gainsight platform via n8n (register-close / milestone-complete / sweep) | Overdue/lagging → `risk_detector_agent`; champion change → `account_intel_agent` |
| `competitor_intel_agent` | Competitive Intelligence Analyst | Records competitor mentions (channel-weighted) and computes account displacement risk in your Klue/Crayon tooling via n8n | High displacement → `save_play_agent`; stakeholder shift → `account_intel_agent`; alert-only → `risk_detector_agent` |

## The flow — peer-to-peer mesh (Rule 1)

```
   entry points (telemetry, billing, support, csat/nps, scheduled sweep,
                 QBR due, renewal window, exec change, manual, …)
                          │
                          ▼
                  health_score_agent  (knowledge: health_rules)
            ┌─────────────┼──────────────────────┐
   strategic drop      healthy + conf>=0.7     band != healthy
   (ARR>=$500K,            │                    (non-strategic)
    delta<=-10)            ▼                        │
        │              cs_audit_logger             ▼
        ▼                                    risk_detector_agent (knowledge: risk_patterns)
   senior_csm_human                              │       │
                                      exec_turnover?    requires_human_review?
                                            │            │
                                            ▼            ▼
                                     account_intel    senior_csm_human
                                     (stakeholder)
                                            │
                          PLAY MATRIX on risk_detector's own edges
                          (pattern × severity × ARR → one edge/row):
                          ┌─────────┬───────────┬──────────────┬──────────┐
                          ▼         ▼           ▼              ▼
                     save_play  qbr_drafter  renewals_agent  hold→audit
                          │         │             │
                          │         ▼             │ expansion? → ae_human
                          │     csm_human         │ strategic/low-conf? → renewals_manager
                          ▼
                csm_human / senior_csm_human / vp_cs_human
                (HITL — every customer message reviewed before send)
                          │
                          ▼
                  cs_audit_logger_agent (hash-chained WORM)
```

Parallel cron responsibilities (NOT in the per-event flow):
- `renewals_agent` — daily 09:00 window-detection (30/60/90-day accounts).
- `cs_operations_agent` / `customer_onboarding_agent` sweeps — shipped
  with `wake_up` crons commented out; enable after wiring the n8n
  connectors.

## Quick start

```bash
cp env .env       # fill in keys
pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7-alpine
python validate_config.py     # pre-flight
python main.py
```

### Day-0 demo — runs with NO connectors and NO license

The four analysts pull their context from a dev store
(`agency/_shared/store.py`) seeded with realistic accounts (ACME @ $300K,
Initech @ $600K with a departed champion, Globex @ $42K, Hooli @ $18K), so
their `@pre_compose` context-pulls and `@chain` floors run end-to-end
before anything is wired. Drive the seeded accounts straight through the
deterministic layer — no API keys needed:

```python
import asyncio
from agency import health_score_agent, risk_detector_agent, renewals_agent
from agency._shared import store

# @pre_compose pulls ACME's real rolling window + a baseline health score
ctx = health_score_agent.load_account_context({"customer_id": "cust-acme-001"}, {})
print("ACME signals in window:", ctx["signal_count"],
      "| baseline:", ctx["baseline_health"])

# @chain enforces the band/safety floor even if the LLM tried "healthy"
verdict = asyncio.run(health_score_agent.enforce_band_floors(
    {"customer_id": "cust-acme-001", "health_score": 85, "score_delta_30d": -18,
     "confidence": 0.4, "risk_band": "healthy",          # LLM's (wrong) band
     "executive_summary": "looks fine", "top_3_contributors": [{"factor": "dau"}]}, {}))
print("enforced band:", verdict["risk_band"])             # -> 'watch' (safety floor)

# strategic-review floor: $300K + real severity ALWAYS gets a human first
risk = asyncio.run(risk_detector_agent.enforce_strategic_review(
    {"primary_pattern": "usage_decay", "primary_severity": 0.7,
     "arr_cents": 30_000_000, "requires_human_review": False, "patterns": []}, {}))
print("requires_human_review:", risk["requires_human_review"])   # -> True

# daily renewal-window scan finds the seeded in-window accounts (Initech ~24d)
scan = store.accounts_in_renewal_window()
print("accounts entering renewal window:", [a["customer_id"] for a in scan["accounts"]])
```

Once you set `LEAFMESH_LICENSE_KEY` + an LLM key, the same modules run
inside the live mesh — trigger a synthetic telemetry signal over HTTP:

```bash
curl -X POST http://127.0.0.1:18820/api/mesh/request \
  -H "Content-Type: application/json" \
  -d '{
    "entry_point": "product_telemetry",
    "data": {
      "customer_event": {
        "customer_id": "cust-acme-001",
        "signal_source": "telemetry",
        "signal_type": "usage_drop",
        "arr_cents": 30000000,
        "segment": "enterprise",
        "tier": "named_account",
        "raw_payload": {"dau_delta_30d": -0.42}
      }
    }
  }'
```

## Trust mechanisms

- **No auto-send to customers, ever** — every save-play / QBR / renewal
  draft routes through a CSM tier for review before send.
- **The play matrix is visible wiring** — pattern × severity × ARR →
  play lives as conditional edges on `risk_detector_agent` in
  `config.yaml`; an auditor reads the routing, not a Python module.
- **Deterministic floors the LLM can't waive** — the load-bearing rules
  live in BOTH the prompt (so the model is grounded) AND a Python
  `@chain` (so it's guaranteed):
  - **Strategic safeguard** — ARR ≥ $250K can never `auto_renew`
    (forced → `high_touch` in `renewals_agent`'s `@chain`) AND always
    routes to `renewals_manager_human`; strategic drops (ARR ≥ $500K)
    bypass the risk detector straight to `senior_csm_human`.
  - **Strategic review gate** — ARR ≥ $250K with real severity ALWAYS
    sets `requires_human_review` in `risk_detector_agent`'s `@chain`
    before any play can fire; an invalid pattern name collapses to hold.
  - **Health-score safety floor** — confidence < 0.6 with a "healthy"
    band is force-downgraded to "watch"; an unexplained score has its
    confidence capped (`agency/_shared/health_math.py`); `risk_band` is
    always recomputed, never trusted from the LLM.
  - **Exec-signature + bounded touch loop** — `save_play_agent`'s
    `@chain` forces a VP personal note on ARR ≥ $500K immediate plays,
    and caps save-play touches per account (default 3) — past the cap
    the play escalates to a senior CSM (Rule 3).
- **Two-signal corroboration on exec turnover** — `account_intel`
  (stakeholder mode) will not confirm a champion change on a single
  non-CRM signal.
- **HITL fallback is escalation, never auto-action** — every human's
  `fallback_response` escalates to the next tier (default-deny); VP CS
  timeout halts for personal review.
- **Hash-chained WORM audit** — every customer touch is appended with a
  SHA-256 chain to detect post-hoc tampering.
- **Knowledge-grounded LLMs** — health rules, save-play library, QBR
  template, renewal forecast model live in the customer's knowledge
  base; edit the docs, agents pick them up on next query.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Coverage: config-shape invariants (multi-entry, conditional wires
everywhere, HITL fallbacks set, audit sink wired, connectors are pure
YAML) and rule-preservation checks (band + safety floors, exec
two-signal rule, the play matrix on `risk_detector`'s edges, save-play
exec-signature rule, renewal strategic safeguard — all asserted to live
in the YAML prompts/conditions).
