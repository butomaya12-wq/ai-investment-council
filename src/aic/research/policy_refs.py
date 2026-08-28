from __future__ import annotations

from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import POLICY_REFERENCE_V1

from .model_policy import API_INVARIANTS, MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from .policy import ResearchPolicy


RESEARCH_POLICY_NAME = "B3_RESEARCH_POLICY"
RESEARCH_POLICY_ID = "RESEARCH_POLICY"
MODEL_POLICY_NAME = "B3_MODEL_POLICY"
MODEL_POLICY_ID = "MODEL_POLICY"


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


def build_research_policy_reference(policy: ResearchPolicy):
    return _build_policy_reference(
        policy_name=RESEARCH_POLICY_NAME,
        policy_id=RESEARCH_POLICY_ID,
        version=policy.policy_version,
        policy_payload=policy,
    )


def model_policy_payload() -> dict[str, Any]:
    return {
        "model_policy_version": MODEL_POLICY_VERSION,
        "candidate_ladder": [
            candidate.model_dump(mode="json") for candidate in MODEL_CANDIDATE_LADDER
        ],
        "api_invariants": API_INVARIANTS.model_dump(mode="json"),
    }


def build_model_policy_reference():
    return _build_policy_reference(
        policy_name=MODEL_POLICY_NAME,
        policy_id=MODEL_POLICY_ID,
        version=MODEL_POLICY_VERSION,
        policy_payload=model_policy_payload(),
    )
