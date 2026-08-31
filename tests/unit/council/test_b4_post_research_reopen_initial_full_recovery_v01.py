from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from aic.council import post_research_reopen_initial_execute_production_v01 as original
from aic.council import post_research_reopen_initial_full_recovery_v01 as recovery
from aic.council import post_research_reopen_initial_production_dispatch_v01 as dispatch
from aic.domain.canonical import canonical_sha256


HEAD = "b" * 40
COST = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
HISTORICAL_LEDGER = Path(".aic-runtime/b4_post_research_reopen_initial_paid_dispatch_ledger_v0_1.json")
PREFLIGHT = Path(".aic-runtime/b4_post_research_reopen_initial_paid_failure_recovery_preflight_v0_1.json")
_DEFAULT_APPROVAL = object()


def _inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    cost = json.loads(COST.read_text(encoding="utf-8"))
    historical = json.loads(HISTORICAL_LEDGER.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    capability = original.load_context_capability()
    readiness = recovery.build_full_recovery_readiness(
        code_commit_sha=HEAD,
        cost_preflight=cost,
        context_capability=capability,
        historical_ledger=historical,
        historical_ledger_file_sha256=recovery.forensic.file_sha256(HISTORICAL_LEDGER),
        recovery_preflight=preflight,
        historical_raw_response_dir_exists=False,
        historical_raw_response_file_count=0,
        fresh_initial_result_exists=False,
    )
    approval = recovery.build_full_recovery_owner_approval(
        code_commit_sha=HEAD,
        readiness_hash=readiness["artifact_hash"],
        cost_preflight=cost,
        owner_approval_id="RECOVERY-OWNER-TEST",
        owner_approval_at_utc="2026-08-31T00:00:00Z",
    )
    return cost, historical, preflight, capability, readiness | {"approval": approval}


def _record(item) -> dict[str, str]:
    record = {"request_hash": item.request.request_hash, "candidate_id": item.candidate_id, "lane": item.lane.value}
    record["record_hash"] = canonical_sha256(record)
    return record


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, execute: bool = True, approval=_DEFAULT_APPROVAL, transport=None, process=None):
    cost, historical, preflight, capability, readiness_with_approval = _inputs()
    readiness = dict(readiness_with_approval)
    actual_approval = readiness.pop("approval")
    monkeypatch.setattr(recovery, "process_reopen_initial_provider_response", process or (lambda item, **_: _record(item)))
    monkeypatch.setattr(recovery, "actual_cost_usd", lambda *_, **__: Decimal("0.1"))
    calls: list[dict[str, object]] = []

    def factory():
        return transport or (lambda payload: calls.append(dict(payload)) or {"id": f"fresh-{len(calls)}", "provider_metric": 1.25})

    result = recovery.execute_paid_full_recovery(
        execute_paid_full_recovery=execute,
        branch=dispatch.EXPECTED_BRANCH,
        code_commit_sha=HEAD,
        worktree_clean=True,
        cost_preflight=cost,
        recovery_readiness=readiness,
        recovery_preflight=preflight,
        historical_ledger=historical,
        historical_ledger_file_sha256=recovery.forensic.file_sha256(HISTORICAL_LEDGER),
        historical_raw_response_dir_exists=False,
        historical_raw_response_file_count=0,
        fresh_initial_result_exists=False,
        approval=actual_approval if approval is _DEFAULT_APPROVAL else approval,
        context_capability=capability,
        recovery_ledger_path=tmp_path / "recovery-ledger.json",
        raw_response_dir=tmp_path / "raw",
        result_path=tmp_path / "result.json",
        transport_factory=factory,
    )
    return result, calls


