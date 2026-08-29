from __future__ import annotations

from typing import Any, Mapping, Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import CANDIDATE_PACKET_V1, MATERIAL_CLAIM_V1
from aic.research.handoff import B2RealEventHandoff, EXPECTED_TOP3

from .models import B4Model, CouncilInputFreezeArtifact


MODEL_INPUT_VERSION = "B4_INITIAL_MODEL_INPUT_v0_1"
EXPECTED_B3_RECONCILIATION_ARTIFACT_VERSION = "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1"
EXPECTED_B3_RECONCILIATION_STATUS = "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED"


class CouncilModelInputError(ValueError):
    pass


class B4ComputedValueView(B4Model):
    computed_value_id: str
    metric_id: str
    value: str
    unit: str

    @field_validator("computed_value_id", "metric_id", "value", "unit")
    @classmethod
    def _trimmed(cls, value: str, info) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty trimmed string")
        return value


class InitialCouncilModelInput(B4Model):
    model_input_version: str
    candidate_id: str
    council_input_bundle: Mapping[str, Any]
    candidate_packet: Mapping[str, Any]
    material_claims: tuple[Mapping[str, Any], ...]
    computed_values: tuple[B4ComputedValueView, ...]
    data_gap_refs: tuple[str, ...]
    model_input_hash: str

    @field_validator("candidate_id")
    @classmethod
    def _candidate(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip() or value != value.upper():
            raise ValueError("candidate_id must be canonical uppercase")
        return value

    @field_validator("data_gap_refs")
    @classmethod
    def _gap_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("data_gap_refs must be unique")
        for item in value:
            if not isinstance(item, str) or not item or item != item.strip():
                raise ValueError("data_gap_refs must be non-empty trimmed strings")
        return value

    @field_validator("model_input_hash")
    @classmethod
    def _hash_shape(cls, value: str) -> str:
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("model_input_hash must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _self_hash(self) -> Self:
        if self.model_input_version != MODEL_INPUT_VERSION:
            raise ValueError("unexpected B4 initial model input version")
        expected = canonical_sha256(self, exclude_fields=("model_input_hash",))
        if self.model_input_hash != expected:
            raise ValueError("B4 initial model_input_hash mismatch")
        return self

    @classmethod
    def from_unhashed(cls, **values: object) -> "InitialCouncilModelInput":
        if "model_input_hash" in values:
            raise ValueError("model_input_hash is application-generated")
        provisional = cls.model_construct(**values, model_input_hash="0" * 64)
        return cls(
            **values,
            model_input_hash=canonical_sha256(provisional, exclude_fields=("model_input_hash",)),
        )


def _verify_reconciliation_hash(reconciliation: Mapping[str, Any]) -> str:
    actual = reconciliation.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise CouncilModelInputError("B3 reconciliation artifact_hash missing")
    expected = canonical_sha256(reconciliation, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise CouncilModelInputError("B3 reconciliation artifact_hash mismatch")
    return actual


def _candidate_records(reconciliation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = reconciliation.get("candidates")
    if not isinstance(raw, list):
        raise CouncilModelInputError("B3 reconciliation candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise CouncilModelInputError("B3 reconciliation candidate record must be object")
        candidate = item.get("candidate")
        if not isinstance(candidate, str) or candidate in result:
            raise CouncilModelInputError("B3 reconciliation candidate identity invalid")
        result[candidate] = item
    if tuple(result) != EXPECTED_TOP3:
        raise CouncilModelInputError("B4 model input requires exact frozen top-3 order")
    return result


def build_initial_model_inputs(
    freeze: CouncilInputFreezeArtifact,
    reconciliation: Mapping[str, Any],
    handoff: B2RealEventHandoff,
) -> tuple[InitialCouncilModelInput, InitialCouncilModelInput, InitialCouncilModelInput]:
    """Build exactly three model-facing initial Council inputs with zero I/O."""

    reconciliation_hash = _verify_reconciliation_hash(reconciliation)
    if reconciliation_hash != freeze.b3_reconciliation_artifact_hash:
        raise CouncilModelInputError("B4 freeze does not bind supplied B3 reconciliation")
    if reconciliation.get("artifact_version") != EXPECTED_B3_RECONCILIATION_ARTIFACT_VERSION:
        raise CouncilModelInputError("unexpected B3 reconciliation artifact version")
    if reconciliation.get("canonical_reconciliation") != EXPECTED_B3_RECONCILIATION_STATUS:
        raise CouncilModelInputError("B3 reconciliation is not final")
    if reconciliation.get("reconstructibility_status") != "PASS":
        raise CouncilModelInputError("B3 reconciliation is not reconstructible")
    if handoff.handoff_hash != freeze.b2_handoff_hash:
        raise CouncilModelInputError("B4 freeze does not bind supplied B2 handoff")
    if reconciliation.get("handoff_hash") != handoff.handoff_hash:
        raise CouncilModelInputError("B3 reconciliation/B2 handoff mismatch")
    if reconciliation.get("mandate_version") != freeze.mandate_version:
        raise CouncilModelInputError("B3 reconciliation/B4 mandate mismatch")
    if freeze.candidate_order != EXPECTED_TOP3:
        raise CouncilModelInputError("B4 freeze candidate order drift")

    records = _candidate_records(reconciliation)
    outputs: list[InitialCouncilModelInput] = []
    for bundle in freeze.bundles:
        candidate = bundle.candidate_id
        record = records[candidate]
        if record.get("status") != "CANONICAL_RECONCILED":
            raise CouncilModelInputError(f"{candidate} is not CANONICAL_RECONCILED")
        if record.get("reconstructibility_status") != "PASS":
            raise CouncilModelInputError(f"{candidate} reconciliation is not reconstructible")
        if record.get("bundle_hash") != bundle.research_snapshot_hash:
            raise CouncilModelInputError(f"{candidate} research snapshot hash mismatch")

        packet_raw = record.get("candidate_packet")
        claims_raw = record.get("material_claims")
        if not isinstance(packet_raw, Mapping) or not isinstance(claims_raw, list):
            raise CouncilModelInputError(f"{candidate} canonical packet/claims missing")
        try:
            packet = CANDIDATE_PACKET_V1.model_validate(dict(packet_raw))
            claims = tuple(
                MATERIAL_CLAIM_V1.model_validate(dict(item))
                for item in claims_raw
                if isinstance(item, Mapping)
            )
        except Exception as exc:
            raise CouncilModelInputError(f"{candidate} canonical B3 objects invalid") from exc
        if len(claims) != len(claims_raw):
            raise CouncilModelInputError(f"{candidate} malformed MaterialClaim record")
        if packet.candidate_id != candidate or packet.candidate_packet_id != bundle.candidate_packet_id:
            raise CouncilModelInputError(f"{candidate} CandidatePacket identity mismatch")
        if packet.packet_hash != bundle.candidate_packet_hash:
            raise CouncilModelInputError(f"{candidate} CandidatePacket hash mismatch")
        if packet.b2_snapshot_id != bundle.b2_snapshot_id or packet.b2_snapshot_id != handoff.b2_snapshot_ref:
            raise CouncilModelInputError(f"{candidate} B2 snapshot lineage mismatch")
        if packet.deep_comparison_id != bundle.deep_comparison_id or packet.deep_comparison_id != handoff.deep_comparison_ref:
            raise CouncilModelInputError(f"{candidate} deep-comparison lineage mismatch")
        if packet.mandate_version != bundle.mandate_version:
            raise CouncilModelInputError(f"{candidate} mandate lineage mismatch")

        claim_ids = tuple(claim.claim_id for claim in claims)
        if claim_ids != bundle.allowed_material_claim_ids:
            raise CouncilModelInputError(f"{candidate} MaterialClaim allowlist/order mismatch")
        if any(claim.candidate_id != candidate for claim in claims):
            raise CouncilModelInputError(f"{candidate} MaterialClaim candidate mismatch")

        handoff_candidate = handoff.candidate(candidate)
        computed = tuple(
            B4ComputedValueView(
                computed_value_id=item.computed_value_id,
                metric_id=item.metric_id,
                value=item.value,
                unit=item.unit,
            )
            for item in handoff_candidate.metrics
        )
        computed_ids = tuple(item.computed_value_id for item in computed)
        if computed_ids != bundle.allowed_computed_value_ids:
            raise CouncilModelInputError(f"{candidate} ComputedValue allowlist/order mismatch")
        if tuple(packet.computed_value_ids) != computed_ids:
            raise CouncilModelInputError(f"{candidate} CandidatePacket ComputedValue closure mismatch")

        record_gaps_raw = record.get("source_gaps")
        if not isinstance(record_gaps_raw, list) or any(not isinstance(item, str) for item in record_gaps_raw):
            raise CouncilModelInputError(f"{candidate} reconciliation source_gaps invalid")
        record_gaps = tuple(record_gaps_raw)
        packet_gaps = tuple(packet.source_gaps)
        if record_gaps != packet_gaps:
            raise CouncilModelInputError(f"{candidate} source-gap lineage mismatch")

        outputs.append(
            InitialCouncilModelInput.from_unhashed(
                model_input_version=MODEL_INPUT_VERSION,
                candidate_id=candidate,
                council_input_bundle=bundle.model_dump(mode="json", exclude_none=False),
                candidate_packet=packet.model_dump(mode="json", exclude_none=False, warnings=False),
                material_claims=tuple(
                    claim.model_dump(mode="json", exclude_none=False, warnings=False)
                    for claim in claims
                ),
                computed_values=computed,
                data_gap_refs=packet_gaps,
            )
        )

    if tuple(item.candidate_id for item in outputs) != EXPECTED_TOP3:
        raise CouncilModelInputError("B4 model-input output order drift")
    return tuple(outputs)  # type: ignore[return-value]
