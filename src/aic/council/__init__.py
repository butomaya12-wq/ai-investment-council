"""Bounded B4 Council/Judge contracts and deterministic orchestration foundations."""

from .claim_promotion_authority import (
    NORMALIZATION_VERSION as CLAIM_PROMOTION_NORMALIZATION_VERSION,
)
from .input_bundle import CouncilInputFreezeError, build_council_input_freeze
from .model_policy import (
    API_INVARIANTS,
    INITIAL_MODEL_LADDER,
    JUDGE_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    REBUTTAL_MODEL_LADDER,
    CouncilModelStage,
    select_stage_model_from_eval,
)
from .models import (
    INITIAL_COUNCIL_LANES,
    CouncilClaimKind,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilInputFreezeArtifact,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
)
from .policy import (
    COUNCIL_POLICY,
    COUNCIL_POLICY_VERSION,
    JUDGE_POLICY,
    JUDGE_POLICY_VERSION,
)
from .policy_refs import (
    build_council_model_policy_reference,
    build_council_policy_reference,
    council_model_policy_payload,
)
from .promotion import (
    CouncilPromotionError,
    InitialOpinionPromotionResult,
    promote_initial_council_opinion,
)
from .proposal import (
    CouncilClaimMetadata,
    InitialCouncilOpinionProposal,
    validate_initial_proposal_lineage,
)

__all__ = [
    "API_INVARIANTS",
    "CLAIM_PROMOTION_NORMALIZATION_VERSION",
    "COUNCIL_POLICY",
    "COUNCIL_POLICY_VERSION",
    "CouncilClaimKind",
    "CouncilClaimMetadata",
    "CouncilClaimType",
    "CouncilInputBundle",
    "CouncilInputFreezeArtifact",
    "CouncilInputFreezeError",
    "CouncilLane",
    "CouncilMateriality",
    "CouncilModelStage",
    "CouncilPromotionError",
    "CouncilSupportStatus",
    "INITIAL_COUNCIL_LANES",
    "INITIAL_MODEL_LADDER",
    "InitialCouncilOpinionProposal",
    "InitialOpinionPromotionResult",
    "JUDGE_MODEL_LADDER",
    "JUDGE_POLICY",
    "JUDGE_POLICY_VERSION",
    "MODEL_POLICY_VERSION",
    "ProposedCouncilClaim",
    "REBUTTAL_MODEL_LADDER",
    "build_council_input_freeze",
    "build_council_model_policy_reference",
    "build_council_policy_reference",
    "council_model_policy_payload",
    "promote_initial_council_opinion",
    "select_stage_model_from_eval",
    "validate_initial_proposal_lineage",
]
