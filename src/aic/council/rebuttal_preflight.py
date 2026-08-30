from __future__ import annotations

from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1

from .initial_runtime import (
    INITIAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
    INITIAL_COUNCIL_FROZEN_STATUS,
    _validate_processed_record,
    request_body_utf8_bytes,
)
from .model_input import InitialCouncilModelInput
from .model_policy import REBUTTAL_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputFreezeArtifact, CouncilLane
from .rebuttal_schema_repair_v01 import (
    REBUTTAL_ALLOWED_CLAIM_TYPES,
    REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
    REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
    REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
    REBUTTAL_SCHEMA_REPAIR_VERSION,
    REBUTTAL_SCHEMA_VERSION,
    build_bounded_rebuttal_request_v01,
)


REBUTTAL_CONTEXT_VERSION = "B4_REBUTTAL_FROZEN_CONTEXT_v0_1"
REBUTTAL_SOURCE_PREFLIGHT_VERSION = "B4_REBUTTAL_SOURCE_REQUEST_PREFLIGHT_v0_1"
REBUTTAL_SOURCE_PREFLIGHT_STATUS = "PASS_ZERO_CALL_REBUTTAL_SOURCE_REQUEST_PREFLIGHT"
EXPECTED_PRODUCTION_REBUTTAL_CALLS = 3
EXPECTED_REBUTTAL_EVAL_CASE_IDS = ("E4", "E8", "E13", "E16")
EXPECTED_REBUTTAL_EVAL_PAID_CALLS_MAX = 12


class RebuttalPreflightError(ValueError):
    pass


