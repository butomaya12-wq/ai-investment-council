import json

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.model_selection import (
    DEFAULT_SELECTED_MODEL_AUTHORITY_PATH,
    EXPECTED_MODEL_EVAL_CASE_IDS,
    SelectedModelAuthority,
    load_selected_model_authority,
    verify_model_eval_artifact,
)


def test_selected_model_authority_freezes_eval_selected_m2() -> None:
    authority = load_selected_model_authority()
    assert authority.model_eval_artifact_hash == "842677125a3f80c73b6d5db23d557a2ba0e2a28384c095d064438f8c3236f336"
    assert authority.selected_candidate.candidate_key == "M2"
    assert authority.selected_candidate.model == "gpt-5.6-terra"
    assert authority.selected_candidate.reasoning_effort == "medium"
    assert authority.selected_eval_metrics.passed_cases == 12
    assert authority.selected_eval_metrics.critical_safety_failures == 0
    assert set(authority.full_ladder_pass_summary) == {"M1", "M2", "M3"}


def test_selected_model_authority_recomputes_selection_rule_after_tamper() -> None:
    raw = json.loads(DEFAULT_SELECTED_MODEL_AUTHORITY_PATH.read_text(encoding="utf-8"))
    raw["full_ladder_pass_summary"]["M1"]["estimated_cost_usd"] = "0.001"
    raw["selection_hash"] = canonical_sha256(raw, exclude_fields=("selection_hash",))

    with pytest.raises(ValueError, match="candidate disagrees with frozen selection rule"):
        SelectedModelAuthority.model_validate(raw)


def test_selected_model_authority_hash_fails_closed_on_payload_drift() -> None:
    raw = json.loads(DEFAULT_SELECTED_MODEL_AUTHORITY_PATH.read_text(encoding="utf-8"))
    raw["model_eval_artifact_hash"] = "0" + raw["model_eval_artifact_hash"][1:]
    with pytest.raises(ValueError, match="selection_hash"):
        SelectedModelAuthority.model_validate(raw)


def _full_eval_payload_for_authority(authority):
    candidates = []
    for candidate_key in ("M1", "M2", "M3"):
        metrics = authority.full_ladder_pass_summary[candidate_key]
        candidate = next(
            item
            for item in (
                authority.selected_candidate.model_copy(update={"candidate_key": "M1", "reasoning_effort": "low", "ladder_position": 1}),
                authority.selected_candidate,
                authority.selected_candidate.model_copy(update={"candidate_key": "M3", "model": "gpt-5.6-sol", "ladder_position": 3}),
            )
            if item.candidate_key == candidate_key
        )
        cases = [
            {
                "case_id": case_id,
                "passed": True,
            }
            for case_id in EXPECTED_MODEL_EVAL_CASE_IDS
        ]
        record = {
            "candidate_key": candidate.candidate_key,
            "model": candidate.model,
            "reasoning_effort": candidate.reasoning_effort,
            "ladder_position": candidate.ladder_position,
            "cases": cases,
            "all_required_checks_passed": True,
            "critical_safety_failures": metrics.critical_safety_failures,
            "estimated_cost_usd": metrics.estimated_cost_usd,
            "latency_ms": metrics.latency_ms,
            "total_tokens": metrics.total_tokens,
        }
        record["record_hash"] = canonical_sha256(record)
        candidates.append(record)

    payload = {
        "artifact_version": "B3_MODEL_EVAL_ARTIFACT_v0_2",
        "run_class": "B3_REAL_REPRESENTATIVE_MODEL_EVAL",
        "eval_version": authority.eval_version,
        "model_policy_version": authority.model_policy_version,
        "case_ids": list(EXPECTED_MODEL_EVAL_CASE_IDS),
        "prompt_manifest": authority.prompt_manifest.model_dump(mode="json"),
        "candidates": candidates,
        "selection": {
            "status": "SELECTED",
            "selected_candidate": authority.selected_candidate.model_dump(mode="json"),
            "reason_code": authority.selection_reason_code,
        },
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_verify_model_eval_artifact_reads_full_case_records_not_public_summary_fields() -> None:
    authority = load_selected_model_authority()
    payload = _full_eval_payload_for_authority(authority)
    synthetic_authority = authority.model_copy(
        update={"model_eval_artifact_hash": payload["artifact_hash"]}
    )

    verify_model_eval_artifact(payload, authority=synthetic_authority)


def test_verify_model_eval_artifact_rejects_case_order_drift() -> None:
    authority = load_selected_model_authority()
    payload = _full_eval_payload_for_authority(authority)
    payload["candidates"][0]["cases"][0]["case_id"] = "E2"
    payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    synthetic_authority = authority.model_copy(
        update={"model_eval_artifact_hash": payload["artifact_hash"]}
    )

    with pytest.raises(ValueError, match="exact ordered E1-E12"):
        verify_model_eval_artifact(payload, authority=synthetic_authority)
