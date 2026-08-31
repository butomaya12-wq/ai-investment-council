from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from aic.council import post_research_reopen_initial_execute_production_v01 as runtime
from aic.council import post_research_reopen_initial_production_dispatch_v01 as dispatch
from aic.domain.canonical import canonical_sha256


HEAD = "a" * 40
PREFLIGHT = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")


def _inputs():
    cost = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    capability = runtime.load_context_capability()
    readiness = runtime.build_readiness(code_commit_sha=HEAD, cost_preflight=cost, context_capability=capability)
    approval = runtime.build_owner_approval(code_commit_sha=HEAD, readiness_hash=readiness["artifact_hash"], cost_preflight=cost, owner_approval_id="OWNER-TEST", owner_approval_at_utc="2026-08-31T00:00:00Z")
    return cost, capability, readiness, approval


def _record(item):
    value = {"request_hash": item.request.request_hash, "candidate_id": item.candidate_id, "lane": item.lane.value}
    value["record_hash"] = canonical_sha256(value)
    return value


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, approval=None, readiness=None, execute=True, transport=None):
    cost, capability, actual_readiness, actual_approval = _inputs()
    readiness = actual_readiness if readiness is None else readiness
    approval = actual_approval if approval is None else approval
    monkeypatch.setattr(runtime, "process_reopen_initial_provider_response", lambda item, **_: _record(item))
    monkeypatch.setattr(runtime, "actual_cost_usd", lambda *_, **__: Decimal("0"))
    calls: list[dict] = []
    def factory():
        return transport or (lambda payload: calls.append(dict(payload)) or {"id": f"resp-{len(calls)}"})
    result = runtime.execute_paid_initial(
        execute_paid_initial=execute, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True,
        cost_preflight=cost, readiness=readiness, approval=approval, context_capability=capability,
        ledger_path=tmp_path / "ledger.json", raw_response_dir=tmp_path / "raw", result_path=tmp_path / "result.json", transport_factory=factory,
    )
    return result, calls


def test_context_record_and_readiness_bind_frozen_nine() -> None:
    cost, capability, readiness, _approval = _inputs()
    items = runtime.frozen_initial_items(cost)
    runtime.verify_context_admissibility(items, capability)
    assert len(items) == 9
    assert runtime.verify_readiness(readiness, code_commit_sha=HEAD, cost_preflight=cost, context_capability=capability) == readiness["artifact_hash"]
    assert readiness["context_admissibility"] == "PASS"


@pytest.mark.parametrize("mutation", [
    ("dispatch_readiness_artifact_hash", "0" * 64),
    ("approved_dispatch_code_commit_sha", "b" * 40),
    ("request_set_hash", "0" * 64),
    ("request_hashes", ["0" * 64] * 9),
    ("model", "gpt-5.6-sol"),
    ("reasoning_effort", "medium"),
    ("call_count_ceiling", 10),
    ("max_output_tokens_per_call", 4097),
    ("approved_max_estimated_cost_usd", "5.726044"),
])
def test_authority_mismatch_stops_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation) -> None:
    cost, capability, readiness, approval = _inputs()
    key, value = mutation
    approval[key] = value
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    called = False
    def factory():
        nonlocal called
        called = True
        return lambda _payload: {}
    monkeypatch.setattr(runtime, "process_reopen_initial_provider_response", lambda *_a, **_k: {})
    with pytest.raises(runtime.PostResearchInitialExecutionError):
        runtime.execute_paid_initial(execute_paid_initial=True, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True, cost_preflight=cost, readiness=readiness, approval=approval, context_capability=capability, ledger_path=tmp_path / "ledger", raw_response_dir=tmp_path / "raw", result_path=tmp_path / "result", transport_factory=factory)
    assert called is False


def test_no_flag_or_approval_stops_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cost, capability, readiness, _approval = _inputs()
    called = False
    def factory():
        nonlocal called
        called = True
        return lambda _payload: {}
    with pytest.raises(runtime.PostResearchInitialExecutionError):
        runtime.execute_paid_initial(execute_paid_initial=False, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True, cost_preflight=cost, readiness=readiness, approval=None, context_capability=capability, ledger_path=tmp_path / "ledger", raw_response_dir=tmp_path / "raw", result_path=tmp_path / "result", transport_factory=factory)
    assert called is False


def test_context_failure_stops_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cost, capability, readiness, approval = _inputs()
    capability = dict(capability)
    capability["context_window_tokens"] = 100
    called = False
    def factory():
        nonlocal called
        called = True
        return lambda _payload: {}
    with pytest.raises(runtime.PostResearchInitialExecutionError, match="context"):
        runtime.execute_paid_initial(execute_paid_initial=True, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True, cost_preflight=cost, readiness=readiness, approval=approval, context_capability=capability, ledger_path=tmp_path / "ledger", raw_response_dir=tmp_path / "raw", result_path=tmp_path / "result", transport_factory=factory)
    assert called is False


