from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json

import pytest

from aic.council.bounded_request import build_bounded_initial_request
from aic.council.eval_cost import load_openai_text_pricing
from aic.council.initial_runtime import (
    INITIAL_COUNCIL_FROZEN_STATUS,
    InitialRuntimeError,
    build_initial_council_freeze_artifact,
    build_initial_runtime_plan,
    process_initial_provider_response,
    processed_response_record,
    request_body_utf8_bytes,
)
from aic.council.initial_runtime_cost import build_initial_runtime_cost_preflight
from aic.council.initial_runtime_preflight import (
    INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
    INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
    RUNTIME_REQUEST_PREFLIGHT_STATUS,
)
from aic.council.model_input import InitialCouncilModelInput, MODEL_INPUT_VERSION
from aic.council.model_policy import CouncilModelStage, OUTPUT_TOKEN_BUDGET_VERSION, STAGE_MAX_OUTPUT_TOKENS
from aic.council.model_selection import load_initial_selected_model_authority
from aic.council.models import CouncilInputBundle, CouncilInputFreezeArtifact, CouncilLane
from aic.council.policy import COUNCIL_POLICY_VERSION, JUDGE_POLICY_VERSION
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1


CANDIDATES = ("AAA", "BBB", "CCC")
MANDATE = "TEST_B4_INITIAL_RUNTIME_MANDATE"
FROZEN_AT = datetime(2026, 8, 30, 4, 0, tzinfo=UTC)


def _source_claim(candidate: str):
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id=f"B3_SOURCE_{candidate}",
        candidate_id=candidate,
        category="business_model",
        claim_text=f"{candidate} has a supported business condition.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=[f"EVID_{candidate}"],
        computed_value_ids=[],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _bundle(candidate: str, claim_id: str) -> CouncilInputBundle:
    return CouncilInputBundle.from_unhashed(
        bundle_id=f"B4_INPUT_{candidate}",
        candidate_id=candidate,
        candidate_packet_id=f"B3_PACKET_{candidate}",
        candidate_packet_hash=canonical_sha256({"packet": candidate}),
        research_snapshot_id=f"B3_RESEARCH_{candidate}",
        research_snapshot_hash=canonical_sha256({"research": candidate}),
        b2_snapshot_id="B2_TEST",
        deep_comparison_id="DC_TEST",
        mandate_version=MANDATE,
        council_policy_version=COUNCIL_POLICY_VERSION,
        judge_policy_version=JUDGE_POLICY_VERSION,
        model_policy_version="MODEL_POLICY_vB4_0_1",
        allowed_material_claim_ids=(claim_id,),
        allowed_computed_value_ids=(),
        allowed_conflict_ids=(),
        shared_portfolio_context_refs=(),
        created_at=FROZEN_AT,
    )


