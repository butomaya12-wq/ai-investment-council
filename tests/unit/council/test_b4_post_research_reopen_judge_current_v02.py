from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from aic.council import post_research_reopen_judge_current_v02 as judge
from aic.domain.canonical import canonical_sha256
from aic.council.proposal import JudgeNextDirective, JudgeOutcome


ROOT = Path(".aic-runtime")
CODE = "a" * 40


def _read(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _cached() -> tuple:
    closure = _read("b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
    residual = _read("b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json")
    gaps = _read("b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
    initial = _read("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    cost = _read("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    rebuttal = _read("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    selection = _read("b4_judge_selected_model_authority_v0_1.json")
    evaluation = _read("b4_judge_model_eval_v0_1.json")
    receipts = [json.loads(line) for line in (ROOT / "b4_judge_model_eval_paid_receipts_v0_1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    pricing = json.loads(Path("config/event/openai_text_pricing_2026_08_30.json").read_text(encoding="utf-8"))
    historical = [_read("b4_reopen_judge_production_request_preflight_v0_2.json")["request_hash"]]
    entry = judge.build_entry(code_commit_sha=CODE, closure=closure, residual_plan=residual, remaining_gaps_closure=gaps, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal)
    inputs = {"closure": closure, "residual_plan": residual, "remaining_gaps_closure": gaps, "initial_freeze": initial, "initial_cost": cost, "rebuttal_freeze": rebuttal, "entry": entry, "selection": selection, "eval_artifact": evaluation, "receipts": receipts, "pricing": pricing, "historical_request_hashes": historical}
    preflight = judge.build_preflight(code_commit_sha=CODE, **inputs)
    readiness = judge.build_readiness(code_commit_sha=CODE, preflight=preflight, **inputs)
    approval = judge.build_owner_approval(code_commit_sha=CODE, readiness_hash=readiness["artifact_hash"], preflight=preflight, entry=entry, owner_approval_id="TEST_OWNER", owner_approval_at_utc="2026-09-01T00:00:00Z")
    return inputs, preflight, readiness, approval


def _values() -> tuple[dict, dict, dict, dict]:
    inputs, preflight, readiness, approval = deepcopy(_cached())
    return inputs, preflight, readiness, approval


def _proposal(outcome: JudgeOutcome, request) -> SimpleNamespace:
    model_input = json.loads(request.request_payload["input"])["model_input"]
    return SimpleNamespace(
        outcome=outcome,
        next_directive=JudgeNextDirective.MONITOR if outcome == JudgeOutcome.WATCH else JudgeNextDirective.STOP,
        judge_input_hash=request.input_hash,
        mandate_version=model_input["mandate_version"],
        deep_comparison_id=model_input["deep_comparison_id"],
        council_policy_version=model_input["council_policy_version"],
        judge_policy_version=model_input["judge_policy_version"],
        model_policy_version=model_input["model_policy_version"],
        model_run_ref=judge.MODEL_RUN_REF,
        execution_authority=False,
        research_reopen_required=False,
        research_reopen_reason_codes=(),
    )


def _execute(tmp_path: Path, *, approval: dict | None, sender):
    inputs, preflight, readiness, expected_approval = _values()
    return judge.execute_paid(
        execute_paid_judge=True,
        branch="hackathon/alpaca-2026",
        code_commit_sha=CODE,
        worktree_clean=True,
        preflight=preflight,
        readiness=readiness,
        approval=expected_approval if approval is None else approval,
        ledger_path=tmp_path / "ledger.json",
        raw_path=tmp_path / "raw.json",
        result_path=tmp_path / "result.json",
        transport_factory=lambda: sender,
        **inputs,
    )


def test_candidate_aware_b3_provenance_and_non_invest_authority() -> None:
    inputs, preflight, readiness, _ = _values()
    closure, residual, gaps = inputs["closure"], inputs["residual_plan"], inputs["remaining_gaps_closure"]
    assert judge.verify_residual_plan(residual) == judge.RESIDUAL_HASH
    assert judge.verify_remaining_gaps_closure(gaps) == judge.GAPS_HASH
    nvda = judge.classify_reason(candidate_id="NVDA", raw_reason="ALPACA_NEWS_PAGINATION_INCOMPLETE", closure=closure, residual_plan=residual, remaining_gaps_closure=gaps)
    msft = judge.classify_reason(candidate_id="MSFT", raw_reason="ALPACA_NEWS_PAGINATION_INCOMPLETE", closure=closure, residual_plan=residual, remaining_gaps_closure=gaps)
    meta = judge.classify_reason(candidate_id="META", raw_reason="ALPACA_NEWS_PAGINATION_INCOMPLETE", closure=closure, residual_plan=residual, remaining_gaps_closure=gaps)
    assert nvda["canonical_requirement_ids"] == ["NVDA_CURRENT_DEVELOPMENTS_Q4"]
    assert nvda["final_closure_statuses"] == ["CLOSED_DECISION_USABLE_NONEXHAUSTIVE"]
    assert msft["canonical_requirement_ids"] == ["MSFT_VALUATION_CONTEXT_DEPTH", "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]
    assert "NVDA_CURRENT_DEVELOPMENTS_Q4" not in msft["canonical_requirement_ids"]
    assert meta["canonical_requirement_ids"] == []
    assert meta["decision_context_condition_ids"] == ["META_CONDITION_001", "META_CONDITION_002", "META_CONDITION_003"]
    assert all(row["global_reason_closed"] and row["visible_to_judge_as_uncertainty"] and not row["may_independently_force_new_research_reopen"] for row in (nvda, msft, meta))
    entry = inputs["entry"]
    assert entry["invest_eligibility_policy_status"] == judge.POLICY_STATUS
    assert entry["invest_eligible_candidates"] == []
    assert entry["invest_blocked_candidates"] == ["NVDA", "MSFT", "META"]
    assert all(row["hard_invest_blockers"] == [judge.POLICY_STATUS] for row in entry["candidate_sufficiency"])
    assert entry["allowed_judge_outcomes"] == ["WATCH", "ABSTAIN"]
    assert preflight["request_hash"] not in inputs["historical_request_hashes"]
    assert preflight["new_paid_calls_planned"] == preflight["new_paid_call_count_ceiling"] == 1
    assert (preflight["model"], preflight["reasoning_effort"], preflight["max_output_tokens"]) == ("gpt-5.6-terra", "medium", 8192)
    assert judge.verify_readiness(readiness, code_commit_sha=CODE, preflight=preflight, **inputs) == readiness["artifact_hash"]


@pytest.mark.parametrize(("outcome", "directive"), [(JudgeOutcome.WATCH, JudgeNextDirective.MONITOR), (JudgeOutcome.ABSTAIN, JudgeNextDirective.STOP)])
def test_watch_and_abstain_are_accepted_and_invest_is_rejected(outcome: JudgeOutcome, directive: JudgeNextDirective) -> None:
    inputs, _, _, _ = _values()
    request = judge._request(inputs["entry"])
    proposal = _proposal(outcome, request)
    assert proposal.next_directive == directive
    judge._validate_proposal(proposal, request=request)
    with pytest.raises(Exception, match="rejects INVEST"):
        judge._validate_proposal(_proposal(JudgeOutcome.INVEST, request), request=request)


def test_invalid_approval_fails_before_transport(tmp_path: Path) -> None:
    _, _, _, approval = _values()
    calls = 0

    def sender(_):
        nonlocal calls
        calls += 1
        return {"id": "never"}

    approval["new_paid_call_count"] = 2
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    with pytest.raises(Exception, match="approval"):
        _execute(tmp_path, approval=approval, sender=sender)
    assert calls == 0
    assert not (tmp_path / "ledger.json").exists()


def test_dispatch_unknown_raw_capture_then_validation_failure_blocks_resend(tmp_path: Path) -> None:
    calls = 0
    state_seen: list[str] = []

    def sender(_):
        nonlocal calls
        calls += 1
        state_seen.append(json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))["entries"][0]["state"])
        return {"id": "response-invalid", "unexpected": True}

    with pytest.raises(Exception, match="captured response failed validation"):
        _execute(tmp_path, approval=None, sender=sender)
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    capture = json.loads((tmp_path / "raw.json").read_text(encoding="utf-8"))
    assert calls == 1 and state_seen == ["DISPATCH_STARTED_UNKNOWN"]
    assert ledger["entries"][0]["state"] == "DISPATCH_STARTED_UNKNOWN"
    assert ledger["entries"][0]["stop_reason"].startswith("RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:")
    assert ledger["entries"][0]["raw_response_hash"] == judge.verify_raw_capture(capture, request_hash=ledger["entries"][0]["request_hash"])
    assert ledger["entries"][0]["raw_response_path"] == str(tmp_path / "raw.json")
    with pytest.raises(Exception, match="pre-transport gate"):
        _execute(tmp_path, approval=None, sender=sender)
    assert calls == 1


def test_ambiguous_transport_stops_without_raw_capture_and_blocks_resend(tmp_path: Path) -> None:
    calls = 0

    def sender(_):
        nonlocal calls
        calls += 1
        raise OSError("ambiguous")

    with pytest.raises(Exception, match="ambiguous provider outcome"):
        _execute(tmp_path, approval=None, sender=sender)
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert calls == 1 and ledger["entries"][0]["state"] == "DISPATCH_STARTED_UNKNOWN"
    assert ledger["entries"][0]["stop_reason"] == "AMBIGUOUS_PROVIDER_OUTCOME:OSError"
    assert not (tmp_path / "raw.json").exists()
    with pytest.raises(Exception, match="pre-transport gate"):
        _execute(tmp_path, approval=None, sender=sender)
    assert calls == 1


@pytest.mark.parametrize(("outcome", "directive"), [(JudgeOutcome.WATCH, JudgeNextDirective.MONITOR), (JudgeOutcome.ABSTAIN, JudgeNextDirective.STOP)])
def test_valid_authority_terminal_watch_and_abstain(monkeypatch, tmp_path: Path, outcome: JudgeOutcome, directive: JudgeNextDirective) -> None:
    inputs, _, _, _ = _values()
    request = judge._request(inputs["entry"])
    proposal = _proposal(outcome, request)
    calls = 0

    def sender(_):
        nonlocal calls
        calls += 1
        return {"id": "response-valid"}

    monkeypatch.setattr(judge, "parse_council_responses_payload", lambda *_args, **_kwargs: (SimpleNamespace(response_id="response-valid"), proposal))
    monkeypatch.setattr(judge, "FrozenJudgeDecisionProposal", SimpleNamespace(from_draft=lambda _proposal: SimpleNamespace(model_dump=lambda **_kwargs: {"frozen": "proposal"})))
    monkeypatch.setattr(judge, "actual_cost_usd", lambda *_args, **_kwargs: Decimal("0.01"))
    result = _execute(tmp_path, approval=None, sender=sender)
    assert calls == 1
    assert result["processed_record"]["outcome"] == outcome.value
    assert result["processed_record"]["next_directive"] == directive.value
    assert result["final_b4_decision_created"] is True
    assert result["research_reopen_created"] is False
    assert result["b5_handoff_eligible"] is result["b5_handoff_created"] is False
    assert result["broker_writes"] == result["alpaca_orders"] == 0
