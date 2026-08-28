from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Collection

from .models import AssetRecord, InstrumentType, ProofStatus, SecurityTypeProof


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
