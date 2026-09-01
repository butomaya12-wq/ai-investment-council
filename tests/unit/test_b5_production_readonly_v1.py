from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
import ast
import json
from pathlib import Path

import pytest

from aic.b5.production_readonly_v1 import (
    B5ProductionBlocked,
    RecoveredB4Decision,
    create_b5_entry,
    load_recovered_b4_artifact,
    normalize_market_input,
    parse_recovered_b4_artifact,
    select_readonly_b5,
)
from aic.b5 import production_readonly_v1, runtime_readonly_v1
from aic.data.providers.alpaca_options_readonly import (
    AlpacaOptionsReadOnlyAdapter,
    ContractPages,
    PaginationReport,
    ReadSurface,
    SnapshotPages,
    derive_long_option_position_risk,
    normalize_alpaca_integer,
)
from aic.domain.canonical import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
B4_ARTIFACT = ROOT / ".aic-runtime" / "b4_post_research_reopen_current_judge_captured_response_recovery_v0_1__442e8d7.json"
COMMIT = "2c968edb3251159ccafcc46632dbcd37d448c181"
REAL_ARTIFACT_HASH = "f9a9e08a30b58ebf6fcb358c2b35a82717682ddef3ac5fd58c912d518d3fadf0"
REAL_RECORD_HASH = "e632f31b7439c3835bba20ac57af1a69b027a317a46691e285ad5c3fca915031"
REAL_JUDGE_PROPOSAL_HASH = "6cd5970a6cb56178429e4b8f148cab1ff35f6ce120784ebb5ed4e38ebf162be5"
SYNTHETIC_B4_DECISION = RecoveredB4Decision(
    REAL_ARTIFACT_HASH, REAL_RECORD_HASH, REAL_JUDGE_PROPOSAL_HASH, "NVDA"
)


def synthetic_recovered_b4_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_version": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_CAPTURED_RESPONSE_RECOVERY_v0_1",
        "artifact_hash": "",
        "status": "B4_CAPTURED_RESPONSE_RECOVERED_ZERO_CALL",
        "repaired_validation": "PASS",
        "recovery_model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "processed_record": {
            "outcome": "INVEST",
            "next_directive": "PROMOTE_FINAL_DECISION",
            "record_hash": REAL_RECORD_HASH,
            "frozen_judge_proposal": {
                "judge_proposal_hash": REAL_JUDGE_PROPOSAL_HASH,
                "draft": {
                    "outcome": "INVEST",
                    "next_directive": "PROMOTE_FINAL_DECISION",
                    "primary_candidate_id": "NVDA",
                    "research_reopen_required": False,
                    "blocking_reason_codes": [],
                    "execution_authority": False,
                },
            },
        },
    }
    payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    return payload


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
    return create_b5_entry(SYNTHETIC_B4_DECISION, b5_code_commit_sha=COMMIT)


