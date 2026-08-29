from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from aic.domain.canonical import canonical_sha256


NORMALIZATION_VERSION = "B4_CLAIM_PROMOTION_NORMALIZATION_v0_1"
NORMALIZATION_HASH = "c6c51c2471d10facca4dafb6b9a5c0132f040d066027552f1d5324b51b845fe9"
DRIVE_AMENDMENT_ID = "18OsdjKybP0iOheGkZN6oNvWWO02TjkfDC0T6iHObH7E"
DEFAULT_NORMALIZATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "event"
    / "b4_claim_promotion_normalization_v1.json"
)

_EXPECTED_KIND_MAPPING = {
    "FACT_RESTATEMENT": "FACT",
    "INFERENCE": "INFERENCE",
    "PROCESS_FINDING": "INFERENCE",
}
_EXPECTED_PROVENANCE = {
    "evidence_ids": "ORDERED_UNIQUE_PARENT_UNION",
    "computed_value_ids": "ORDERED_UNIQUE_PARENT_UNION_PLUS_DIRECT",
    "conflict_ids": "ORDERED_UNIQUE_PARENT_UNION_PLUS_DIRECT",
    "assumptions": "ORDERED_UNIQUE_PARENT_UNION",
    "uncertainty_note": "NULL_ONLY",
}


class ClaimPromotionAuthorityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimPromotionNormalizationAuthority:
    normalization_version: str
    normalization_hash: str
    drive_amendment_id: str
    claim_kind_mapping: Mapping[str, str]


def _require_exact(payload: Mapping[str, object], key: str, expected: object) -> None:
    if payload.get(key) != expected:
        raise ClaimPromotionAuthorityError(f"B4 claim-promotion authority drift: {key}")


def validate_claim_promotion_normalization_payload(
    payload: Mapping[str, object],
) -> ClaimPromotionNormalizationAuthority:
    expected_keys = {
        "normalization_version",
        "drive_amendment_id",
        "category_mapping",
        "claim_kind_mapping",
        "provenance_closure",
        "material_requires_supported",
        "fact_restatement_parent_inference_forbidden",
        "local_ref_persistence_allowed",
        "legacy_council_claim_id_allowed",
        "numeric_authority",
        "process_finding_metadata_only",
        "normalization_hash",
    }
    if set(payload) != expected_keys:
        raise ClaimPromotionAuthorityError("B4 claim-promotion authority fields drift")

    _require_exact(payload, "normalization_version", NORMALIZATION_VERSION)
    _require_exact(payload, "drive_amendment_id", DRIVE_AMENDMENT_ID)
    _require_exact(
        payload,
        "category_mapping",
        {"source_field": "claim_type", "mode": "EXACT_VALUE"},
    )
    _require_exact(payload, "claim_kind_mapping", _EXPECTED_KIND_MAPPING)
    _require_exact(payload, "provenance_closure", _EXPECTED_PROVENANCE)
    _require_exact(payload, "material_requires_supported", True)
    _require_exact(payload, "fact_restatement_parent_inference_forbidden", True)
    _require_exact(payload, "local_ref_persistence_allowed", False)
    _require_exact(payload, "legacy_council_claim_id_allowed", False)
    _require_exact(
        payload,
        "numeric_authority",
        "SOURCE_CLAIM_TOKEN_OR_EXACT_COMPUTED_VALUE_ONLY",
    )
    _require_exact(payload, "process_finding_metadata_only", True)

    actual_hash = canonical_sha256(payload, exclude_fields=("normalization_hash",))
    if actual_hash != NORMALIZATION_HASH or payload.get("normalization_hash") != actual_hash:
        raise ClaimPromotionAuthorityError("B4 claim-promotion normalization_hash mismatch")

    mapping = payload["claim_kind_mapping"]
    if not isinstance(mapping, Mapping):
        raise ClaimPromotionAuthorityError("claim_kind_mapping must be an object")
    return ClaimPromotionNormalizationAuthority(
        normalization_version=NORMALIZATION_VERSION,
        normalization_hash=actual_hash,
        drive_amendment_id=DRIVE_AMENDMENT_ID,
        claim_kind_mapping=dict(mapping),
    )


def load_claim_promotion_normalization(
    path: Path = DEFAULT_NORMALIZATION_PATH,
) -> ClaimPromotionNormalizationAuthority:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimPromotionAuthorityError(
            "B4 claim-promotion normalization authority is unavailable"
        ) from exc
    if not isinstance(raw, dict):
        raise ClaimPromotionAuthorityError("B4 claim-promotion normalization must be an object")
    return validate_claim_promotion_normalization_payload(raw)
