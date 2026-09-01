# Competition Demo Runbook

This is a 2–3 minute, zero-network replay. It uses no API keys, model calls, provider reads, broker calls, or runtime authority artifacts.

## Recording commands

```bash
cd /path/to/ai-investment-council
PYTHONPATH="$PWD/src" uv run --frozen python -B scripts/demo_competition_watch_v1.py
```

Open the [architecture diagram](ARCHITECTURE.md) and keep the terminal visible for the replay.

## Timed script

### 0:00–0:20 — Problem and thesis

“Most trading agents combine research, decision, risk, and execution in one opaque loop. AI Investment Council separates those authorities so a model recommendation cannot directly become a trade.”

### 0:20–0:45 — Architecture

Show `ARCHITECTURE.md`. Explain the progression from market, SEC, and Alpaca evidence through deterministic eligibility, research, Bull/Bear/Red Team review, Rebuttal, and a bounded Judge. Point out that only INVEST could ever approach deterministic risk, human approval, and PAPER execution.

### 0:45–1:15 — Adversarial Council

Explain that the system evaluated exactly three finalists — NVDA, MSFT, and META — with 9 initial opinions across Bull, Bear, and Red Team, followed by 3 rebuttal bundles. The Judge received 105 canonical claims and 15 computed values.

### 1:15–1:45 — Terminal replay

Run the command above. Let the terminal show the Council counts, the bounded outcome surface, the WATCH verdict, and the MONITOR directive.

### 1:45–2:10 — Why WATCH is a success

“The frozen deterministic policy did not prove positive executable INVEST authority. Rather than invent a threshold to force a trade, the system removed INVEST from the final Judge surface. The evidence-complete Judge selected WATCH.”

### 2:10–2:30 — Execution boundary

Point to `B5 eligible: NO`, `Broker writes: 0`, `Alpaca orders: 0`, `Live money: PROHIBITED`, and `Blind paid retries: 0`. Explain that no order was attempted.

### 2:30–2:45 — Close

“AI Investment Council is built for investment decisions that can explain why they traded — and why they refused to trade. The next work is post-decision persistence integration, not a rerun or a trade.”

## Honest limitations to state if asked

Canonical `FINAL_DECISION_V1` promotion is blocked by missing authoritative `DECISION_DRAFT_B4_v0_4.created_at`. Monitor subscription and Decision Journal integration are not complete. These are post-decision persistence/integration gaps, not a Judge failure and not evidence for an execution attempt.
