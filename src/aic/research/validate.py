from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import CANDIDATE_PACKET_V1, MATERIAL_CLAIM_V1

from .policy import ResearchPolicy
from .policy_refs import build_model_policy_reference, build_research_policy_reference
from .synthesize import (
    CLAIM_CATEGORIES,
    CandidatePacketDraft,
    CandidateSynthesisDraft,
    MaterialClaimDraft,
    SynthesisInputEnvelope,
)


class CandidatePacketValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidatePacketValidationResult:
    candidate_packet: object
    material_claims: tuple[object, ...]
    validator_results: tuple[Mapping[str, object], ...]


_GROUP_FIELDS: dict[str, str] = {
    "business_model": "business_model_claim_ids",
    "growth_quality": "growth_quality_claim_ids",
    "financial_quality": "financial_quality_claim_ids",
    "competitive_position": "competitive_position_claim_ids",
    "valuation_context": "valuation_context_claim_ids",
    "market_context": "market_context_claim_ids",
    "capital_allocation": "capital_allocation_claim_ids",
    "catalyst": "catalyst_claim_ids",
    "risk": "risk_claim_ids",
    "portfolio_interaction": "portfolio_interaction_claim_ids",
}

_FORBIDDEN_DECISION_RE = re.compile(
    r"(?i)(?:\bBUY\b|\bSELL\b|\bINVEST\b|\bABSTAIN\b|"
    r"\bPOSITION\s+SIZE\b|\bTARGET\s+PRICE\b|\bTRADE\s+ACTION\b|"
    r"\bPLACE\s+(?:AN?\s+)?ORDER\b|\bBROKER\s+(?:ORDER|COMMAND)\b|"
    r"\bAUTHORIZE\s+(?:A\s+)?TRADE\b)"
)
_INJECTION_RE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"system\s+prompt|developer\s+message|use\s+(?:the\s+)?tool|"
    r"run\s+(?:a\s+)?shell|execute\s+(?:this\s+)?command|"
    r"curl\s+https?://|wget\s+https?://)"
)
_NUMERIC_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?")


def _result(check_id: str, status: str, detail: str) -> Mapping[str, object]:
    return {"check_id": check_id, "status": status, "detail": detail}


def _all_packet_strings(packet: CandidatePacketDraft) -> Iterable[str]:
    for field_name in (
        "material_unknowns",
        "material_conflicts",
        "source_gaps",
        "research_questions_resolved",
        "research_questions_unresolved",
    ):
        yield from getattr(packet, field_name)


def _validate_refs(
    draft: CandidateSynthesisDraft,
    synthesis_input: SynthesisInputEnvelope,
) -> None:
    allowed_evidence = {item.evidence_id for item in synthesis_input.evidence_items}
    allowed_computed = {item.computed_value_id for item in synthesis_input.computed_values}
    allowed_conflicts = set(synthesis_input.conflict_ids)

    for claim in draft.claims:
        if not set(claim.evidence_ids).issubset(allowed_evidence):
            raise CandidatePacketValidationError(
                f"claim {claim.claim_id} cites evidence outside frozen candidate bundle"
            )
        if not set(claim.computed_value_ids).issubset(allowed_computed):
            raise CandidatePacketValidationError(
                f"claim {claim.claim_id} cites computed value outside frozen candidate bundle"
            )
        if not set(claim.conflict_ids).issubset(allowed_conflicts):
            raise CandidatePacketValidationError(
                f"claim {claim.claim_id} cites conflict outside frozen candidate bundle"
            )

    if not set(draft.packet.evidence_ids).issubset(allowed_evidence):
        raise CandidatePacketValidationError("CandidatePacket evidence_ids escape frozen bundle")
    if not set(draft.packet.computed_value_ids).issubset(allowed_computed):
        raise CandidatePacketValidationError("CandidatePacket computed_value_ids escape frozen bundle")


