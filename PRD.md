# PRD — customer-success (Customer Success, proactive retention)

## Overview & story

Customer Success is the SaaS "biggest single prize" — protecting revenue
you already have. This agency is a **department of self-reliant agents**
that watches the whole customer base for churn risk **before** the
customer complains, and routes every customer-facing action through a
human reviewer. It realises the **`cs × saas`** ROI cell.

**Story.** Maya is a CSM with 40 accounts. At 02:00, ACME's telemetry
fires `product_telemetry` (DAU −42%/30d). `health_score_agent` scores it
58 (at_risk, delta −18, confidence 0.82) and explains it; hands to
`risk_detector_agent`, which names `usage_decay` sev 0.7 on a $300K
account. That cell of the play matrix (one conditional edge on the risk
detector) fires `save_play_agent`, which drafts a specific, warm
outreach — never auto-sent. Because the account is $300K and urgent,
recipient_role is `exec_sponsor`, so the draft lands in
`senior_csm_human`'s inbox. The senior CSM edits and approves, and the
`play_runner_agent` finisher sends it (re-checking the approval + the
per-account cap first). Every hop is appended to the hash-chained WORM
audit. Had no one answered in 8h, the request would wait no longer:
its Inbox card closes and the ticket records that no reply came.
Nothing is sent on silence, and nobody is chased automatically.

## Flow diagram

```
   entry points (telemetry · billing · support_threshold · csat_drop ·
                 nps_dip · usage_anomaly · scheduled_health_sweep ·
                 qbr_due · renewal_window · exec_change · manual ·
                 explain_health_score · usage_metrics_pull ·
                 expansion_scan · onboarding/competitor ingest)
                          │
                          ▼
                  health_score_agent  (LLM chain, playbook + memory)
            ┌─────────────┼───────────────────────┐
   strategic drop      healthy + conf>=0.7      band != healthy
   ARR>=$500K          → cs_audit_logger         (non-strategic)
   delta<=-10                                         │
        │                                             ▼
        ▼                                   risk_detector_agent (LLM chain, playbook)
   senior_csm_human                              │            │
                                       requires_stakeholder?  requires_human_review?
                                              │                │
                                              ▼                ▼
                                       account_intel_agent  senior_csm_human
                                       (stakeholder mode)
                                              │
                        ══ PLAY MATRIX (risk_detector's own edges) ══
                        pattern × severity × ARR → exactly one row:
                        ┌──────────┬────────────┬───────────────┬─────────┐
                        ▼          ▼            ▼               ▼
                   save_play   qbr_drafter   renewals_agent   hold→audit
                        │          │              │
                        │          ▼              ├─ auto_renew+conf → audit
                        │      csm_human          ├─ expansion → ae_human
                        │                         ├─ save/risk → senior_csm
                        │                         └─ low-conf/risk/strategic
                        ▼                            → renewals_manager
            csm_human / senior_csm_human / vp_cs_human
            (HITL — every customer message reviewed before send;
             hold → audit; no answer → card closes, nothing sent)
                        │ approve / send / book
                        ▼
              play_runner_agent  (FINISHER — re-checks approval + cap,
               fails closed, records the artifact) → audit
                        │
                        ▼
              cs_audit_logger_agent  (external custom — hash-chained WORM)

  Connector agents (pure YAML, n8n → your systems):
    cs_operations_agent       → Pendo/Amplitude/Segment  (analytics → health_score)
    customer_onboarding_agent → Salesforce/Gainsight     (overdue → risk_detector)
    competitor_intel_agent    → Klue/Crayon              (displacement → save_play)

  Cron citizens (parallel, NOT in the per-event flow; timezone-pinned):
    renewals_agent      — 09:00 daily 30/60/90-day window detection
    account_sweep_agent — 07:00 daily base-wide sweep, scaling.copies.count: 3
                          (each copy claims a disjoint account batch)
    cs_operations / onboarding sweeps — wake_up shipped commented
```

## Actor inventory (job-title roster)