def test_ambiguous_transport_outcome_persists_unknown_and_never_resends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = 0
    def failing(_payload):
        nonlocal count
        count += 1
        raise TimeoutError("ambiguous")
    with pytest.raises(runtime.PostResearchInitialExecutionError, match="ambiguous"):
        _run(tmp_path, monkeypatch, transport=failing)
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert count == 1
    assert ledger["entries"][0]["state"] == dispatch.DISPATCH_STARTED_UNKNOWN
    assert not (tmp_path / "result.json").exists()


def test_raw_response_is_durable_before_local_validation_failure_and_blocks_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cost, capability, readiness, approval = _inputs()
    calls = 0
    provider_response = {"id": "resp-captured-before-validation", "status": "completed", "usage": {"input_tokens": 1}}

    def transport(_payload):
        nonlocal calls
        calls += 1
        return provider_response

    def fail_processing(*_args, **_kwargs):
        raise ValueError("synthetic local schema failure")

    monkeypatch.setattr(runtime, "process_reopen_initial_provider_response", fail_processing)
    ledger_path = tmp_path / "ledger.json"
    raw_dir = tmp_path / "raw"
    result_path = tmp_path / "result.json"
    with pytest.raises(runtime.PostResearchInitialExecutionError, match="captured provider response failed validation"):
        runtime.execute_paid_initial(execute_paid_initial=True, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True, cost_preflight=cost, readiness=readiness, approval=approval, context_capability=capability, ledger_path=ledger_path, raw_response_dir=raw_dir, result_path=result_path, transport_factory=lambda: transport)

    assert calls == 1
    raw_paths = list(raw_dir.glob("*.json"))
    assert len(raw_paths) == 1
    capture = json.loads(raw_paths[0].read_text(encoding="utf-8"))
    assert runtime.verify_raw_response_capture(capture, request_hash=cost["initial_requests"][0]["request_hash"]) == capture["raw_response_hash"]
    assert capture["raw_response"] == provider_response
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = ledger["entries"][0]
    assert entry["state"] == dispatch.DISPATCH_STARTED_UNKNOWN
    assert entry["raw_response_hash"] == capture["raw_response_hash"]
    assert entry["stop_reason"].startswith("RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:")

    resend_calls = 0
    def resend_factory():
        nonlocal resend_calls
        resend_calls += 1
        return transport
    with pytest.raises(runtime.PostResearchInitialExecutionError, match="prior dispatch ledger"):
        runtime.execute_paid_initial(execute_paid_initial=True, branch=dispatch.EXPECTED_BRANCH, code_commit_sha=HEAD, worktree_clean=True, cost_preflight=cost, readiness=readiness, approval=approval, context_capability=capability, ledger_path=ledger_path, raw_response_dir=raw_dir, result_path=result_path, transport_factory=resend_factory)
    assert calls == 1
    assert resend_calls == 0


def test_partial_or_existing_result_blocks_blind_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ledger.json").write_text("{}", encoding="utf-8")
    with pytest.raises(runtime.PostResearchInitialExecutionError, match="prior dispatch ledger"):
        _run(tmp_path, monkeypatch)


def test_all_nine_fake_success_freezes_only_initial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _run(tmp_path, monkeypatch)
    assert len(calls) == 9
    assert runtime.verify_result(result) == result["artifact_hash"]
    assert result["rebuttal_authorized"] is False
    assert result["judge_authorized"] is False
    assert result["b5_handoff_created"] is False
    assert len(list((tmp_path / "raw").glob("*.json"))) == 9


def test_historical_v01_readiness_is_not_written_by_new_runner() -> None:
    old = Path("scripts/b4_post_research_reopen_initial_production_dispatch_zero_call_v01.py").read_text(encoding="utf-8")
    paid = Path("scripts/b4_post_research_reopen_initial_execute_production_v01.py").read_text(encoding="utf-8")
    assert "production_dispatch_zero_call_preflight_v0_1.json" not in paid
    assert "--execute" not in old
    assert "--execute-paid-initial" in paid


def test_v03_readiness_runner_preserves_v02_historical_evidence() -> None:
    script = Path("scripts/b4_post_research_reopen_initial_production_dispatch_zero_call_v03.py").read_text(encoding="utf-8")
    assert "production_dispatch_zero_call_preflight_v0_2.json" in script
    assert "68d623c089ab529bb1e0d8a6892f7c67be14d8c4239e6070526d5dbc2bf68578" in script
    assert "production_dispatch_zero_call_preflight_v0_3.json" in script