def _validate_claim_support(claim: MaterialClaimDraft) -> None:
    if claim.materiality == "MATERIAL" and claim.support_status != "SUPPORTED":
        raise CandidatePacketValidationError(
            f"unsupported/conflicted MATERIAL claim may not persist: {claim.claim_id}"
        )
    if claim.claim_kind == "FACT" and claim.assumptions:
        raise CandidatePacketValidationError(
            f"FACT claim may not hide assumptions: {claim.claim_id}"
        )
    if claim.claim_kind == "INFERENCE":
        if not (claim.evidence_ids or claim.computed_value_ids):
            raise CandidatePacketValidationError(
                f"INFERENCE requires supporting references: {claim.claim_id}"
            )
        if claim.uncertainty_note is None:
            raise CandidatePacketValidationError(
                f"INFERENCE requires uncertainty note: {claim.claim_id}"
            )
    if claim.support_status == "INSUFFICIENT" and claim.materiality == "MATERIAL":
        raise CandidatePacketValidationError(
            f"material unknown must be surfaced outside persisted claim graph: {claim.claim_id}"
        )


def _validate_claim_groups(draft: CandidateSynthesisDraft) -> None:
    claim_by_id = {claim.claim_id: claim for claim in draft.claims}
    grouped_ids: list[str] = []
    for category in CLAIM_CATEGORIES:
        field_name = _GROUP_FIELDS[category]
        ids = getattr(draft.packet, field_name)
        for claim_id in ids:
            claim = claim_by_id.get(claim_id)
            if claim is None:
                raise CandidatePacketValidationError(
                    f"packet group references unknown claim_id: {claim_id}"
                )
            if claim.category != category:
                raise CandidatePacketValidationError(
                    f"claim {claim_id} is assigned to wrong packet category group"
                )
        grouped_ids.extend(ids)
    if len(set(grouped_ids)) != len(grouped_ids):
        raise CandidatePacketValidationError("claim_id appears in more than one packet group")
    if set(grouped_ids) != set(claim_by_id):
        raise CandidatePacketValidationError(
            "every persisted MaterialClaim must appear exactly once in its category group"
        )


def _validate_packet_ref_closure(draft: CandidateSynthesisDraft) -> None:
    claim_evidence = {ref for claim in draft.claims for ref in claim.evidence_ids}
    claim_computed = {ref for claim in draft.claims for ref in claim.computed_value_ids}
    if set(draft.packet.evidence_ids) != claim_evidence:
        raise CandidatePacketValidationError(
            "CandidatePacket evidence_ids must equal MaterialClaim evidence-ref closure"
        )
    if set(draft.packet.computed_value_ids) != claim_computed:
        raise CandidatePacketValidationError(
            "CandidatePacket computed_value_ids must equal MaterialClaim computed-ref closure"
        )


def _validate_questions_and_gaps(
    draft: CandidateSynthesisDraft,
    synthesis_input: SynthesisInputEnvelope,
) -> None:
    allowed_questions = {q.question_id for q in synthesis_input.research_questions}
    resolved = set(draft.packet.research_questions_resolved)
    unresolved = set(draft.packet.research_questions_unresolved)
    if resolved & unresolved:
        raise CandidatePacketValidationError("research question cannot be both resolved and unresolved")
    if resolved | unresolved != allowed_questions:
        raise CandidatePacketValidationError(
            "CandidatePacket must classify every frozen research question"
        )
    required_gaps = set(synthesis_input.application_source_gaps)
    if not required_gaps.issubset(set(draft.packet.source_gaps)):
        raise CandidatePacketValidationError(
            "application-owned source gaps may not be hidden by synthesis"
        )
    if synthesis_input.evidence_status.value != "COMPLETE":
        if draft.packet.research_status == "COMPLETE":
            raise CandidatePacketValidationError(
                "non-COMPLETE ResearchEvidenceBundle cannot yield COMPLETE CandidatePacket"
            )
        if not draft.packet.source_gaps:
            raise CandidatePacketValidationError(
                "degraded/incomplete CandidatePacket must surface source_gaps"
            )
        if not unresolved:
            raise CandidatePacketValidationError(
                "non-COMPLETE evidence requires at least one unresolved research question"
            )


