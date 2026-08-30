from __future__ import annotations

from datetime import UTC, datetime
from itertools import product

from jsonschema import Draft202012Validator

from aic.council.bounded_request import build_bounded_initial_request
from aic.council.initial_schema_repair_v04 import build_initial_output_schema_v04
from aic.council.initial_schema_repair_v05 import (
    INITIAL_ALLOWED_CLAIM_TYPES,
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    INITIAL_STAGE_CLAIM_TYPE_CONTRACT_VERSION,
    JUDGE_ONLY_CLAIM_TYPES,
    PROMOTION_SEMANTICS_CONTRACT_VERSION,
    build_bounded_initial_request_v05,
    build_initial_output_schema_v05,
    initial_stage_claim_type_contract_satisfied,
    prompt_authority_unchanged,
)
from aic.council.model_policy import INITIAL_MODEL_LADDER, MODEL_POLICY_VERSION
from aic.council.models import CouncilInputBundle, CouncilLane
from aic.council.policy import COUNCIL_POLICY_VERSION
from aic.council.request import CouncilRequestStage
from aic.domain.contracts import MATERIAL_CLAIM_V1


CLAIM_FIELDS = {
    "claim_local_ref",
    "candidate_id",
    "lane",
    "claim_type",
    "claim_text",
    "source_material_claim_ids",
    "computed_value_ids",
    "conflict_ids",
    "claim_kind",
    "support_status",
    "materiality",
}


def _bundle() -> CouncilInputBundle:
    return CouncilInputBundle.from_unhashed(
        bundle_id="B4_INPUT_NVDA",
        candidate_id="NVDA",
        candidate_packet_id="B3_PACKET_NVDA",
        candidate_packet_hash="1" * 64,
        research_snapshot_id="B3_RESEARCH_NVDA",
        research_snapshot_hash="2" * 64,
        b2_snapshot_id="B2_SNAPSHOT",
        deep_comparison_id="B2_DEEP",
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        council_policy_version=COUNCIL_POLICY_VERSION,
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version=MODEL_POLICY_VERSION,
        allowed_material_claim_ids=("SRC_FACT",),
        allowed_computed_value_ids=("CV1",),
        allowed_conflict_ids=("CONFLICT1",),
        shared_portfolio_context_refs=(),
        created_at=datetime(2026, 8, 29, 16, 0, tzinfo=UTC),
    )


def _source_fact():
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id="SRC_FACT",
        candidate_id="NVDA",
        category="financial_quality",
        claim_text="Frozen revenue metric is forty two.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=["EVID1"],
        computed_value_ids=["CV1"],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _model_input() -> dict:
    return {
        "material_claims": [
            _source_fact().model_dump(mode="json", exclude_none=False, warnings=False)
        ],
        "marker": "UNTRUSTED_COUNCIL_DATA_TEST",
    }


def _claim_union(schema: dict) -> dict:
    matches: list[dict] = []

    def walk(value):
        if isinstance(value, dict):
            branches = value.get("anyOf")
            if isinstance(branches, list) and branches and all(
                isinstance(branch, dict)
                and isinstance(branch.get("properties"), dict)
                and CLAIM_FIELDS.issubset(branch["properties"])
                for branch in branches
            ):
                matches.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    assert len(matches) == 1
    return matches[0]


def _claim_validator(schema: dict) -> Draft202012Validator:
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": schema.get("$defs", {}),
        **_claim_union(schema),
    }
    return Draft202012Validator(root)


def _claim(
    *,
    claim_type: str,
    kind: str,
    materiality: str,
    support: str,
    source: bool,
    computed: bool,
    conflict: bool,
) -> dict:
    return {
        "claim_local_ref": "C1",
        "candidate_id": "NVDA",
        "lane": "BULL",
        "claim_type": claim_type,
        "claim_text": "Bounded claim without numeric literals.",
        "source_material_claim_ids": ["SRC_FACT"] if source else [],
        "computed_value_ids": ["CV1"] if computed else [],
        "conflict_ids": ["CONFLICT1"] if conflict else [],
        "claim_kind": kind,
        "support_status": support,
        "materiality": materiality,
    }


