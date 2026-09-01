"""Regression tests for the offline, non-canonical Competition V1 vertical slice."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from aic.b5.competition_v1 import (
    B5Blocked,
    TEST_MODE,
    build_option_intent,
    calculate_premium_risk,
    eligible_contracts,
    load_test_fixture,
    parse_contracts,
    relative_spread_from_quote,
    select_contract,
)
from aic.b6.competition_v1 import (
    B6Blocked,
    FakePaperTransport,
    APPROVAL_STATE_PATH,
    commit_revalidate,
    create_test_approval,
    send_after_commit,
    validate_approval,
)
from aic.domain.contracts import FINAL_DECISION_V1


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vertical_slice"
HISTORICAL_SNAPSHOT = ROOT / "demo" / "competition_watch_snapshot_v1.json"
HISTORICAL_JUDGE = ROOT / ".aic-runtime" / "b4_post_research_reopen_current_judge_council_freeze_v0_3.json"


def read_json(name: str) -> dict[str, object]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def setup_intent():
    fixture = load_test_fixture(FIXTURES / "b4_invest_fixture_v1.json", mode=TEST_MODE)
    chain = read_json("option_chain_v1.json")
    return fixture, chain, build_option_intent(fixture, chain, mode=TEST_MODE)


def test_b4_test_fixture_is_structurally_valid_but_refuses_production_mode() -> None:
    raw = read_json("b4_invest_fixture_v1.json")
    FINAL_DECISION_V1.model_validate(raw["final_decision"])
    assert load_test_fixture(FIXTURES / "b4_invest_fixture_v1.json", mode=TEST_MODE)["execution_authority"] is False
    with pytest.raises(B5Blocked, match="TEST_FIXTURE"):
        load_test_fixture(FIXTURES / "b4_invest_fixture_v1.json", mode="PRODUCTION")


def test_historical_watch_is_immutable_and_never_enters_b5() -> None:
    before = HISTORICAL_JUDGE.read_bytes()
    snapshot = json.loads(HISTORICAL_SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot["final_outcome"] == "WATCH"
    assert snapshot["b5_handoff_eligible"] is False
    assert snapshot["broker_writes"] == 0
    assert snapshot["alpaca_orders"] == 0
    fixture, chain, _ = setup_intent()
    fixture["final_decision"]["outcome"] = "WATCH"
    with pytest.raises(B5Blocked, match="INVEST"):
        build_option_intent(fixture, chain, mode=TEST_MODE)
    assert HISTORICAL_JUDGE.read_bytes() == before


def test_selector_is_decimal_deterministic_and_filters_every_invalid_contract() -> None:
    _, chain, intent = setup_intent()
    as_of, contracts = parse_contracts(chain)
    assert intent.option_symbol == "AAPL261006C00200000"
    assert intent.dte == 35 and intent.delta == Decimal("0.50")
    assert intent.relative_spread == Decimal("2") / Decimal("49")
    assert [item.option_symbol for item in eligible_contracts(contracts, as_of=as_of)] == [intent.option_symbol]
    assert select_contract(list(reversed(contracts)), as_of=as_of) == select_contract(contracts, as_of=as_of)
    for mutation in (
        replace(contracts[0], expiration=date(2026, 9, 20)),
        replace(contracts[0], delta=Decimal("0.40")),
        replace(contracts[0], bid=Decimal("2.00"), ask=Decimal("2.50")),
        replace(contracts[0], open_interest=99),
        replace(contracts[0], quote_age_seconds=61),
        replace(contracts[0], open_interest_current_for_latest_completed_session=False),
    ):
        with pytest.raises(B5Blocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
            select_contract([mutation], as_of=as_of)


def test_midpoint_spread_and_selection_freshness_are_fail_closed() -> None:
    _, chain, _ = setup_intent()
    as_of, contracts = parse_contracts(chain)
    assert relative_spread_from_quote(Decimal("95.10"), Decimal("105")) < Decimal("0.10")
    assert relative_spread_from_quote(Decimal("94.90"), Decimal("105")) > Decimal("0.10")
    assert select_contract([replace(contracts[0], bid=Decimal("95.10"), ask=Decimal("105"))], as_of=as_of).option_symbol == contracts[0].option_symbol
    with pytest.raises(B5Blocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        select_contract([replace(contracts[0], bid=Decimal("94.90"), ask=Decimal("105"))], as_of=as_of)
    raw = json.loads(json.dumps(chain))
    raw["contracts"][0].pop("open_interest_current_for_latest_completed_session")
    _, missing_freshness = parse_contracts(raw)
    with pytest.raises(B5Blocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        select_contract([missing_freshness[0]], as_of=as_of)


def test_sizing_blocks_insufficient_budget_and_cap_excess() -> None:
    fixture, chain, intent = setup_intent()
    assert intent.quantity == 2 and intent.max_loss_usd == Decimal("500.00")
    too_small = json.loads(json.dumps(chain))
    too_small["account"]["account_equity"] = "1000"
    too_small["account"]["cash_available"] = "600"
    with pytest.raises(B5Blocked, match="BLOCK_INSUFFICIENT_RISK_BUDGET"):
        build_option_intent(fixture, too_small, mode=TEST_MODE)
    cap_exceeded = json.loads(json.dumps(chain))
    cap_exceeded["account"]["current_same_underlying_premium_risk"] = "601"
    with pytest.raises(B5Blocked, match="BLOCK_RISK_CAP_EXCEEDED"):
        build_option_intent(fixture, cap_exceeded, mode=TEST_MODE)


def test_approval_exact_binding_and_commit_revalidation_drift_block() -> None:
    _, _, intent = setup_intent()
    approval = create_test_approval(intent, mode=TEST_MODE)
    assert APPROVAL_STATE_PATH == (
        "PROPOSED", "APPROVAL_REQUIRED", "APPROVED", "COMMIT_REVALIDATION", "READY_FOR_PAPER_SEND"
    )
    validate_approval(intent, approval, mode=TEST_MODE)
    for changed_intent in (
        replace(intent, option_symbol="AAPL261006C00210000"),
        replace(intent, quantity=1),
        replace(intent, approved_limit_price=Decimal("2.55")),
        replace(intent, final_decision_hash="a" * 64),
        replace(intent, options_policy_hash="b" * 64),
    ):
        with pytest.raises(B6Blocked, match="exact option intent"):
            validate_approval(changed_intent, approval, mode=TEST_MODE)
    with pytest.raises(B6Blocked, match="TEST_OWNER_APPROVAL"):
        create_test_approval(intent, mode="PRODUCTION")
    states = read_json("b6_commit_state_v1.json")
    clean = states["clean"]
    assert commit_revalidate(intent, approval, clean, mode=TEST_MODE).state == "READY_FOR_PAPER_SEND"
    risk = calculate_premium_risk(clean, intent.premium_per_contract)
    assert risk.status == "PASS" and risk.quantity == 2
    one_qty = dict(clean, account_equity="10000", cash_available="6000", risk_status="PASS")
    zero_qty = dict(clean, account_equity="1000", cash_available="600", risk_status="PASS")
    assert commit_revalidate(intent, approval, one_qty, mode=TEST_MODE).state == "BLOCK_COMMIT_REVALIDATION"
    assert commit_revalidate(intent, approval, zero_qty, mode=TEST_MODE).state == "BLOCK_COMMIT_REVALIDATION"
    assert commit_revalidate(intent, approval, dict(clean, quote_age_seconds=16), mode=TEST_MODE).state == "BLOCK_COMMIT_REVALIDATION"
    assert commit_revalidate(intent, approval, dict(clean, bid="2.25", ask="2.50"), mode=TEST_MODE).state == "BLOCK_COMMIT_REVALIDATION"
    transport = FakePaperTransport()
    blocked, receipt = send_after_commit(intent, approval, states["drift_block"], transport, mode=TEST_MODE)
    assert blocked.state == "BLOCK_COMMIT_REVALIDATION" and receipt is None and transport.calls == 0
    ready, receipt = send_after_commit(intent, approval, clean, transport, mode=TEST_MODE)
    assert ready.state == "READY_FOR_PAPER_SEND" and receipt is not None and transport.calls == 1
    assert receipt["status"] == "WOULD_SUBMIT_PAPER_ORDER"
    assert receipt["broker_writes"] == 0 and receipt["alpaca_orders"] == 0


def test_vertical_slice_has_no_real_broker_or_network_imports() -> None:
    modules = [ROOT / "src" / "aic" / "b5" / "competition_v1.py", ROOT / "src" / "aic" / "b6" / "competition_v1.py"]
    imported = set()
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported.update(alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        imported.update(node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
    assert not {"alpaca", "requests", "httpx", "urllib", "openai"} & imported


def test_golden_runner_clean_and_drift_are_offline_and_safe() -> None:
    runner = ROOT / "scripts" / "run_vertical_slice_v1.py"
    clean = subprocess.run([sys.executable, str(runner)], cwd=ROOT, check=False, capture_output=True, text=True)
    drift = subprocess.run([sys.executable, str(runner), "--scenario", "drift-block"], cwd=ROOT, check=False, capture_output=True, text=True)
    assert clean.returncode == 0 and drift.returncode == 0
    assert "READY_FOR_PAPER_SEND" in clean.stdout
    assert "FAKE / WOULD_SUBMIT_PAPER_ORDER" in clean.stdout
    assert "BLOCK_COMMIT_REVALIDATION" in drift.stdout
    assert "FAKE / NOT SENT" in drift.stdout
    assert "BROKER WRITES: 0" in clean.stdout and "ALPACA ORDERS: 0" in clean.stdout