def test_readiness_binds_immutable_historical_failure_and_exact_nine_frozen_recovery_requests() -> None:
    before = HISTORICAL_LEDGER.read_bytes()
    cost, historical, preflight, capability, readiness_with_approval = _inputs()
    readiness = dict(readiness_with_approval)
    readiness.pop("approval")
    assert HISTORICAL_LEDGER.read_bytes() == before
    assert historical["entries"][0]["state"] == dispatch.DISPATCH_STARTED_UNKNOWN
    assert historical["entries"][0]["request_hash"] == "02a5559a11d587ef27f74389e783b960f75bdf610f83a4e0e554504d2a07c232"
    assert recovery.verify_full_recovery_readiness(
        readiness,
        code_commit_sha=HEAD,
        cost_preflight=cost,
        context_capability=capability,
        historical_ledger=historical,
        historical_ledger_file_sha256=recovery.forensic.file_sha256(HISTORICAL_LEDGER),
        recovery_preflight=preflight,
    ) == readiness["artifact_hash"]
    assert readiness["historical_request_1_failure_classification"] == "PROVIDER_RESPONSE_RETURNED_CAPTURE_FAILED_LOCAL_SERIALIZATION"
    assert readiness["request_hashes"] == [row["request_hash"] for row in cost["initial_requests"]]
    assert readiness["recovery_kinds"] == [recovery.REPLACEMENT_KIND] + [recovery.FIRST_DISPATCH_KIND] * 8
    assert readiness["new_paid_calls_planned"] == 9
    assert readiness["total_provider_call_lineage_if_complete"] == 10
    assert readiness["total_lineage_conservative_max_usd"] == "6.362530"


def test_full_recovery_requires_special_flag_and_new_exact_approval_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    constructed = False

    def no_transport_factory():
        nonlocal constructed
        constructed = True
        return lambda _payload: {}

    cost, historical, preflight, capability, readiness_with_approval = _inputs()
    readiness = dict(readiness_with_approval)
    approval = readiness.pop("approval")
    with pytest.raises(recovery.FullInitialRecoveryError, match="--execute-paid-full-recovery"):
        recovery.execute_paid_full_recovery(
            execute_paid_full_recovery=False, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True,
            cost_preflight=cost, recovery_readiness=readiness, recovery_preflight=preflight, historical_ledger=historical,
            historical_ledger_file_sha256=recovery.forensic.file_sha256(HISTORICAL_LEDGER), historical_raw_response_dir_exists=False,
            historical_raw_response_file_count=0, fresh_initial_result_exists=False, approval=approval, context_capability=capability, recovery_ledger_path=tmp_path / "ledger",
            raw_response_dir=tmp_path / "raw", result_path=tmp_path / "result", transport_factory=no_transport_factory,
        )
    assert constructed is False
    with pytest.raises(recovery.FullInitialRecoveryError, match="owner approval"):
        _run(tmp_path, monkeypatch, approval=None)


def test_old_original_approval_cannot_authorize_recovery_and_original_executor_is_blocked_by_historical_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cost, historical, preflight, capability, readiness_with_approval = _inputs()
    readiness = dict(readiness_with_approval)
    readiness.pop("approval")
    old_approval = json.loads(Path(".aic-runtime/b4_post_research_reopen_initial_owner_approval_v0_3.json").read_text(encoding="utf-8"))
    with pytest.raises(recovery.FullInitialRecoveryError, match="owner approval drift"):
        recovery.execute_paid_full_recovery(
            execute_paid_full_recovery=True, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True,
            cost_preflight=cost, recovery_readiness=readiness, recovery_preflight=preflight, historical_ledger=historical,
            historical_ledger_file_sha256=recovery.forensic.file_sha256(HISTORICAL_LEDGER), historical_raw_response_dir_exists=False,
            historical_raw_response_file_count=0, fresh_initial_result_exists=False, approval=old_approval, context_capability=capability, recovery_ledger_path=tmp_path / "ledger",
            raw_response_dir=tmp_path / "raw", result_path=tmp_path / "result", transport_factory=lambda: pytest.fail("no transport"),
        )
    original_readiness = original.build_readiness(code_commit_sha=HEAD, cost_preflight=cost, context_capability=capability)
    original_approval = original.build_owner_approval(code_commit_sha=HEAD, readiness_hash=original_readiness["artifact_hash"], cost_preflight=cost, owner_approval_id="OWNER", owner_approval_at_utc="2026-08-31T00:00:00Z")
    with pytest.raises(original.PostResearchInitialExecutionError, match="prior dispatch ledger"):
        original.execute_paid_initial(
            execute_paid_initial=True, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True, cost_preflight=cost,
            readiness=original_readiness, approval=original_approval, context_capability=capability, ledger_path=HISTORICAL_LEDGER,
            raw_response_dir=tmp_path / "original-raw", result_path=tmp_path / "original-result", transport_factory=lambda: pytest.fail("no transport"),
        )