@pytest.mark.parametrize(
    ("mutate", "recompute_hash"),
    [
        (lambda value: value.__setitem__("artifact_hash", "0" * 64), False),
        (lambda value: value["processed_record"].__setitem__("outcome", "WATCH"), True),
        (lambda value: value["processed_record"].__setitem__("outcome", "ABSTAIN"), True),
        (lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("primary_candidate_id", "AAPL"), True),
        (lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("research_reopen_required", True), True),
        (lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("blocking_reason_codes", ["BLOCKED"]), True),
        (lambda value: value["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("execution_authority", True), True),
        (lambda value: value.__setitem__("recovery_model_calls", 1), True),
    ],
)
def test_b4_authority_mutations_block(tmp_path: Path, monkeypatch, mutate, recompute_hash: bool) -> None:
    payload = synthetic_recovered_b4_payload()
    mutate(payload)
    if recompute_hash:
        payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(production_readonly_v1, "RECOVERED_B4_ARTIFACT_HASH", payload["artifact_hash"])
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(B5ProductionBlocked):
        load_recovered_b4_artifact(path)


def test_synthetic_recovered_b4_payload_exercises_parser_without_event_runtime(monkeypatch) -> None:
    payload = synthetic_recovered_b4_payload()
    monkeypatch.setattr(production_readonly_v1, "RECOVERED_B4_ARTIFACT_HASH", payload["artifact_hash"])
    assert parse_recovered_b4_artifact(payload) == RecoveredB4Decision(
        payload["artifact_hash"], REAL_RECORD_HASH, REAL_JUDGE_PROPOSAL_HASH, "NVDA"
    )


def test_event_bound_recovered_b4_authority_when_artifact_is_available() -> None:
    if not B4_ARTIFACT.is_file():
        pytest.skip("event-bound recovered B4 artifact absent in clean checkout")
    decision = load_recovered_b4_artifact(B4_ARTIFACT)
    assert decision.artifact_hash == REAL_ARTIFACT_HASH
    assert decision.primary_candidate_id == "NVDA"


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

        def get(self, *, surface: ReadSurface, path: str, query: dict[str, str]):
            self.calls.append((surface, path, query))
            return {"fake": True}

    fake = FakeTransport()
    adapter = AlpacaOptionsReadOnlyAdapter(fake)
    adapter.read_paper_account()
    adapter.read_paper_positions()
    assert [(surface, path) for surface, path, _ in fake.calls] == [
        (ReadSurface.PAPER_TRADING_API, "/v2/account"),
        (ReadSurface.PAPER_TRADING_API, "/v2/positions"),
    ]
    source = (ROOT / "src" / "aic" / "data" / "providers" / "alpaca_options_readonly.py").read_text(encoding="utf-8")
    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert not {"alpaca", "requests", "httpx", "urllib"} & imports


def account_payload() -> dict[str, str]:
    return {"equity": "100000", "cash": "80000", "options_buying_power": "50000"}


def contract(symbol: str = "NVDA261006C00200000", **changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "symbol": symbol,
        "status": "active",
        "tradable": True,
        "expiration_date": "2026-10-06",
        "underlying_symbol": "NVDA",
        "type": "call",
        "strike_price": "200",
        "size": "100",
        "open_interest": "100",
        "open_interest_date": "2026-08-31",
    }
    result.update(changes)
    return result


def snapshot(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "latestQuote": {"bp": "2.40", "ap": "2.50", "t": "2026-09-01T14:59:00Z"},
        "greeks": {"delta": "0.50"},
    }
    result.update(changes)
    return result


class QueuedFakeTransport:
    def __init__(self, responses: dict[tuple[ReadSurface, str], list[object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[ReadSurface, str, dict[str, str]]] = []

    def get(self, *, surface: ReadSurface, path: str, query: dict[str, str]) -> object:
        self.calls.append((surface, path, dict(query)))
        return self.responses[(surface, path)].pop(0)


def test_contract_queries_are_explicitly_bounded_and_paginated() -> None:
    transport = QueuedFakeTransport(
        {(ReadSurface.PAPER_TRADING_API, "/v2/options/contracts"): [
            {"option_contracts": [contract()], "next_page_token": "next"},
            {"option_contracts": [contract("NVDA261006C00210000", strike_price="210")], "next_page_token": None},
        ]}
    )
    pages = AlpacaOptionsReadOnlyAdapter(transport).read_nvda_option_contract_metadata(as_of_date=date(2026, 9, 1))
    assert pages.report.pages_read == 2 and pages.report.contracts_seen == 2 and pages.report.pagination_complete is True
    first, second = transport.calls
    assert first == (
        ReadSurface.PAPER_TRADING_API,
        "/v2/options/contracts",
        {"underlying_symbols": "NVDA", "type": "call", "status": "active", "expiration_date_gte": "2026-09-22", "expiration_date_lte": "2026-10-20", "limit": "1000"},
    )
    assert second[2]["page_token"] == "next"


def test_contract_and_snapshot_pagination_never_accept_incomplete_universe() -> None:
    contracts = QueuedFakeTransport(
        {(ReadSurface.PAPER_TRADING_API, "/v2/options/contracts"): [{"option_contracts": [contract()], "next_page_token": "next"}]}
    )
    with pytest.raises(B5ProductionBlocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        AlpacaOptionsReadOnlyAdapter(contracts).read_nvda_option_contract_metadata(as_of_date=date(2026, 9, 1), max_pages=1)
    snapshots = QueuedFakeTransport(
        {(ReadSurface.MARKET_DATA_API, "/v1beta1/options/snapshots/NVDA"): [{"snapshots": {"NVDA261006C00200000": snapshot()}, "next_page_token": "next"}]}
    )
    with pytest.raises(B5ProductionBlocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        AlpacaOptionsReadOnlyAdapter(snapshots).read_nvda_option_snapshots(max_pages=1)


def test_snapshot_pagination_uses_market_surface_and_records_completion() -> None:
    transport = QueuedFakeTransport(
        {(ReadSurface.MARKET_DATA_API, "/v1beta1/options/snapshots/NVDA"): [
            {"snapshots": {"NVDA261006C00200000": snapshot()}, "next_page_token": "next"},
            {"snapshots": {"NVDA261006C00210000": snapshot()}, "next_page_token": None},
        ]}
    )
    pages = AlpacaOptionsReadOnlyAdapter(transport).read_nvda_option_snapshots()
    assert pages.report.pages_read == 2 and pages.report.contracts_seen == 2 and pages.report.pagination_complete is True
    assert all(surface is ReadSurface.MARKET_DATA_API for surface, _, _ in transport.calls)
    assert transport.calls[0][2] == {"limit": "1000"} and transport.calls[1][2]["page_token"] == "next"


def test_positions_risk_is_truthful_and_fails_closed() -> None:
    assert derive_long_option_position_risk([]).current_aggregate_option_premium_risk == 0
    risk = derive_long_option_position_risk([
        {"asset_class": "us_option", "symbol": "NVDA261006C00200000", "side": "long", "qty": "1", "cost_basis": "250"},
        {"asset_class": "us_option", "symbol": "AAPL261006C00200000", "side": "long", "qty": "2", "cost_basis": "300"},
    ])
    assert risk.current_same_underlying_premium_risk == 250
    assert risk.current_aggregate_option_premium_risk == 550
    for position in (
        {"asset_class": "us_option", "symbol": "bad", "side": "long", "qty": "1", "cost_basis": "1"},
        {"asset_class": "us_option", "symbol": "NVDA261006C00200000", "side": "short", "qty": "1", "cost_basis": "1"},
        {"asset_class": "us_option", "symbol": "NVDA261006C00200000", "side": "long", "qty": "1", "cost_basis": "-1"},
    ):
        with pytest.raises(B5ProductionBlocked):
            derive_long_option_position_risk([position])


def test_raw_alpaca_join_skips_incomplete_contract_but_keeps_complete_second_contract() -> None:
    contract_pages = QueuedFakeTransport(
        {(ReadSurface.PAPER_TRADING_API, "/v2/options/contracts"): [{
            "option_contracts": [contract(), contract("NVDA261006C00210000", strike_price="210")], "next_page_token": None
        }]}
    )
    snapshot_pages = QueuedFakeTransport(
        {(ReadSurface.MARKET_DATA_API, "/v1beta1/options/snapshots/NVDA"): [{
            "snapshots": {
                "NVDA261006C00200000": snapshot(greeks={}),
                "NVDA261006C00210000": snapshot(),
            }, "next_page_token": None
        }]}
    )
    contract_result = AlpacaOptionsReadOnlyAdapter(contract_pages).read_nvda_option_contract_metadata(as_of_date=date(2026, 9, 1))
    snapshot_result = AlpacaOptionsReadOnlyAdapter(snapshot_pages).read_nvda_option_snapshots()
    market = AlpacaOptionsReadOnlyAdapter.normalize_market_read(
        snapshot_timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        as_of_date=date(2026, 9, 1),
        latest_completed_session_date=date(2026, 8, 31),
        account_payload=account_payload(),
        positions_payload=[],
        contract_pages=contract_result,
        snapshot_pages=snapshot_result,
    )
    selected_contract = market.contracts[0].selector_contract
    assert selected_contract.option_symbol == "NVDA261006C00210000"
    assert selected_contract.multiplier == 100 and selected_contract.open_interest == 100
    assert select_readonly_b5(entry(), market).status == "B5_READY_FOR_APPROVAL"
    incomplete_contracts = ContractPages((contract(open_interest=None),), contract_result.report)
    with pytest.raises(B5ProductionBlocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        AlpacaOptionsReadOnlyAdapter.normalize_market_read(
            snapshot_timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=UTC), as_of_date=date(2026, 9, 1), latest_completed_session_date=date(2026, 8, 31),
            account_payload=account_payload(), positions_payload=[], contract_pages=incomplete_contracts, snapshot_pages=snapshot_result,
        )


def normalize_one_documented_contract(**changes: object):
    raw_contract = contract(**changes)
    report = PaginationReport(1, 1, True)
    contract_pages = ContractPages((raw_contract,), report)
    snapshot_pages = SnapshotPages({raw_contract["symbol"]: snapshot()}, report)
    return AlpacaOptionsReadOnlyAdapter.normalize_market_read(
        snapshot_timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        as_of_date=date(2026, 9, 1),
        latest_completed_session_date=date(2026, 8, 31),
        account_payload=account_payload(),
        positions_payload=[],
        contract_pages=contract_pages,
        snapshot_pages=snapshot_pages,
    )


def test_documented_raw_integer_strings_reach_frozen_selection() -> None:
    market = normalize_one_documented_contract(size="100", open_interest="100")
    contract_value = market.contracts[0].selector_contract
    assert contract_value.multiplier == 100
    assert contract_value.open_interest == 100
    assert select_readonly_b5(entry(), market).status == "B5_READY_FOR_APPROVAL"


@pytest.mark.parametrize("raw_size", ["0", "-1", "100.0", True, None])
def test_invalid_raw_contract_size_blocks_when_it_is_the_only_contract(raw_size: object) -> None:
    with pytest.raises(B5ProductionBlocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        normalize_one_documented_contract(size=raw_size)


@pytest.mark.parametrize(
    ("raw_open_interest", "expected_status"),
    [("0", "BLOCK_INCOMPLETE_OPTION_MARKET"), ("99", "BLOCK_INCOMPLETE_OPTION_MARKET"), ("100", "B5_READY_FOR_APPROVAL")],
)
def test_raw_open_interest_is_normalized_truthfully_then_frozen_selector_applies(
    raw_open_interest: str, expected_status: str
) -> None:
    market = normalize_one_documented_contract(open_interest=raw_open_interest)
    assert market.contracts[0].selector_contract.open_interest == int(raw_open_interest)
    assert select_readonly_b5(entry(), market).status == expected_status


@pytest.mark.parametrize("raw_open_interest", ["-1", "100.0", True, None])
def test_invalid_raw_open_interest_blocks_when_it_is_the_only_contract(raw_open_interest: object) -> None:
    with pytest.raises(B5ProductionBlocked, match="BLOCK_INCOMPLETE_OPTION_MARKET"):
        normalize_one_documented_contract(open_interest=raw_open_interest)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (100, 100), ("0", 0), ("6168", 6168)],
)
def test_strict_alpaca_integer_helper_accepts_only_documented_shapes(value: object, expected: int) -> None:
    assert normalize_alpaca_integer(value, "field") == expected


@pytest.mark.parametrize("value", [True, 1.0, -1, "-1", "100.0", "+100", " 100", "", None])
def test_strict_alpaca_integer_helper_rejects_ambiguous_shapes(value: object) -> None:
    with pytest.raises(B5ProductionBlocked):
        normalize_alpaca_integer(value, "field")


def test_runtime_lineage_uses_git_head_not_parent(monkeypatch) -> None:
    feature_head = "2c968edb3251159ccafcc46632dbcd37d448c181"
    parent_head = "809a75f53b95b55acec3f942d16d502c2e480e17"
    monkeypatch.setattr(runtime_readonly_v1, "load_recovered_b4_artifact", lambda _path: SYNTHETIC_B4_DECISION)

    def fake_git_dirty(_repository: Path, *args: str) -> str:
        return " M tests/unit/test_b5_production_readonly_v1.py\n" if args[0] == "status" else feature_head + "\n"

    monkeypatch.setattr(runtime_readonly_v1, "_git", fake_git_dirty)
    with pytest.raises(B5ProductionBlocked, match="tracked-clean"):
        runtime_readonly_v1.create_entry_at_clean_expected_head(
            repository=ROOT, recovered_b4_artifact=B4_ARTIFACT, expected_commit_sha=feature_head
        )

    calls: list[tuple[str, ...]] = []

    def fake_git_clean(_repository: Path, *args: str) -> str:
        calls.append(args)
        return "" if args[0] == "status" else feature_head + "\n"

    monkeypatch.setattr(runtime_readonly_v1, "_git", fake_git_clean)
    with pytest.raises(B5ProductionBlocked, match="does not match"):
        runtime_readonly_v1.create_entry_at_clean_expected_head(
            repository=ROOT, recovered_b4_artifact=B4_ARTIFACT, expected_commit_sha=parent_head
        )
    result = runtime_readonly_v1.create_entry_at_clean_expected_head(
        repository=ROOT, recovered_b4_artifact=B4_ARTIFACT, expected_commit_sha=feature_head
    )
    assert result.b5_code_commit_sha == feature_head
    assert parent_head != result.b5_code_commit_sha
    assert calls == [
        ("status", "--porcelain=v1", "--untracked-files=no"),
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1", "--untracked-files=no"),
        ("rev-parse", "HEAD"),
    ]
