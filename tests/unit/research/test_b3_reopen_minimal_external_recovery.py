from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_minimal_external_recovery as recovery
from aic.research.reopen_minimal_external_recovery import (
    MinimalExternalRecoveryError,
    build_recovery_artifact,
    reconstruct_meta_quantity,
    recover_terminal_market_page,
    select_market_close,
    select_portfolio_equity,
)


def test_empty_string_market_token_is_recovered_as_cli_terminal_zero_value():
    observed = recover_terminal_market_page({"bars": {}, "next_page_token": ""})
    assert observed["terminal_page_recovered"] is True
    assert observed["pagination_continuation_required"] is False
    assert observed["provider_rerun_required"] is False
    assert observed["observed_next_page_token_representation"] == "EMPTY_STRING"


def test_nonempty_market_token_remains_fail_closed():
    with pytest.raises(MinimalExternalRecoveryError, match="real next page token"):
        recover_terminal_market_page({"bars": {}, "next_page_token": "opaque-next"})


def test_null_market_token_cannot_explain_historical_block():
    with pytest.raises(MinimalExternalRecoveryError, match="would not have rejected null"):
        recover_terminal_market_page({"bars": {}, "next_page_token": None})


def test_reverse_reconstruction_uses_current_position_only_as_anchor():
    observed = reconstruct_meta_quantity(
        positions_payload=[],
        activities_payload=[
            {
                "activity_type": "FILL",
                "symbol": "META",
                "qty": "2",
                "side": "sell",
                "transaction_time": "2026-08-28T15:00:00Z",
            }
        ],
        anchor_utc="2026-08-30T18:30:44.589479Z",
    )
    assert observed["current_meta_quantity"] == "0"
    assert observed["reconstructed_meta_quantity_at_b2_cutoff"] == "2"
    assert observed["post_cutoff_meta_fill_count"] == 1
    assert observed["quantity_reconstruction_complete"] is True


def test_reverse_reconstruction_rejects_fill_outside_approved_window():
    with pytest.raises(MinimalExternalRecoveryError, match="outside approved reconstruction window"):
        reconstruct_meta_quantity(
            positions_payload=[],
            activities_payload=[
                {
                    "activity_type": "FILL",
                    "symbol": "META",
                    "qty": "1",
                    "side": "sell",
                    "transaction_time": "2026-08-27T19:59:00Z",
                }
            ],
            anchor_utc="2026-08-30T18:30:44Z",
        )


def test_portfolio_equity_selects_latest_datapoint_not_after_b2_cutoff():
    observed = select_portfolio_equity(
        {
            "timestamp": [
                "2026-08-27T19:58:00Z",
                "2026-08-27T20:00:00Z",
                "2026-08-27T20:01:00Z",
            ],
            "equity": ["99000", "100000", "100100"],
        }
    )
    assert observed["selected_equity"] == "100000"
    assert observed["selected_equity_timestamp_utc"] == "2026-08-27T20:00:00Z"


def test_market_close_selects_latest_completed_bar_at_or_before_cutoff():
    observed = select_market_close(
        {
            "bars": {
                "MSFT": [
                    {"t": "2026-08-28T17:32:00Z", "c": "509.5"},
                    {"t": "2026-08-28T17:33:00Z", "c": "510.25"},
                    {"t": "2026-08-28T17:34:00Z", "c": "511"},
                ]
            },
            "next_page_token": "",
        },
        symbol="MSFT",
        cutoff_utc="2026-08-28T17:33:00Z",
    )
    assert observed["close"] == "510.25"
    assert observed["bar_timestamp_utc"] == "2026-08-28T17:33:00Z"


def _write_json(path: Path, payload) -> bytes:
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return raw


def _with_hash(payload: dict, field: str = "artifact_hash") -> dict:
    value = dict(payload)
    value[field] = canonical_sha256(value)
    return value


