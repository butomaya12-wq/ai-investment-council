"""Replay the safe, derived WATCH competition snapshot without network access."""

from __future__ import annotations

import json
from pathlib import Path


SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "demo" / "competition_watch_snapshot_v1.json"


def load_snapshot() -> dict[str, object]:
    """Load and minimally validate the tracked, non-authoritative demo data."""
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    required = {"snapshot_kind": "SUBMISSION_DEMO_DERIVED_SNAPSHOT", "final_outcome": "WATCH", "next_directive": "MONITOR", "b5_handoff_eligible": False, "broker_writes": 0, "alpaca_orders": 0, "automatic_retries": 0, "live_money": "PROHIBITED", "known_actual_b4_cost_usd": "3.089588"}
    if not isinstance(snapshot, dict) or any(snapshot.get(key) != value for key, value in required.items()):
        raise SystemExit("STOP: derived competition snapshot failed safety validation")
    if snapshot.get("candidate_ids") != ["NVDA", "MSFT", "META"]:
        raise SystemExit("STOP: derived competition snapshot candidates drifted")
    return snapshot


def main() -> None:
    """Print a recordable, zero-network replay of the completed decision path."""
    snapshot = load_snapshot()
    context = snapshot["judge_context_counts"]
    print("=" * 50)
    print("AI INVESTMENT COUNCIL")
    print("Competition Decision Replay")
    print("=" * 50)
    print()
    print("UNIVERSE")
    print("3 finalists: NVDA | MSFT | META")
    print()
    print("RESEARCH")
    print("B3 evidence lifecycle: CLOSED")
    print()
    print("COUNCIL")
    print("9 independent initial opinions")
    print("Bull / Bear / Red Team x 3 candidates")
    print()
    print("CROSS-EXAMINATION")
    print("3 rebuttal bundles")
    print()
    print("JUDGE")
    print("Evidence context:")
    print(f"{context['canonical_claims']} canonical claims")
    print(f"{context['computed_values']} computed values")
    print()
    print("Executable INVEST authority: NOT PROVEN")
    print("Allowed final outcomes: WATCH | ABSTAIN")
    print()
    print("FINAL VERDICT")
    print(snapshot["final_outcome"])
    print()
    print("NEXT DIRECTIVE")
    print(snapshot["next_directive"])
    print()
    print("EXECUTION GATE")
    print("B5 eligible: NO")
    print("Broker writes: 0")
    print("Alpaca orders: 0")
    print("Live money: PROHIBITED")
    print()
    print("SAFETY")
    print("Blind paid retries: 0")
    print()
    print("PRODUCTION COST")
    print("Known actual valid B4 cycle:")
    print(f"${snapshot['known_actual_b4_cost_usd']}")
    print()
    print("=" * 50)
    print("SYSTEM STOPPED SAFELY BEFORE EXECUTION")
    print("=" * 50)


if __name__ == "__main__":
    main()
