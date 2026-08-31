from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_runtime_v01 as runtime


HEAD = "a" * 40


def _preflight() -> dict:
    rows = []
    for bundle_id, template_hash in zip(runtime.BUNDLE_IDS, runtime.EXPECTED_TEMPLATE_HASHES, strict=True):
        rows.append({"bundle_id": bundle_id, "request_template_hash": template_hash})
    payload = {
        "status": runtime.preflight_v01.PASS_STATUS,
        "source_residual_external_read_plan_hash": runtime.EXPECTED_PLAN_HASH,
        "request_manifest_hash": runtime.EXPECTED_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": runtime.EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts_max": runtime.MAX_DISPATCH_ATTEMPTS,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "request_preflights": rows,
    }
    payload["artifact_hash"] = runtime.EXPECTED_PREFLIGHT_HASH
    return payload


def test_dry_binds_exact_preflight_and_has_no_authority(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "verify_preflight", lambda payload: runtime.EXPECTED_PREFLIGHT_HASH)
    dry = runtime.build_dry(preflight=_preflight(), code_commit_sha=HEAD)
    assert dry["status"] == runtime.READY_STATUS
    assert dry["source_preflight_hash"] == runtime.EXPECTED_PREFLIGHT_HASH
    assert dry["request_manifest_hash"] == runtime.EXPECTED_REQUEST_MANIFEST_HASH
    assert dry["request_template_hashes"] == list(runtime.EXPECTED_TEMPLATE_HASHES)
    assert dry["provider_dispatch_attempts_max"] == 9
    assert dry["provider_reads_authorized"] is False
    assert dry["model_calls_authorized"] is False
    assert dry["automatic_retries"] == 0
    assert dry["conditional_followup_reads_authorized"] is False
    assert dry["artifact_hash"] == canonical_sha256(dry, exclude_fields=("artifact_hash",))
    assert runtime.verify_dry(dry, expected_code_commit_sha=HEAD) == dry["artifact_hash"]


def test_authorization_requires_exact_owner_id(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "verify_preflight", lambda payload: runtime.EXPECTED_PREFLIGHT_HASH)
    dry = runtime.build_dry(preflight=_preflight(), code_commit_sha=HEAD)
    with pytest.raises(runtime.ResidualExternalReadRuntimeError, match="approval id drift"):
        runtime.build_authorization(
            dry=dry,
            owner_approval_id="WRONG",
            owner_approval_at_utc="2026-08-31T09:00:00Z",
            code_commit_sha=HEAD,
        )


def test_authorization_binds_dry_cutoff_manifest_and_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "verify_preflight", lambda payload: runtime.EXPECTED_PREFLIGHT_HASH)
    dry = runtime.build_dry(preflight=_preflight(), code_commit_sha=HEAD)
    auth = runtime.build_authorization(
        dry=dry,
        owner_approval_id="OWNER-B3-RESEARCH-REOPEN-RESIDUAL-EXTERNAL-READ-V01",
        owner_approval_at_utc="2026-08-31T09:00:00Z",
        code_commit_sha=HEAD,
    )
    assert auth["source_runner_dry_hash"] == dry["artifact_hash"]
    assert auth["source_preflight_hash"] == runtime.EXPECTED_PREFLIGHT_HASH
    assert auth["request_manifest_hash"] == runtime.EXPECTED_REQUEST_MANIFEST_HASH
    assert auth["reopen_cutoff_utc"] == runtime.EXPECTED_REOPEN_CUTOFF_UTC
    assert auth["provider_dispatch_attempts_max"] == 9
    assert auth["authority_consumption_rule"] == "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT"
    assert auth["automatic_retries"] == 0
    assert auth["conditional_followup_reads_authorized"] is False
    assert auth["model_calls_authorized"] is False
    assert auth["broker_writes_authorized"] is False
    assert auth["alpaca_orders_authorized"] is False


