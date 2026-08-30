from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from time import perf_counter_ns
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1

from .initial_runtime_cost_v02 import actual_cost_usd
from .models import CouncilInputFreezeArtifact
from .proposal import (
    CouncilClaimMetadata,
    FrozenRebuttalBundle,
    RebuttalBundleDraft,
)
from .rebuttal_model_selection_v02 import (
    verify_rebuttal_selected_model_authority_v02,
)
from .rebuttal_promotion import promote_rebuttal_bundle
from .rebuttal_runtime import (
    EXPECTED_PRODUCTION_CALLS,
    REBUTTAL_RUNTIME_VERSION,
    RebuttalRuntimePlanItem,
)
from .rebuttal_runtime_preflight import (
    EXPECTED_INITIAL_FREEZE_HASH,
    EXPECTED_SELECTED,
    EXPECTED_SELECTION_HASH,
    verify_rebuttal_runtime_cost_preflight,
    verify_rebuttal_runtime_request_preflight,
)
from .request import parse_council_responses_payload


REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION = (
    "B4_REBUTTAL_COUNCIL_FREEZE_ARTIFACT_v0_1"
)
REBUTTAL_COUNCIL_FREEZE_RUN_CLASS = "B4_REAL_SELECTED_MODEL_REBUTTAL_COUNCIL"
REBUTTAL_COUNCIL_FROZEN_STATUS = "REBUTTAL_COUNCIL_FROZEN"
REBUTTAL_COUNCIL_BLOCKED_STATUS = "BLOCKED_REBUTTAL_COUNCIL_NOT_FROZEN"


class RebuttalRuntimeExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RebuttalProductionCallRun:
    candidate_id: str
    request_hash: str
    response_id: str | None
    effective_model: str | None
    latency_ms: int
    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    actual_cost_usd: Decimal | None
    cost_receipt_status: str
    output_hash: str | None
    structured_output: Mapping[str, Any] | None
    structured_output_hash: str | None
    processed_record: Mapping[str, Any] | None
    validation_status: str
    validation_error: str | None
    model_calls: int


