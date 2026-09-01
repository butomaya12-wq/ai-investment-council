# AI Investment Council

AI Investment Council is an evidence-grounded, multi-agent decision system that separates investment research from the authority to take risk or place an order.

## The problem

Autonomous trading agents often collapse research, decision-making, risk, and execution into one opaque model loop. That makes it hard to explain a recommendation, challenge a weak thesis, or prevent a plausible-looking model output from becoming a trade.

AI Investment Council separates those authorities. Evidence is structured before the Council sees it; adversarial agents challenge each other; a bounded Judge chooses an allowed outcome; deterministic risk and explicit human approval sit between a decision and any PAPER broker action.

## How it works

```text
Market / SEC / Alpaca evidence
  -> deterministic eligibility and evidence layer
  -> exactly 3 candidates
  -> research
  -> Bull / Bear / Red Team
  -> cross-examination / Rebuttal
  -> bounded Judge
  -> INVEST / WATCH / ABSTAIN
  -> only INVEST may proceed toward deterministic options/risk
  -> explicit human approval
  -> Alpaca PAPER execution
```

The completed competition run ended at **WATCH**. It did not proceed to B5, risk selection, owner approval, or broker execution.

See the judge-friendly [architecture diagram](docs/competition/ARCHITECTURE.md) and run the [offline decision replay](docs/competition/DEMO_RUNBOOK.md).

## Completed competition run

| Evidence | Result |
| --- | --- |
| Candidates | NVDA, MSFT, META |
| Initial Council opinions | 9 |
| Rebuttal bundles | 3 |
| Final Judge | 1 evidence-complete adjudication |
| Judge context | 3 CandidatePackets, 9 Initial views, 3 Rebuttal bundles, 105 canonical claims, 15 computed values |
| Verdict | **WATCH** |
| B5 | Not eligible |
| Broker writes / Alpaca orders | 0 / 0 |
| Automatic paid retries | 0 |
| Known actual valid B4 production-cycle cost | $3.089588 |

The cost above is the known actual cost for the valid B4 production cycle; it is not total project spend.

## Why WATCH is a feature

The deterministic executable-investment policy did not prove positive INVEST authority. The system therefore removed INVEST from the final Judge outcome surface rather than inventing an investment threshold to force a trade. The evidence-complete Judge could select WATCH or ABSTAIN, and selected **WATCH**.

This is a successful safety outcome: the system preserved the evidence and stopped before execution when its policy could not justify executable investment authority.

## Safety architecture

- A model cannot choose executable quantity, price, or risk.
- A model cannot place a broker order.
- B5 is reachable only after INVEST, and WATCH is not B5-eligible.
- Owner approval gates paid/runtime authority.
- Raw provider responses are durably captured before local validation.
- Ambiguous or failed processing is fail-closed; blind retries are not allowed.
- Execution is PAPER-only by architecture; live money is prohibited.

## Demo

The replay is local, deterministic, zero-network, and requires no API keys:

```bash
PYTHONPATH="$PWD/src" uv run --frozen python -B scripts/demo_competition_watch_v1.py
```

It reads a tracked safe derived snapshot, not provider payloads, credentials, paid model responses, or trading authority.

## Repository evidence

- [Build evidence](docs/competition/BUILD_EVIDENCE.md)
- [Submission readiness](docs/competition/SUBMISSION_READINESS.md)
- [Demo runbook](docs/competition/DEMO_RUNBOOK.md)
- [Submission copy](docs/competition/SUBMISSION_COPY.md)

Canonical `FINAL_DECISION_V1` promotion remains blocked because the authoritative `DECISION_DRAFT_B4_v0_4.created_at` source is absent. This is a post-decision persistence/integration gap, not a final Judge failure, and it does not justify rerunning B4 or a trade.
