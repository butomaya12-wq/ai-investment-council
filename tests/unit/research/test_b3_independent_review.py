from pathlib import Path

import pytest

from aic.research.independent_review import (
    ATTACK_CLASSES,
    INDEPENDENT_REVIEW_SCHEMA_NAME,
    REVIEWER_CANDIDATE,
    IndependentReviewDraft,
    bound_review_value,
    build_independent_review_request,
    build_privacy_retention_boundary,
    build_static_safety_manifest,
    independent_review_prompt_hash,
)


def _attack(attack_class: str, status: str = "PASS") -> dict[str, object]:
    return {
        "attack_class": attack_class,
        "status": status,
        "finding": "No material acceptance gap was identified in the supplied frozen evidence.",
        "evidence_refs": (f"STATIC:{attack_class}",),
        "materiality_rationale": "The supplied evidence supports the stated acceptance classification.",
    }


def test_independent_review_pass_requires_exact_frozen_15_attack_classes() -> None:
    review = IndependentReviewDraft.model_validate(
        {
            "review_status": "PASS",
            "attack_results": [_attack(value) for value in ATTACK_CLASSES],
            "material_gap_summary": [],
            "inconclusive_summary": [],
        }
    )
    assert len(review.attack_results) == 15
    assert tuple(item.attack_class for item in review.attack_results) == ATTACK_CLASSES


def test_independent_review_fails_closed_on_attack_order_or_status_drift() -> None:
    attacks = [_attack(value) for value in ATTACK_CLASSES]
    attacks[0], attacks[1] = attacks[1], attacks[0]
    with pytest.raises(ValueError, match="frozen order"):
        IndependentReviewDraft.model_validate(
            {
                "review_status": "PASS",
                "attack_results": attacks,
                "material_gap_summary": [],
                "inconclusive_summary": [],
            }
        )

    gap_attacks = [_attack(value) for value in ATTACK_CLASSES]
    gap_attacks[4] = _attack(ATTACK_CLASSES[4], "MATERIAL_GAP")
    with pytest.raises(ValueError, match="review_status"):
        IndependentReviewDraft.model_validate(
            {
                "review_status": "PASS",
                "attack_results": gap_attacks,
                "material_gap_summary": [],
                "inconclusive_summary": [],
            }
        )


def test_independent_review_request_is_one_shot_tool_free_strict_m3() -> None:
    review_input = {
        "review_input_version": "B3_INDEPENDENT_REVIEW_INPUT_v0_1",
        "review_contract": {"attack_classes": list(ATTACK_CLASSES)},
        "evidence": [{"review_ref": "STATIC:test", "value": "safe"}],
    }
    request = build_independent_review_request(review_input)
    payload = request.request_payload
    assert request.reviewer == REVIEWER_CANDIDATE
    assert REVIEWER_CANDIDATE.candidate_key == "M3"
    assert REVIEWER_CANDIDATE.model == "gpt-5.6-sol"
    assert REVIEWER_CANDIDATE.reasoning_effort == "medium"
    assert payload["store"] is False
    assert payload["tools"] == []
    assert payload["parallel_tool_calls"] is False
    assert payload["truncation"] == "disabled"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["name"] == INDEPENDENT_REVIEW_SCHEMA_NAME
    assert payload["text"]["format"]["strict"] is True
    assert request.prompt_hash == independent_review_prompt_hash()


def test_review_evidence_bounding_is_explicit_and_keeps_both_ends() -> None:
    value = "A" * 90 + "MIDDLE" + "Z" * 90
    bounded = bound_review_value(value, max_chars=100)
    assert bounded["review_value_truncated"] is True
    assert bounded["original_char_count"] == len(value)
    assert str(bounded["review_value"]).startswith("A" * 20)
    assert str(bounded["review_value"]).endswith("Z" * 20)
    assert "BOUNDED REVIEW TRUNCATION" in str(bounded["review_value"])

    short = bound_review_value("complete evidence", max_chars=100)
    assert short == {
        "review_value": "complete evidence",
        "review_value_truncated": False,
        "original_char_count": 17,
    }


def test_static_safety_manifest_is_green_on_repository_sources() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest = build_static_safety_manifest(repo_root)
    assert manifest["manifest_version"] == "B3_STATIC_SAFETY_MANIFEST_v0_2"
    assert manifest["all_checks_pass"] is True
    assert all(manifest["checks"].values())
    assert set(manifest["files"]) == {
        "model_policy",
        "research_policy",
        "synthesis",
        "synthesis_runtime",
        "runtime",
        "reconciliation",
    }
    assert manifest["checks"]["research_policy_repair_attempt_limit_one"] is True
    assert manifest["checks"]["synthesis_runtime_requires_repair_limit_one"] is True
    assert manifest["checks"]["synthesis_runtime_repair_exhausts_after_one"] is True
    assert manifest["checks"]["runtime_http_error_body_not_persisted"] is True
    assert manifest["checks"]["reconciliation_public_summary_excludes_raw_drafts"] is True
    assert manifest["privacy_retention_boundary"]["review_ref"].startswith("PRIVACY_BOUNDARY:")


def test_privacy_retention_boundary_is_explicit_and_does_not_claim_zero_retention() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    boundary = build_privacy_retention_boundary(repo_root)
    assert boundary["review_ref"].startswith("PRIVACY_BOUNDARY:")
    assert len(boundary["boundary_hash"]) == 64
    assert len(boundary["file_sha256"]) == 64

    application = boundary["application_boundary"]
    provider = boundary["provider_boundary"]
    semantics = boundary["review_semantics"]
    assert application["responses_store"] is False
    assert application["agents_sdk_tracing_enabled"] is False
    assert application["secret_values_may_be_serialized"] is False
    assert provider["endpoint"] == "/v1/responses"
    assert provider["application_state_control"] == "store=false"
    assert provider["default_abuse_monitoring_retention"] == "UP_TO_30_DAYS_UNLESS_LEGALLY_REQUIRED_LONGER"
    assert provider["zero_data_retention_claimed"] is False
    assert provider["modified_abuse_monitoring_claimed"] is False
    assert provider["source_url"] == "https://platform.openai.com/docs/models/default-usage-policies-by-endpoint"
    assert semantics["claims_zero_provider_retention"] is False
    assert semantics["residual_provider_retention_is_explicit"] is True