def _usage_counts(raw: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        raise RebuttalRuntimeExecutionError("provider response lacks usage")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    if type(input_tokens) is not int or input_tokens < 0:
        raise RebuttalRuntimeExecutionError("usage.input_tokens invalid")
    if type(output_tokens) is not int or output_tokens < 0:
        raise RebuttalRuntimeExecutionError("usage.output_tokens invalid")
    if not isinstance(input_details, Mapping):
        raise RebuttalRuntimeExecutionError("usage.input_tokens_details missing")
    cached_tokens = input_details.get("cached_tokens")
    cache_write_tokens = input_details.get("cache_write_tokens")
    if type(cached_tokens) is not int or cached_tokens < 0:
        raise RebuttalRuntimeExecutionError("usage.cached_tokens invalid")
    if type(cache_write_tokens) is not int or cache_write_tokens < 0:
        raise RebuttalRuntimeExecutionError("usage.cache_write_tokens invalid")
    if cached_tokens + cache_write_tokens > input_tokens:
        raise RebuttalRuntimeExecutionError(
            "cached_tokens + cache_write_tokens exceed input_tokens"
        )
    reasoning_tokens = 0
    if isinstance(output_details, Mapping):
        value = output_details.get("reasoning_tokens")
        if value is not None:
            if type(value) is not int or value < 0:
                raise RebuttalRuntimeExecutionError("usage.reasoning_tokens invalid")
            reasoning_tokens = value
    return (
        input_tokens,
        cached_tokens,
        cache_write_tokens,
        output_tokens,
        reasoning_tokens,
    )


def _candidate_initial_records(
    initial_freeze: Mapping[str, Any],
    *,
    candidate_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    records = initial_freeze.get("processed_records")
    if not isinstance(records, list):
        raise RebuttalRuntimeExecutionError("Initial freeze processed records missing")
    matches = [
        record
        for record in records
        if isinstance(record, Mapping) and record.get("candidate_id") == candidate_id
    ]
    if len(matches) != 3:
        raise RebuttalRuntimeExecutionError(
            f"{candidate_id} requires exactly three frozen Initial records"
        )
    if tuple(record.get("lane") for record in matches) != (
        "BULL",
        "BEAR",
        "RED_TEAM",
    ):
        raise RebuttalRuntimeExecutionError(
            f"{candidate_id} Initial records are not Bull/Bear/Red-Team ordered"
        )
    return tuple(matches)  # type: ignore[return-value]


def _processed_record(
    *,
    item: RebuttalRuntimePlanItem,
    call: Any,
    proposal: RebuttalBundleDraft,
    promotion: Any,
    latency_ms: int,
    usage: tuple[int, int, int, int, int],
    actual_cost: Decimal,
) -> dict[str, Any]:
    input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens = usage
    structured = proposal.model_dump(mode="json", exclude_none=False)
    frozen = promotion.frozen_rebuttal_bundle
    record: dict[str, Any] = {
        "candidate_id": item.candidate_id,
        "context_hash": item.context_hash,
        "request_hash": item.request.request_hash,
        "response_id": call.response_id,
        "effective_model": call.effective_model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "actual_cost_usd": str(actual_cost),
        "output_hash": call.output_hash,
        "structured_output": structured,
        "structured_output_hash": canonical_sha256(structured),
        "frozen_rebuttal_bundle": frozen.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        "rebuttal_bundle_id": frozen.draft.rebuttal_bundle_id,
        "rebuttal_bundle_hash": frozen.bundle_hash,
        "material_claims": [
            claim.model_dump(mode="json", exclude_none=False, warnings=False)
            for claim in promotion.material_claims
        ],
        "claim_metadata": [
            metadata.model_dump(mode="json", exclude_none=False)
            for metadata in promotion.claim_metadata
        ],
        "validator_results": [dict(row) for row in promotion.validator_results],
        "research_reopen_required": frozen.draft.research_reopen_required,
        "research_reopen_reason_codes": list(
            frozen.draft.research_reopen_reason_codes
        ),
        "required_unknown_refs": list(item.required_unknown_refs),
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def validate_rebuttal_processed_record(raw: Mapping[str, Any]) -> None:
    record_hash = raw.get("record_hash")
    if not isinstance(record_hash, str) or record_hash != canonical_sha256(
        raw, exclude_fields=("record_hash",)
    ):
        raise RebuttalRuntimeExecutionError("Rebuttal processed record hash mismatch")
    candidate_id = raw.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise RebuttalRuntimeExecutionError("Rebuttal processed candidate missing")
    context_hash = raw.get("context_hash")
    request_hash = raw.get("request_hash")
    if not isinstance(context_hash, str) or len(context_hash) != 64:
        raise RebuttalRuntimeExecutionError("Rebuttal processed context hash missing")
    if not isinstance(request_hash, str) or len(request_hash) != 64:
        raise RebuttalRuntimeExecutionError("Rebuttal processed request hash missing")
    structured = raw.get("structured_output")
    if not isinstance(structured, Mapping) or raw.get(
        "structured_output_hash"
    ) != canonical_sha256(structured):
        raise RebuttalRuntimeExecutionError(
            "Rebuttal processed structured output hash mismatch"
        )
    draft = RebuttalBundleDraft.model_validate(dict(structured))
    if draft.candidate_id != candidate_id:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal processed draft candidate identity drift"
        )
    frozen_raw = raw.get("frozen_rebuttal_bundle")
    if not isinstance(frozen_raw, Mapping):
        raise RebuttalRuntimeExecutionError("frozen Rebuttal bundle missing")
    frozen = FrozenRebuttalBundle.model_validate(dict(frozen_raw))
    if frozen.draft != draft:
        raise RebuttalRuntimeExecutionError(
            "frozen Rebuttal draft differs from structured output"
        )
    if raw.get("rebuttal_bundle_id") != frozen.draft.rebuttal_bundle_id:
        raise RebuttalRuntimeExecutionError("Rebuttal bundle ID drift")
    if raw.get("rebuttal_bundle_hash") != frozen.bundle_hash:
        raise RebuttalRuntimeExecutionError("Rebuttal bundle hash drift")
    claims = raw.get("material_claims")
    if not isinstance(claims, list):
        raise RebuttalRuntimeExecutionError("Rebuttal promoted claims malformed")
    claim_ids: set[str] = set()
    for claim_raw in claims:
        claim = MATERIAL_CLAIM_V1.model_validate(claim_raw)
        if claim.candidate_id != candidate_id:
            raise RebuttalRuntimeExecutionError(
                "Rebuttal promoted claim candidate drift"
            )
        if claim.claim_id in claim_ids:
            raise RebuttalRuntimeExecutionError(
                "duplicate Rebuttal promoted claim ID"
            )
        claim_ids.add(claim.claim_id)
    metadata_rows = raw.get("claim_metadata")
    if not isinstance(metadata_rows, list):
        raise RebuttalRuntimeExecutionError("Rebuttal claim metadata malformed")
    metadata_claim_ids: set[str] = set()
    for metadata_raw in metadata_rows:
        metadata = CouncilClaimMetadata.model_validate(metadata_raw)
        if metadata.material_claim_id not in claim_ids:
            raise RebuttalRuntimeExecutionError(
                "Rebuttal metadata points outside promoted claims"
            )
        metadata_claim_ids.add(metadata.material_claim_id)
    if metadata_claim_ids != claim_ids:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal promoted claim/metadata closure drift"
        )
    validators = raw.get("validator_results")
    if not isinstance(validators, list) or not validators:
        raise RebuttalRuntimeExecutionError("Rebuttal validator results missing")
    if any(
        not isinstance(row, Mapping) or row.get("status") != "PASS"
        for row in validators
    ):
        raise RebuttalRuntimeExecutionError("Rebuttal validator result is not PASS")
    required_unknowns = raw.get("required_unknown_refs")
    if not isinstance(required_unknowns, list) or not all(
        isinstance(value, str) for value in required_unknowns
    ):
        raise RebuttalRuntimeExecutionError("Rebuttal required unknown refs malformed")
    for item in frozen.draft.items:
        if not set(required_unknowns).issubset(item.remaining_uncertainty_refs):
            raise RebuttalRuntimeExecutionError(
                "Rebuttal frozen bundle erased required material unknown"
            )


def execute_rebuttal_runtime_item_once(
    item: RebuttalRuntimePlanItem,
    *,
    initial_freeze: Mapping[str, Any],
    api_key: str,
    transport: Any,
    pricing: Mapping[str, Any],
    frozen_at: datetime,
) -> RebuttalProductionCallRun:
    started = perf_counter_ns()
    raw: Mapping[str, Any] | None = None
    response_id: str | None = None
    effective_model: str | None = None
    output_hash: str | None = None
    structured_output: Mapping[str, Any] | None = None
    structured_output_hash: str | None = None
    processed: Mapping[str, Any] | None = None
    validation_error: str | None = None
    usage: tuple[int, int, int, int, int] | None = None
    cost: Decimal | None = None
    cost_status = "INCOMPLETE"

    try:
        raw_value = transport.post(
            payload=item.request.request_payload,
            api_key=api_key,
        )
        if not isinstance(raw_value, Mapping):
            raise RebuttalRuntimeExecutionError(
                "Responses payload must be an object"
            )
        raw = raw_value
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        call, proposal = parse_council_responses_payload(
            raw,
            request=item.request,
            latency_ms=latency_ms,
        )
        response_id = call.response_id
        effective_model = call.effective_model
        output_hash = call.output_hash
        if not isinstance(proposal, RebuttalBundleDraft):
            raise RebuttalRuntimeExecutionError(
                "Rebuttal production response produced wrong DTO type"
            )
        structured_output = proposal.model_dump(mode="json", exclude_none=False)
        structured_output_hash = canonical_sha256(structured_output)
        initial_records = _candidate_initial_records(
            initial_freeze,
            candidate_id=item.candidate_id,
        )
        promotion = promote_rebuttal_bundle(
            proposal,
            bundle=item.bundle,
            model_input=item.model_input,
            initial_records=initial_records,
            required_unknown_refs=item.required_unknown_refs,
        )
        usage = _usage_counts(raw)
        model = item.request.request_payload.get("model")
        if not isinstance(model, str) or not model:
            raise RebuttalRuntimeExecutionError("production request model missing")
        cost = actual_cost_usd(raw, model=model, pricing=pricing)
        cost_status = "COMPLETE"
        processed = _processed_record(
            item=item,
            call=call,
            proposal=proposal,
            promotion=promotion,
            latency_ms=latency_ms,
            usage=usage,
            actual_cost=cost,
        )
        validate_rebuttal_processed_record(processed)
    except Exception as exc:
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        validation_error = f"{type(exc).__name__}: {exc}"
        if raw is not None and cost_status != "COMPLETE":
            try:
                usage = _usage_counts(raw)
                model = item.request.request_payload.get("model")
                if not isinstance(model, str) or not model:
                    raise RebuttalRuntimeExecutionError(
                        "production request model missing"
                    )
                cost = actual_cost_usd(raw, model=model, pricing=pricing)
                cost_status = "COMPLETE"
            except Exception as cost_exc:
                validation_error += (
                    f"; cost receipt: {type(cost_exc).__name__}: {cost_exc}"
                )

    if usage is None:
        input_tokens = cached_tokens = cache_write_tokens = None
        output_tokens = reasoning_tokens = None
    else:
        (
            input_tokens,
            cached_tokens,
            cache_write_tokens,
            output_tokens,
            reasoning_tokens,
        ) = usage

    return RebuttalProductionCallRun(
        candidate_id=item.candidate_id,
        request_hash=item.request.request_hash,
        response_id=response_id,
        effective_model=effective_model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        actual_cost_usd=cost,
        cost_receipt_status=cost_status,
        output_hash=output_hash,
        structured_output=structured_output,
        structured_output_hash=structured_output_hash,
        processed_record=processed,
        validation_status="PASS" if processed is not None else "FAIL",
        validation_error=validation_error,
        model_calls=1 if raw is not None else 0,
    )


def build_rebuttal_council_freeze_artifact(
    *,
    processed_records: Sequence[Mapping[str, Any]],
    freeze: CouncilInputFreezeArtifact,
    runtime_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    selection_authority: Mapping[str, Any],
    run_id: str,
    paid_authorization_artifact_hash: str,
    receipt_manifest_hash: str,
    actual_cost_usd_total: Decimal,
) -> dict[str, Any]:
    runtime_hash = verify_rebuttal_runtime_request_preflight(runtime_preflight)
    cost_hash = verify_rebuttal_runtime_cost_preflight(cost_preflight)
    selection_hash = verify_rebuttal_selected_model_authority_v02(
        selection_authority
    )
    if selection_hash != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimeExecutionError("Rebuttal freeze selection hash drift")
    if selection_authority.get("selected_candidate") != EXPECTED_SELECTED:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal freeze selected model is not frozen R3"
        )
    if runtime_preflight.get(
        "selected_model_authority_selection_hash"
    ) != selection_hash:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal freeze request preflight selection mismatch"
        )
    if cost_preflight.get(
        "runtime_request_preflight_artifact_hash"
    ) != runtime_hash:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal freeze cost preflight request binding mismatch"
        )
    if runtime_preflight.get(
        "initial_council_freeze_artifact_hash"
    ) != EXPECTED_INITIAL_FREEZE_HASH:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal freeze Initial Council binding drift"
        )
    if tuple(runtime_preflight.get("candidate_order", ())) != freeze.candidate_order:
        raise RebuttalRuntimeExecutionError(
            "Rebuttal freeze candidate order differs from B4 input freeze"
        )
    if len(processed_records) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError(
            "REBUTTAL_COUNCIL_FROZEN requires exactly three processed bundles"
        )

    candidate_ids: list[str] = []
    bundle_ids: list[str] = []
    bundle_hashes: list[str] = []
    research_reopen_candidates: list[str] = []
    for raw in processed_records:
        validate_rebuttal_processed_record(raw)
        candidate_id = raw.get("candidate_id")
        bundle_id = raw.get("rebuttal_bundle_id")
        bundle_hash = raw.get("rebuttal_bundle_hash")
        if not isinstance(candidate_id, str):
            raise RebuttalRuntimeExecutionError(
                "Rebuttal processed candidate identity missing"
            )
        if not isinstance(bundle_id, str) or not bundle_id:
            raise RebuttalRuntimeExecutionError("Rebuttal bundle ID missing")
        if not isinstance(bundle_hash, str) or len(bundle_hash) != 64:
            raise RebuttalRuntimeExecutionError("Rebuttal bundle hash missing")
        candidate_ids.append(candidate_id)
        bundle_ids.append(bundle_id)
        bundle_hashes.append(bundle_hash)
        if raw.get("research_reopen_required") is True:
            research_reopen_candidates.append(candidate_id)
    if tuple(candidate_ids) != freeze.candidate_order:
        raise RebuttalRuntimeExecutionError(
            "REBUTTAL_COUNCIL_FROZEN candidate order/coverage drift"
        )
    if len(set(bundle_ids)) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError(
            "REBUTTAL_COUNCIL_FROZEN requires three unique bundle IDs"
        )
    if len(set(bundle_hashes)) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError(
            "REBUTTAL_COUNCIL_FROZEN requires three unique bundle hashes"
        )

    artifact: dict[str, Any] = {
        "artifact_version": REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
        "runtime_version": REBUTTAL_RUNTIME_VERSION,
        "run_class": REBUTTAL_COUNCIL_FREEZE_RUN_CLASS,
        "status": REBUTTAL_COUNCIL_FROZEN_STATUS,
        "run_id": run_id,
        "code_commit_sha": runtime_preflight["code_commit_sha"],
        "b4_input_freeze_artifact_hash": freeze.artifact_hash,
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "source_request_preflight_artifact_hash": runtime_preflight[
            "source_request_preflight_artifact_hash"
        ],
        "runtime_request_preflight_artifact_hash": runtime_hash,
        "runtime_request_manifest_hash": runtime_preflight[
            "request_manifest_hash"
        ],
        "runtime_cost_preflight_artifact_hash": cost_hash,
        "selected_model_authority_selection_hash": selection_hash,
        "selected_candidate": dict(EXPECTED_SELECTED),
        "paid_authorization_artifact_hash": paid_authorization_artifact_hash,
        "candidate_order": list(freeze.candidate_order),
        "rebuttal_bundle_count": EXPECTED_PRODUCTION_CALLS,
        "rebuttal_bundle_ids": bundle_ids,
        "rebuttal_bundle_hashes": bundle_hashes,
        "processed_records": [dict(row) for row in processed_records],
        "research_reopen_required_candidates": research_reopen_candidates,
        "dispatch_attempts": EXPECTED_PRODUCTION_CALLS,
        "model_calls": EXPECTED_PRODUCTION_CALLS,
        "automatic_repair_calls": 0,
        "judge_model_calls": 0,
        "actual_cost_usd": str(actual_cost_usd_total),
        "cost_receipt_status": "COMPLETE",
        "receipt_manifest_hash": receipt_manifest_hash,
        "rebuttal_freeze_barrier": True,
        "judge_authorized": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_rebuttal_council_freeze_artifact(
    payload: Mapping[str, Any],
) -> str:
    artifact_hash = payload.get("artifact_hash")
    if not isinstance(artifact_hash, str) or artifact_hash != canonical_sha256(
        payload, exclude_fields=("artifact_hash",)
    ):
        raise RebuttalRuntimeExecutionError(
            "Rebuttal Council freeze artifact hash mismatch"
        )
    if payload.get("artifact_version") != REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION:
        raise RebuttalRuntimeExecutionError(
            "unexpected Rebuttal Council freeze artifact version"
        )
    if payload.get("runtime_version") != REBUTTAL_RUNTIME_VERSION:
        raise RebuttalRuntimeExecutionError("Rebuttal Council runtime version drift")
    if payload.get("run_class") != REBUTTAL_COUNCIL_FREEZE_RUN_CLASS:
        raise RebuttalRuntimeExecutionError("Rebuttal Council run class drift")
    if payload.get("status") != REBUTTAL_COUNCIL_FROZEN_STATUS:
        raise RebuttalRuntimeExecutionError("Rebuttal Council is not frozen")
    if payload.get("initial_council_freeze_artifact_hash") != EXPECTED_INITIAL_FREEZE_HASH:
        raise RebuttalRuntimeExecutionError("Rebuttal Council Initial freeze drift")
    if payload.get("selected_model_authority_selection_hash") != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimeExecutionError("Rebuttal Council selection drift")
    if payload.get("selected_candidate") != EXPECTED_SELECTED:
        raise RebuttalRuntimeExecutionError("Rebuttal Council selected config drift")
    if payload.get("rebuttal_bundle_count") != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError("Rebuttal Council bundle count drift")
    if payload.get("dispatch_attempts") != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError("Rebuttal Council dispatch count drift")
    if payload.get("model_calls") != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError("Rebuttal Council model-call count drift")
    if payload.get("automatic_repair_calls") != 0:
        raise RebuttalRuntimeExecutionError("Rebuttal Council contains repair calls")
    if payload.get("rebuttal_freeze_barrier") is not True:
        raise RebuttalRuntimeExecutionError("Rebuttal Council freeze barrier missing")
    if payload.get("cost_receipt_status") != "COMPLETE":
        raise RebuttalRuntimeExecutionError("Rebuttal Council cost receipts incomplete")
    records = payload.get("processed_records")
    if not isinstance(records, list) or len(records) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeExecutionError("Rebuttal Council processed records missing")
    observed_candidates: list[str] = []
    observed_bundle_ids: list[str] = []
    observed_bundle_hashes: list[str] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            raise RebuttalRuntimeExecutionError("Rebuttal processed record malformed")
        validate_rebuttal_processed_record(raw)
        observed_candidates.append(str(raw["candidate_id"]))
        observed_bundle_ids.append(str(raw["rebuttal_bundle_id"]))
        observed_bundle_hashes.append(str(raw["rebuttal_bundle_hash"]))
    if observed_candidates != payload.get("candidate_order"):
        raise RebuttalRuntimeExecutionError("Rebuttal Council candidate order drift")
    if observed_bundle_ids != payload.get("rebuttal_bundle_ids"):
        raise RebuttalRuntimeExecutionError("Rebuttal Council bundle ID list drift")
    if observed_bundle_hashes != payload.get("rebuttal_bundle_hashes"):
        raise RebuttalRuntimeExecutionError("Rebuttal Council bundle hash list drift")
    if payload.get("judge_model_calls") != 0 or payload.get("judge_authorized") is not False:
        raise RebuttalRuntimeExecutionError("Rebuttal Council unexpectedly authorizes Judge")
    if payload.get("rerun_authorized") is not False:
        raise RebuttalRuntimeExecutionError("Rebuttal Council unexpectedly authorizes rerun")
    for field in ("provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise RebuttalRuntimeExecutionError(
                f"Rebuttal Council side-effect invariant violated: {field}"
            )
    if payload.get("live_money") != "PROHIBITED":
        raise RebuttalRuntimeExecutionError("Rebuttal Council live-money invariant drift")
    return artifact_hash
