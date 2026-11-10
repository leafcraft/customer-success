# customer-success — example scenarios

Four realistic scenarios that exercise the major paths through this mesh.

## How to run

```bash
create-leafmesh --load .   # once: loads the team into the Redis in .env (or use create-leafmesh --start .)
python examples/run_examples.py                 # all scenarios
python examples/run_examples.py 01_             # one scenario (substring match)
```

## Scenario map

- **01_telemetry_dau_drop** — `product_telemetry` — Mid-market SaaS account — DAU dropped 35% week-over-week. Triggers health_score → risk_detector (play matrix on its edges) → CSM save-play.
- **02_billing_failed_payment** — `billing_event` — Failed credit card payment on a renewal — risk_detector picks it up, account_intel (stakeholder mode) confirms champion is still there, save_play drafts a CSM outreach.
- **03_renewal_window_60d** — `renewal_window` — 60 days from renewal — renewals_agent builds the forecast, account_intel (stakeholder mode) confirms decision-makers haven't churned.
- **04_executive_change** — `exec_change` — CRM detected the champion left — account_intel (stakeholder mode) is the entry point, stakeholder map gets rebuilt.

## Audit trail

Every scenario appends one hash-chained entry to the template's
`logs/<audit>.jsonl` file. Tail it during a run to see the
ledger build up across scenarios.
