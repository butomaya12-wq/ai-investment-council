from __future__ import annotations

from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import POLICY_REFERENCE_V1

from .model_policy import (
    API_INVARIANTS,
    INITIAL_MODEL_LADDER,
    JUDGE_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    REBUTTAL_MODEL_LADDER,
)
from .policy import COUNCIL_POLICY, COUNCIL_POLICY_VERSION


COUNCIL_POLICY_NAME = "B4_COUNCIL_POLICY"
COUNCIL_POLICY_ID = "COUNCIL_POLICY"
COUNCIL_MODEL_POLICY_NAME = "B4_COUNCIL_MODEL_POLICY"
COUNCIL_MODEL_POLICY_ID = "MODEL_POLICY"


def _build_policy_reference(
    *,
    policy_name: str,
    policy_id: str,
    version: str,
    policy_payload: Any,
):
    policy_hash = canonical_sha256(policy_payload)
    policy_reference_id = canonical_sha256(
        [policy_name, policy_id, version, policy_hash]
    )
    return POLICY_REFERENCE_V1.from_unhashed(
        policy_name=policy_name,
        policy_id=policy_id,
        version=version,
        policy_hash=policy_hash,
        policy_reference_id=policy_reference_id,
    )


def council_model_policy_payload() -> dict[str, Any]:
    return {
        "model_policy_version": MODEL_POLICY_VERSION,
        "initial_model_ladder": [
            candidate.model_dump(mode="json") for candidate in INITIAL_MODEL_LADDER
        ],
        "rebuttal_model_ladder": [
            candidate.model_dump(mode="json") for candidate in REBUTTAL_MODEL_LADDER
        ],
        "judge_model_ladder": [
            candidate.model_dump(mode="json") for candidate in JUDGE_MODEL_LADDER
        ],
        "api_invariants": API_INVARIANTS.model_dump(mode="json"),
    }


def build_council_policy_reference():
    return _build_policy_reference(
        policy_name=COUNCIL_POLICY_NAME,
        policy_id=COUNCIL_POLICY_ID,
        version=COUNCIL_POLICY_VERSION,
        policy_payload=COUNCIL_POLICY,
    )


def build_council_model_policy_reference():
    return _build_policy_reference(
        policy_name=COUNCIL_MODEL_POLICY_NAME,
        policy_id=COUNCIL_MODEL_POLICY_ID,
        version=MODEL_POLICY_VERSION,
        policy_payload=council_model_policy_payload(),
    )
