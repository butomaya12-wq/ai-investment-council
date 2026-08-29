from __future__ import annotations

from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MODEL_RUN_RECEIPT_V1

from .model_policy import MODEL_POLICY_VERSION, ModelCandidate
from .prompts import SYNTHESIS_PROMPT_VERSION, SYNTHESIS_REPAIR_PROMPT_VERSION
from .synthesize import SYNTHESIS_SCHEMA_NAME, SynthesisInputEnvelope


class CandidatePromotionError(ValueError):
    pass


def bind_mandate_version(
    legacy_input: SynthesisInputEnvelope,
    *,
    mandate_version: str,
) -> SynthesisInputEnvelope:
    if legacy_input.mandate_version is not None:
        raise CandidatePromotionError("legacy synthesis input already has mandate_version bound")
    if not isinstance(mandate_version, str) or not mandate_version.strip():
        raise CandidatePromotionError("mandate_version must be a non-empty string")
    legacy_payload = legacy_input.model_dump(mode="python")
    promoted_payload = dict(legacy_payload)
    promoted_payload["mandate_version"] = mandate_version
    promoted = SynthesisInputEnvelope.model_validate(promoted_payload)

    left = legacy_input.model_dump(mode="json")
    right = promoted.model_dump(mode="json")
    left_mandate = left.pop("mandate_version")
    right_mandate = right.pop("mandate_version")
    if left_mandate is not None or right_mandate != mandate_version:
        raise CandidatePromotionError("mandate binding produced unexpected lineage state")
    if left != right:
        raise CandidatePromotionError("promotion changed synthesis input beyond mandate_version")
    return promoted


def build_model_run_receipt_from_synthesis_record(
    *,
    candidate: str,
    record: Mapping[str, Any],
    model_candidate: ModelCandidate,
    research_policy_version: str,
    research_snapshot_hash: str,
    synthesis_artifact_hash: str,
):
    if record.get("status") != "DRAFT_VALIDATED":
        raise CandidatePromotionError("model receipt requires DRAFT_VALIDATED synthesis record")
    repair_attempts = record.get("repair_attempts")
    if repair_attempts not in (0, 1):
        raise CandidatePromotionError("unexpected synthesis repair count")
    usage = record.get("usage")
    validators = record.get("validator_results")
    if not isinstance(usage, Mapping) or not isinstance(validators, list):
        raise CandidatePromotionError("synthesis record lacks usage/validator evidence")
    input_hash = record.get("synthesis_input_hash")
    output_hash = record.get("output_hash")
    response_id = record.get("response_id")
    if not all(isinstance(value, str) and value for value in (input_hash, output_hash, response_id)):
        raise CandidatePromotionError("synthesis record lacks model lineage hashes/response id")

    stage = "SYNTHESIS" if repair_attempts == 0 else "REPAIR"
    prompt_version = (
        SYNTHESIS_PROMPT_VERSION if repair_attempts == 0 else SYNTHESIS_REPAIR_PROMPT_VERSION
    )
    reason = None if repair_attempts == 0 else record.get("initial_validator_error")
    if repair_attempts == 1 and not isinstance(reason, str):
        raise CandidatePromotionError("repaired synthesis record lacks validator reason")

    model_run_id = f"B3_{stage}_{candidate}_{output_hash[:16]}"
    run_id = f"B3_SYNTHESIS_{synthesis_artifact_hash[:16]}"
    return MODEL_RUN_RECEIPT_V1.from_unhashed(
        model_run_id=model_run_id,
        run_id=run_id,
        candidate_id=candidate,
        stage=stage,
        prompt_version=prompt_version,
        schema_version=SYNTHESIS_SCHEMA_NAME,
        research_policy_version=research_policy_version,
        model_policy_version=MODEL_POLICY_VERSION,
        requested_model=record.get("requested_model"),
        effective_model=record.get("effective_model"),
        reasoning_effort=model_candidate.reasoning_effort,
        openai_response_id=response_id,
        input_bundle_hash=input_hash,
        research_snapshot_hash=research_snapshot_hash,
        output_hash=output_hash,
        latency_ms=record.get("latency_ms"),
        usage_input_tokens=usage.get("input_tokens"),
        usage_output_tokens=usage.get("output_tokens"),
        usage_reasoning_tokens=usage.get("reasoning_tokens"),
        usage_cached_tokens=usage.get("cached_tokens"),
        validator_results=validators,
        repair_or_escalation_reason=reason,
        final_status="DRAFT_VALIDATED",
        store=False,
        tools_enabled=False,
    )


def verify_record_hash(record: Mapping[str, Any]) -> None:
    actual = record.get("record_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise CandidatePromotionError("synthesis candidate record_hash missing")
    expected = canonical_sha256(record, exclude_fields=("record_hash",))
    if actual != expected:
        raise CandidatePromotionError("synthesis candidate record_hash mismatch")
