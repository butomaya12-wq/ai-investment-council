from __future__ import annotations

from datetime import UTC, datetime
from itertools import product

from jsonschema import Draft202012Validator

from aic.council.bounded_request import build_bounded_rebuttal_request
from aic.council.model_policy import MODEL_POLICY_VERSION, REBUTTAL_MODEL_LADDER
from aic.council.models import CouncilInputBundle, CouncilLane
from aic.council.policy import COUNCIL_POLICY_VERSION
from aic.council.rebuttal_schema_repair_v01 import (
    JUDGE_ONLY_CLAIM_TYPES,
    REBUTTAL_ALLOWED_CLAIM_TYPES,
    REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
    REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
    REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
    REBUTTAL_SCHEMA_REPAIR_VERSION,
    REBUTTAL_SCHEMA_VERSION,
    build_bounded_rebuttal_request_v01,
    build_rebuttal_output_schema_v01,
    prompt_authority_unchanged,
)
from aic.council.request import build_rebuttal_output_schema
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
ITEM_FIELDS = {
    "rebuttal_item_id",
    "responding_lane",
    "opposing_finding_ids",
    "response_type",
    "response_proposed_claims",
    "remaining_uncertainty_refs",
}


def _bundle(*, include_inference_source: bool = False) -> CouncilInputBundle:
    source_ids = ("SRC_FACT", "SRC_INF") if include_inference_source else ("SRC_FACT",)
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
        allowed_material_claim_ids=source_ids,
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


def _source_inference():
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id="SRC_INF",
        candidate_id="NVDA",
        category="growth_quality",
        claim_text="Demand durability is inferred from the frozen record.",
        claim_kind="INFERENCE",
        materiality="MATERIAL",
        evidence_ids=["EVID2"],
        computed_value_ids=[],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _model_input(*, include_inference_source: bool = False) -> dict:
    claims = [_source_fact()]
    if include_inference_source:
        claims.append(_source_inference())
    candidate = {
        "material_claims": [
            claim.model_dump(mode="json", exclude_none=False, warnings=False)
            for claim in claims
        ],
        "marker": "FROZEN_CANDIDATE_INPUT",
    }
    return {
        "candidate_model_input": candidate,
        "initial_council": {"marker": "FROZEN_INITIAL_COUNCIL"},
    }


def _opposing() -> dict[CouncilLane, tuple[str, ...]]:
    return {
        CouncilLane.BULL: ("BEAR_INIT_1", "RED_INIT_1"),
        CouncilLane.BEAR: ("BULL_INIT_1", "RED_INIT_1"),
        CouncilLane.RED_TEAM: ("BULL_INIT_1", "BEAR_INIT_1"),
    }


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _item_union(schema: dict) -> dict:
    matches = []
    for node in _walk(schema):
        branches = node.get("anyOf")
        if isinstance(branches, list) and branches and all(
            isinstance(branch, dict)
            and isinstance(branch.get("properties"), dict)
            and ITEM_FIELDS.issubset(branch["properties"])
            for branch in branches
        ):
            matches.append(node)
    assert len(matches) == 1
    return matches[0]


def _legacy_item_object(schema: dict) -> dict:
    matches = []
    for node in _walk(schema):
        props = node.get("properties")
        if isinstance(props, dict) and ITEM_FIELDS.issubset(props):
            matches.append(node)
    assert len(matches) == 1
    return matches[0]


def _lane_item_branch(schema: dict, lane: CouncilLane) -> dict:
    for branch in _item_union(schema)["anyOf"]:
        if branch["properties"]["responding_lane"] == {
            "type": "string",
            "const": lane.value,
        }:
            return branch
    raise AssertionError(f"lane branch missing: {lane.value}")


def _claim_validator(schema: dict, lane: CouncilLane) -> Draft202012Validator:
    item = _lane_item_branch(schema, lane)
    union = item["properties"]["response_proposed_claims"]["items"]
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": schema.get("$defs", {}),
        **union,
    }
    return Draft202012Validator(root)


def _item_validator(schema: dict) -> Draft202012Validator:
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": schema.get("$defs", {}),
        **_item_union(schema),
    }
    return Draft202012Validator(root)


