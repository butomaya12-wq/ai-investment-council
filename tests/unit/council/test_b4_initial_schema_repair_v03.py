from __future__ import annotations

from datetime import UTC, datetime

from jsonschema import Draft202012Validator

from aic.council.bounded_request import build_bounded_initial_request
from aic.council.initial_schema_repair_v03 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    assert_initial_schema_repair,
    build_bounded_initial_request_v03,
    prompt_authority_unchanged,
)
from aic.council.model_policy import INITIAL_MODEL_LADDER
from aic.council.models import CouncilInputBundle
from aic.council.request import CouncilRequestStage


def _bundle() -> CouncilInputBundle:
    return CouncilInputBundle.from_unhashed(
        bundle_id="BUNDLE_NVDA",
        candidate_id="NVDA",
        candidate_packet_id="PACKET_NVDA",
        candidate_packet_hash="b" * 64,
        research_snapshot_id="SNAP_NVDA",
        research_snapshot_hash="c" * 64,
        b2_snapshot_id="B2_NVDA",
        deep_comparison_id="DEEP_1",
        mandate_version="MANDATE_TEST",
        council_policy_version="COUNCIL_POLICY_TEST",
        judge_policy_version="JUDGE_POLICY_TEST",
        model_policy_version="MODEL_POLICY_TEST",
        allowed_material_claim_ids=("SRC1",),
        allowed_computed_value_ids=("CV1",),
        allowed_conflict_ids=("CON1",),
        shared_portfolio_context_refs=(),
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
    )


def _proposal(bundle: CouncilInputBundle, *, materiality: str, support_status: str):
    return {
        "opinion_id": "OPINION_1",
        "candidate_id": "NVDA",
        "lane": "BULL",
        "council_input_bundle_hash": bundle.bundle_hash,
        "candidate_packet_hash": bundle.candidate_packet_hash,
        "mandate_version": bundle.mandate_version,
        "council_policy_version": bundle.council_policy_version,
        "model_policy_version": bundle.model_policy_version,
        "model_run_ref": "RUN_1",
        "proposed_claims": [
            {
                "claim_local_ref": "C1",
                "candidate_id": "NVDA",
                "lane": "BULL",
                "claim_type": "ARGUMENT",
                "claim_text": "Evidence-grounded claim.",
                "source_material_claim_ids": ["SRC1"],
                "computed_value_ids": [],
                "conflict_ids": [],
                "claim_kind": "INFERENCE",
                "support_status": support_status,
                "materiality": materiality,
            }
        ],
        "primary_claim_ids": ["C1"],
        "critical_assumption_claim_ids": [],
        "falsifier_claim_ids": [],
        "material_unknown_refs": [],
        "material_conflict_refs": [],
        "research_reopen_required": False,
        "research_reopen_reason_codes": [],
        "role_boundary_status": "VALID",
    }


def _requests():
    bundle = _bundle()
    candidate = INITIAL_MODEL_LADDER[1]
    kwargs = dict(
        stage=CouncilRequestStage.BULL_INITIAL,
        model_candidate=candidate,
        bundle=bundle,
        model_run_ref="RUN_1",
        model_input={"candidate_id": "NVDA"},
        allowed_data_gap_refs=(),
    )
    return bundle, build_bounded_initial_request(**kwargs), build_bounded_initial_request_v03(**kwargs)


def test_v03_repair_preserves_prompt_authority_and_changes_only_request_schema_surface():
    _, legacy, repaired = _requests()

    assert prompt_authority_unchanged(legacy, repaired) is True
    assert repaired.schema_version == INITIAL_SCHEMA_VERSION
    assert repaired.schema_version != legacy.schema_version
    assert repaired.request_hash != legacy.request_hash
    assert repaired.request_payload["text"]["format"]["name"] == "b4_bull_initial_v0_3"
    assert repaired.request_payload["text"]["format"]["strict"] is True
    assert repaired.request_payload["max_output_tokens"] == legacy.request_payload["max_output_tokens"]
    assert repaired.request_payload["store"] is False
    assert repaired.request_payload["tools"] == []
    assert repaired.request_payload["parallel_tool_calls"] is False
    assert repaired.request_payload["truncation"] == "disabled"
    assert INITIAL_SCHEMA_REPAIR_VERSION == "B4_INITIAL_PROMOTABLE_SUPPORT_SCHEMA_REPAIR_v0_3"


def test_v03_strict_schema_forbids_exact_material_non_supported_contract_gap():
    bundle, legacy, repaired = _requests()
    legacy_schema = legacy.request_payload["text"]["format"]["schema"]
    repaired_schema = repaired.request_payload["text"]["format"]["schema"]
    assert_initial_schema_repair(repaired_schema)

    legacy_validator = Draft202012Validator(legacy_schema)
    repaired_validator = Draft202012Validator(repaired_schema)

    for support_status in ("CONFLICTED", "INSUFFICIENT"):
        bad = _proposal(bundle, materiality="MATERIAL", support_status=support_status)
        assert legacy_validator.is_valid(bad) is True
        assert repaired_validator.is_valid(bad) is False

    material_supported = _proposal(
        bundle, materiality="MATERIAL", support_status="SUPPORTED"
    )
    assert repaired_validator.is_valid(material_supported) is True

    for support_status in ("SUPPORTED", "CONFLICTED", "INSUFFICIENT"):
        supporting = _proposal(
            bundle, materiality="SUPPORTING", support_status=support_status
        )
        assert repaired_validator.is_valid(supporting) is True
