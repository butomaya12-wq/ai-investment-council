from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Collection, Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .models import AssetRecord, B2Model, InstrumentType, ProofStatus, SecurityTypeProof


class EligibilityReason(StrEnum):
    NON_US_EQUITY = "NON_US_EQUITY"
    INACTIVE = "INACTIVE"
    NOT_TRADABLE = "NOT_TRADABLE"
    EXCHANGE_NOT_ALLOWED = "EXCHANGE_NOT_ALLOWED"
    SECURITY_TYPE_UNPROVEN = "SECURITY_TYPE_UNPROVEN"
    NOT_OPERATING_COMPANY_COMMON_STOCK = "NOT_OPERATING_COMPANY_COMMON_STOCK"
    ISSUER_IDENTITY_CONFLICT = "ISSUER_IDENTITY_CONFLICT"
    REQUIRED_EVIDENCE_INCOMPLETE = "REQUIRED_EVIDENCE_INCOMPLETE"
    MANDATE_EXCLUDED = "MANDATE_EXCLUDED"


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason_codes: tuple[EligibilityReason, ...]


def evaluate_asset_eligibility(
    *,
    asset: AssetRecord,
    security_type_proof: SecurityTypeProof,
    allowed_exchanges: Collection[str],
    evidence_complete: bool,
    mandate_allowed: bool,
    issuer_identity_consistent: bool = True,
) -> EligibilityDecision:
    reasons: list[EligibilityReason] = []

    if asset.asset_class != "us_equity":
        reasons.append(EligibilityReason.NON_US_EQUITY)
    if asset.status != "active":
        reasons.append(EligibilityReason.INACTIVE)
    if not asset.tradable:
        reasons.append(EligibilityReason.NOT_TRADABLE)
    if asset.exchange not in allowed_exchanges:
        reasons.append(EligibilityReason.EXCHANGE_NOT_ALLOWED)

    proof_matches_symbol = security_type_proof.symbol == asset.symbol
    if security_type_proof.status is not ProofStatus.PROVEN or not proof_matches_symbol:
        reasons.append(EligibilityReason.SECURITY_TYPE_UNPROVEN)
    elif security_type_proof.instrument_type is not InstrumentType.OPERATING_COMPANY_COMMON_STOCK:
        reasons.append(EligibilityReason.NOT_OPERATING_COMPANY_COMMON_STOCK)

    if not issuer_identity_consistent:
        reasons.append(EligibilityReason.ISSUER_IDENTITY_CONFLICT)
    if not evidence_complete:
        reasons.append(EligibilityReason.REQUIRED_EVIDENCE_INCOMPLETE)
    if not mandate_allowed:
        reasons.append(EligibilityReason.MANDATE_EXCLUDED)

    return EligibilityDecision(eligible=not reasons, reason_codes=tuple(reasons))


class EligibilityProof(B2Model):
    eligibility_proof_id: str
    asset: AssetRecord
    security_type_proof: SecurityTypeProof
    allowed_exchanges: tuple[str, ...]
    evidence_complete: bool
    mandate_allowed: bool
    issuer_identity_consistent: bool
    eligible: bool
    reason_codes: tuple[EligibilityReason, ...]
    eligibility_proof_hash: str

    @field_validator(
        "evidence_complete",
        "mandate_allowed",
        "issuer_identity_consistent",
        "eligible",
        mode="before",
    )
    @classmethod
    def _strict_bool(cls, value: Any) -> Any:
        if type(value) is not bool:
            raise TypeError("eligibility booleans must be exact bool values")
        return value

    @model_validator(mode="after")
    def _bind_full_eligibility_decision(self) -> Self:
        if not self.eligibility_proof_id.strip():
            raise ValueError("eligibility_proof_id must not be empty")
        if not self.allowed_exchanges:
            raise ValueError("allowed_exchanges must not be empty")
        if len(set(self.allowed_exchanges)) != len(self.allowed_exchanges):
            raise ValueError("allowed_exchanges must be unique")
        if self.allowed_exchanges != tuple(sorted(self.allowed_exchanges)):
            raise ValueError("allowed_exchanges must use deterministic sorted order")
        for exchange in self.allowed_exchanges:
            if not exchange or exchange != exchange.strip() or exchange != exchange.upper():
                raise ValueError("allowed_exchanges must be canonical uppercase strings")

        expected = evaluate_asset_eligibility(
            asset=self.asset,
            security_type_proof=self.security_type_proof,
            allowed_exchanges=self.allowed_exchanges,
            evidence_complete=self.evidence_complete,
            mandate_allowed=self.mandate_allowed,
            issuer_identity_consistent=self.issuer_identity_consistent,
        )
        if self.eligible is not expected.eligible or self.reason_codes != expected.reason_codes:
            raise ValueError("EligibilityProof decision does not match deterministic eligibility gates")

        expected_hash = canonical_sha256(self, exclude_fields=("eligibility_proof_hash",))
        if self.eligibility_proof_hash != expected_hash:
            raise ValueError("eligibility_proof_hash does not bind the canonical EligibilityProof")
        return self

    @classmethod
    def build(
        cls,
        *,
        eligibility_proof_id: str,
        asset: AssetRecord,
        security_type_proof: SecurityTypeProof,
        allowed_exchanges: Collection[str],
        evidence_complete: bool,
        mandate_allowed: bool,
        issuer_identity_consistent: bool = True,
    ) -> Self:
        canonical_exchanges = tuple(sorted(allowed_exchanges))
        decision = evaluate_asset_eligibility(
            asset=asset,
            security_type_proof=security_type_proof,
            allowed_exchanges=canonical_exchanges,
            evidence_complete=evidence_complete,
            mandate_allowed=mandate_allowed,
            issuer_identity_consistent=issuer_identity_consistent,
        )
        payload = {
            "eligibility_proof_id": eligibility_proof_id,
            "asset": asset,
            "security_type_proof": security_type_proof,
            "allowed_exchanges": canonical_exchanges,
            "evidence_complete": evidence_complete,
            "mandate_allowed": mandate_allowed,
            "issuer_identity_consistent": issuer_identity_consistent,
            "eligible": decision.eligible,
            "reason_codes": decision.reason_codes,
        }
        payload["eligibility_proof_hash"] = canonical_sha256(payload)
        return cls(**payload)
