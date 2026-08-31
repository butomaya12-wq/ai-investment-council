from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest

from aic.council import post_research_reopen_judge_current_v01 as judge
from aic.domain.canonical import canonical_sha256


ROOT = Path(".aic-runtime")


def _read(name: str) -> dict:
    return json.loads((ROOT / name).read_text())


@lru_cache(maxsize=1)
def _cached():
    code = "a" * 40
    closure = _read("b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
    initial = _read("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    cost = _read("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    rebuttal = _read("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    selection = _read("b4_judge_selected_model_authority_v0_1.json")
    evaluation = _read("b4_judge_model_eval_v0_1.json")
    receipts = [json.loads(line) for line in (ROOT / "b4_judge_model_eval_paid_receipts_v0_1.jsonl").read_text().splitlines()]
    pricing = json.loads(Path("config/event/openai_text_pricing_2026_08_30.json").read_text())
    historical = [_read("b4_reopen_judge_production_request_preflight_v0_2.json")["request_hash"]]
    entry = judge.build_current_judge_entry(code_commit_sha=code, closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal)
    preflight = judge.build_current_judge_preflight(code_commit_sha=code, closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal, entry=entry, selection=selection, eval_artifact=evaluation, receipts=receipts, pricing=pricing, historical_request_hashes=historical)
    readiness = judge.build_current_judge_readiness(code_commit_sha=code, preflight=preflight, closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal, entry=entry, selection=selection, eval_artifact=evaluation, receipts=receipts, pricing=pricing, historical_request_hashes=historical)
    approval = judge.build_judge_owner_approval(code_commit_sha=code, readiness_hash=readiness["artifact_hash"], preflight=preflight, entry=entry, owner_approval_id="OWNER", owner_approval_at_utc="2026-09-01T00:00:00Z")
    return closure, initial, cost, rebuttal, selection, evaluation, receipts, pricing, historical, entry, preflight, readiness, approval


def _inputs():
    return deepcopy(_cached())


def _execute(tmp_path: Path, *, approval: dict | None, sender, process=None, execute=True):
    closure, initial, cost, rebuttal, selection, evaluation, receipts, pricing, historical, entry, preflight, readiness, expected = _inputs()
    return judge.execute_paid_judge(execute_paid_judge=execute, branch="hackathon/alpaca-2026", code_commit_sha="a" * 40, worktree_clean=True, closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal, entry=entry, preflight=preflight, readiness=readiness, selection=selection, eval_artifact=evaluation, receipts=receipts, pricing=pricing, historical_request_hashes=historical, approval=expected if approval is None else approval, ledger_path=tmp_path / "ledger.json", raw_path=tmp_path / "raw.json", result_path=tmp_path / "result.json", transport_factory=lambda: sender, process=process, now=lambda: datetime(2026, 9, 1, tzinfo=UTC))


def test_b3_closure_and_current_lineage_parity_are_explicit() -> None:
    closure, initial, cost, rebuttal, selection, evaluation, receipts, pricing, historical, entry, preflight, readiness, _ = _inputs()
    assert judge.verify_b3_final_closure(closure) == judge.B3_HASH
    assert judge.verify_current_rebuttal_freeze(rebuttal) == judge.CURRENT_REBUTTAL_HASH
    audit = judge.build_semantic_parity_audit(closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal)
    assert audit["canonical_open_research_requirements_after_b3"] == []
    assert audit["local_lifecycle_closure_parity_bug"] is True
    assert {row["lifecycle_classification"] for row in audit["reason_rows"]} == {"CLOSED_BUT_DECISION_CONTEXT_ONLY"}
    assert entry["invest_eligible_candidates"] == ["NVDA", "META"]
    assert entry["invest_blocked_candidates"] == ["MSFT"]
    assert entry["allowed_judge_outcomes"] == ["INVEST", "WATCH", "ABSTAIN"]
    assert judge.verify_historical_judge_selection(selection, eval_artifact=evaluation, receipts=receipts) == selection["artifact_hash"]
    assert preflight["model"] == "gpt-5.6-terra" and preflight["reasoning_effort"] == "medium" and preflight["max_output_tokens"] == 8192
    assert preflight["request_hash"] not in historical
    assert judge.verify_current_judge_readiness(readiness, code_commit_sha="a" * 40, preflight=preflight, closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal, entry=entry, selection=selection, eval_artifact=evaluation, receipts=receipts, pricing=pricing, historical_request_hashes=historical) == readiness["artifact_hash"]


def test_unmapped_reason_fails_closed_before_judge() -> None:
    closure, initial, cost, rebuttal, *_ = _inputs()
    rebuttal["processed_records"][0]["research_reopen_reason_codes"] = ["BRAND_NEW_CANONICAL_REQUIREMENT"]
    rebuttal["processed_records"][0]["record_hash"] = canonical_sha256(rebuttal["processed_records"][0], exclude_fields=("record_hash",))
    rebuttal["processed_record_hashes"][0] = rebuttal["processed_records"][0]["record_hash"]
    rebuttal["artifact_hash"] = canonical_sha256(rebuttal, exclude_fields=("artifact_hash",))
    with pytest.raises(Exception, match="hash drift"):
        judge.build_semantic_parity_audit(closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal)


def test_invalid_approval_fails_before_transport(tmp_path: Path) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; return {}
    *_, approval = _inputs()
    approval["new_paid_call_count"] = 2
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    with pytest.raises(Exception, match="approval"):
        _execute(tmp_path, approval=approval, sender=sender)
    assert calls == 0 and not (tmp_path / "ledger.json").exists()


def test_raw_capture_precedes_validation_and_partial_ledger_blocks_resend(tmp_path: Path) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; return {"id": "resp-1", "finite": 1.25}
    with pytest.raises(Exception, match="captured Judge response failed validation"):
        _execute(tmp_path, approval=None, sender=sender)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    capture = json.loads((tmp_path / "raw.json").read_text())
    assert calls == 1 and ledger["entries"][0]["state"] == "DISPATCH_STARTED_UNKNOWN"
    assert ledger["entries"][0]["stop_reason"].startswith("RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:")
    assert judge.verify_judge_raw_response_capture(capture, request_hash=ledger["entries"][0]["request_hash"]) == ledger["entries"][0]["raw_response_hash"]
    with pytest.raises(Exception, match="pre-transport gate"):
        _execute(tmp_path, approval=None, sender=sender)
    assert calls == 1


@pytest.mark.parametrize(("outcome", "b5"), [("INVEST", True), ("WATCH", False), ("ABSTAIN", False)])
def test_valid_authority_reaches_transport_and_terminal_semantics(monkeypatch, tmp_path: Path, outcome: str, b5: bool) -> None:
    calls = 0
    def sender(_):
        nonlocal calls; calls += 1; return {"id": "resp-ok"}
    def process(_):
        record = {"outcome": outcome, "record_hash": canonical_sha256({"outcome": outcome})}
        return record, Decimal("0.01")
    result = _execute(tmp_path, approval=None, sender=sender, process=process)
    assert calls == 1 and result["final_b4_decision_created"] is True
    assert result["b5_handoff_eligible"] is b5 and result["b5_handoff_created"] is False
    assert result["research_reopen_created"] is False
    assert result["broker_writes"] == result["alpaca_orders"] == 0
