from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from aic.council import post_research_reopen_rebuttal_production_v01 as rebuttal
from aic.domain.canonical import canonical_sha256


ROOT = Path(".aic-runtime")


def _read(name: str) -> dict:
    return json.loads((ROOT / name).read_text())


@lru_cache(maxsize=1)
def _cached_inputs():
    code = "a" * 40
    freeze = _read("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    cost = _read("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    pricing = json.loads(Path("config/event/openai_text_pricing_2026_08_30.json").read_text())
    selection = _read("b4_rebuttal_selected_model_authority_v0_2.json")
    evaluation = _read("b4_rebuttal_model_eval_v0_1.json")
    receipts = [json.loads(line) for line in (ROOT / "b4_rebuttal_model_eval_paid_receipts_v0_1.jsonl").read_text().splitlines()]
    old = _read("b4_reopen_rebuttal_production_cost_preflight_zero_call_v0_2.json")
    historical = [row["request_hash"] for row in old["request_rows"]]
    preflight = rebuttal.build_current_rebuttal_preflight(code_commit_sha=code, initial_freeze=freeze, initial_cost=cost, pricing=pricing, selection_authority=selection, eval_artifact=evaluation, receipts=receipts, historical_request_hashes=historical)
    readiness = rebuttal.build_final_rebuttal_readiness(code_commit_sha=code, preflight=preflight, initial_freeze=freeze, initial_cost=cost, pricing=pricing, selection_authority=selection, eval_artifact=evaluation, receipts=receipts, historical_request_hashes=historical)
    approval = rebuttal.build_rebuttal_owner_approval(code_commit_sha=code, readiness_hash=readiness["artifact_hash"], preflight=preflight, owner_approval_id="OWNER", owner_approval_at_utc="2026-08-31T00:00:00Z")
    return freeze, cost, pricing, selection, evaluation, receipts, historical, preflight, readiness, approval


def _inputs(code: str = "a" * 40):
    assert code == "a" * 40
    return deepcopy(_cached_inputs())


def _execute(tmp_path: Path, *, approval: dict | None, sender, execute: bool = True):
    freeze, cost, pricing, selection, evaluation, receipts, historical, preflight, readiness, expected_approval = _inputs()
    return rebuttal.execute_paid_rebuttal(execute_paid_rebuttal=execute, branch="hackathon/alpaca-2026", code_commit_sha="a" * 40, worktree_clean=True, preflight=preflight, readiness=readiness, initial_freeze=freeze, initial_cost=cost, pricing=pricing, selection_authority=selection, eval_artifact=evaluation, receipts=receipts, historical_request_hashes=historical, approval=expected_approval if approval is None else approval, ledger_path=tmp_path / "ledger.json", raw_dir=tmp_path / "raw", result_path=tmp_path / "result.json", transport_factory=lambda: sender, now=lambda: datetime(2026, 8, 31, tzinfo=UTC))


def test_current_initial_freeze_and_selection_replay_build_three_new_requests() -> None:
    freeze, cost, pricing, selection, evaluation, receipts, historical, preflight, readiness, _ = _inputs()
    assert rebuttal.verify_current_initial_freeze(freeze, initial_cost=cost) == rebuttal.CURRENT_INITIAL_FREEZE_HASH
    assert rebuttal.verify_historical_rebuttal_selection_authority(selection, eval_artifact=evaluation, receipts=receipts) == rebuttal.SELECTION_HASH
    assert preflight["candidate_order"] == ["NVDA", "MSFT", "META"]
    assert len(preflight["request_hashes"]) == 3
    assert not set(preflight["request_hashes"]).intersection(historical)
    assert preflight["max_output_tokens_per_call"] == 6144
    assert preflight["historical_rebuttal_outputs_reused"] is False
    assert rebuttal.verify_final_rebuttal_readiness(readiness, code_commit_sha="a" * 40, preflight=preflight, initial_freeze=freeze, initial_cost=cost, pricing=pricing, selection_authority=selection, eval_artifact=evaluation, receipts=receipts, historical_request_hashes=historical) == readiness["artifact_hash"]


def test_current_initial_freeze_fails_closed_on_tampered_record() -> None:
    freeze, cost, *_ = _inputs()
    freeze["processed_records"][0]["lane"] = "BEAR"
    freeze["artifact_hash"] = canonical_sha256(freeze, exclude_fields=("artifact_hash",))
    with pytest.raises(Exception, match="hash drift"):
        rebuttal.verify_current_initial_freeze(freeze, initial_cost=cost)


def test_paid_flag_and_invalid_approval_fail_before_transport(tmp_path: Path) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; return {}
    with pytest.raises(Exception, match="execute-paid"):
        _execute(tmp_path, approval=None, sender=sender, execute=False)
    assert calls == 0 and not (tmp_path / "ledger.json").exists()
    *_, approval = _inputs()
    approval["new_paid_call_count"] = 2
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    with pytest.raises(Exception, match="approval"):
        _execute(tmp_path, approval=approval, sender=sender)
    assert calls == 0 and not (tmp_path / "ledger.json").exists()


def test_capture_precedes_validation_and_partial_ledger_blocks_resend(tmp_path: Path) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; return {"id": "resp-finite", "finite": 1.25}
    with pytest.raises(Exception, match="captured Rebuttal response failed validation"):
        _execute(tmp_path, approval=None, sender=sender)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    capture_path = Path(ledger["entries"][0]["raw_response_path"])
    capture = json.loads(capture_path.read_text())
    assert calls == 1 and capture_path.is_file()
    assert rebuttal.verify_rebuttal_raw_response_capture(capture, request_hash=ledger["entries"][0]["request_hash"]) == ledger["entries"][0]["raw_response_hash"]
    assert ledger["entries"][0]["state"] == "DISPATCH_STARTED_UNKNOWN"
    assert ledger["entries"][0]["stop_reason"].startswith("RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:")
    with pytest.raises(Exception, match="pre-transport gate"):
        _execute(tmp_path, approval=None, sender=sender)
    assert calls == 1


def test_ambiguous_transport_stops_without_resend(tmp_path: Path) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; raise TimeoutError("ambiguous")
    with pytest.raises(Exception, match="ambiguous Rebuttal provider outcome"):
        _execute(tmp_path, approval=None, sender=sender)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert calls == 1 and ledger["entries"][0]["state"] == "DISPATCH_STARTED_UNKNOWN"
    assert ledger["entries"][0]["stop_reason"] == "AMBIGUOUS_PROVIDER_OUTCOME:TimeoutError"


def test_fake_all_three_success_creates_current_freeze(monkeypatch, tmp_path: Path) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; return {"id": f"resp-{calls}", "finite": 1.25}
    def fake_process(item, raw, **_):
        return {"candidate_id": item.candidate_id, "request_hash": item.request.request_hash, "record_hash": canonical_sha256({"candidate": item.candidate_id, "request": item.request.request_hash})}, Decimal("0.01")
    monkeypatch.setattr(rebuttal, "_process", fake_process)
    result = _execute(tmp_path, approval=None, sender=sender)
    assert calls == 3 and result["status"] == "B4_POST_RESEARCH_REOPEN_REBUTTAL_COUNCIL_FROZEN"
    assert result["request_hashes"] == _inputs()[7]["request_hashes"]
    assert result["judge_authorized"] is False and result["b5_handoff_created"] is False