def _build_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_payloads = {
        recovery.READ_IDS[0]: [],
        recovery.READ_IDS[1]: [
            {
                "activity_type": "FILL",
                "symbol": "META",
                "qty": "2",
                "side": "sell",
                "transaction_time": "2026-08-28T15:00:00Z",
            }
        ],
        recovery.READ_IDS[2]: {
            "timestamp": ["2026-08-27T19:59:00Z", "2026-08-27T20:00:00Z"],
            "equity": ["99900", "100000"],
        },
        recovery.READ_IDS[3]: {
            "bars": {
                "MSFT": [
                    {"t": "2026-08-28T17:32:00Z", "c": "509"},
                    {"t": "2026-08-28T17:33:00Z", "c": "510"},
                ],
                "META": [
                    {"t": "2026-08-27T19:59:00Z", "c": "500"},
                    {"t": "2026-08-28T17:33:00Z", "c": "520"},
                ],
            },
            "next_page_token": "",
        },
    }
    raw_hashes: dict[str, str] = {}
    raw_bytes: dict[str, int] = {}
    for read_id, payload in raw_payloads.items():
        raw = _write_json(raw_dir / f"{read_id}.json", payload)
        raw_hashes[read_id] = hashlib.sha256(raw).hexdigest()
        raw_bytes[read_id] = len(raw)
    monkeypatch.setattr(recovery, "EXPECTED_RAW_HASHES", raw_hashes)
    monkeypatch.setattr(recovery, "EXPECTED_RAW_BYTES", raw_bytes)

    preflight_hash = "a" * 64
    owner_id = "OWNER-TEST"
    capture_sha = "1" * 40
    monkeypatch.setattr(recovery, "EXPECTED_PREFLIGHT_HASH", preflight_hash)
    monkeypatch.setattr(recovery, "EXPECTED_OWNER_APPROVAL_ID", owner_id)
    monkeypatch.setattr(recovery, "EXPECTED_CAPTURE_CODE_SHA", capture_sha)

    authorization = _with_hash(
        {
            "owner_approval_id": owner_id,
            "approved_preflight_hash": preflight_hash,
            "approved_provider_dispatch_attempts_max": 4,
            "automatic_retries": 0,
            "rerun_authorized": False,
        }
    )
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps(authorization), encoding="utf-8")
    monkeypatch.setattr(recovery, "EXPECTED_AUTHORIZATION_HASH", authorization["artifact_hash"])

    events: list[dict] = []
    receipt_hashes: list[str] = []
    for attempt, read_id in enumerate(recovery.READ_IDS, start=1):
        for event_name in ("PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RESPONSE_RECEIVED"):
            event = {
                "event": event_name,
                "read_id": read_id,
                "global_dispatch_attempt": attempt,
            }
            if event_name == "PROVIDER_RESPONSE_RECEIVED":
                event["stdout_sha256"] = raw_hashes[read_id]
            event["receipt_hash"] = canonical_sha256(event)
            events.append(event)
            receipt_hashes.append(event["receipt_hash"])
    receipts_path = tmp_path / "receipts.jsonl"
    receipts_path.write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
    receipt_manifest = canonical_sha256({"receipt_hashes": receipt_hashes})
    monkeypatch.setattr(recovery, "EXPECTED_RECEIPT_MANIFEST_HASH", receipt_manifest)

    captures = []
    response_times = [
        "2026-08-30T18:30:44.589479Z",
        "2026-08-30T18:30:45.286513Z",
        "2026-08-30T18:30:45.995546Z",
        "2026-08-30T18:30:46.924134Z",
    ]
    for read_id, response_time in zip(recovery.READ_IDS, response_times, strict=True):
        captures.append(
            {
                "read_id": read_id,
                "raw_path": str(raw_dir / f"{read_id}.json"),
                "stdout_sha256": raw_hashes[read_id],
                "stdout_bytes": raw_bytes[read_id],
                "response_received_at_utc": response_time,
            }
        )
    result = _with_hash(
        {
            "status": recovery.EXPECTED_BLOCKED_STATUS,
            "stop_reason": recovery.EXPECTED_STOP_REASON,
            "code_commit_sha": capture_sha,
            "source_authorization_hash": authorization["artifact_hash"],
            "source_preflight_hash": preflight_hash,
            "receipt_manifest_hash": receipt_manifest,
            "receipt_hashes": receipt_hashes,
            "provider_dispatch_attempts": 4,
            "provider_reads": 4,
            "authorization_consumed": True,
            "rerun_authorized": False,
            "model_calls": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "captures": captures,
        }
    )
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(recovery, "EXPECTED_BLOCKED_RESULT_HASH", result["artifact_hash"])
    return raw_dir, result_path, auth_path, receipts_path


def test_full_zero_call_recovery_reuses_four_captured_responses_and_computes_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    raw_dir, result_path, auth_path, receipts_path = _build_fixture(tmp_path, monkeypatch)
    artifact = build_recovery_artifact(
        code_commit_sha="2" * 40,
        blocked_result_path=result_path,
        authorization_path=auth_path,
        receipts_path=receipts_path,
        raw_dir=raw_dir,
    )
    assert artifact["status"] == recovery.PASS_STATUS
    assert artifact["new_provider_reads"] == 0
    assert artifact["reused_provider_responses"] == 4
    assert artifact["pagination_recovery"]["terminal_page_recovered"] is True
    assert artifact["valuation_recovery"]["MSFT"]["price"]["close"] == "510"
    assert artifact["valuation_recovery"]["META"]["price"]["close"] == "520"
    assert artifact["portfolio_recovery"]["reconstructed_meta_quantity_at_b2_cutoff"] == "2"
    assert artifact["portfolio_recovery"]["reconstructed_meta_market_value_at_b2_cutoff"] == "1000"
    assert artifact["portfolio_recovery"]["reconstructed_meta_portfolio_weight"] == "0.010000000000"
    assert artifact["portfolio_recovery"]["portfolio_interaction_evidence_complete"] is True
    assert artifact["next_gate"] == recovery.NEXT_GATE


def test_full_recovery_rejects_tampered_raw_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    raw_dir, result_path, auth_path, receipts_path = _build_fixture(tmp_path, monkeypatch)
    target = raw_dir / f"{recovery.READ_IDS[3]}.json"
    target.write_text('{"bars":{},"next_page_token":""}\n', encoding="utf-8")
    with pytest.raises(MinimalExternalRecoveryError, match="raw byte-size mismatch|raw SHA256 mismatch"):
        build_recovery_artifact(
            code_commit_sha="2" * 40,
            blocked_result_path=result_path,
            authorization_path=auth_path,
            receipts_path=receipts_path,
            raw_dir=raw_dir,
        )


def test_runner_has_no_alpaca_or_model_execution_surface():
    source = Path("scripts/b3_reopen_minimal_external_read_recovery_zero_call_v01.py").read_text(encoding="utf-8")
    assert "execute-provider-reads" not in source
    assert '"alpaca"' not in source
    assert "OPENAI_API_KEY" not in source
    assert "submit_order" not in source