def test_v05_exhaustively_matches_promotion_semantics_plus_initial_claim_type_authority() -> None:
    schema = build_initial_output_schema_v05(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_V05",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    validator = _claim_validator(schema)

    all_types = INITIAL_ALLOWED_CLAIM_TYPES + JUDGE_ONLY_CLAIM_TYPES
    observed = 0
    for claim_type, kind, materiality, support, source, computed, conflict in product(
        all_types,
        ("FACT_RESTATEMENT", "INFERENCE", "PROCESS_FINDING"),
        ("MATERIAL", "SUPPORTING"),
        ("SUPPORTED", "CONFLICTED", "INSUFFICIENT"),
        (False, True),
        (False, True),
        (False, True),
    ):
        raw = _claim(
            claim_type=claim_type,
            kind=kind,
            materiality=materiality,
            support=support,
            source=source,
            computed=computed,
            conflict=conflict,
        )
        support_ok = materiality == "SUPPORTING" or support == "SUPPORTED"
        provenance_ok = (source or computed) if kind == "FACT_RESTATEMENT" else (
            source or computed or conflict
        )
        claim_type_ok = claim_type in INITIAL_ALLOWED_CLAIM_TYPES
        assert validator.is_valid(raw) is (
            support_ok and provenance_ok and claim_type_ok
        ), raw
        observed += 1

    assert observed == 864
    assert initial_stage_claim_type_contract_satisfied(schema)


def test_v04_accepts_decision_basis_shape_that_v05_rejects() -> None:
    legacy = build_initial_output_schema_v04(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_GAP",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    repaired = build_initial_output_schema_v05(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_GAP",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    raw = _claim(
        claim_type="DECISION_BASIS",
        kind="INFERENCE",
        materiality="MATERIAL",
        support="SUPPORTED",
        source=True,
        computed=False,
        conflict=False,
    )
    assert _claim_validator(legacy).is_valid(raw)
    assert not _claim_validator(repaired).is_valid(raw)


def test_v05_keeps_dto_definition_broad_but_overrides_model_output_branch() -> None:
    schema = build_initial_output_schema_v05(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_DTO",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    assert "DECISION_BASIS" in schema["$defs"]["CouncilClaimType"]["enum"]
    for branch in _claim_union(schema)["anyOf"]:
        assert branch["properties"]["claim_type"] == {
            "type": "string",
            "enum": list(INITIAL_ALLOWED_CLAIM_TYPES),
        }


def test_v05_request_preserves_prompt_input_model_and_api_safety_authority() -> None:
    bundle = _bundle()
    model_input = _model_input()
    selected = INITIAL_MODEL_LADDER[1]
    legacy = build_bounded_initial_request(
        stage=CouncilRequestStage.BULL_INITIAL,
        model_candidate=selected,
        bundle=bundle,
        model_run_ref="RUN_AUTHORITY_V05",
        model_input=model_input,
        allowed_data_gap_refs=(),
    )
    repaired = build_bounded_initial_request_v05(
        stage=CouncilRequestStage.BULL_INITIAL,
        model_candidate=selected,
        bundle=bundle,
        model_run_ref="RUN_AUTHORITY_V05",
        model_input=model_input,
        allowed_data_gap_refs=(),
    )
    assert prompt_authority_unchanged(legacy, repaired)
    assert legacy.request_hash != repaired.request_hash
    assert repaired.schema_version == INITIAL_SCHEMA_VERSION
    assert repaired.request_payload["text"]["format"]["name"] == "b4_bull_initial_v0_5"
    assert repaired.request_payload["model"] == selected.model
    assert repaired.request_payload["reasoning"]["effort"] == selected.reasoning_effort
    assert repaired.request_payload["store"] is False
    assert repaired.request_payload["tools"] == []
    assert repaired.request_payload["parallel_tool_calls"] is False
    assert repaired.request_payload["truncation"] == "disabled"
    assert repaired.request_payload["text"]["format"]["strict"] is True
    assert INITIAL_SCHEMA_REPAIR_VERSION == "B4_INITIAL_STAGE_CLAIM_TYPE_SCHEMA_REPAIR_v0_5"
    assert PROMOTION_SEMANTICS_CONTRACT_VERSION == "B4_INITIAL_SCHEMA_PROMOTION_SEMANTICS_v0_2"
    assert INITIAL_STAGE_CLAIM_TYPE_CONTRACT_VERSION == "B4_INITIAL_STAGE_CLAIM_TYPE_AUTHORITY_v0_1"
