from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import CANDIDATE_PACKET_V1, MATERIAL_CLAIM_V1
from aic.research.handoff import EXPECTED_TOP3

from .model_policy import MODEL_POLICY_VERSION
from .models import CouncilInputBundle, CouncilInputFreezeArtifact
from .policy import COUNCIL_POLICY_VERSION, JUDGE_POLICY_VERSION


EXPECTED_B3_RECONCILIATION_ARTIFACT_VERSION = "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1"
EXPECTED_B3_RECONCILIATION_RUN_CLASS = "B3_SELECTED_MODEL_REAL_CANDIDATE_RECONCILIATION"
EXPECTED_B3_RECONCILIATION_STATUS = "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED"

_PACKET_CLAIM_GROUP_FIELDS = (
    "business_model_claim_ids",
    "growth_quality_claim_ids",
    "financial_quality_claim_ids",
    "competitive_position_claim_ids",
    "valuation_context_claim_ids",
    "market_context_claim_ids",
    "capital_allocation_claim_ids",
    "catalyst_claim_ids",
    "risk_claim_ids",
    "portfolio_interaction_claim_ids",
)


class CouncilInputFreezeError(ValueError):
    pass


def _verify_hash(payload: Mapping[str, Any], *, field_name: str = "artifact_hash") -> str:
    actual = payload.get(field_name)
    if not isinstance(actual, str) or len(actual) != 64:
        raise CouncilInputFreezeError(f"{field_name} missing")
    if any(ch not in "0123456789abcdef" for ch in actual):
        raise CouncilInputFreezeError(f"{field_name} must be lowercase SHA-256")
    expected = canonical_sha256(payload, exclude_fields=(field_name,))
    if actual != expected:
        raise CouncilInputFreezeError(f"{field_name} mismatch")
    return actual


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CouncilInputFreezeError("created_at must be timezone-aware")
    return value.astimezone(UTC)


