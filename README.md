# AI Investment Council

Evidence-grounded decision-integrity architecture for an AI-assisted
investment workflow.

## Current status
Alpaca AI Trading Agents Hackathon event-window build is active on `hackathon/alpaca-2026`.

B1 foundation: FROZEN
G02 validation: 1408/1408 PASS
Pre-event freeze commit: 6337068...

## Event-window progress
- B2 deterministic evidence/screening core implemented
- owner-approved `DEMO_UNIVERSE_V1` frozen in `config/event/demo_universe_v1.json`
- owner-approved `SCREENING_POLICY_V1` frozen in `config/b2/screening_policy_v1.json`
- market/evidence reads remain read-only
- no Alpaca paper order has been submitted by this build

## Planned next
- exact B2 real-runtime shortlist/deep comparison
- research orchestrator
- Bull / Bear / Red-Team Council
- deterministic options risk
- explicit human-approved Alpaca PAPER execution
- cockpit / thesis monitor

## B7-P0 Decision Integrity Cockpit

The initial cockpit is a desktop-first, server-rendered FastAPI/Jinja2 UI. It
is a read-only projection of the stable B4 judge-entry boundary on this branch;
it has no path to create decisions, calculate risk, approve capital, or submit
an Alpaca order.

The supplied projection keeps the event's material research gap visible and
therefore presents `NO FINAL DECISION`, `DATA INCOMPLETE`, and `NO ORDER` rather
than manufacturing an `INVEST` lifecycle. B5/B6 data is intentionally pending:
there is no options sizing, price, symbol, approval, or execution payload.

Run locally:

```bash
PYTHONPATH=src uv run uvicorn aic.cockpit.app:app --reload
```

Open `/` for the cockpit, `/decisions` for the read-only registry, and
`/decisions/b4-research-reopen` for the integrity detail and trace.

## Safety
Paper only.
No live-money execution.