| Agent | Role (job title) | Type |
|---|---|---|
| `health_score_agent` | Customer Health Analyst | llm (chain) + memory |
| `risk_detector_agent` | Retention Risk Strategist | llm (chain) |
| `account_intel_agent` | Account Intelligence Analyst | llm (chain, mode-dispatched) |
| `save_play_agent` | Retention Copywriter | llm (chain) + super_agent |
| `qbr_drafter_agent` | QBR Author | llm (chain, Claude + thinking) |
| `renewals_agent` | Renewals Analyst | llm (chain) + 09:00 cron |
| `account_sweep_agent` | Portfolio Health Sweeper | programmatic + scaling.copies.count: 3 + 07:00 cron |
| `play_runner_agent` | Retention Play Executor (finisher) | programmatic |
| `csm_human` | Customer Success Manager (standard) | human (dual) |
| `senior_csm_human` | Senior CSM (named/strategic) | human (dual) |
| `renewals_manager_human` | Renewals Manager | human (dual) |
| `ae_human` | Account Executive | human (dual) |
| `vp_cs_human` | VP Customer Success | human (dual) |
| `cs_audit_logger_agent` | Compliance Audit Officer | external (custom) |
| `cs_operations_agent` | CS Operations Analyst (telemetry) | programmatic (n8n) |
| `customer_onboarding_agent` | Onboarding Manager | programmatic (n8n) |
| `competitor_intel_agent` | Competitive Intelligence Analyst | programmatic (n8n) |

