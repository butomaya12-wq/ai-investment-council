"""Run the offline Competition V1-only B4 -> B5 -> B6 test fixture slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aic.b5.competition_v1 import TEST_MODE, build_option_intent, load_test_fixture
from aic.b6.competition_v1 import FakePaperTransport, create_test_approval, send_after_commit


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "vertical_slice"


def _json(name: str) -> dict[str, object]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"STOP: fixture {name} is not an object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("clean", "drift-block"), default="clean")
    args = parser.parse_args()
    b4 = load_test_fixture(FIXTURES / "b4_invest_fixture_v1.json", mode=TEST_MODE)
    chain = _json("option_chain_v1.json")
    commit_fixture = _json("b6_commit_state_v1.json")
    intent = build_option_intent(b4, chain, mode=TEST_MODE)
    approval = create_test_approval(intent, mode=TEST_MODE)
    state_name = "clean" if args.scenario == "clean" else "drift_block"
    state = commit_fixture[state_name]
    if not isinstance(state, dict):
        raise SystemExit("STOP: commit-state fixture drift")
    transport = FakePaperTransport()
    revalidation, receipt = send_after_commit(intent, approval, state, transport, mode=TEST_MODE)

    print("AI INVESTMENT COUNCIL")
    print("COMPETITION V1 VERTICAL SLICE")
    print()
    print("B4 INPUT")
    print("TEST_FIXTURE INVEST")
    print("production authority: NO")
    print()
    print("B5 OPTION SELECTION")
    print(f"selected: {intent.option_symbol}")
    print(f"DTE: {intent.dte}")
    print(f"delta: {intent.delta}")
    print(f"spread: {intent.relative_spread}")
    print(f"OI: {intent.open_interest}")
    print()
    print("B5 RISK")
    print(intent.risk_status)
    print(f"qty: {intent.quantity}")
    print(f"max loss: {intent.max_loss_usd}")
    print(f"premium risk: {intent.premium_risk_after}")
    print()
    print("B6 APPROVAL")
    print("TEST FIXTURE APPROVAL: VALID")
    print()
    print("B6 COMMIT REVALIDATION")
    print("PASS" if revalidation.state == "READY_FOR_PAPER_SEND" else "BLOCK_COMMIT_REVALIDATION")
    print()
    print("PAPER ORDER GATE")
    print(revalidation.state)
    print()
    print("BROKER TRANSPORT")
    print("FAKE / NOT SENT" if receipt is None else "FAKE / WOULD_SUBMIT_PAPER_ORDER")
    print()
    print(f"BROKER WRITES: {receipt['broker_writes'] if receipt else 0}")
    print(f"ALPACA ORDERS: {receipt['alpaca_orders'] if receipt else 0}")
    print("LIVE MONEY: PROHIBITED")


if __name__ == "__main__":
    main()
