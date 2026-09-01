# Submission Copy

## PROJECT NAME

AI Investment Council

## ONE-LINE PITCH

An evidence-grounded AI investment council that separates research, adversarial reasoning, decision authority, deterministic risk, human approval, and PAPER broker execution.

## PROBLEM

Many autonomous trading agents place research, decision, risk, and execution inside one opaque model loop. That makes it difficult to challenge a thesis, explain a result, or stop a plausible model output from becoming a trade.

## SOLUTION

AI Investment Council makes each authority explicit. Deterministic evidence and eligibility rules precede a Bull/Bear/Red Team Council and cross-examination. A bounded Judge selects only an allowed outcome. Deterministic risk, explicit human approval, and PAPER execution remain downstream gates, not model choices.

## HOW IT WORKS

Market, SEC, and Alpaca evidence enters a deterministic eligibility/evidence layer. The system selects exactly three candidates, synthesizes research, obtains Bull/Bear/Red Team opinions, runs Rebuttal, and gives a bounded Judge an evidence-complete context. Only INVEST could approach deterministic options/risk, explicit owner approval, and Alpaca PAPER execution; WATCH and ABSTAIN stop before execution.

## ALPACA INTEGRATION

Alpaca is the market-data and broker boundary in the architecture. Its broker interface remains behind deterministic risk and explicit human approval gates. The completed competition run produced no broker writes and no Alpaca orders.

## WHAT MAKES IT DIFFERENT

The product is designed to preserve a defensible refusal. In the completed run, the frozen policy did not justify positive executable INVEST authority. Instead of inventing a threshold to force a trade, the system removed INVEST from the Judge’s allowed outcomes. The evidence-complete Judge selected WATCH and the system stopped safely before B5.

## SAFETY

Models cannot select executable quantity, price, or risk, and cannot place broker orders. Raw provider responses are captured before local validation, ambiguous states fail closed, blind paid retries are prohibited, execution is PAPER-only, and live money is prohibited.

## COMPLETED DEMO RESULT

The Council evaluated NVDA, MSFT, and META using 9 initial opinions, 3 rebuttal bundles, 105 canonical claims, and 15 computed values. The final evidence-complete Judge verdict was WATCH with MONITOR as the next directive. B5 was not eligible; broker writes and Alpaca orders were both 0. Known actual valid B4 production-cycle cost was $3.089588, not total project spend.

## TECH STACK

Python, Pydantic contracts, JSON Schema, canonical hashing, deterministic policy checks, pytest, Mermaid, and the Alpaca market-data/broker boundary.

## CURRENT LIMITATIONS

Canonical `FINAL_DECISION_V1` promotion is blocked because authoritative `DECISION_DRAFT_B4_v0_4.created_at` is absent. Canonical monitor subscription and Decision Journal append are therefore not complete. These are post-decision persistence/integration gaps, not failures of the final Judge, reasons to rerun B4, or evidence that a trade should occur.

## FUTURE WORK

Resolve the authoritative post-decision persistence contract, complete monitored WATCH integration, and continue validating deterministic options/risk and human-approved PAPER execution without weakening the authority boundaries.