AI specialists are `chain` (a `dual→dual` edge auto-bounces replies — gotcha
#17); `dual` is confined to the human/webhook boundary. Human tiers do not wire
to each other; the tier is chosen up front by the routing (ARR, urgency,
touch count). The Manager raises only errors to `vp_cs_human`.

## Data artifacts

- **`CustomerEvent`** — the ingress envelope (customer_id, account_id,
  signal_source, signal_type, arr_cents, segment, tier, raw_payload).
- **`HealthVerdict`** — score 0..100, score_delta_30d, risk_band
  (healthy/watch/at_risk/critical), contributing_factors, confidence.
- **Per-agent yields** — declared on each agent; identity fields
  (`customer_id`, `arr_cents`, `segment`) are echoed unchanged so
  downstream tier-routing edges can gate on them.
- **Audit entry** — `{audit_event_id, written_at_utc, session_id,
  payload, hash_chain_prev, hash_chain_current}`; SHA-256 chains each
  entry to the prior body.
- **State of record lives in the customer's systems** — health/CSAT/
  telemetry/renewal/competitor state lives in Gainsight, Salesforce,
  Pendo/Amplitude, Klue/Crayon behind the n8n connector agents. Redis
  is cache + session store only; the audit JSONL is a dev sink (swap
  for S3 Object Lock / Datadog / Splunk in prod).

## Per-agent spec (grounded in config.yaml)

- **health_score_agent** (llm chain, playbooks `health_scoring` /
  `score_explanation`, **memory**) — scores + explains in one pass;
  deterministic band derivation; safety floor (low-confidence "healthy" →
  "watch"); quality floor (no defensible summary → cap confidence 0.3).
  Edges: strategic-drop → `senior_csm_human`; non-healthy (non-strategic)
  → `risk_detector_agent`; healthy+confident → audit.
- **risk_detector_agent** (llm chain, playbooks `pattern_taxonomy` /
  `intervention_urgency`) — names one of a closed pattern set; its
  outbound edges **are** the play matrix; sets `requires_stakeholder_map`
  / `requires_human_review`. Calls `account_intel_agent`.
- **account_intel_agent** (llm chain, playbooks `stakeholder_alignment` /
  `expansion_scan`; commented CRM `mcp:`) — two responsibilities by mode;
  two-signal confirmation rule; expansion floor thresholds. Mode-gated
  edges prevent cross-fire.
- **save_play_agent** (llm chain, **super_agent** dict, playbook
  `save_play_craft`) — drafts at-risk outreach; channel/role whitelists;
  exec-signature rule; quality floor + bounded touch loop; three
  mutually-exclusive review-tier edges.
- **qbr_drafter_agent** (llm chain, Claude + thinking, playbook
  `qbr_authoring`) — data-honesty floor; always → `csm_human`.
- **renewals_agent** (llm chain, playbook `renewal_forecast`, 09:00 cron
  + timezone) — forecast + expansion sizing + window detection; strategic
  safeguard (no auto_renew ≥ $250K) AND strategic edge (`arr_cents >=
  25000000` → `renewals_manager_human`); window-scan runs are terminal.
- **account_sweep_agent** (programmatic, `scaling.copies.count: 3`, 07:00 cron +
  timezone) — base-wide health sweep; each copy atomically claims a
  disjoint account batch (`claim_account_batch`) and flags at-risk
  accounts; combined digest → audit. The atomic claim is the fan-out
  contract (numbers SUM, lists CONCAT under `scaling.copies.handoff: last`).
- **play_runner_agent** (programmatic FINISHER) — after a human approves,
  RUNS the play: SENDS the save-message / BOOKS the QBR through the vault
  records `cs_comms_webhook` / `cs_calendar_webhook` ('Outbound webhook',
  read with `leafmesh.connection_rows`) and returns the artifact. `@chain`
  fails closed on an unapproved verdict (allow-token list; unknown →
  blocked), an over-cap account (2 plays / 24h) or an empty message,
  dropping the artifact. Dev-store fallback records the artifact day-0.
- **Human tiers** — `csm` (4h) / `senior_csm` (8h) / `renewals_manager`
  (6h) / `ae` (4h) / `vp_cs` (12h). Approvals go to `play_runner_agent`;
  hold or any other reply → audit. On leafmesh 2.4.486 a reviewer who
  does not answer in time closes the card and the ticket records that no
  reply came — nothing is sent or approved. Each seat declares
  `fallback_response: held_for_review` (→ audit) as the intended timeout
  answer. No human→human wire.
- **cs_audit_logger_agent** (external custom) — registered via
  `mesh.intelligence("cs_audit_logger_agent")` in `main.py`; terminal.
- **Connector agents** (programmatic n8n) — pure YAML; inert until the
  webhook URL is wired; results returned in the declared yields shape.
- **Dev-store tools** (`agency/tools.py`) — on-demand deep reads +
  the atomic claim; each announces `available:false` with no real source.

## Entry points

15 entry points cover every trigger channel: `product_telemetry`,
`billing_event`, `support_threshold`, `csat_drop_signal`,
`nps_score_dip`, `usage_anomaly`, `scheduled_health_sweep`, `qbr_due`,
`renewal_window`, `exec_change`, `manual_cs_action`,
`explain_health_score`, `usage_metrics_pull`, `expansion_scan`, plus the
direct-call / onboarding / competitor ingest points. Each is API-
addressable for ad-hoc invocation and debugging.

## Controls & trust

- Forward-flow DAG; the only back-edge is the bounded save-play touch
  loop (3 sent touches in 30 days → senior CSM). Silence never sends.
- The play matrix is visible conditional wiring on one agent — no
  conductor hub, no hidden Python router.
- Strategic safeguards: no auto_renew ≥ $250K; strategic forecasts
  always to renewals_manager; strategic score drops bypass straight to
  senior CSM.
- Two-signal corroboration before confirming a champion change.
- Deterministic floors are enforced in Python `@chain` steps the LLM
  can't waive (band/safety/quality, strategic review, exec-signature,
  the bounded save-play touch loop, the renewal safeguard + window date
  gate) — the prompts still state the rules so the model is grounded.
- Hash-chained WORM audit terminus on every branch.
- HITL gates carry no placeholder `operator_ids` — unset routes to the
  shared inbox (placeholder emails would make tasks invisible).

## Configuration pointer

