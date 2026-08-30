from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_minimal_external_read import (
    BLOCKED_STATUS,
    EXPECTED_OWNER_APPROVAL_ID,
    EXPECTED_PREFLIGHT_HASH,
    EXPECTED_PREFLIGHT_STATUS,
    EXPECTED_PREFLIGHT_CODE_SHA,
    EXPECTED_READ_IDS,
    EXPECTED_SOURCE_EVIDENCE_PLAN_HASH,
    EXPECTED_SOURCE_LOCAL_PRIMITIVES_HASH,
    EXPECTED_SOURCE_SCOPE_HASH,
    PARTIAL_STATUS,
    SUCCESS_STATUS,
    MinimalExternalReadError,
    build_authorization_artifact,
    execute_provider_reads,
    inspect_activity_page,
    inspect_market_page,
    validate_preflight_payload,
    verify_cli_help_still_bound,
)


def _plan() -> list[dict]:
    return [
        {
            "read_id": EXPECTED_READ_IDS[0],
            "max_dispatch_attempts": 1,
        },
        {
            "read_id": EXPECTED_READ_IDS[1],
            "max_dispatch_attempts": 1,
            "page_size": 100,
            "max_pages": 1,
            "pagination_continuation_authorized": False,
            "after_exclusive": "2026-08-27T20:00:00Z",
        },
        {
            "read_id": EXPECTED_READ_IDS[2],
            "max_dispatch_attempts": 1,
        },
        {
            "read_id": EXPECTED_READ_IDS[3],
            "max_dispatch_attempts": 1,
            "symbols": ["MSFT", "META"],
            "max_pages": 1,
            "pagination_continuation_authorized": False,
            "start": "2026-08-27T19:55:00Z",
            "end": "2026-08-28T17:34:00Z",
            "feed": "iex",
            "timeframe": "1Min",
            "limit": 1000,
        },
    ]


