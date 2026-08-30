from __future__ import annotations

from datetime import UTC, datetime
from itertools import product

from jsonschema import Draft202012Validator

from aic.council.initial_schema_repair_v03 import build_initial_output_schema_v03
from aic.council.initial_schema_repair_v04 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    PROMOTION_SEMANTICS_CONTRACT_VERSION,
    build_bounded_initial_request_v04,
    build_initial_output_schema_v04,
    promotion_contract_branch_count,
    prompt_authority_unchanged,
)
from aic.council.model_policy import INITIAL_MODEL_LADDER, MODEL_POLICY_VERSION
from aic.council.models import CouncilInputBundle, CouncilLane
from aic.council.policy import COUNCIL_POLICY_VERSION
from aic.council.request import CouncilRequestStage
from aic.council.bounded_request import build_bounded_initial_request
from aic.domain.contracts import MATERIAL_CLAIM_V1


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
        allowed_material_claim_ids=("SRC_FACT", "SRC_INF"),
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
        claim_text="Frozen revenue was 42.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=("EVID1",),
        computed_value_ids=("CV1",),
        conflict_ids=(),
        assumptions=(),
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _source_inference():
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id="SRC_INF",
        candidate_id="NVDA",
        category="growth_quality",
        claim_text="Demand durability remains an inference.",
        claim_kind="INFERENCE",
        materiality="MATERIAL",
        evidence_ids=("EVID2",),
        computed_value_ids=(),
        conflict_ids=(),
        assumptions=("Demand remains durable",),
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _model_input() -> dict:
    return {
        "material_claims": [
            _source_fact().model_dump(mode="json", exclude_none=False, warnings=False),
            _source_inference().model_dump(mode="json", exclude_none=False, warnings=False),
        ],
        "marker": "UNTRUSTED_COUNCIL_DATA_TEST",
    }


def _claim_schema(schema: dict) -> dict:
    fields = {
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
    matches = []

    def walk(value):
        if isinstance(value, dict):
            branches = value.get("anyOf")
            if isinstance(branches, list) and branches and all(
                isinstance(branch, dict)
                and isinstance(branch.get("properties"), dict)
                and fields.issubset(branch["properties"])
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


def _claim(
    *,
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
        "claim_type": "ARGUMENT",
        "claim_text": "Bounded claim without numeric literals.",
        "source_material_claim_ids": ["SRC_FACT"] if source else [],
        "computed_value_ids": ["CV1"] if computed else [],
        "conflict_ids": ["CONFLICT1"] if conflict else [],
        "claim_kind": kind,
        "support_status": support,
        "materiality": materiality,
    }


def test_v04_schema_exhaustively_matches_support_and_provenance_promotion_rules() -> None:
    schema = build_initial_output_schema_v04(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_V04",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    claim_schema = _claim_schema(schema)
    validator = Draft202012Validator(claim_schema)

    observed = 0
    for kind, materiality, support, source, computed, conflict in product(
        ("FACT_RESTATEMENT", "INFERENCE", "PROCESS_FINDING"),
        ("MATERIAL", "SUPPORTING"),
        ("SUPPORTED", "CONFLICTED", "INSUFFICIENT"),
        (False, True),
        (False, True),
        (False, True),
    ):
        raw = _claim(
            kind=kind,
            materiality=materiality,
            support=support,
            source=source,
            computed=computed,
            conflict=conflict,
        )
        support_ok = materiality == "SUPPORTING" or support == "SUPPORTED"
        if kind == "FACT_RESTATEMENT":
            provenance_ok = source or computed
        else:
            provenance_ok = source or computed or conflict
        assert validator.is_valid(raw) is (support_ok and provenance_ok), raw
        observed += 1

    assert observed == 144
    assert promotion_contract_branch_count(schema) == 10
    assert INITIAL_SCHEMA_REPAIR_VERSION == "B4_INITIAL_PROMOTION_SEMANTICS_SCHEMA_REPAIR_v0_4"
    assert INITIAL_SCHEMA_VERSION == "P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA_REPAIR_v0.4"
    assert PROMOTION_SEMANTICS_CONTRACT_VERSION == "B4_INITIAL_SCHEMA_PROMOTION_SEMANTICS_v0_1"


def test_v04_fact_restatement_rejects_inference_parent_even_with_computed_provenance() -> None:
    schema = build_initial_output_schema_v04(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_V04",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    claim_schema = _claim_schema(schema)
    raw = _claim(
        kind="FACT_RESTATEMENT",
        materiality="MATERIAL",
        support="SUPPORTED",
        source=True,
        computed=True,
        conflict=False,
    )
    raw["source_material_claim_ids"] = ["SRC_INF"]
    assert not Draft202012Validator(claim_schema).is_valid(raw)


def test_retained_process_finding_shape_is_allowed_by_v03_and_rejected_by_v04() -> None:
    legacy = build_initial_output_schema_v03(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_V03",
        allowed_data_gap_refs=(),
    )
    repaired = build_initial_output_schema_v04(
        bundle=_bundle(),
        lane=CouncilLane.BULL,
        model_run_ref="RUN_V03",
        model_input=_model_input(),
        allowed_data_gap_refs=(),
    )
    raw = _claim(
        kind="PROCESS_FINDING",
        materiality="SUPPORTING",
        support="INSUFFICIENT",
        source=False,
        computed=False,
        conflict=False,
    )
    assert Draft202012Validator(_claim_schema(legacy)).is_valid(raw)
    assert not Draft202012Validator(_claim_schema(repaired)).is_valid(raw)


def test_v04_request_preserves_prompt_input_model_and_api_safety_authority() -> None:
    bundle = _bundle()
    model_input = _model_input()
    selected = INITIAL_MODEL_LADDER[1]
    legacy = build_bounded_initial_request(
        stage=CouncilRequestStage.BULL_INITIAL,
        model_candidate=selected,
        bundle=bundle,
        model_run_ref="RUN_AUTHORITY",
        model_input=model_input,
        allowed_data_gap_refs=(),
    )
    repaired = build_bounded_initial_request_v04(
        stage=CouncilRequestStage.BULL_INITIAL,
        model_candidate=selected,
        bundle=bundle,
        model_run_ref="RUN_AUTHORITY",
        model_input=model_input,
        allowed_data_gap_refs=(),
    )
    assert prompt_authority_unchanged(legacy, repaired)
    assert legacy.request_hash != repaired.request_hash
    assert repaired.schema_version == INITIAL_SCHEMA_VERSION
    assert repaired.request_payload["model"] == selected.model
    assert repaired.request_payload["reasoning"]["effort"] == selected.reasoning_effort
    assert repaired.request_payload["store"] is False
    assert repaired.request_payload["tools"] == []
    assert repaired.request_payload["parallel_tool_calls"] is False
    assert repaired.request_payload["truncation"] == "disabled"
    assert repaired.request_payload["text"]["format"]["strict"] is True
