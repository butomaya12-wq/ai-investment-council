from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import ast
import json
from pathlib import Path

import pytest

from aic.b5.production_readonly_v1 import (
    B5ProductionBlocked,
    create_b5_entry,
    load_recovered_b4_artifact,
    normalize_market_input,
    select_readonly_b5,
)
from aic.data.providers.alpaca_options_readonly import AlpacaOptionsReadOnlyAdapter


ROOT = Path(__file__).resolve().parents[2]
B4_ARTIFACT = ROOT / ".aic-runtime" / "b4_post_research_reopen_current_judge_captured_response_recovery_v0_1__442e8d7.json"
COMMIT = "442e8d7becaef3402d42d1cedb43d4ab8607a708"


def valid_market() -> dict[str, object]:
    return {
        "snapshot_timestamp": "2026-09-01T15:00:00Z",
        "as_of_date": "2026-09-01",
        "underlying_symbol": "NVDA",
        "account": {
            "account_equity": "100000",
            "cash_available": "80000",
            "current_same_underlying_premium_risk": "0",
            "current_aggregate_option_premium_risk": "0",
            "broker_capacity": "50000",
        },
        "option_contracts": [
            {
                "option_symbol": "NVDA261006C00200000",
                "contract_type": "CALL",
                "opening_direction": "BUY_TO_OPEN",
                "expiration": "2026-10-06",
                "strike": "200",
                "multiplier": 100,
                "bid": "2.40",
                "ask": "2.50",
                "delta": "0.50",
                "open_interest": 100,
                "active": True,
                "tradable": True,
                "greeks_present": True,
                "quote_timestamp": "2026-09-01T14:59:00Z",
                "open_interest_as_of_date": "2026-08-31",
                "open_interest_current_for_latest_completed_session": True,
            }
        ],
    }


def entry():
    return create_b5_entry(load_recovered_b4_artifact(B4_ARTIFACT), b5_code_commit_sha=COMMIT)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("artifact_hash", "0" * 64),
        lambda value: value["processed_record"].__setitem__("outcome", "WATCH"),
        lambda value: value["processed_record"].__setitem__("outcome", "ABSTAIN"),
        lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("primary_candidate_id", "AAPL"),
        lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("research_reopen_required", True),
        lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("blocking_reason_codes", ["BLOCKED"]),
        lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("execution_authority", True),
        lambda value: value.__setitem__("recovery_model_calls", 1),
    ],
)
def test_b4_authority_mutations_block(tmp_path: Path, mutate) -> None:
    payload = json.loads(B4_ARTIFACT.read_text(encoding="utf-8"))
    mutate(payload)
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B5ProductionBlocked):
        load_recovered_b4_artifact(path)


def test_b4_entry_is_deterministic_and_never_grants_authority() -> None:
    first, second = entry(), entry()
    assert first == second
    assert first.status == "B5_ENTRY_READY"
    assert first.primary_candidate_id == "NVDA"
    assert first.execution_authority is False
    assert first.broker_write_authority is False
    assert first.live_execution is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["option_contracts"][0].pop("bid"),
        lambda value: value["option_contracts"][0].pop("ask"),
        lambda value: value["option_contracts"][0].pop("delta"),
        lambda value: value["option_contracts"][0].pop("open_interest"),
        lambda value: value["option_contracts"][0].__setitem__("open_interest_current_for_latest_completed_session", False),
        lambda value: value["option_contracts"][0].__setitem__("quote_timestamp", "2026-09-01T14:58:59Z"),
        lambda value: value["option_contracts"][0].__setitem__("tradable", False),
        lambda value: value["option_contracts"][0].__setitem__("active", False),
        lambda value: value["option_contracts"][0].__setitem__("option_symbol", "bad"),
    ],
)
def test_market_normalization_rejects_incomplete_or_ambiguous_inputs(mutation) -> None:
    payload = valid_market()
    mutation(payload)
    with pytest.raises(B5ProductionBlocked):
        normalize_market_input(payload)