def _validate_forbidden_text(draft: CandidateSynthesisDraft) -> None:
    text_fields = [claim.claim_text for claim in draft.claims]
    text_fields.extend(_all_packet_strings(draft.packet))
    for text in text_fields:
        if _FORBIDDEN_DECISION_RE.search(text):
            raise CandidatePacketValidationError(
                "B3 synthesis contains forbidden investment/trade decision content"
            )
        if _INJECTION_RE.search(text):
            raise CandidatePacketValidationError(
                "B3 synthesis contains prompt/tool directive residue"
            )


def _validate_numeric_provenance(
    draft: CandidateSynthesisDraft,
    synthesis_input: SynthesisInputEnvelope,
) -> None:
    computed_by_id = {
        item.computed_value_id: item for item in synthesis_input.computed_values
    }
    evidence_by_id = {item.evidence_id: item for item in synthesis_input.evidence_items}
    for claim in draft.claims:
        tokens = tuple(match.group(0) for match in _NUMERIC_RE.finditer(claim.claim_text))
        if not tokens:
            continue
        if not (claim.computed_value_ids or claim.evidence_ids):
            raise CandidatePacketValidationError(
                f"numeric claim lacks provenance refs: {claim.claim_id}"
            )
        support_text = " ".join(
            str(evidence_by_id[ref].normalized_value)
            for ref in claim.evidence_ids
            if ref in evidence_by_id
        )
        allowed_numeric_strings = {
            computed_by_id[ref].value
            for ref in claim.computed_value_ids
            if ref in computed_by_id
        }
        for token in tokens:
            canonical_token = token[:-1] if token.endswith("%") else token
            if canonical_token in allowed_numeric_strings:
                continue
            if canonical_token and canonical_token in support_text:
                continue
            raise CandidatePacketValidationError(
                f"numeric token is not bound to supplied evidence/computed value: {token}"
            )


def _validate_cutoff(synthesis_input: SynthesisInputEnvelope) -> None:
    for item in synthesis_input.evidence_items:
        for timestamp in (item.published_at, item.observed_at, item.as_of):
            if timestamp is not None and timestamp > synthesis_input.research_cutoff:
                raise CandidatePacketValidationError(
                    f"future evidence escaped frozen cutoff: {item.evidence_id}"
                )


def validate_synthesis_draft(
    draft: CandidateSynthesisDraft,
    *,
    synthesis_input: SynthesisInputEnvelope,
) -> tuple[Mapping[str, object], ...]:
    if draft.candidate_id != synthesis_input.candidate_id:
        raise CandidatePacketValidationError("candidate isolation violation")
    _validate_refs(draft, synthesis_input)
    for claim in draft.claims:
        _validate_claim_support(claim)
    _validate_claim_groups(draft)
    _validate_packet_ref_closure(draft)
    _validate_questions_and_gaps(draft, synthesis_input)
    _validate_forbidden_text(draft)
    _validate_numeric_provenance(draft, synthesis_input)
    _validate_cutoff(synthesis_input)
    return (
        _result("B3-P1-P3", "PASS", "schema/lineage/reference closure valid"),
        _result("B3-P4-P5", "PASS", "claim graph complete and material claims supported"),
        _result("B3-P6", "PASS", "numeric provenance validated"),
        _result("B3-P7-P10", "PASS", "no forbidden decision/tool/injection residue"),
        _result("B3-P8", "PASS", "research cutoff preserved"),
        _result("B3-P11", "PASS", "unknowns/source gaps/question status preserved"),
    )