See `README.md` → **Configuration (three tiers)** and the `env` file.
Tier 1 (license + LLM keys + Redis) is required to boot; Tier 2
(escalation channels) and Tier 3 (connectors) are optional and
inert until wired. The Slack escalation target ships commented: it needs
a connected Slack channel adapter whose bot token lives in a vault Slack
connection (Infrastructure → Messaging → Slack) — never in `.env` or the
config.
The n8n connectors and the CRM `mcp:` block each pick a vault connection
by name (`cs_operations_n8n`, `customer_onboarding_n8n`,
`competitor_intel_n8n`, `crm_mcp`); the finisher's comms and calendar
sends read the 'Outbound webhook' records `cs_comms_webhook` and
`cs_calendar_webhook`.

## Reflection / decisions

- **Deep AND usable.** The play matrix and tiers live in prompts +
  conditional edges (legible to an auditor), but the load-bearing floors
  are ALSO enforced in Python the LLM can't waive. Five self-reliant
  analysts ship an auto-binding `agency/<name>_agent.py`:
  - `@pre_compose` pulls the account's real history (rolling signal
    window, usage/adoption, prior plays, ARR/renewal record) from a dev
    store seeded with realistic accounts, so the chain runs day-0 with
    no connectors. In prod the store's signatures are the contract you
    swap n8n/CRM reads into.
  - `@chain` enforces the band/safety/quality floors
    (`_shared/health_math.py`), the strategic-review gate, exec-signature,
    the bounded save-play touch loop (sent touches counted from the
    store's play history), and the renewal safeguard + window date gate.
  - `save_play_agent` uses `@compose` to hand the CSM/senior tiers the
    draft and the VP tier the escalation brief.
  The hash-chained audit sink remains a custom external intelligence
  function. The three connector agents stay pure YAML (n8n → your
  systems).
- **Play matrix as visible edges, not a module.** The former
  `playbook_trigger_agent` was dissolved into one conditional `can_call`
  edge per matrix row on `risk_detector_agent`, so an auditor reads the
  routing directly.
- **Consolidations (Rule 2).** `account_intel_agent` absorbed the
  expansion scanner + stakeholder mapper (mode-dispatched);
  `renewals_agent` absorbed the renewal-window monitor (one daily cron
  carries both responsibilities); `cs_operations_agent` carries sweep +
  per-account analytics.
- **Adversarial fixes applied.** (1) `health_score_agent` narration
  promised a strategic-drop route to `senior_csm_human` with no backing
  edge → added the edge (mutually exclusive with the risk_detector
  edge). (2) `risk_detector_agent` narration promised a parallel
  competitor route that the play matrix had replaced → narration aligned
  to the enforced single-row save route. (3) `save_play_agent` narration
  overstated a vp_cs parallel route at $250K → aligned to the enforced
  three-edge tier logic. (4) `renewals_agent` narration promised a
  strategic-account renewals_manager review with no ARR yield/edge →
  added `arr_cents` passthrough yield + the `arr_cents >= 25000000` edge.
  (5) `env` documented `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` as
  plug-and-play escalation channels though they are consumed nowhere →
  comments corrected to "wire into a channels adapter."
- **Modern capabilities (SDK 2.4.131), fitted to the domain.** memory
  (cross-session health on `health_score_agent`); super_agent (DICT-form)
  on the `save_play_agent` drafter; per-intent `playbook:` blocks
  replacing the mongo knowledge groups so the pod runs day-0; `scaling.copies.count:
  3` + an atomic claim (`claim_account_batch`) on `account_sweep_agent`
  for backlog fan-out; a `timezone` on every live `wake_up`; a commented
  CRM `mcp:` block on `account_intel_agent` backed by `agency/tools.py`
  dev-store tools that announce `available:false` with no real source.
- **Conservative where unverifiable.** The live `start()` path requires
  a license key not present in this environment; verification rests on
  `validate_config` (all checks pass), real-SDK `from_yaml` on 2.4.131
  (17 agents parsed, **0 dual warnings**), direct calls to the `@chain`
  finisher / floor functions and the atomic claim, and the test suite
  (71 passing).
