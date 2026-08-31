from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_s00_scope_v01 as scope


HEAD = "a" * 40


def _condition(i: int) -> dict:
    return {
        "condition_id": f"META_CONDITION_{i:03d}",
        "condition_text": f"Condition {i}",
        "source_or_claim_refs": [f"META_REF_{i}"],
    }


def _sources(monkeypatch):
    reopen = {"request_hash": scope.EXPECTED_REOPEN_HASH}
    postprocess = {"artifact_hash": scope.EXPECTED_POSTPROCESS_HASH}
    judge = {
        "artifact_hash": scope.EXPECTED_JUDGE_RESULT_HASH,
        "judge_proposal": {
            "what_would_change_decision": [_condition(i) for i in range(1, 5)],
        },
    }
    rebuttal = {"artifact_hash": scope.EXPECTED_REBUTTAL_FREEZE_HASH}
    valuation = {
        "claim_text": "Point-in-time P/E is insufficient valuation context.",
        "evidence_ids": ["MSFT_VAL_EVID"],
    }
    durability = {
        "claim_text": "Forward cloud and AI return durability is unverified.",
        "evidence_ids": ["MSFT_SEC_1"],
        "computed_value_ids": ["MSFT_GROWTH", "MSFT_MARGIN"],
    }
    monkeypatch.setattr(scope, "verify_reopen_request", lambda payload: scope.EXPECTED_REOPEN_HASH)
    monkeypatch.setattr(scope, "verify_postprocess", lambda payload: scope.EXPECTED_POSTPROCESS_HASH)
    monkeypatch.setattr(scope, "verify_judge_result", lambda payload: scope.EXPECTED_JUDGE_RESULT_HASH)
    monkeypatch.setattr(
        scope,
        "verify_rebuttal_freeze",
        lambda payload: (scope.EXPECTED_REBUTTAL_FREEZE_HASH, valuation, durability),
    )
    return reopen, postprocess, judge, rebuttal


def test_scope_separates_canonical_reopen_from_meta_exit_conditions(monkeypatch) -> None:
    reopen, postprocess, judge, rebuttal = _sources(monkeypatch)
    artifact = scope.build_scope_artifact(
        reopen_request=reopen,
        postprocess=postprocess,
        judge_result=judge,
        rebuttal_freeze=rebuttal,
        code_commit_sha=HEAD,
    )
    assert artifact["status"] == scope.PASS_STATUS
    assert artifact["canonical_reopen_reason_codes"] == list(scope.EXPECTED_REOPEN_REASONS)
    assert artifact["canonical_reopen_requirement_count"] == 3
    assert [row["requirement_id"] for row in artifact["canonical_reopen_requirements"]] == [
        "NVDA_CURRENT_DEVELOPMENTS_Q4",
        "MSFT_VALUATION_CONTEXT_DEPTH",
        "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
    ]
    assert artifact["judge_change_condition_count"] == 4
    assert [row["condition_id"] for row in artifact["judge_change_conditions_for_executable_invest"]] == list(scope.EXPECTED_META_CONDITION_IDS)
    assert all(row["canonical_reopen_reason"] is False for row in artifact["judge_change_conditions_for_executable_invest"])
    assert artifact["planned_current_developments_candidate_symbols"] == ["NVDA", "MSFT", "META"]
    assert artifact["broad_b3_rerun_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["research_run_started"] is False
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["execution_authority"] is False
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["next_gate"] == scope.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert scope.verify_scope_artifact(artifact, expected_code_commit_sha=HEAD) == artifact["artifact_hash"]


def test_scope_preserves_frozen_msft_claim_text_and_lineage(monkeypatch) -> None:
    reopen, postprocess, judge, rebuttal = _sources(monkeypatch)
    artifact = scope.build_scope_artifact(
        reopen_request=reopen,
        postprocess=postprocess,
        judge_result=judge,
        rebuttal_freeze=rebuttal,
        code_commit_sha=HEAD,
    )
    valuation = artifact["canonical_reopen_requirements"][1]
    durability = artifact["canonical_reopen_requirements"][2]
    assert valuation["source_ref_id"] == scope.EXPECTED_REOPEN_REASONS[1]
    assert valuation["frozen_claim_text"] == "Point-in-time P/E is insufficient valuation context."
    assert valuation["frozen_evidence_ids"] == ["MSFT_VAL_EVID"]
    assert durability["source_ref_id"] == scope.EXPECTED_REOPEN_REASONS[2]
    assert durability["frozen_claim_text"] == "Forward cloud and AI return durability is unverified."
    assert durability["frozen_evidence_ids"] == ["MSFT_SEC_1"]
    assert durability["frozen_computed_value_ids"] == ["MSFT_GROWTH", "MSFT_MARGIN"]


def test_scope_verifier_fails_closed_on_call_authority_tamper(monkeypatch) -> None:
    reopen, postprocess, judge, rebuttal = _sources(monkeypatch)
    artifact = scope.build_scope_artifact(
        reopen_request=reopen,
        postprocess=postprocess,
        judge_result=judge,
        rebuttal_freeze=rebuttal,
        code_commit_sha=HEAD,
    )
    tampered = deepcopy(artifact)
    tampered["provider_reads_authorized"] = True
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(scope.JudgeReopenS00ScopeError, match="cannot authorize calls"):
        scope.verify_scope_artifact(tampered, expected_code_commit_sha=HEAD)


def test_scope_verifier_fails_closed_on_meta_condition_loss(monkeypatch) -> None:
    reopen, postprocess, judge, rebuttal = _sources(monkeypatch)
    artifact = scope.build_scope_artifact(
        reopen_request=reopen,
        postprocess=postprocess,
        judge_result=judge,
        rebuttal_freeze=rebuttal,
        code_commit_sha=HEAD,
    )
    tampered = deepcopy(artifact)
    tampered["judge_change_conditions_for_executable_invest"] = tampered["judge_change_conditions_for_executable_invest"][:3]
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(scope.JudgeReopenS00ScopeError, match="META condition drift"):
        scope.verify_scope_artifact(tampered, expected_code_commit_sha=HEAD)


def test_scope_runner_has_no_provider_or_model_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_s00_scope_zero_call_v01.py").read_text(encoding="utf-8")
    forbidden = (
        "OPENAI_API_KEY",
        "urlopen",
        "requests.",
        "httpx",
        "execute_paid",
        "provider.post",
        "alpaca data",
    )
    for token in forbidden:
        assert token not in source