def test_durable_journal_writes_attempt_before_response(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = runtime.DurableJournal(path, authorization_hash="b" * 64)
    attempt_hash = journal.dispatch_attempt(bundle_id="ER4_CURRENT_PAPER_POSITIONS", request_hash="c" * 64, dispatch_index_within_bundle=1)
    response_hash = journal.response_receipt(bundle_id="ER4_CURRENT_PAPER_POSITIONS", dispatch_index_within_bundle=1, response_bytes=b"[]")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == ["PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RESPONSE_RECEIPT"]
    assert rows[0]["event_hash"] == attempt_hash
    assert rows[1]["event_hash"] == response_hash
    assert rows[0]["global_dispatch_index"] == 1


def test_dynamic_er6_binding_is_durable_before_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = runtime.DurableJournal(path, authorization_hash="b" * 64)
    final_request = {"symbols": ["MSFT", "META", "AAPL"], "timeframe": "1Hour"}
    request_hash = canonical_sha256(final_request)
    journal.binding(bundle_id="ER6_DYNAMIC_MARKET_CONTEXT", request_hash=request_hash, binding_payload=final_request)
    journal.dispatch_attempt(bundle_id="ER6_DYNAMIC_MARKET_CONTEXT", request_hash="d" * 64, dispatch_index_within_bundle=1)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == ["DYNAMIC_REQUEST_BINDING", "PROVIDER_DISPATCH_ATTEMPT"]
    assert rows[0]["request_hash"] == request_hash
    assert rows[0]["binding_payload"] == final_request


def test_durable_journal_rejects_tenth_dispatch(tmp_path: Path) -> None:
    journal = runtime.DurableJournal(tmp_path / "journal.jsonl", authorization_hash="b" * 64)
    for index in range(1, 10):
        journal.dispatch_attempt(bundle_id="ER1_NVDA_NEWS_REFRESH", request_hash=(f"{index:064x}")[-64:], dispatch_index_within_bundle=index)
    with pytest.raises(runtime.ResidualExternalReadRuntimeError, match="ceiling exceeded"):
        journal.dispatch_attempt(bundle_id="ER1_NVDA_NEWS_REFRESH", request_hash="f" * 64, dispatch_index_within_bundle=10)


def test_position_symbol_binding_is_equity_only_and_bounded() -> None:
    payload = [
        {"symbol": "AAPL", "asset_class": "us_equity"},
        {"symbol": "MSFT", "asset_class": "us_equity"},
        {"symbol": "BTCUSD", "asset_class": "crypto"},
    ]
    assert runtime._position_symbols(payload) == ["AAPL", "MSFT"]
    assert runtime._final_market_symbols(["AAPL", "MSFT"]) == ["MSFT", "META", "AAPL"]


def test_dynamic_market_binding_rejects_more_than_eighteen_additional_positions() -> None:
    symbols = [f"X{index:02d}" for index in range(19)]
    with pytest.raises(runtime.ResidualExternalReadRuntimeError, match="exceeds 20"):
        runtime._final_market_symbols(symbols)


def test_dry_verifier_rejects_call_authority_tamper(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "verify_preflight", lambda payload: runtime.EXPECTED_PREFLIGHT_HASH)
    dry = runtime.build_dry(preflight=_preflight(), code_commit_sha=HEAD)
    tampered = deepcopy(dry)
    tampered["provider_reads_authorized"] = True
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(runtime.ResidualExternalReadRuntimeError, match="cannot authorize reads"):
        runtime.verify_dry(tampered, expected_code_commit_sha=HEAD)


def test_runner_requires_explicit_execute_flag_and_contains_no_order_surface() -> None:
    source = Path("scripts/b3_research_reopen_execute_residual_external_reads_v01.py").read_text(encoding="utf-8")
    assert "--execute-provider-reads" in source
    assert "--owner-approval-id" in source
    assert "--approve-runner-dry-hash" in source
    forbidden = ("order submit", "submit_order", "position close", "close-all", "ALPACA_LIVE_TRADE=true")
    for token in forbidden:
        assert token not in source