def _legacy_item_validator(schema: dict) -> Draft202012Validator:
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": schema.get("$defs", {}),
        **_legacy_item_object(schema),
    }
    return Draft202012Validator(root)


def _claim(
    *,
    lane: str = "BULL",
    claim_type: str,
    kind: str,
    materiality: str,
    support: str,
    source: bool,
    computed: bool,
    conflict: bool,
    source_id: str = "SRC_FACT",
) -> dict:
    return {
        "claim_local_ref": "C1",
        "candidate_id": "NVDA",
        "lane": lane,
        "claim_type": claim_type,
        "claim_text": "Bounded rebuttal claim without numeric literals.",
        "source_material_claim_ids": [source_id] if source else [],
        "computed_value_ids": ["CV1"] if computed else [],
        "conflict_ids": ["CONFLICT1"] if conflict else [],
        "claim_kind": kind,
        "support_status": support,
        "materiality": materiality,
    }


def _item(*, lane: str, opposing: list[str], claims: list[dict]) -> dict:
    return {
        "rebuttal_item_id": f"R_{lane}",
        "responding_lane": lane,
        "opposing_finding_ids": opposing,
        "response_type": "REBUT",
        "response_proposed_claims": claims,
        "remaining_uncertainty_refs": [],
    }


def _schema(*, include_inference_source: bool = False) -> dict:
    return build_rebuttal_output_schema_v01(
        bundle=_bundle(include_inference_source=include_inference_source),
        model_input=_model_input(include_inference_source=include_inference_source),
        initial_opinion_ids=("OP_BULL", "OP_BEAR", "OP_RED"),
        initial_opinion_hashes=("a" * 64, "b" * 64, "c" * 64),
        opposing_claim_ids_by_lane=_opposing(),
        allowed_uncertainty_refs=("GAP1",),
    )


def test_rebuttal_schema_exhaustively_matches_promotion_semantics_and_claim_type_authority() -> None:
    schema = _schema()
    validator = _claim_validator(schema, CouncilLane.BULL)
    all_types = REBUTTAL_ALLOWED_CLAIM_TYPES + JUDGE_ONLY_CLAIM_TYPES
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
        claim_type_ok = claim_type in REBUTTAL_ALLOWED_CLAIM_TYPES
        assert validator.is_valid(raw) is (
            support_ok and provenance_ok and claim_type_ok
        ), raw
        observed += 1

    assert observed == 864


def test_rebuttal_schema_binds_opposing_finding_ids_to_other_lanes() -> None:
    bundle = _bundle()
    generic = build_rebuttal_output_schema(
        bundle=bundle,
        initial_opinion_ids=("OP_BULL", "OP_BEAR", "OP_RED"),
        initial_opinion_hashes=("a" * 64, "b" * 64, "c" * 64),
        allowed_opposing_claim_ids=("BULL_INIT_1", "BEAR_INIT_1", "RED_INIT_1"),
        allowed_source_material_claim_ids=bundle.allowed_material_claim_ids,
        allowed_uncertainty_refs=("GAP1",),
    )
    repaired = _schema()
    invalid_bull_self_target = _item(
        lane="BULL",
        opposing=["BULL_INIT_1"],
        claims=[],
    )
    assert _legacy_item_validator(generic).is_valid(invalid_bull_self_target)
    assert not _item_validator(repaired).is_valid(invalid_bull_self_target)

    valid_bull_target = _item(
        lane="BULL",
        opposing=["BEAR_INIT_1"],
        claims=[],
    )
    assert _item_validator(repaired).is_valid(valid_bull_target)


def test_rebuttal_schema_binds_nested_claim_lane_to_responding_lane() -> None:
    schema = _schema()
    validator = _claim_validator(schema, CouncilLane.BULL)
    mismatched = _claim(
        lane="BEAR",
        claim_type="ARGUMENT",
        kind="INFERENCE",
        materiality="MATERIAL",
        support="SUPPORTED",
        source=True,
        computed=False,
        conflict=False,
    )
    assert not validator.is_valid(mismatched)