def _fixture():
    authority = load_initial_selected_model_authority()
    bundles = []
    model_inputs = []
    for candidate in CANDIDATES:
        claim = _source_claim(candidate)
        bundle = _bundle(candidate, claim.claim_id)
        bundles.append(bundle)
        model_inputs.append(
            InitialCouncilModelInput.from_unhashed(
                model_input_version=MODEL_INPUT_VERSION,
                candidate_id=candidate,
                council_input_bundle=bundle.model_dump(mode="json", exclude_none=False),
                candidate_packet={"candidate_id": candidate},
                material_claims=(
                    claim.model_dump(mode="json", exclude_none=False, warnings=False),
                ),
                computed_values=(),
                data_gap_refs=(),
            )
        )

    provisional = CouncilInputFreezeArtifact.model_construct(
        artifact_version="B4_COUNCIL_INPUT_FREEZE_ARTIFACT_v0_1",
        run_class="B4_LOCAL_ZERO_CALL_INPUT_FREEZE",
        b3_reconciliation_artifact_hash="1" * 64,
        b2_handoff_hash="2" * 64,
        mandate_version=MANDATE,
        candidate_order=CANDIDATES,
        bundles=tuple(bundles),
        model_calls=0,
        provider_reads=0,
        broker_writes=0,
        alpaca_orders=0,
        live_money="PROHIBITED",
        artifact_hash="0" * 64,
    )
    freeze = CouncilInputFreezeArtifact(
        **provisional.model_dump(mode="python", exclude={"artifact_hash"}),
        artifact_hash=canonical_sha256(provisional, exclude_fields=("artifact_hash",)),
    )

    variants = []
    stage_by_lane = {
        CouncilLane.BULL: CouncilRequestStage.BULL_INITIAL,
        CouncilLane.BEAR: CouncilRequestStage.BEAR_INITIAL,
        CouncilLane.RED_TEAM: CouncilRequestStage.RED_TEAM_INITIAL,
    }
    for model_input, bundle in zip(model_inputs, bundles, strict=True):
        payload = model_input.model_dump(mode="json", exclude_none=False)
        for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
            model_run_ref = f"B4_INITIAL_{model_input.candidate_id}_{lane.value}_L2_{model_input.model_input_hash[:12]}"
            request = build_bounded_initial_request(
                stage=stage_by_lane[lane],
                model_candidate=authority.selected_candidate,
                bundle=bundle,
                model_run_ref=model_run_ref,
                model_input=payload,
                allowed_data_gap_refs=(),
            )
            variants.append(
                {
                    "logical_call": f"{model_input.candidate_id}:{lane.value}",
                    "candidate": model_input.candidate_id,
                    "lane": lane.value,
                    "stage": stage_by_lane[lane].value,
                    "model_candidate_key": "L2",
                    "model": authority.selected_candidate.model,
                    "reasoning_effort": authority.selected_candidate.reasoning_effort,
                    "model_run_ref": model_run_ref,
                    "model_input_hash": model_input.model_input_hash,
                    "request_hash": request.request_hash,
                    "request_body_utf8_bytes": request_body_utf8_bytes(request.request_payload),
                    "max_output_tokens": request.request_payload["max_output_tokens"],
                }
            )

    runtime = {
        "artifact_version": INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
        "run_class": INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
        "status": RUNTIME_REQUEST_PREFLIGHT_STATUS,
        "code_commit_sha": "a" * 40,
        "source_request_preflight_artifact_hash": "3" * 64,
        "b4_input_freeze_artifact_hash": freeze.artifact_hash,
        "b3_reconciliation_artifact_hash": freeze.b3_reconciliation_artifact_hash,
        "b2_handoff_hash": freeze.b2_handoff_hash,
        "mandate_version": freeze.mandate_version,
        "selected_model_authority_version": authority.artifact_version,
        "selected_model_authority_selection_hash": authority.selection_hash,
        "selected_model_eval_artifact_hash": authority.model_eval_artifact_hash,
        "selected_candidate": authority.selected_candidate.model_dump(mode="json"),
        "candidate_order": list(CANDIDATES),
        "logical_call_count": 9,
        "planned_paid_calls_max": 9,
        "automatic_repair_calls_authorized": False,
        "output_token_budget_version": OUTPUT_TOKEN_BUDGET_VERSION,
        "max_output_tokens_per_call": STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL],
        "selected_request_variants": variants,
        "request_manifest_hash": canonical_sha256({"selected_request_variants": variants}),
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    runtime["artifact_hash"] = canonical_sha256(runtime)
    return authority, freeze, tuple(model_inputs), runtime


def _proposal(item, *, empty: bool = False) -> dict:
    claim_type = {
        CouncilLane.BULL: "ARGUMENT",
        CouncilLane.BEAR: "CHALLENGE",
        CouncilLane.RED_TEAM: "INTEGRITY_FINDING",
    }[item.lane]
    local_ref = "C1"
    claims = [] if empty else [
        {
            "claim_local_ref": local_ref,
            "candidate_id": item.candidate_id,
            "lane": item.lane.value,
            "claim_type": claim_type,
            "claim_text": f"{item.candidate_id} supplied business condition remains material.",
            "source_material_claim_ids": [f"B3_SOURCE_{item.candidate_id}"],
            "computed_value_ids": [],
            "conflict_ids": [],
            "claim_kind": "FACT_RESTATEMENT",
            "support_status": "SUPPORTED",
            "materiality": "MATERIAL",
        }
    ]
    return {
        "opinion_id": f"OP_{item.candidate_id}_{item.lane.value}",
        "candidate_id": item.candidate_id,
        "lane": item.lane.value,
        "council_input_bundle_hash": item.bundle.bundle_hash,
        "candidate_packet_hash": item.bundle.candidate_packet_hash,
        "mandate_version": item.bundle.mandate_version,
        "council_policy_version": item.bundle.council_policy_version,
        "model_policy_version": item.bundle.model_policy_version,
        "model_run_ref": item.request.request_payload["text"]["format"]["schema"]["properties"]["model_run_ref"]["const"],
        "proposed_claims": claims,
        "primary_claim_ids": [] if empty else [local_ref],
        "critical_assumption_claim_ids": [],
        "falsifier_claim_ids": [],
        "material_unknown_refs": [],
        "material_conflict_refs": [],
        "research_reopen_required": False,
        "research_reopen_reason_codes": [],
        "role_boundary_status": "VALID",
    }


