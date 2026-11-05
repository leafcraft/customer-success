# customer-success — Customer Success (proactive retention)

A LeafMesh deployment that watches the whole customer base for risk
signals **before** customers escalate or churn. A peer-to-peer team of
governed agents (Rule 1) — the Manager hovers above: it raises hard or
repeated errors to the VP CS and alerts on contract violations; it does
not route the work.

## Story — one signal, end to end

Maya is a CSM with 40 mid-market and enterprise accounts. She can't
watch all of them every day, so the mesh does.

At 02:00, ACME Corp's product telemetry fires a `product_telemetry`
signal — daily active users down 42% over 30 days. The
**health_score_agent** pulls ACME's rolling 30-day signal window from
the store and, grounded in its `health_scoring` playbook (fetched on
demand) and its cross-session **memory** of ACME's prior scores, returns
**58 (at_risk), delta −18, confidence 0.82** — plus a plain-English
explanation Maya can quote to sales. Because the band isn't healthy and
ACME isn't a $500K strategic-drop account, it hands off to the
**risk_detector_agent**.

The risk detector loads its `pattern_taxonomy` playbook and names the
pattern: `usage_decay`, severity 0.7, on a $300K-ARR account. That exact
cell of the **play matrix** — one conditional edge on the risk detector
itself — fires the **save_play_agent** (usage_decay, sev ≥ 0.6, ARR ≥
$50K → immediate save). Save_play (a **super_agent** — plan → draft →
self-check) writes a warm, specific outreach ("Hi Sarah — your team's
logins dropped 40% since the March API change…") with two alternative
angles and a clear ask. It is **never auto-sent**. Because ACME is $300K
and the play is urgent, recipient_role is `exec_sponsor`, so the draft
lands in **senior_csm_human**'s inbox. Maya's senior colleague edits the
personalisation, approves, and the **play_runner_agent** finisher sends
it (fail-closed: it re-checks the approval and the per-account frequency
cap before any send). Every hop — the score, the pattern, the draft, the
approval, the send — is appended to the hash-chained
**cs_audit_logger_agent** WORM log. If the senior CSM had not answered
within 8 hours, the request would wait no longer: its Inbox card closes
and the ticket records that no reply came. Nothing is sent on silence,
and nobody is chased automatically.

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
  nothing is sent when a reviewer does not answer; strategic accounts carry
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
| *(vault connection `escalation_webhook`)* | optional | manager escalation → PagerDuty-style webhook: create vault connection `escalation_webhook` (an "Add custom" record on the Infrastructure page, service type 'Outbound webhook'), then uncomment the webhook target (in `configs/config.yaml` before the first start, in Studio's YAML editor after it) |
| `SLACK_CS_CHANNEL` | optional | Slack channel id for the manager's Slack escalation target — shipped commented in `config.yaml` |
| *(vault Slack connection)* | optional | the Slack target needs a Slack channel adapter: connect Slack on a human seat (a vault Slack record, Infrastructure → Messaging → Slack), then uncomment the target. The bot token / signing secret live only in the vault — not in `.env`, and refused inline in the config |

### Tier 3 — Connectors (inert until wired)

The three connector agents are pure-YAML n8n calls. Addresses and tokens
live **only in the vault** (a URL or token written in `config.yaml` is
refused at load): create each connection (n8n ones: on Infrastructure → Integrations → n8n, or an "Add custom" record with service type
Tools — base URL = the workflow's webhook URL, secret = its auth token),
then uncomment the agent's `connector_config:` lines — in `configs/config.yaml`
before the first start, in Studio's YAML editor after it (see *Changing the
configuration after the first start*). Each agent stays
inert (and its `wake_up` cron stays commented) until its connection points
at a live n8n workflow that returns JSON in the declared `yields` shape.

| Vault connection | Required | Connector agent → your system |
|---|---|---|
| `cs_operations_n8n` | optional | `cs_operations_agent` → Pendo / Amplitude / Segment |
| `customer_onboarding_n8n` | optional | `customer_onboarding_agent` → Salesforce / Gainsight |
| `competitor_intel_n8n` | optional | `competitor_intel_agent` → Klue / Crayon |
| `crm_mcp` | optional | `account_intel_agent`'s commented CRM `mcp:` block → your CRM / CS platform's MCP server (URL + token); until then the `agency/tools.py` dev-store tools serve day-0 |
| `cs_comms_webhook` | optional | `play_runner_agent` sends the approved save-message through your comms platform (Slack / email gateway / CPaaS) |
| `cs_calendar_webhook` | optional | `play_runner_agent` books the approved QBR through your calendar or booking tool |

`cs_comms_webhook` and `cs_calendar_webhook` are "Add custom" records on the
Infrastructure page, service type 'Outbound webhook': base URL = the address,
the token as a secret header row. `agency/play_runner_agent.py` reads them with
`leafmesh.connection_rows` and uses the message / meeting id your system
answers with. With no record, it records the send / booking in the dev store
(`source: dev_store`). The dev-store tools in `agency/tools.py` check whether
the `crm_mcp` / `cs_operations_n8n` / `customer_onboarding_n8n` records exist,
only to word an empty answer. Nothing in `.env` switches any of this.

## Agent table (the department)

Role = the human job title a VP of Customer Success would recognise.
Grounded in each agent's actual type, playbook, tools and edges in
`configs/config.yaml`. Human tiers do not hand off to each other
(a `dual→dual` footgun, gotcha #17): a gate either approves → the
finisher, or holds → audit. Which tier a request lands on is decided
up front by the routing (ARR, urgency, touch count), not by a timeout.
If a reviewer does not answer within their window, the request waits
no longer: its Inbox card closes and the ticket records that no reply
came. Nothing is sent or approved on silence, and nobody is chased
automatically.

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `health_score_agent` | Customer Health Analyst | Aggregates usage/billing/support/engagement into a 0..100 score + 30d velocity (playbooks: `health_scoring`, `score_explanation`); **memory** reads the account's prior scores across sessions; band + confidence safety floors | Non-healthy bands → `risk_detector_agent`; strategic-drop (ARR ≥ $500K, score fell >10/30d) → `senior_csm_human`; healthy + confident → audit |
| `risk_detector_agent` | Retention Risk Strategist | Names the churn pattern with evidence (playbooks: `pattern_taxonomy`, `intervention_urgency`); its outbound edges ARE the play matrix (pattern × severity × ARR) | Fires `save_play_agent` / `qbr_drafter_agent` / `renewals_agent` per matrix row; champion-loss → `account_intel_agent`; strategic / low-confidence → `senior_csm_human`; no-match → audit |
| `account_intel_agent` | Account Intelligence Analyst | Stakeholder mode: confirms a champion change against the two-signal rule and rebuilds the buying-authority map. Expansion mode: scores upsell/cross-sell (playbooks: `stakeholder_alignment`, `expansion_scan`; commented CRM mcp for day-N) | Confirmed change + authority gap → `senior_csm_human`; confirmed, no gap → `csm_human`; expansion ≥ threshold → `ae_human`; else audit |
| `save_play_agent` | Retention Copywriter (save-play drafter) | **super_agent** that drafts personalised at-risk outreach (playbook: `save_play_craft`) with alternatives + a clear ask; channel/role whitelists, exec-signature rule, quality floor + bounded touch loop. Never auto-sends | Standard → `csm_human`; exec-routed → `senior_csm_human`; exec-signature (ARR ≥ $500K immediate) → `vp_cs_human` |
| `qbr_drafter_agent` | QBR Author (executive writer) | Drafts the quarterly business review (Claude + thinking; playbook: `qbr_authoring`); data-honesty floor forbids invented numbers | Always → `csm_human` for review |
| `renewals_agent` | Renewals Analyst | Forecasts renewal probability + expansion sizing (playbook: `renewal_forecast`); strategic safeguard blocks auto-renew on ARR ≥ $250K; daily 09:00 cron (timezone-pinned) detects 30/60/90-day renewal windows | auto_renew + high confidence → audit; expansion → `ae_human`; save/risk → `senior_csm_human`; low-confidence / risk / strategic → `renewals_manager_human` |
| `account_sweep_agent` | Portfolio Health Sweeper | **Programmatic** base-wide sweep FANNED OUT over `scaling.copies.count: 3`; each copy atomically claims a disjoint account batch (`claim_account_batch`), flags at-risk accounts; daily 07:00 (timezone-pinned) | Combined digest → audit |
| `play_runner_agent` | Retention Play Executor (finisher) | After a human approves, RUNS the play — SENDS the save-message / BOOKS the QBR through the `cs_comms_webhook` / `cs_calendar_webhook` vault records — renders a real artifact to `./out` and returns it; fail-closed on unapproved / over-cap (2 plays per account per 24h) / empty message (deletes the partial on block); dev-store fallback records the artifact day-0 | → audit (executed or blocked) |
| `csm_human` | Customer Success Manager (standard accounts) | Reviews, edits and approves drafts for standard accounts before any send | Approve/send/book → `play_runner_agent`; hold or any other reply → audit; no answer in 4h → card closes, nothing sent |
| `senior_csm_human` | Senior CSM (named / strategic accounts) | Reviews exec-sponsor plays, plays past the touch cap, strategic-drop alerts and confirmed champion changes | Approve/send/book → `play_runner_agent`; hold or any other reply → audit; no answer in 8h → card closes, nothing sent |
| `renewals_manager_human` | Renewals Manager | Owns the renewal window; reviews every strategic-account forecast and negotiates terms | Every reply → audit; no answer in 6h → card closes |
| `ae_human` | Account Executive | Owns expansion deals (expansion is a sales motion, never customer-facing CS) | Every reply → audit; no answer in 4h → card closes |
| `vp_cs_human` | VP Customer Success | Strategic plays (ARR ≥ $500K, immediate) needing a personal note; receives the Manager's error alerts | Approve/send/book → `play_runner_agent`; hold or any other reply → audit; no answer in 12h → card closes, nothing sent |
| `cs_audit_logger_agent` | Compliance Audit Officer | Appends every CS decision and customer touch to a SHA-256 hash-chained WORM log | Terminal sink |

The self-reliant analysts (`health_score_agent`, `risk_detector_agent`,
`account_intel_agent`, `save_play_agent`, `renewals_agent`) ship an
`agency/<name>_agent.py` module that auto-binds by filename: a
`@pre_compose` that pulls the account's real history from the dev store,
and a `@chain` that enforces the deterministic floors the LLM cannot
waive (see Trust mechanisms). The `play_runner_agent` finisher and the
`account_sweep_agent` are programmatic modules too. The three connector
agents stay pure YAML (n8n reaches your systems); the audit sink is a
custom external intelligence function. On-demand deep reads go through
the dev-store tools in `agency/tools.py`, which **announce themselves**
(`available:false`) when an account is unknown or no source is wired.
| `cs_operations_agent` | CS Operations Analyst (telemetry) | Sweeps the whole base (mode=sweep) and pulls DAU/feature-breadth/quiet-runs for one account (mode=analytics) from your Pendo/Amplitude/Segment stack via n8n | analytics result → `health_score_agent` as a clean signal |
| `customer_onboarding_agent` | Onboarding Manager | Tracks post-sale milestones + TTFV in your Salesforce/Gainsight platform via n8n (register-close / milestone-complete / sweep) | Overdue/lagging → `risk_detector_agent`; champion change → `account_intel_agent` |
| `competitor_intel_agent` | Competitive Intelligence Analyst | Records competitor mentions (channel-weighted) and computes account displacement risk in your Klue/Crayon tooling via n8n | High displacement → `save_play_agent`; stakeholder shift → `account_intel_agent`; alert-only → `risk_detector_agent` |

## The flow — peer-to-peer mesh (Rule 1)

```
   entry points (telemetry, billing, support, csat/nps, scheduled sweep,
                 QBR due, renewal window, exec change, manual, …)
                          │
                          ▼
                  health_score_agent  (playbook: health_scoring, memory)
            ┌─────────────┼──────────────────────┐
   strategic drop      healthy + conf>=0.7     band != healthy
   (ARR>=$500K,            │                    (non-strategic)
    delta<=-10)            ▼                        │
        │              cs_audit_logger             ▼
        ▼                                    risk_detector_agent (playbook: pattern_taxonomy)
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
                (HITL — every customer message reviewed before send;
                 hold → audit; no answer → card closes, nothing sent)
                          │ approve / send / book
                          ▼
                  play_runner_agent  (FINISHER — re-checks approval +
                   frequency cap, fails closed, records the artifact)
                          │
                          ▼
                  cs_audit_logger_agent (hash-chained WORM)
```

Parallel cron responsibilities (NOT in the per-event flow):
- `renewals_agent` — daily 09:00 window-detection (30/60/90-day accounts).
- `account_sweep_agent` — daily 07:00 base-wide health sweep, FANNED OUT
  over `scaling.copies.count: 3` (each copy atomically claims a disjoint batch).
- Both crons declare a `timezone` (else they fire in UTC — gotcha #13).
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
Initech @ $600K with a departed champion, Globex @ $42K, Hooli @ $18K, plus
the four accounts the `examples/` payloads use — Bytewing, Orbital Systems,
Northgate Analytics, Pacific Coast Shipping), so
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
    and caps save-play touches per account: once an account has had 3
    save-messages sent in 30 days (read from the play history, so it
    survives restarts and new sessions), the next draft goes to a senior
    CSM with the reason attached (Rule 3).
- **Two-signal corroboration on exec turnover** — `account_intel`
  (stakeholder mode) will not confirm a champion change on a single
  non-CRM signal.
- **Silence never sends** — on leafmesh 2.4.486, when a reviewer does
  not answer within their window the request waits no longer: its Inbox
  card closes and the ticket records that no reply came. Nothing is sent
  or approved, and nobody is chased automatically. Each seat still
  declares `fallback_response: held_for_review` (routed to audit) as the
  intended timeout answer. The Manager raises only errors (a hard error,
  too many failing agents in one session) to `vp_cs_human`; there is no
  human→human wire.
- **The finisher fails closed** — `play_runner_agent` runs a play only
  on an explicit approve token (allow-list + block-list; any unknown
  verdict fails closed), under the per-account cap (2 plays in 24h), and
  never sends a save-message with no text. On a
  clean run it renders a real, inspectable artifact to `./out` (and the
  dev-store ledger); a blocked run deletes that partial artifact and
  records the reason — never a half-run file. Verified in
  `tests/test_agent_logic.py`.
- **Hash-chained WORM audit** — every customer touch is appended with a
  SHA-256 chain to detect post-hoc tampering.
- **Playbook-grounded LLMs** — the health-scoring rubric, save-play
  craft, QBR template, risk taxonomy and renewal model ship as per-agent
  `playbook:` blocks (only the `description` in the prompt; the
  `responsibility` loads on demand — gotcha #15), so the pod runs day-0
  with no knowledge backend.

## Modern capabilities (SDK 2.4.131)

- **memory** — `health_score_agent` reads an account's prior scores
  across sessions, catching the slow multi-week decline a single event
  misses.
- **super_agent** — the `save_play_agent` drafter is wrapped in the
  plan → execute → verify orchestrator (DICT-form tuning only).
- **scaling.copies + atomic claim** — `account_sweep_agent` fans a daily wake
  into 3 parallel copies (`scaling.copies: {mode: fixed, count: 3, handoff:
  last}`) that split the account backlog via the thread-locked
  `claim_account_batch` (no duplicated work). leafmesh 2.4.484's
  `copies.mode: auto` with `claim_tool:` could replace this manual split; that
  is an owner decision and is not done here.
- **timezone** — every live `wake_up` cron declares a timezone so it
  fires in business time, not UTC.
- **playbooks** — per-intent craft, loaded on demand, on every LLM
  specialist.
- **mcp (commented) + dev-store tools** — `account_intel_agent` ships a
  commented CRM `mcp:` block (it picks the vault connection `crm_mcp`);
  the `agency/tools.py` dev-store tools are the day-0 fallback and
  announce `available:false` when there's no real source.

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

## Changing the configuration after the first start

`configs/config.yaml` is only the first-start seed. On the first start (an empty store) the agency imports it as version 1 and from then on keeps its configuration in the store. After that the file is not read again: an edit to it is **not** applied, even after a restart (the boot log says "that edit was NOT applied").

To change a running agency:

- **Studio** — its settings pages, or the raw YAML editor.
- **API** — `PUT /api/yaml/raw` with an `X-API-Key` header and the body `{"yaml_content": "<the whole YAML>", "save_to_disk": true}`, or `PATCH /api/yaml/config` with `{"updates": {...}, "save_to_disk": true}`.

Each save is a new version and applies live on every pod — no restart. (A few settings, such as brokers, make the agency reload itself in place within seconds.) So wherever this README or a comment says "uncomment X in `configs/config.yaml`", do it in the file before the first start, or in Studio's YAML editor after it.

## Changing a running project (Docker)

What reaches a project that is already running (leafmesh 2.4.497). The
image holds the Python code and the first-start `configs/config.yaml`;
`.env` is passed in when the container is created; the store (Redis) keeps
its data on its own volume.

| You change | When it applies |
|---|---|
| An agent in Studio (prompt, model, tools, routes, human seats, schedules, …) | At once, no restart |
| A human seat's `channels` (Slack, email, …) | The agency reloads itself in place within seconds |
| Add or delete an agent in Studio | At once, including routes and schedules |
| A connection in the vault (model keys, MCP, webhooks, connectors, Slack, email) | At once. An agent switched off for a missing connection or address switches back on by itself |
| Anything in `.env` (licence, agency name, store, ports) | After `docker compose up -d` (the container is recreated) |
| Python code (`agency/*.py`: programmatic agents, tools, `@pre_compose`) | After a rebuild: `docker compose up -d --build` |
| `configs/config.yaml` | Never after the first start. It is read only when the store is empty |
| Several app containers on one store | A Studio save reaches every one within about 15 seconds |

- Keep model keys in the vault. A key there wins over the same key in
  `.env` (the log warns), and a change applies at once. From 2.4.497 that
  holds for every value the vault holds (endpoints, organisation / project
  ids, cloud identities); the `.env` value returns when the record goes.
- Code the image does not have: an agent marked pure Python
  (`integration: python`) is switched off until its function is in the image;
  any other programmatic agent stays on, and each call to code it does not
  have fails. Rebuild the image with the code first.
- A new Redis volume is an empty store, so `configs/config.yaml` is imported
  again as version 1.
- **If the agency refuses to start after an upgrade** (a new leafmesh pin,
  then a rebuild: "Configuration has N error(s) in the stored
  configuration"), repair the stored configuration with the agency stopped:

  ```bash
  docker compose run --rm customer-success leafmesh config check      # each problem, with its line
  docker compose run --rm customer-success leafmesh config versions   # kept versions: which pass, which is in use
  docker compose run --rm customer-success leafmesh config rollback   # back to the newest version that passes (saved as a new version)
  ```

  Or `leafmesh config export > config.yaml`, fix it by hand, then
  `leafmesh config import config.yaml` (it is checked before it is saved).

## Where the data store is set

From leafmesh 2.4.482 the store (Redis or Postgres) is set **only in `.env`** — never in `configs/config.yaml`. A `persistence:` block in the YAML is ignored with a warning on leafmesh 2.4.482–2.4.485; from 2.4.486 the agency refuses to start if that block differs from `.env`, and names each `.env` line to add. The project's `.env` sets the store explicitly (`LEAFMESH_STORE`, `LEAFMESH_AGENCY_NAME`, `REDIS_*`).

- Redis (default): `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`; optional `REDIS_DEFAULT_TTL`, `REDIS_SESSION_TTL`, `REDIS_SSL*`, `REDIS_CLUSTER_NODES`.
- Postgres instead: `LEAFMESH_STORE=postgres` plus `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (optional `POSTGRES_SSLMODE`).
- Or the whole server in one value: `REDIS_URL=redis://user:pw@host:6379/0` (`rediss://` turns TLS on) or `DATABASE_URL=postgresql://user:pw@host:5432/db`. The single names fill in what the URL leaves out.
- With Docker Compose the app reaches Redis as `redis` (the service name); compose sets that for you.
- Check it in Studio → **Infrastructure → Datastores**: it shows whether the store is reachable (never the address or password).

## This mesh's sign-in (leafmesh 2.4.497)

From leafmesh 2.4.497 each mesh signs in to LeafCraft with its own key, not
a key shared by every install.

- **First start.** The mesh makes a key pair and registers it with
  `LEAFMESH_LICENSE_KEY` and `LEAFMESH_ENV_TOKEN` from `.env` (copy the
  environment token from Studio — a placeholder such as `development` is
  refused). With both valid it works at once. If your organisation turned on
  "require approval", the first start stops and says so: approve it in
  Studio → **Keys → SDK access**, then start again. Organisation admins get an
  email for every new registration.
- **Where the key lives.** In the vault, in the store, encrypted with
  `LEAFMESH_VAULT_KEY`: it survives rebuilds and every copy of the app shares
  it. A new Redis volume, or a new vault key, makes a new key pair that
  registers again — revoke the old one in Studio.
- **Check or change it** (in Docker: `docker compose run --rm customer-success leafmesh auth status`):
  - `leafmesh auth status` — credential id, key fingerprint, registration and
    mode, never a key; `--check` signs in now and reports the tokens.
  - `leafmesh auth register` — register now.
  - `leafmesh auth rotate` — a new key pair: once it is active the mesh
    switches to it and revokes the old one. A revoked or expired key is never
    revived; rotate instead.
- **Lost the environment token?** From 5 Dec 2026 Studio shows the licence
  key and environment token only once, when they are made (then only a hint).
  Rotate the environment token in Studio and put the new one in `.env`.
  Rotating it changes where the agency's data is kept in the store, so do it
  before the agency holds data.
- **An existing project whose `.env` says `LEAFMESH_ENV_TOKEN=development`.**
  2.4.497 needs the real token. The token also names where the agency's data
  sits in the store, so with the real token the agency starts on an empty
  store (vault, sessions, board) — the old data stays under the old name. Set
  the real token before you put data in the agency, or contact us to move it.

## Your vault key — back it up

`create-leafmesh` put a `LEAFMESH_VAULT_KEY` in your `.env` when it made this project. It is the master key that encrypts every connection and credential you store in the vault (in Redis / Postgres).

- **Back it up like a master password.** Lose it and every stored connection has to be entered again.
- **Never change or regenerate it** for an existing project — a new key locks the existing vault. To change it, use `leafmesh vault rotate-key`.
- Every copy of the app (every replica) must get the **same** value. Docker Compose passes it in from `.env` as an environment variable.
- Never commit `.env` (it is already in `.gitignore`).