def build_canonical_candidate_packet(
    draft: CandidateSynthesisDraft,
    *,
    synthesis_input: SynthesisInputEnvelope,
    research_policy: ResearchPolicy,
    model_run_id: str,
    model_output_hash: str,
) -> CandidatePacketValidationResult:
    validator_results = validate_synthesis_draft(
        draft,
        synthesis_input=synthesis_input,
    )
    if research_policy.policy_version != synthesis_input.research_policy_version:
        raise CandidatePacketValidationError("research policy lineage mismatch")
    if synthesis_input.mandate_version is None:
        raise CandidatePacketValidationError(
            "MANDATE_VERSION_UNBOUND: exact accepted mandate lineage is required before persistence"
        )
    if not synthesis_input.mandate_version.strip():
        raise CandidatePacketValidationError("mandate_version must be non-empty")

    material_claims = tuple(
        MATERIAL_CLAIM_V1.from_unhashed(
            claim_id=claim.claim_id,
            candidate_id=claim.candidate_id,
            category=claim.category,
            claim_text=claim.claim_text,
            claim_kind=claim.claim_kind,
            materiality=claim.materiality,
            evidence_ids=list(claim.evidence_ids),
            computed_value_ids=list(claim.computed_value_ids),
            conflict_ids=list(claim.conflict_ids),
            assumptions=list(claim.assumptions),
            support_status=claim.support_status,
            uncertainty_note=claim.uncertainty_note,
        )
        for claim in draft.claims
    )

    research_policy_ref = build_research_policy_reference(research_policy)
    model_policy_ref = build_model_policy_reference()
    packet_id = (
        f"B3_CANDIDATE_PACKET_{synthesis_input.candidate_id}_"
        f"{model_output_hash[:16]}"
    )
    packet = CANDIDATE_PACKET_V1.from_unhashed(
        candidate_packet_id=packet_id,
        candidate_id=synthesis_input.candidate_id,
        symbol=synthesis_input.symbol,
        issuer_id=synthesis_input.issuer_id,
        b2_snapshot_id=synthesis_input.b2_snapshot_id,
        research_snapshot_id=synthesis_input.research_snapshot_id,
        mandate_version=synthesis_input.mandate_version,
        deep_comparison_id=synthesis_input.deep_comparison_id,
        research_policy_ref=research_policy_ref,
        research_model_policy_ref=model_policy_ref,
        model_run_ref=model_run_id,
        business_model_claim_ids=list(draft.packet.business_model_claim_ids),
        growth_quality_claim_ids=list(draft.packet.growth_quality_claim_ids),
        financial_quality_claim_ids=list(draft.packet.financial_quality_claim_ids),
        competitive_position_claim_ids=list(draft.packet.competitive_position_claim_ids),
        valuation_context_claim_ids=list(draft.packet.valuation_context_claim_ids),
        market_context_claim_ids=list(draft.packet.market_context_claim_ids),
        capital_allocation_claim_ids=list(draft.packet.capital_allocation_claim_ids),
        catalyst_claim_ids=list(draft.packet.catalyst_claim_ids),
        risk_claim_ids=list(draft.packet.risk_claim_ids),
        portfolio_interaction_claim_ids=list(draft.packet.portfolio_interaction_claim_ids),
        material_unknowns=list(draft.packet.material_unknowns),
        material_conflicts=list(draft.packet.material_conflicts),
        source_gaps=list(draft.packet.source_gaps),
        computed_value_ids=list(draft.packet.computed_value_ids),
        evidence_ids=list(draft.packet.evidence_ids),
        research_questions_resolved=list(draft.packet.research_questions_resolved),
        research_questions_unresolved=list(draft.packet.research_questions_unresolved),
        research_status=draft.packet.research_status,
    )
    return CandidatePacketValidationResult(
        candidate_packet=packet,
        material_claims=material_claims,
        validator_results=validator_results + (
            _result("B3-P12", "PASS", "canonical MaterialClaim/CandidatePacket self-hashes built"),
        ),
    )