def _response(item, *, empty: bool = False) -> dict:
    output = json.dumps(_proposal(item, empty=empty), ensure_ascii=False)
    return {
        "id": f"resp_{item.candidate_id}_{item.lane.value}",
        "status": "completed",
        "error": None,
        "model": "gpt-5.6-terra",
        "store": False,
        "tools": [],
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


def test_initial_runtime_plan_reconstructs_exact_nine_preflight_requests() -> None:
    authority, freeze, model_inputs, runtime = _fixture()
    plan = build_initial_runtime_plan(
        freeze=freeze,
        model_inputs=model_inputs,
        runtime_preflight=runtime,
        authority=authority,
    )
    assert len(plan) == 9
    assert tuple((item.candidate_id, item.lane.value) for item in plan) == tuple(
        (candidate, lane)
        for candidate in CANDIDATES
        for lane in ("BULL", "BEAR", "RED_TEAM")
    )
    assert all(item.request.request_payload["model"] == "gpt-5.6-terra" for item in plan)
    assert all(item.request.request_payload["reasoning"]["effort"] == "low" for item in plan)


def test_initial_runtime_request_tamper_fails_before_dispatch() -> None:
    authority, freeze, model_inputs, runtime = _fixture()
    tampered = deepcopy(runtime)
    tampered["selected_request_variants"][0]["request_hash"] = "0" * 64
    tampered["request_manifest_hash"] = canonical_sha256(
        {"selected_request_variants": tampered["selected_request_variants"]}
    )
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(InitialRuntimeError, match="request hash differs"):
        build_initial_runtime_plan(
            freeze=freeze,
            model_inputs=model_inputs,
            runtime_preflight=tampered,
            authority=authority,
        )


def test_initial_runtime_rejects_empty_structured_opinion_without_repair() -> None:
    authority, freeze, model_inputs, runtime = _fixture()
    plan = build_initial_runtime_plan(
        freeze=freeze,
        model_inputs=model_inputs,
        runtime_preflight=runtime,
        authority=authority,
    )
    pricing = load_openai_text_pricing()
    with pytest.raises(InitialRuntimeError, match="no structured Council claims"):
        process_initial_provider_response(
            plan[0],
            raw_response=_response(plan[0], empty=True),
            latency_ms=5,
            frozen_at=FROZEN_AT,
            pricing=pricing,
        )


def test_nine_promoted_opinions_cross_initial_freeze_barrier() -> None:
    authority, freeze, model_inputs, runtime = _fixture()
    plan = build_initial_runtime_plan(
        freeze=freeze,
        model_inputs=model_inputs,
        runtime_preflight=runtime,
        authority=authority,
    )
    pricing = load_openai_text_pricing()
    records = []
    total = 0
    for item in plan:
        processed = process_initial_provider_response(
            item,
            raw_response=_response(item),
            latency_ms=5,
            frozen_at=FROZEN_AT,
            pricing=pricing,
        )
        total += processed.actual_cost_usd
        records.append(processed_response_record(processed))

    cost = build_initial_runtime_cost_preflight(runtime, pricing=pricing)
    artifact = build_initial_council_freeze_artifact(
        processed_records=tuple(records),
        freeze=freeze,
        runtime_preflight=runtime,
        cost_preflight=cost,
        authority=authority,
        run_id="TEST_INITIAL_RUN",
        paid_authorization_artifact_hash="4" * 64,
        receipt_manifest_hash="5" * 64,
        actual_cost_usd_total=total,
    )
    assert artifact["status"] == INITIAL_COUNCIL_FROZEN_STATUS
    assert artifact["initial_opinion_count"] == 9
    assert artifact["model_calls"] == 9
    assert artifact["automatic_repair_calls"] == 0
    assert artifact["rebuttal_authorized"] is False
    assert artifact["judge_authorized"] is False


def test_eight_opinions_cannot_cross_initial_freeze_barrier() -> None:
    authority, freeze, model_inputs, runtime = _fixture()
    plan = build_initial_runtime_plan(
        freeze=freeze,
        model_inputs=model_inputs,
        runtime_preflight=runtime,
        authority=authority,
    )
    pricing = load_openai_text_pricing()
    records = tuple(
        processed_response_record(
            process_initial_provider_response(
                item,
                raw_response=_response(item),
                latency_ms=5,
                frozen_at=FROZEN_AT,
                pricing=pricing,
            )
        )
        for item in plan[:8]
    )
    cost = build_initial_runtime_cost_preflight(runtime, pricing=pricing)
    with pytest.raises(InitialRuntimeError, match="exactly nine"):
        build_initial_council_freeze_artifact(
            processed_records=records,
            freeze=freeze,
            runtime_preflight=runtime,
            cost_preflight=cost,
            authority=authority,
            run_id="TEST_INITIAL_RUN",
            paid_authorization_artifact_hash="4" * 64,
            receipt_manifest_hash="5" * 64,
            actual_cost_usd_total=0,
        )