def test_quote_boundary_and_selection_boundaries() -> None:
    payload = valid_market()
    assert select_readonly_b5(entry(), normalize_market_input(payload)).status == "B5_READY_FOR_APPROVAL"
    payload["option_contracts"][0]["quote_timestamp"] = "2026-09-01T14:59:00Z"
    assert select_readonly_b5(entry(), normalize_market_input(payload)).status == "B5_READY_FOR_APPROVAL"
    for dte, expected in ((20, "BLOCK_INCOMPLETE_OPTION_MARKET"), (21, "B5_READY_FOR_APPROVAL"), (49, "B5_READY_FOR_APPROVAL"), (50, "BLOCK_INCOMPLETE_OPTION_MARKET")):
        candidate = valid_market()
        candidate["option_contracts"][0]["expiration"] = {20: "2026-09-21", 21: "2026-09-22", 49: "2026-10-20", 50: "2026-10-21"}[dte]
        assert select_readonly_b5(entry(), normalize_market_input(candidate)).status == expected
    for delta, expected in (("0.44", "BLOCK_INCOMPLETE_OPTION_MARKET"), ("0.45", "B5_READY_FOR_APPROVAL"), ("0.60", "B5_READY_FOR_APPROVAL"), ("0.61", "BLOCK_INCOMPLETE_OPTION_MARKET")):
        candidate = valid_market()
        candidate["option_contracts"][0]["delta"] = delta
        assert select_readonly_b5(entry(), normalize_market_input(candidate)).status == expected
    candidate = valid_market()
    candidate["option_contracts"][0].update({"bid": "2.00", "ask": "2.50"})
    assert select_readonly_b5(entry(), normalize_market_input(candidate)).status == "BLOCK_INCOMPLETE_OPTION_MARKET"
    candidate = valid_market()
    candidate["option_contracts"][0]["open_interest"] = 99
    assert select_readonly_b5(entry(), normalize_market_input(candidate)).status == "BLOCK_INCOMPLETE_OPTION_MARKET"


def test_deterministic_tie_break_and_risk_blocks() -> None:
    payload = valid_market()
    other = deepcopy(payload["option_contracts"][0])
    other["option_symbol"] = "NVDA261006C00210000"
    payload["option_contracts"].append(other)
    result = select_readonly_b5(entry(), normalize_market_input(payload))
    assert result.candidate is not None and result.candidate.option_symbol == "NVDA261006C00200000"
    for field, value in (
        ("account_equity", "1000"),
        ("current_same_underlying_premium_risk", "3001"),
        ("current_aggregate_option_premium_risk", "6001"),
        ("broker_capacity", "0"),
    ):
        candidate = valid_market()
        candidate["account"][field] = value
        result = select_readonly_b5(entry(), normalize_market_input(candidate))
        assert result.status.startswith("BLOCK_")
    candidate = valid_market()
    result = select_readonly_b5(entry(), normalize_market_input(candidate))
    assert result.candidate is not None
    assert 0 < result.candidate.quantity <= 2
    assert result.candidate.execution_authority is False
    assert result.candidate.broker_write_authority is False


def test_fake_transport_reads_only_and_no_write_capability_is_imported() -> None:
    class FakeTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        def get(self, *, path: str, query: dict[str, str]):
            self.calls.append((path, query))
            return {"fake": True}

    fake = FakeTransport()
    adapter = AlpacaOptionsReadOnlyAdapter(fake)
    adapter.read_paper_account()
    adapter.read_nvda_option_contract_metadata()
    adapter.read_nvda_option_snapshots()
    assert [path for path, _ in fake.calls] == ["/v2/account", "/v2/options/contracts", "/v1beta1/options/snapshots/NVDA"]
    adapter.normalize(
        snapshot_timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        as_of_date="2026-09-01",
        account=valid_market()["account"],
        option_contracts=valid_market()["option_contracts"],
    )
    source = (ROOT / "src" / "aic" / "data" / "providers" / "alpaca_options_readonly.py").read_text(encoding="utf-8")
    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not {"alpaca", "requests", "httpx", "urllib"} & imports