def _preflight(*, artifact_hash: str | None = None) -> dict:
    checks = {
        "market_multi_bars": {
            "command": ["alpaca", "data", "multi-bars"],
            "help_sha256": "a" * 64,
            "required_flags": ["--symbols"],
        },
        "current_positions": {
            "command": ["alpaca", "position", "list"],
            "help_sha256": "b" * 64,
            "required_flags": [],
        },
        "account_activities": {
            "command": ["alpaca", "account", "activity", "list"],
            "help_sha256": "c" * 64,
            "required_flags": ["--after"],
        },
        "portfolio_history": {
            "command": ["alpaca", "account", "portfolio"],
            "help_sha256": "d" * 64,
            "required_flags": ["--start"],
        },
    }
    payload = {
        "artifact_version": "B3_REOPEN_MINIMAL_EXTERNAL_READ_PREFLIGHT_v0_1",
        "status": EXPECTED_PREFLIGHT_STATUS,
        "code_commit_sha": EXPECTED_PREFLIGHT_CODE_SHA,
        "source_local_primitives_hash": EXPECTED_SOURCE_LOCAL_PRIMITIVES_HASH,
        "source_evidence_plan_hash": EXPECTED_SOURCE_EVIDENCE_PLAN_HASH,
        "source_remaining_gaps_scope_hash": EXPECTED_SOURCE_SCOPE_HASH,
        "target_candidates": ["MSFT", "META"],
        "non_target_candidate_ids": ["NVDA"],
        "planned_provider_reads_max": 4,
        "provider_reads_authorized": False,
        "provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "automatic_retries": 0,
        "rerun_authorized": False,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "owner_approval_required": True,
        "next_gate": "B3_REOPEN_MINIMAL_EXTERNAL_READ_OWNER_APPROVAL",
        "provider_read_plan": _plan(),
        "alpaca_cli_path": "/fake/alpaca",
        "cli_help_checks": checks,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    if artifact_hash is not None:
        payload["artifact_hash"] = artifact_hash
    return payload


def _authority() -> dict:
    return {
        "artifact_hash": "e" * 64,
        "owner_approval_id": EXPECTED_OWNER_APPROVAL_ID,
        "approved_preflight_hash": EXPECTED_PREFLIGHT_HASH,
        "approved_provider_dispatch_attempts_max": 4,
        "automatic_retries": 0,
        "rerun_authorized": False,
    }


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 30, 18, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class _Runner:
    def __init__(
        self,
        *,
        activities: list[dict] | None = None,
        market_token: str | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        self.activities = [] if activities is None else activities
        self.market_token = market_token
        self.fail_on_call = fail_on_call
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        call_no = len(self.calls)
        if self.fail_on_call == call_no:
            return subprocess.CompletedProcess(command, 7, stdout=b"", stderr=b"failed")
        if command[1:3] == ["position", "list"]:
            raw = b'[{"symbol":"META","qty":"1"}]'
        elif command[1:4] == ["account", "activity", "list"]:
            import json

            raw = json.dumps(self.activities).encode()
        elif command[1:3] == ["account", "portfolio"]:
            raw = b'{"timestamp":[1787860800],"equity":[100000]}'
        elif command[1:3] == ["data", "multi-bars"]:
            import json

            raw = json.dumps(
                {
                    "bars": {"MSFT": [], "META": []},
                    "next_page_token": self.market_token,
                }
            ).encode()
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=raw, stderr=b"")


def _execute(tmp_path: Path, runner: _Runner):
    preflight = _preflight(artifact_hash=EXPECTED_PREFLIGHT_HASH)
    return execute_provider_reads(
        code_commit_sha="f" * 40,
        preflight=preflight,
        authorization=_authority(),
        receipt_path=tmp_path / "receipts.jsonl",
        result_path=tmp_path / "result.json",
        raw_dir=tmp_path / "raw",
        runner=runner,
        now=_Clock(),
        which=lambda _: "/fake/alpaca",
    )


def test_validate_preflight_accepts_self_hashed_contract_with_explicit_expected_hash():
    payload = _preflight()
    observed = payload["artifact_hash"]
    validated = validate_preflight_payload(payload, expected_hash=observed)
    assert validated["planned_provider_reads_max"] == 4


def test_validate_preflight_rejects_dispatch_bound_drift():
    payload = _preflight()
    payload["provider_read_plan"][1]["max_pages"] = 2
    payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    with pytest.raises(MinimalExternalReadError, match="account-activity bound drift"):
        validate_preflight_payload(payload, expected_hash=payload["artifact_hash"])


def test_verify_cli_help_rejects_hash_drift():
    preflight = _preflight()

    def inspector():
        checks = {key: dict(value) for key, value in preflight["cli_help_checks"].items()}
        checks["market_multi_bars"]["help_sha256"] = "9" * 64
        return {"alpaca_cli_path": "/fake/alpaca", "cli_help_checks": checks}

    with pytest.raises(MinimalExternalReadError, match="help changed"):
        verify_cli_help_still_bound(preflight, inspector=inspector)


def test_authorization_requires_exact_owner_id_and_preflight_hash():
    preflight = {"artifact_hash": EXPECTED_PREFLIGHT_HASH}
    artifact = build_authorization_artifact(
        code_commit_sha="1" * 40,
        preflight=preflight,
        owner_approval_id=EXPECTED_OWNER_APPROVAL_ID,
        approved_preflight_hash=EXPECTED_PREFLIGHT_HASH,
    )
    assert artifact["approved_provider_dispatch_attempts_max"] == 4
    assert artifact["automatic_retries"] == 0
    assert artifact["rerun_authorized"] is False

    with pytest.raises(MinimalExternalReadError, match="owner approval id mismatch"):
        build_authorization_artifact(
            code_commit_sha="1" * 40,
            preflight=preflight,
            owner_approval_id="WRONG",
            approved_preflight_hash=EXPECTED_PREFLIGHT_HASH,
        )


def test_activity_page_bound_is_fail_closed():
    import json

    rows = [{"activity_type": "FILL", "symbol": "META", "qty": "1"} for _ in range(100)]
    inspection = inspect_activity_page(json.dumps(rows).encode())
    assert inspection["record_count"] == 100
    assert inspection["page_bound_reached"] is True


def test_security_affecting_non_fill_is_detected():
    raw = b'[{"activity_type":"SPLIT","symbol":"META","qty":"2"}]'
    inspection = inspect_activity_page(raw)
    assert inspection["unsupported_security_affecting_non_fill_count"] == 1


def test_market_response_requires_explicit_null_page_token():
    with pytest.raises(MinimalExternalReadError, match="missing next_page_token"):
        inspect_market_page(b'{"bars":{}}')
    assert inspect_market_page(b'{"bars":{},"next_page_token":null}')["next_page_token_present"] is False


def test_success_path_uses_exactly_four_dispatches_and_no_retry(tmp_path):
    runner = _Runner()
    result = _execute(tmp_path, runner)
    assert result["status"] == SUCCESS_STATUS
    assert result["provider_dispatch_attempts"] == 4
    assert result["provider_reads"] == 4
    assert result["authorization_consumed"] is True
    assert result["gap_closed"] is False
    assert result["next_gate"] == "B3_REOPEN_MINIMAL_EXTERNAL_READ_RECONCILIATION_ZERO_CALL"
    assert len(runner.calls) == 4


def test_full_activity_page_stops_after_second_dispatch(tmp_path):
    rows = [{"activity_type": "FILL", "symbol": "META", "qty": "1"} for _ in range(100)]
    runner = _Runner(activities=rows)
    result = _execute(tmp_path, runner)
    assert result["status"] == PARTIAL_STATUS
    assert result["stop_reason"] == "ACCOUNT_ACTIVITY_PAGE_BOUND_REACHED"
    assert result["provider_dispatch_attempts"] == 2
    assert result["provider_reads"] == 2
    assert len(runner.calls) == 2


def test_unsupported_non_fill_stops_after_second_dispatch(tmp_path):
    runner = _Runner(activities=[{"activity_type": "ACATC", "symbol": "META", "qty": "1"}])
    result = _execute(tmp_path, runner)
    assert result["status"] == PARTIAL_STATUS
    assert result["stop_reason"] == "UNSUPPORTED_SECURITY_AFFECTING_NON_FILL_ACTIVITY"
    assert len(runner.calls) == 2


def test_market_pagination_token_is_partial_without_second_page(tmp_path):
    runner = _Runner(market_token="opaque-next")
    result = _execute(tmp_path, runner)
    assert result["status"] == PARTIAL_STATUS
    assert result["stop_reason"] == "MARKET_BARS_PAGINATION_NOT_COMPLETE"
    assert result["provider_dispatch_attempts"] == 4
    assert len(runner.calls) == 4


def test_provider_failure_is_blocked_and_never_retried(tmp_path):
    runner = _Runner(fail_on_call=3)
    result = _execute(tmp_path, runner)
    assert result["status"] == BLOCKED_STATUS
    assert result["provider_dispatch_attempts"] == 3
    assert result["provider_reads"] == 2
    assert "Alpaca CLI returned non-zero status" in result["stop_reason"]
    assert len(runner.calls) == 3