def test_rebuttal_fact_restatement_cannot_restate_inference_parent() -> None:
    schema = _schema(include_inference_source=True)
    validator = _claim_validator(schema, CouncilLane.BULL)
    invalid = _claim(
        claim_type="ARGUMENT",
        kind="FACT_RESTATEMENT",
        materiality="MATERIAL",
        support="SUPPORTED",
        source=True,
        computed=False,
        conflict=False,
        source_id="SRC_INF",
    )
    valid = _claim(
        claim_type="ARGUMENT",
        kind="FACT_RESTATEMENT",
        materiality="MATERIAL",
        support="SUPPORTED",
        source=True,
        computed=False,
        conflict=False,
        source_id="SRC_FACT",
    )
    assert not validator.is_valid(invalid)
    assert validator.is_valid(valid)


def test_rebuttal_schema_keeps_generic_dto_broad_but_model_output_rejects_judge_only_type() -> None:
    schema = _schema()
    assert "DECISION_BASIS" in schema["$defs"]["CouncilClaimType"]["enum"]
    for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
        item = _lane_item_branch(schema, lane)
        for branch in item["properties"]["response_proposed_claims"]["items"]["anyOf"]:
            assert branch["properties"]["claim_type"] == {
                "type": "string",
                "enum": list(REBUTTAL_ALLOWED_CLAIM_TYPES),
            }
            assert branch["properties"]["lane"] == {
                "type": "string",
                "const": lane.value,
            }


def test_rebuttal_repaired_request_preserves_prompt_input_model_and_api_safety_authority() -> None:
    bundle = _bundle()
    model_input = _model_input()
    selected = REBUTTAL_MODEL_LADDER[0]
    all_opposing = ("BULL_INIT_1", "BEAR_INIT_1", "RED_INIT_1")
    legacy = build_bounded_rebuttal_request(
        model_candidate=selected,
        bundle=bundle,
        model_input=model_input,
        initial_opinion_ids=("OP_BULL", "OP_BEAR", "OP_RED"),
        initial_opinion_hashes=("a" * 64, "b" * 64, "c" * 64),
        allowed_opposing_claim_ids=all_opposing,
        allowed_source_material_claim_ids=bundle.allowed_material_claim_ids,
        allowed_uncertainty_refs=("GAP1",),
    )
    repaired = build_bounded_rebuttal_request_v01(
        model_candidate=selected,
        bundle=bundle,
        model_input=model_input,
        initial_opinion_ids=("OP_BULL", "OP_BEAR", "OP_RED"),
        initial_opinion_hashes=("a" * 64, "b" * 64, "c" * 64),
        opposing_claim_ids_by_lane=_opposing(),
        allowed_uncertainty_refs=("GAP1",),
    )
    assert prompt_authority_unchanged(legacy, repaired)
    assert legacy.request_hash != repaired.request_hash
    assert repaired.schema_version == REBUTTAL_SCHEMA_VERSION
    assert repaired.request_payload["text"]["format"]["name"] == "b4_rebuttal_bundle_v0_1_repair"
    assert repaired.request_payload["max_output_tokens"] == 6144
    assert repaired.request_payload["model"] == selected.model
    assert repaired.request_payload["reasoning"]["effort"] == selected.reasoning_effort
    assert repaired.request_payload["store"] is False
    assert repaired.request_payload["tools"] == []
    assert repaired.request_payload["parallel_tool_calls"] is False
    assert repaired.request_payload["truncation"] == "disabled"
    assert repaired.request_payload["text"]["format"]["strict"] is True
    assert REBUTTAL_SCHEMA_REPAIR_VERSION == "B4_REBUTTAL_PROMOTION_STAGE_SCHEMA_REPAIR_v0_1"
    assert REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION == "B4_REBUTTAL_SCHEMA_PROMOTION_SEMANTICS_v0_1"
    assert REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION == "B4_REBUTTAL_OPPOSING_LANE_AUTHORITY_v0_1"
    assert REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION == "B4_REBUTTAL_CLAIM_TYPE_AUTHORITY_v0_1"