def _verify_hash_bound_mapping(raw: Mapping[str, Any], *, hash_field: str, label: str) -> str:
    observed = raw.get(hash_field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise RebuttalPreflightError(f"{label} {hash_field} missing")
    expected = canonical_sha256(raw, exclude_fields=(hash_field,))
    if observed != expected:
        raise RebuttalPreflightError(f"{label} canonical hash mismatch")
    return observed


def verify_initial_council_freeze_for_rebuttal(
    initial_freeze: Mapping[str, Any],
    *,
    expected_artifact_hash: str | None = None,
) -> str:
    artifact_hash = _verify_hash_bound_mapping(
        initial_freeze,
        hash_field="artifact_hash",
        label="Initial Council freeze",
    )
    if expected_artifact_hash is not None and artifact_hash != expected_artifact_hash:
        raise RebuttalPreflightError("Initial Council freeze hash differs from expected production freeze")
    if initial_freeze.get("artifact_version") != INITIAL_COUNCIL_FREEZE_ARTIFACT_VERSION:
        raise RebuttalPreflightError("unexpected Initial Council freeze artifact version")
    if initial_freeze.get("status") != INITIAL_COUNCIL_FROZEN_STATUS:
        raise RebuttalPreflightError("Rebuttal requires INITIAL_COUNCIL_FROZEN status")
    if initial_freeze.get("initial_freeze_barrier") is not True:
        raise RebuttalPreflightError("Rebuttal requires crossed Initial freeze barrier")
    if initial_freeze.get("initial_opinion_count") != 9:
        raise RebuttalPreflightError("Rebuttal requires exactly nine frozen Initial opinions")
    if initial_freeze.get("dispatch_attempts") != 9 or initial_freeze.get("model_calls") != 9:
        raise RebuttalPreflightError("Initial freeze dispatch/model-call count must be exactly nine")
    if initial_freeze.get("automatic_repair_calls") != 0:
        raise RebuttalPreflightError("Initial freeze contains automatic repair calls")
    if initial_freeze.get("rebuttal_authorized") is not False:
        raise RebuttalPreflightError("Initial freeze unexpectedly pre-authorizes Rebuttal")
    if initial_freeze.get("judge_authorized") is not False:
        raise RebuttalPreflightError("Initial freeze unexpectedly pre-authorizes Judge")
    if initial_freeze.get("broker_writes") != 0 or initial_freeze.get("alpaca_orders") != 0:
        raise RebuttalPreflightError("Initial freeze contains broker/order side effect")
    if initial_freeze.get("live_money") != "PROHIBITED":
        raise RebuttalPreflightError("Initial freeze live-money invariant drift")
    records = initial_freeze.get("processed_records")
    if not isinstance(records, list) or len(records) != 9:
        raise RebuttalPreflightError("Initial freeze processed_records must contain exactly nine records")
    for raw in records:
        if not isinstance(raw, Mapping):
            raise RebuttalPreflightError("Initial processed record must be an object")
        try:
            _validate_processed_record(raw)
        except Exception as exc:
            raise RebuttalPreflightError(f"Initial processed record invalid: {exc}") from exc
    return artifact_hash


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _candidate_records(
    initial_freeze: Mapping[str, Any],
    *,
    candidate_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    records = initial_freeze["processed_records"]
    matches = [raw for raw in records if raw.get("candidate_id") == candidate_id]
    if len(matches) != 3:
        raise RebuttalPreflightError(f"{candidate_id} does not have exactly three frozen Initial records")
    expected_lanes = (CouncilLane.BULL.value, CouncilLane.BEAR.value, CouncilLane.RED_TEAM.value)
    if tuple(raw.get("lane") for raw in matches) != expected_lanes:
        raise RebuttalPreflightError(f"{candidate_id} Initial records are not ordered Bull/Bear/Red-Team")
    return tuple(matches)  # type: ignore[return-value]


def _rebuttal_model_input(
    *,
    initial_freeze_hash: str,
    candidate_input: InitialCouncilModelInput,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    frozen_opinions: list[dict[str, Any]] = []
    for raw in records:
        opinion_raw = raw.get("council_opinion")
        claims_raw = raw.get("material_claims")
        metadata_raw = raw.get("claim_metadata")
        if not isinstance(opinion_raw, Mapping) or not isinstance(claims_raw, list):
            raise RebuttalPreflightError("frozen Initial opinion/claims missing")
        if not isinstance(metadata_raw, list):
            raise RebuttalPreflightError("frozen Initial claim metadata missing")
        frozen_opinions.append(
            {
                "lane": raw["lane"],
                "council_opinion": dict(opinion_raw),
                "council_opinion_hash": raw["council_opinion_hash"],
                "material_claims": [dict(item) for item in claims_raw],
                "claim_metadata": [dict(item) for item in metadata_raw],
            }
        )
    return {
        "candidate_model_input": candidate_input.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        "initial_council": {
            "initial_freeze_artifact_hash": initial_freeze_hash,
            "initial_opinions": frozen_opinions,
        },
    }


def build_rebuttal_frozen_contexts(
    *,
    initial_freeze: Mapping[str, Any],
    freeze: CouncilInputFreezeArtifact,
    initial_model_inputs: tuple[
        InitialCouncilModelInput,
        InitialCouncilModelInput,
        InitialCouncilModelInput,
    ],
    expected_initial_freeze_hash: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    initial_freeze_hash = verify_initial_council_freeze_for_rebuttal(
        initial_freeze,
        expected_artifact_hash=expected_initial_freeze_hash,
    )
    if initial_freeze.get("b4_input_freeze_artifact_hash") != freeze.artifact_hash:
        raise RebuttalPreflightError("Initial Council freeze does not bind supplied B4 input freeze")
    if tuple(initial_freeze.get("candidate_order", ())) != freeze.candidate_order:
        raise RebuttalPreflightError("Initial Council freeze candidate order drift")
    if tuple(item.candidate_id for item in initial_model_inputs) != freeze.candidate_order:
        raise RebuttalPreflightError("Initial model-input candidate order drift")

    bundle_by_candidate = {bundle.candidate_id: bundle for bundle in freeze.bundles}
    input_by_candidate = {item.candidate_id: item for item in initial_model_inputs}
    contexts: list[dict[str, Any]] = []
    for candidate_id in freeze.candidate_order:
        bundle = bundle_by_candidate[candidate_id]
        candidate_input = input_by_candidate[candidate_id]
        records = _candidate_records(initial_freeze, candidate_id=candidate_id)

        opinion_ids: list[str] = []
        opinion_hashes: list[str] = []
        claim_ids_by_lane: dict[str, tuple[str, ...]] = {}
        uncertainty_refs: list[str] = []
        for raw in records:
            opinion_raw = raw["council_opinion"]
            try:
                opinion = COUNCIL_OPINION_V1.model_validate(dict(opinion_raw))
            except Exception as exc:
                raise RebuttalPreflightError(f"{candidate_id} frozen CouncilOpinion invalid: {exc}") from exc
            if opinion.candidate_id != candidate_id or opinion.input_snapshot_hash != bundle.bundle_hash:
                raise RebuttalPreflightError(f"{candidate_id} frozen CouncilOpinion lineage drift")
            if opinion.lane != raw["lane"]:
                raise RebuttalPreflightError(f"{candidate_id} frozen CouncilOpinion lane drift")
            if canonical_sha256(opinion) != raw["council_opinion_hash"]:
                raise RebuttalPreflightError(f"{candidate_id} frozen CouncilOpinion hash drift")

            material_claims = tuple(MATERIAL_CLAIM_V1.model_validate(item) for item in raw["material_claims"])
            material_ids = tuple(claim.claim_id for claim in material_claims)
            if material_ids != tuple(opinion.material_claim_ids):
                raise RebuttalPreflightError(f"{candidate_id} Initial opinion/material-claim closure drift")
            if any(claim.candidate_id != candidate_id for claim in material_claims):
                raise RebuttalPreflightError(f"{candidate_id} promoted Initial claim candidate drift")

            opinion_ids.append(opinion.opinion_id)
            opinion_hashes.append(raw["council_opinion_hash"])
            claim_ids_by_lane[raw["lane"]] = material_ids
            for ref in opinion.data_gap_refs:
                if ref not in uncertainty_refs:
                    uncertainty_refs.append(ref)

        required_unknown_refs = tuple(candidate_input.data_gap_refs)
        for raw in records:
            opinion_refs = set(raw["council_opinion"]["data_gap_refs"])
            if not set(required_unknown_refs).issubset(opinion_refs):
                raise RebuttalPreflightError(
                    f"{candidate_id} frozen Initial opinion hides an application-required unknown"
                )

        opposing: dict[str, list[str]] = {}
        for responding_lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
            values: list[str] = []
            for owner_lane, ids in claim_ids_by_lane.items():
                if owner_lane != responding_lane.value:
                    values.extend(ids)
            opposing[responding_lane.value] = list(_ordered_unique(values))
            if not opposing[responding_lane.value]:
                raise RebuttalPreflightError(f"{candidate_id} {responding_lane.value} has no opposing findings")

        model_input = _rebuttal_model_input(
            initial_freeze_hash=initial_freeze_hash,
            candidate_input=candidate_input,
            records=records,
        )
        context: dict[str, Any] = {
            "context_version": REBUTTAL_CONTEXT_VERSION,
            "candidate_id": candidate_id,
            "initial_freeze_artifact_hash": initial_freeze_hash,
            "b4_input_bundle_hash": bundle.bundle_hash,
            "candidate_model_input_hash": candidate_input.model_input_hash,
            "model_input": model_input,
            "model_input_hash": canonical_sha256(model_input),
            "initial_opinion_ids": opinion_ids,
            "initial_opinion_hashes": opinion_hashes,
            "initial_claim_ids_by_lane": claim_ids_by_lane,
            "opposing_claim_ids_by_lane": opposing,
            "allowed_uncertainty_refs": uncertainty_refs,
            "required_unknown_refs": list(required_unknown_refs),
        }
        context["context_hash"] = canonical_sha256(context)
        contexts.append(context)

    if tuple(item["candidate_id"] for item in contexts) != freeze.candidate_order:
        raise RebuttalPreflightError("Rebuttal context candidate order drift")
    return tuple(contexts)  # type: ignore[return-value]


def _tuple_map(raw: Mapping[str, Sequence[str]]) -> dict[CouncilLane, tuple[str, ...]]:
    result: dict[CouncilLane, tuple[str, ...]] = {}
    for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
        values = raw.get(lane.value)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise RebuttalPreflightError("Rebuttal opposing map malformed")
        result[lane] = tuple(values)
    return result


def build_rebuttal_source_request_preflight(
    *,
    contexts: Sequence[Mapping[str, Any]],
    freeze: CouncilInputFreezeArtifact,
    code_commit_sha: str,
    eval_plan: Mapping[str, Any],
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise RebuttalPreflightError("Rebuttal source preflight requires exact lowercase git commit SHA")
    if len(contexts) != 3:
        raise RebuttalPreflightError("Rebuttal source preflight requires exactly three candidate contexts")

    if eval_plan.get("plan_version") != "B4_STAGE_EVAL_PLAN_v0_1":
        raise RebuttalPreflightError("unexpected B4 stage eval plan version")
    plan_hash = _verify_hash_bound_mapping(eval_plan, hash_field="plan_hash", label="B4 stage eval plan")
    stage_plan = eval_plan.get("stages", {}).get("REBUTTAL") if isinstance(eval_plan.get("stages"), Mapping) else None
    if not isinstance(stage_plan, Mapping):
        raise RebuttalPreflightError("Rebuttal stage eval plan missing")
    if tuple(stage_plan.get("candidate_keys", ())) != tuple(item.candidate_key for item in REBUTTAL_MODEL_LADDER):
        raise RebuttalPreflightError("Rebuttal eval plan candidate ladder drift")
    if tuple(stage_plan.get("case_ids", ())) != EXPECTED_REBUTTAL_EVAL_CASE_IDS:
        raise RebuttalPreflightError("Rebuttal eval case set drift")
    if stage_plan.get("paid_call_count_max") != EXPECTED_REBUTTAL_EVAL_PAID_CALLS_MAX:
        raise RebuttalPreflightError("Rebuttal eval paid-call ceiling drift")

    bundle_by_candidate = {bundle.candidate_id: bundle for bundle in freeze.bundles}
    variants: list[dict[str, Any]] = []
    context_summaries: list[dict[str, Any]] = []
    for context in contexts:
        candidate_id = context.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in bundle_by_candidate:
            raise RebuttalPreflightError("Rebuttal context candidate missing from frozen bundle")
        observed_context_hash = context.get("context_hash")
        if observed_context_hash != canonical_sha256(context, exclude_fields=("context_hash",)):
            raise RebuttalPreflightError(f"{candidate_id} Rebuttal context hash mismatch")
        model_input = context.get("model_input")
        if not isinstance(model_input, Mapping):
            raise RebuttalPreflightError(f"{candidate_id} Rebuttal model input missing")
        opposing_raw = context.get("opposing_claim_ids_by_lane")
        if not isinstance(opposing_raw, Mapping):
            raise RebuttalPreflightError(f"{candidate_id} Rebuttal opposing map missing")
        opposing = _tuple_map(opposing_raw)
        initial_ids = tuple(context.get("initial_opinion_ids", ()))
        initial_hashes = tuple(context.get("initial_opinion_hashes", ()))
        uncertainties = tuple(context.get("allowed_uncertainty_refs", ()))

        context_summaries.append(
            {
                "candidate_id": candidate_id,
                "context_hash": observed_context_hash,
                "model_input_hash": context["model_input_hash"],
                "initial_opinion_ids": list(initial_ids),
                "initial_opinion_hashes": list(initial_hashes),
                "opposing_claim_count_by_lane": {
                    lane.value: len(opposing[lane])
                    for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM)
                },
                "allowed_uncertainty_refs": list(uncertainties),
                "required_unknown_refs": list(context.get("required_unknown_refs", ())),
            }
        )

        for candidate in REBUTTAL_MODEL_LADDER:
            request = build_bounded_rebuttal_request_v01(
                model_candidate=candidate,
                bundle=bundle_by_candidate[candidate_id],
                model_input=model_input,
                initial_opinion_ids=initial_ids,
                initial_opinion_hashes=initial_hashes,
                opposing_claim_ids_by_lane=opposing,
                allowed_uncertainty_refs=uncertainties,
            )
            if request.request_payload.get("max_output_tokens") != STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]:
                raise RebuttalPreflightError("Rebuttal output token bound drift")
            variants.append(
                {
                    "candidate": candidate_id,
                    "candidate_key": candidate.candidate_key,
                    "model": candidate.model,
                    "reasoning_effort": candidate.reasoning_effort,
                    "request_hash": request.request_hash,
                    "request_body_utf8_bytes": request_body_utf8_bytes(request.request_payload),
                    "schema_hash": canonical_sha256(request.request_payload["text"]["format"]["schema"]),
                    "prompt_contract_version": request.prompt_contract_version,
                    "prompt_version": request.prompt_version,
                    "prompt_hash": request.prompt_hash,
                    "schema_version": request.schema_version,
                    "input_hash": request.input_hash,
                    "max_output_tokens": request.request_payload["max_output_tokens"],
                }
            )

    if len(variants) != 9:
        raise RebuttalPreflightError("Rebuttal source preflight must contain three candidates x three ladder configs")
    manifest_hash = canonical_sha256(
        {
            "variants": [
                {
                    "candidate": item["candidate"],
                    "candidate_key": item["candidate_key"],
                    "request_hash": item["request_hash"],
                    "request_body_utf8_bytes": item["request_body_utf8_bytes"],
                }
                for item in variants
            ]
        }
    )
    artifact: dict[str, Any] = {
        "artifact_version": REBUTTAL_SOURCE_PREFLIGHT_VERSION,
        "status": REBUTTAL_SOURCE_PREFLIGHT_STATUS,
        "code_commit_sha": code_commit_sha,
        "b4_input_freeze_artifact_hash": freeze.artifact_hash,
        "initial_council_freeze_artifact_hash": contexts[0]["initial_freeze_artifact_hash"],
        "schema_repair_version": REBUTTAL_SCHEMA_REPAIR_VERSION,
        "schema_version": REBUTTAL_SCHEMA_VERSION,
        "promotion_semantics_contract_version": REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
        "opposing_lane_contract_version": REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
        "claim_type_contract_version": REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
        "rebuttal_allowed_claim_types": list(REBUTTAL_ALLOWED_CLAIM_TYPES),
        "candidate_order": list(freeze.candidate_order),
        "production_rebuttal_calls_after_selection": EXPECTED_PRODUCTION_REBUTTAL_CALLS,
        "model_selection_required": True,
        "selected_candidate": None,
        "eval_plan_version": eval_plan["plan_version"],
        "eval_plan_hash": plan_hash,
        "eval_candidate_keys": list(stage_plan["candidate_keys"]),
        "eval_case_ids": list(stage_plan["case_ids"]),
        "eval_paid_call_count_max": stage_plan["paid_call_count_max"],
        "candidate_contexts": context_summaries,
        "request_variants": variants,
        "request_variant_count": len(variants),
        "request_manifest_hash": manifest_hash,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "paid_eval_authorized": False,
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