def test_recovery_raw_float_capture_precedes_local_validation_failure_and_prevents_blind_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def transport(_payload):
        nonlocal calls
        calls += 1
        return {"id": "fresh-float", "provider_metric": 1.25}

    with pytest.raises(recovery.FullInitialRecoveryError, match="captured recovery provider response failed validation"):
        _run(tmp_path, monkeypatch, transport=transport, process=lambda *_a, **_k: (_ for _ in ()).throw(ValueError("local schema")))
    raw_paths = list((tmp_path / "raw").glob("*.json"))
    ledger = json.loads((tmp_path / "recovery-ledger.json").read_text(encoding="utf-8"))
    assert calls == 1
    assert len(raw_paths) == 1
    capture = json.loads(raw_paths[0].read_text(encoding="utf-8"))
    assert capture["raw_response"]["provider_metric"] == 1.25
    assert original.verify_raw_response_capture(capture, request_hash=ledger["entries"][0]["original_frozen_request_hash"]) == capture["raw_response_hash"]
    assert ledger["entries"][0]["state"] == dispatch.DISPATCH_STARTED_UNKNOWN
    assert ledger["entries"][0]["recovery_kind"] == recovery.REPLACEMENT_KIND
    assert ledger["entries"][0]["stop_reason"].startswith("RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:")
    with pytest.raises(recovery.FullInitialRecoveryError, match="existing recovery dispatch evidence"):
        _run(tmp_path, monkeypatch, transport=transport)
    assert calls == 1


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_recovery_uses_external_json_contract_that_rejects_non_finite_float(non_finite: float) -> None:
    with pytest.raises(original.PostResearchInitialExecutionError, match="NaN/Infinity"):
        original.build_raw_response_capture(request_hash="a" * 64, provider_response={"bad": non_finite}, dispatch_started_at_utc="2026-08-31T00:00:00Z", captured_at_utc="2026-08-31T00:00:01Z")


def test_ambiguous_recovery_transport_stops_unknown_without_resend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def timeout(_payload):
        nonlocal calls
        calls += 1
        raise TimeoutError("ambiguous")

    with pytest.raises(recovery.FullInitialRecoveryError, match="ambiguous"):
        _run(tmp_path, monkeypatch, transport=timeout)
    ledger = json.loads((tmp_path / "recovery-ledger.json").read_text(encoding="utf-8"))
    assert calls == 1
    assert ledger["entries"][0]["state"] == dispatch.DISPATCH_STARTED_UNKNOWN
    assert ledger["entries"][0]["stop_reason"].startswith("AMBIGUOUS_PROVIDER_OUTCOME:")


def test_all_nine_fake_recovery_success_produces_fresh_initial_freeze_with_separate_lineage_costs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _run(tmp_path, monkeypatch)
    ledger = json.loads((tmp_path / "recovery-ledger.json").read_text(encoding="utf-8"))
    assert len(calls) == 9
    assert recovery.verify_full_recovery_result(result) == result["artifact_hash"]
    assert [entry["recovery_kind"] for entry in ledger["entries"]] == [recovery.REPLACEMENT_KIND] + [recovery.FIRST_DISPATCH_KIND] * 8
    assert all(entry["state"] == dispatch.COMPLETED for entry in ledger["entries"])
    assert result["historical_paid_cost_usd"] == "UNKNOWN"
    assert result["historical_paid_cost_max_usd"] == "0.636487"
    assert result["recovery_actual_cost_usd"] == "0.9"
    assert Decimal(result["recovery_actual_cost_usd"]) <= Decimal("5.726043")
    assert result["total_lineage_actual_cost_usd"] == "UNKNOWN"
    assert result["total_lineage_conservative_max_usd"] == "6.362530"
    assert result["rebuttal_authorized"] is False
    assert result["judge_authorized"] is False
    assert result["b5_handoff_created"] is False
    assert result["broker_writes"] == result["alpaca_orders"] == 0
    assert len(list((tmp_path / "raw").glob("*.json"))) == 9


def test_recovery_runner_requires_its_special_flag_and_keeps_outputs_separate() -> None:
    script = Path("scripts/b4_post_research_reopen_initial_execute_full_recovery_v01.py").read_text(encoding="utf-8")
    assert "--execute-paid-full-recovery" in script
    assert "--execute-paid-initial" not in script
    assert "recovery_paid_dispatch_ledger_v0_1.json" in script
    assert "full_recovery_council_freeze_v0_1.json" in script