def _candidate_records(reconciliation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = reconciliation.get("candidates")
    if not isinstance(raw, list):
        raise CouncilInputFreezeError("B3 reconciliation candidates missing")
    by_candidate: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise CouncilInputFreezeError("B3 reconciliation candidate record must be object")
        candidate = item.get("candidate")
        if not isinstance(candidate, str):
            raise CouncilInputFreezeError("B3 reconciliation candidate identity missing")
        if candidate in by_candidate:
            raise CouncilInputFreezeError("duplicate B3 reconciliation candidate")
        by_candidate[candidate] = item
    if tuple(by_candidate) != EXPECTED_TOP3:
        raise CouncilInputFreezeError("B4 requires exact frozen top-3 candidate order")
    return by_candidate


def _canonical_candidate_objects(
    candidate: str,
    record: Mapping[str, Any],
) -> tuple[object, tuple[object, ...]]:
    if record.get("status") != "CANONICAL_RECONCILED":
        raise CouncilInputFreezeError(f"{candidate} is not CANONICAL_RECONCILED")
    if record.get("reconstructibility_status") != "PASS":
        raise CouncilInputFreezeError(f"{candidate} B3 reconciliation is not reconstructible")

    packet_raw = record.get("candidate_packet")
    claims_raw = record.get("material_claims")
    if not isinstance(packet_raw, Mapping) or not isinstance(claims_raw, list):
        raise CouncilInputFreezeError(f"{candidate} canonical packet/claims missing")
    try:
        packet = CANDIDATE_PACKET_V1.model_validate(dict(packet_raw))
        claims = tuple(
            MATERIAL_CLAIM_V1.model_validate(dict(item))
            for item in claims_raw
            if isinstance(item, Mapping)
        )
    except Exception as exc:
        raise CouncilInputFreezeError(f"{candidate} canonical B3 object validation failed") from exc
    if len(claims) != len(claims_raw):
        raise CouncilInputFreezeError(f"{candidate} material claim record malformed")
    if packet.candidate_id != candidate:
        raise CouncilInputFreezeError(f"{candidate} CandidatePacket identity mismatch")
    if any(claim.candidate_id != candidate for claim in claims):
        raise CouncilInputFreezeError(f"{candidate} MaterialClaim identity mismatch")

    claim_ids = tuple(claim.claim_id for claim in claims)
    if len(set(claim_ids)) != len(claim_ids):
        raise CouncilInputFreezeError(f"{candidate} duplicate canonical MaterialClaim ID")
    grouped_claim_ids = tuple(
        claim_id
        for field_name in _PACKET_CLAIM_GROUP_FIELDS
        for claim_id in getattr(packet, field_name)
    )
    if len(set(grouped_claim_ids)) != len(grouped_claim_ids) or set(grouped_claim_ids) != set(claim_ids):
        raise CouncilInputFreezeError(f"{candidate} CandidatePacket claim closure mismatch")
    return packet, claims


def build_council_input_freeze(
    reconciliation: Mapping[str, Any],
    *,
    expected_handoff_hash: str,
    mandate_version: str,
    created_at: datetime,
) -> CouncilInputFreezeArtifact:
    """Build the exact three immutable B4 Council input authorities with zero I/O.

    The caller owns file/network access. This function only validates frozen B3
    lineage and materializes deterministic immutable B4 input bundles.
    """

    reconciliation_hash = _verify_hash(reconciliation)
    if reconciliation.get("artifact_version") != EXPECTED_B3_RECONCILIATION_ARTIFACT_VERSION:
        raise CouncilInputFreezeError("unexpected B3 reconciliation artifact version")
    if reconciliation.get("run_class") != EXPECTED_B3_RECONCILIATION_RUN_CLASS:
        raise CouncilInputFreezeError("unexpected B3 reconciliation run class")
    if reconciliation.get("canonical_reconciliation") != EXPECTED_B3_RECONCILIATION_STATUS:
        raise CouncilInputFreezeError("B3 reconciliation is not final")
    if reconciliation.get("reconstructibility_status") != "PASS":
        raise CouncilInputFreezeError("B3 reconciliation batch is not reconstructible")
    if reconciliation.get("handoff_hash") != expected_handoff_hash:
        raise CouncilInputFreezeError("B3 reconciliation does not match frozen B2 handoff")
    if reconciliation.get("mandate_version") != mandate_version:
        raise CouncilInputFreezeError("B3 reconciliation mandate version mismatch")
    if reconciliation.get("broker_writes") != 0 or reconciliation.get("alpaca_orders") != 0:
        raise CouncilInputFreezeError("B3 reconciliation contains broker/order writes")
    if reconciliation.get("live_money") != "PROHIBITED":
        raise CouncilInputFreezeError("B3 reconciliation live-money invariant drift")

    freeze_time = _aware_utc(created_at)
    records = _candidate_records(reconciliation)
    bundles: list[CouncilInputBundle] = []
    for candidate in EXPECTED_TOP3:
        record = records[candidate]
        packet, claims = _canonical_candidate_objects(candidate, record)
        research_snapshot_hash = record.get("bundle_hash")
        if not isinstance(research_snapshot_hash, str) or len(research_snapshot_hash) != 64:
            raise CouncilInputFreezeError(f"{candidate} research snapshot hash missing")
        if any(ch not in "0123456789abcdef" for ch in research_snapshot_hash):
            raise CouncilInputFreezeError(f"{candidate} research snapshot hash malformed")

        conflict_ids = tuple(
            dict.fromkeys(
                conflict_id
                for claim in claims
                for conflict_id in claim.conflict_ids
            )
        )
        bundle = CouncilInputBundle.from_unhashed(
            bundle_id=f"B4_COUNCIL_INPUT_{candidate}_{packet.packet_hash[:16]}",
            candidate_id=candidate,
            candidate_packet_id=packet.candidate_packet_id,
            candidate_packet_hash=packet.packet_hash,
            research_snapshot_id=packet.research_snapshot_id,
            research_snapshot_hash=research_snapshot_hash,
            b2_snapshot_id=packet.b2_snapshot_id,
            deep_comparison_id=packet.deep_comparison_id,
            mandate_version=packet.mandate_version,
            council_policy_version=COUNCIL_POLICY_VERSION,
            judge_policy_version=JUDGE_POLICY_VERSION,
            model_policy_version=MODEL_POLICY_VERSION,
            allowed_material_claim_ids=tuple(claim.claim_id for claim in claims),
            allowed_computed_value_ids=tuple(packet.computed_value_ids),
            allowed_conflict_ids=conflict_ids,
            shared_portfolio_context_refs=(),
            created_at=freeze_time,
        )
        bundles.append(bundle)

    provisional = CouncilInputFreezeArtifact.model_construct(
        artifact_version="B4_COUNCIL_INPUT_FREEZE_ARTIFACT_v0_1",
        run_class="B4_LOCAL_ZERO_CALL_INPUT_FREEZE",
        b3_reconciliation_artifact_hash=reconciliation_hash,
        b2_handoff_hash=expected_handoff_hash,
        mandate_version=mandate_version,
        candidate_order=EXPECTED_TOP3,
        bundles=tuple(bundles),
        model_calls=0,
        provider_reads=0,
        broker_writes=0,
        alpaca_orders=0,
        live_money="PROHIBITED",
        artifact_hash="0" * 64,
    )
    return CouncilInputFreezeArtifact(
        **provisional.model_dump(mode="python", exclude={"artifact_hash"}),
        artifact_hash=canonical_sha256(provisional, exclude_fields=("artifact_hash",)),
    )
