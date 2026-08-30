from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from time import perf_counter_ns
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import (
    CANDIDATE_PACKET_V1,
    COUNCIL_OPINION_V1,
    MATERIAL_CLAIM_V1,
    RESEARCH_REOPEN_REQUEST_V1,
)
from aic.research.handoff import B2RealEventHandoff

from .bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from .initial_runtime import _validate_processed_record as validate_initial_processed_record
from .initial_runtime_cost_v02 import actual_cost_usd
from .judge_entry_preflight import verify_judge_entry_preflight
from .judge_model_selection_v01 import (
    EXPECTED_SELECTED_JUDGE,
    verify_judge_selected_model_authority,
)
from .model_input import build_initial_model_inputs
from .model_policy import JUDGE_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputFreezeArtifact
from .proposal import (
    FrozenJudgeDecisionProposal,
    JudgeDecisionProposalDraft,
    JudgeNextDirective,
    JudgeOutcome,
    RebuttalResponseType,
)
from .rebuttal_runtime_execution import (
    validate_rebuttal_processed_record,
    verify_rebuttal_council_freeze_artifact,
)
from .request import (
    CouncilRequestEnvelope,
    _object_with_properties,
    _restrict_string_array,
    assert_request_invariants,
    parse_council_responses_payload,
)


JUDGE_PRODUCTION_CONTEXT_VERSION = "B4_JUDGE_PRODUCTION_CONTEXT_v0_1"
JUDGE_PRODUCTION_RUNTIME_VERSION = "B4_JUDGE_PRODUCTION_RUNTIME_v0_1"
JUDGE_PRODUCTION_SCHEMA_TIGHTENING_VERSION = "B4_JUDGE_EVENT_SCHEMA_TIGHTENING_v0_1"
JUDGE_PRODUCTION_RESULT_VERSION = "B4_JUDGE_PRODUCTION_RESULT_v0_1"
JUDGE_PRODUCTION_SUCCESS_STATUS = "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED"
JUDGE_PRODUCTION_BLOCKED_STATUS = "BLOCKED_B4_JUDGE_NOT_COMPLETE"
EXPECTED_INITIAL_FREEZE_HASH = (
    "ca7391e5e0c3a754eabc54fbf959b0f36e0986b552d405a06cf649116135361f"
)
EXPECTED_REBUTTAL_FREEZE_HASH = (
    "dd83aa4f873c0d6ac6582cd6dc89c1612088239aa8e979d18f7f95c3386552a5"
)
EXPECTED_JUDGE_ENTRY_HASH = (
    "74ccd2788645e21769f80224b4b6912c5692c1a46e4fc076700ea602240d25f2"
)
EXPECTED_REQUIRED_UNKNOWN_REFS = ("ALPACA_NEWS_PAGINATION_INCOMPLETE",)
EXPECTED_CANDIDATE_ORDER = ("NVDA", "MSFT", "META")
EXPECTED_ALLOWED_OUTCOMES = (JudgeOutcome.WATCH, JudgeOutcome.ABSTAIN)
EXPECTED_MAX_OUTPUT_TOKENS = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.JUDGE]


class JudgeProductionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JudgeProductionContext:
    candidate_ids: tuple[str, str, str]
    mandate_version: str
    deep_comparison_id: str
    judge_input_hash: str
    model_input: Mapping[str, Any]
    allowed_claim_ids: tuple[str, ...]
    allowed_dispute_refs: tuple[str, ...]
    allowed_conflict_refs: tuple[str, ...]
    allowed_unknown_refs: tuple[str, ...]
    allowed_condition_refs: tuple[str, ...]
    context_hash: str


@dataclass(frozen=True, slots=True)
class JudgeProductionCallRun:
    request_hash: str
    response_id: str | None
    effective_model: str | None
    latency_ms: int
    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    actual_cost_usd: Decimal | None
    cost_receipt_status: str
    output_hash: str | None
    structured_output: Mapping[str, Any] | None
    structured_output_hash: str | None
    judge_proposal_hash: str | None
    research_reopen_request: Mapping[str, Any] | None
    research_reopen_request_hash: str | None
    validation_status: str
    validation_error: str | None
    model_calls: int


def request_body_utf8_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _verify_initial_freeze(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if observed != EXPECTED_INITIAL_FREEZE_HASH:
        raise JudgeProductionError("Initial Council freeze hash drift")
    if observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeProductionError("Initial Council freeze self-hash mismatch")
    if payload.get("status") != "INITIAL_COUNCIL_FROZEN":
        raise JudgeProductionError("Initial Council is not frozen")
    if payload.get("candidate_order") != list(EXPECTED_CANDIDATE_ORDER):
        raise JudgeProductionError("Initial Council candidate order drift")
    if payload.get("initial_opinion_count") != 9:
        raise JudgeProductionError("Initial Council opinion count drift")
    if payload.get("model_calls") != 9 or payload.get("dispatch_attempts") != 9:
        raise JudgeProductionError("Initial Council paid-call completion drift")
    if payload.get("cost_receipt_status") != "COMPLETE":
        raise JudgeProductionError("Initial Council cost receipts incomplete")
    if payload.get("initial_freeze_barrier") is not True:
        raise JudgeProductionError("Initial Council freeze barrier missing")
    if payload.get("rebuttal_authorized") is not False or payload.get("judge_authorized") is not False:
        raise JudgeProductionError("Initial Council artifact grants later-stage authority")
    records = payload.get("processed_records")
    if not isinstance(records, list) or len(records) != 9:
        raise JudgeProductionError("Initial Council processed records missing")
    for record in records:
        if not isinstance(record, Mapping):
            raise JudgeProductionError("Initial Council processed record malformed")
        validate_initial_processed_record(record)
    for field in ("provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeProductionError(f"Initial Council side-effect invariant violated: {field}")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeProductionError("Initial Council live-money invariant drift")
    return observed


def _claim_payload(claim: Any) -> dict[str, Any]:
    return claim.model_dump(mode="json", exclude_none=False, warnings=False)


def _canonical_claims(
    *,
    initial_model_inputs: Sequence[Any],
    initial_freeze: Mapping[str, Any],
    rebuttal_freeze: Mapping[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], tuple[str, ...]]:
    by_id: dict[str, Any] = {}
    conflict_refs: list[str] = []
    unknown_refs: list[str] = []

    def add_claim(raw: Mapping[str, Any]) -> None:
        claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
        existing = by_id.get(claim.claim_id)
        if existing is not None and canonical_sha256(existing) != canonical_sha256(claim):
            raise JudgeProductionError("canonical MaterialClaim ID collision with different payload")
        by_id[claim.claim_id] = claim
        for ref in claim.conflict_ids:
            if ref not in conflict_refs:
                conflict_refs.append(ref)

    for model_input in initial_model_inputs:
        for raw in model_input.material_claims:
            add_claim(raw)
        for ref in model_input.data_gap_refs:
            if ref not in unknown_refs:
                unknown_refs.append(ref)

    initial_records = initial_freeze.get("processed_records")
    assert isinstance(initial_records, list)
    for record in initial_records:
        for raw in record["material_claims"]:
            add_claim(raw)
        opinion = COUNCIL_OPINION_V1.model_validate(record["council_opinion"])
        for ref in opinion.data_gap_refs:
            if ref not in unknown_refs:
                unknown_refs.append(ref)

    rebuttal_records = rebuttal_freeze.get("processed_records")
    assert isinstance(rebuttal_records, list)
    for record in rebuttal_records:
        for raw in record["material_claims"]:
            add_claim(raw)
        for ref in record["required_unknown_refs"]:
            if ref not in unknown_refs:
                unknown_refs.append(ref)
        frozen = record["frozen_rebuttal_bundle"]
        draft = frozen["draft"]
        for item in draft["items"]:
            for ref in item["remaining_uncertainty_refs"]:
                if ref not in unknown_refs:
                    unknown_refs.append(ref)

    if tuple(unknown_refs) != EXPECTED_REQUIRED_UNKNOWN_REFS:
        raise JudgeProductionError("production Judge unknown-ref surface differs from frozen event")
    payloads = tuple(_claim_payload(by_id[claim_id]) for claim_id in by_id)
    return payloads, tuple(conflict_refs), tuple(unknown_refs)


def _initial_role_views(initial_freeze: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    rows = initial_freeze.get("processed_records")
    if not isinstance(rows, list) or len(rows) != 9:
        raise JudgeProductionError("Initial role views unavailable")
    result: list[dict[str, Any]] = []
    for row in rows:
        opinion = COUNCIL_OPINION_V1.model_validate(row["council_opinion"])
        result.append(opinion.model_dump(mode="json", exclude_none=False, warnings=False))
    return tuple(result)


def _rebuttal_views(
    rebuttal_freeze: Mapping[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    rows = rebuttal_freeze.get("processed_records")
    if not isinstance(rows, list) or len(rows) != 3:
        raise JudgeProductionError("Rebuttal views unavailable")
    views: list[dict[str, Any]] = []
    disputes: list[str] = []
    for row in rows:
        validate_rebuttal_processed_record(row)
        frozen = row["frozen_rebuttal_bundle"]
        draft = frozen["draft"]
        promoted_claim_ids = [raw["claim_id"] for raw in row["material_claims"]]
        item_views: list[dict[str, Any]] = []
        for item in draft["items"]:
            response_type = RebuttalResponseType(item["response_type"])
            if response_type == RebuttalResponseType.UNRESOLVED:
                for ref in item["opposing_finding_ids"]:
                    if ref not in disputes:
                        disputes.append(ref)
            item_views.append(
                {
                    "rebuttal_item_id": item["rebuttal_item_id"],
                    "responding_lane": item["responding_lane"],
                    "opposing_finding_ids": list(item["opposing_finding_ids"]),
                    "response_type": item["response_type"],
                    "remaining_uncertainty_refs": list(item["remaining_uncertainty_refs"]),
                }
            )
        views.append(
            {
                "candidate_id": row["candidate_id"],
                "rebuttal_bundle_id": row["rebuttal_bundle_id"],
                "rebuttal_bundle_hash": row["rebuttal_bundle_hash"],
                "rebuttal_material_claim_ids": promoted_claim_ids,
                "items": item_views,
                "research_reopen_required": row["research_reopen_required"],
                "research_reopen_reason_codes": list(row["research_reopen_reason_codes"]),
                "required_unknown_refs": list(row["required_unknown_refs"]),
            }
        )
    if [row["candidate_id"] for row in views] != list(EXPECTED_CANDIDATE_ORDER):
        raise JudgeProductionError("Rebuttal view candidate order drift")
    return tuple(views), tuple(disputes)


def build_judge_production_context(
    *,
    input_freeze: CouncilInputFreezeArtifact,
    reconciliation: Mapping[str, Any],
    handoff: B2RealEventHandoff,
    initial_freeze: Mapping[str, Any],
    rebuttal_freeze: Mapping[str, Any],
    judge_entry: Mapping[str, Any],
    selected_model_authority: Mapping[str, Any],
) -> JudgeProductionContext:
    _verify_initial_freeze(initial_freeze)
    if verify_rebuttal_council_freeze_artifact(rebuttal_freeze) != EXPECTED_REBUTTAL_FREEZE_HASH:
        raise JudgeProductionError("Rebuttal Council freeze authority drift")
    if verify_judge_entry_preflight(judge_entry) != EXPECTED_JUDGE_ENTRY_HASH:
        raise JudgeProductionError("Judge-entry authority drift")
    verify_judge_selected_model_authority(selected_model_authority)
    if selected_model_authority.get("selected_candidate") != EXPECTED_SELECTED_JUDGE:
        raise JudgeProductionError("production Judge selected model is not frozen J1")
    if input_freeze.candidate_order != EXPECTED_CANDIDATE_ORDER:
        raise JudgeProductionError("B4 input freeze candidate order drift")
    if initial_freeze.get("b4_input_freeze_artifact_hash") != input_freeze.artifact_hash:
        raise JudgeProductionError("Initial freeze/input freeze binding drift")
    if rebuttal_freeze.get("b4_input_freeze_artifact_hash") != input_freeze.artifact_hash:
        raise JudgeProductionError("Rebuttal freeze/input freeze binding drift")
    if judge_entry.get("research_reopen_required_candidates") != list(EXPECTED_CANDIDATE_ORDER):
        raise JudgeProductionError("Judge-entry research-reopen candidate set drift")
    if judge_entry.get("invest_persistence_allowed") is not False:
        raise JudgeProductionError("current frozen event unexpectedly permits INVEST")

    model_inputs = build_initial_model_inputs(input_freeze, reconciliation, handoff)
    candidate_packets: list[dict[str, Any]] = []
    computed_values: list[dict[str, Any]] = []
    for model_input in model_inputs:
        packet = CANDIDATE_PACKET_V1.model_validate(dict(model_input.candidate_packet))
        candidate_packets.append(packet.model_dump(mode="json", exclude_none=False, warnings=False))
        computed_values.extend(
            item.model_dump(mode="json", exclude_none=False)
            for item in model_input.computed_values
        )

    deep_ids = {row["deep_comparison_id"] for row in candidate_packets}
    mandate_versions = {row["mandate_version"] for row in candidate_packets}
    if len(deep_ids) != 1 or len(mandate_versions) != 1:
        raise JudgeProductionError("candidate packets do not share Judge lineage")
    deep_comparison_id = next(iter(deep_ids))
    mandate_version = next(iter(mandate_versions))

    claims, conflict_refs, unknown_refs = _canonical_claims(
        initial_model_inputs=model_inputs,
        initial_freeze=initial_freeze,
        rebuttal_freeze=rebuttal_freeze,
    )
    role_views = _initial_role_views(initial_freeze)
    rebuttal_views, dispute_refs = _rebuttal_views(rebuttal_freeze)
    claim_ids = tuple(row["claim_id"] for row in claims)
    condition_refs = tuple(
        dict.fromkeys((*claim_ids, *dispute_refs, *conflict_refs, *unknown_refs))
    )

    base_input: dict[str, Any] = {
        "production_context_version": JUDGE_PRODUCTION_CONTEXT_VERSION,
        "candidate_order": list(EXPECTED_CANDIDATE_ORDER),
        "candidate_packets": candidate_packets,
        "computed_values": computed_values,
        "mandate_version": mandate_version,
        "deep_comparison_id": deep_comparison_id,
        "council_policy_version": input_freeze.bundles[0].council_policy_version,
        "judge_policy_version": input_freeze.bundles[0].judge_policy_version,
        "model_policy_version": input_freeze.bundles[0].model_policy_version,
        "material_claims": list(claims),
        "initial_role_views": list(role_views),
        "rebuttal_bundles": list(rebuttal_views),
        "material_conflict_refs": list(conflict_refs),
        "material_unknown_refs": list(unknown_refs),
        "unresolved_dispute_refs": list(dispute_refs),
        "frozen_authorities": {
            "b4_input_freeze_artifact_hash": input_freeze.artifact_hash,
            "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
            "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
            "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
            "judge_selected_model_authority_hash": selected_model_authority["artifact_hash"],
        },
        "current_event_constraints": {
            "research_reopen_required_candidates": list(EXPECTED_CANDIDATE_ORDER),
            "required_unknown_refs": list(EXPECTED_REQUIRED_UNKNOWN_REFS),
            "invest_persistence_allowed": False,
            "allowed_outcomes": [item.value for item in EXPECTED_ALLOWED_OUTCOMES],
            "required_next_directive": JudgeNextDirective.RESEARCH_REOPEN_REQUEST.value,
            "new_research_inside_b4_allowed": False,
            "b3_reopen_is_separate_lifecycle": True,
        },
        "judge_policy_surface": {
            "majority_vote_rule": "FORBIDDEN",
            "red_team_directional_vote": False,
            "blocking_conflict_allows_invest": False,
            "blocking_unknown_allows_invest": False,
            "research_reopen_allows_invest": False,
            "watch_allowed": True,
            "abstain_allowed": True,
            "execution_authority": False,
        },
    }
    judge_input_hash = canonical_sha256(base_input)
    model_input = {**base_input, "judge_input_hash": judge_input_hash}
    context_hash = canonical_sha256(model_input)
    return JudgeProductionContext(
        candidate_ids=EXPECTED_CANDIDATE_ORDER,
        mandate_version=mandate_version,
        deep_comparison_id=deep_comparison_id,
        judge_input_hash=judge_input_hash,
        model_input=model_input,
        allowed_claim_ids=claim_ids,
        allowed_dispute_refs=dispute_refs,
        allowed_conflict_refs=conflict_refs,
        allowed_unknown_refs=unknown_refs,
        allowed_condition_refs=condition_refs,
        context_hash=context_hash,
    )


def _tighten_event_schema(request: CouncilRequestEnvelope) -> CouncilRequestEnvelope:
    payload = deepcopy(dict(request.request_payload))
    text = payload.get("text")
    if not isinstance(text, dict):
        raise JudgeProductionError("Judge request text config malformed")
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        raise JudgeProductionError("Judge request format malformed")
    schema = fmt.get("schema")
    if not isinstance(schema, dict):
        raise JudgeProductionError("Judge request schema malformed")
    root = _object_with_properties(
        schema,
        {
            "b4_decision_id",
            "outcome",
            "primary_candidate_id",
            "watch_candidate_ids",
            "judge_input_hash",
            "research_reopen_required",
            "research_reopen_reason_codes",
            "next_directive",
            "material_unknown_refs",
        },
    )
    props = root["properties"]
    props["outcome"] = {
        "type": "string",
        "enum": [item.value for item in EXPECTED_ALLOWED_OUTCOMES],
    }
    props["primary_candidate_id"] = {"type": "null"}
    props["research_reopen_required"] = {"type": "boolean", "const": True}
    props["next_directive"] = {
        "type": "string",
        "const": JudgeNextDirective.RESEARCH_REOPEN_REQUEST.value,
    }
    _restrict_string_array(
        props["material_unknown_refs"],
        EXPECTED_REQUIRED_UNKNOWN_REFS,
        exact_count=len(EXPECTED_REQUIRED_UNKNOWN_REFS),
    )
    reasons = props["research_reopen_reason_codes"]
    if not isinstance(reasons, dict):
        raise JudgeProductionError("Judge research reopen reason schema malformed")
    reasons["minItems"] = 1
    conditions = props.get("what_would_change_decision")
    if isinstance(conditions, dict):
        conditions["minItems"] = 1

    body = {
        "request_version": request.request_version,
        "prompt_contract_version": request.prompt_contract_version,
        "stage": request.stage.value,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "model_candidate_key": request.model_candidate_key,
        "request_payload": payload,
    }
    tightened = CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))
    assert_bounded_request_invariants(tightened)
    assert_request_invariants(tightened)
    return tightened


def build_judge_production_request(
    context: JudgeProductionContext,
    selected_model_authority: Mapping[str, Any],
) -> CouncilRequestEnvelope:
    verify_judge_selected_model_authority(selected_model_authority)
    selected = selected_model_authority.get("selected_candidate")
    if selected != EXPECTED_SELECTED_JUDGE:
        raise JudgeProductionError("production Judge requires selected J1 authority")
    candidate = next(
        item for item in JUDGE_MODEL_LADDER if item.candidate_key == selected["candidate_key"]
    )
    request = build_bounded_judge_request(
        model_candidate=candidate,
        model_input=context.model_input,
        candidate_ids=context.candidate_ids,
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version=str(context.model_input["council_policy_version"]),
        judge_policy_version=str(context.model_input["judge_policy_version"]),
        model_policy_version=str(context.model_input["model_policy_version"]),
        model_run_ref="B4_PRODUCTION_JUDGE_J1",
        allowed_claim_ids=context.allowed_claim_ids,
        allowed_dispute_refs=context.allowed_dispute_refs,
        allowed_conflict_refs=context.allowed_conflict_refs,
        allowed_unknown_refs=context.allowed_unknown_refs,
        allowed_condition_refs=context.allowed_condition_refs,
    )
    tightened = _tighten_event_schema(request)
    if tightened.request_payload.get("model") != EXPECTED_SELECTED_JUDGE["model"]:
        raise JudgeProductionError("production Judge model drift")
    reasoning = tightened.request_payload.get("reasoning")
    if not isinstance(reasoning, Mapping) or reasoning.get("effort") != EXPECTED_SELECTED_JUDGE["reasoning_effort"]:
        raise JudgeProductionError("production Judge reasoning effort drift")
    if tightened.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise JudgeProductionError("production Judge output cap drift")
    return tightened


def _usage_counts(raw: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        raise JudgeProductionError("provider response lacks usage")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    if type(input_tokens) is not int or input_tokens < 0:
        raise JudgeProductionError("usage.input_tokens invalid")
    if type(output_tokens) is not int or output_tokens < 0:
        raise JudgeProductionError("usage.output_tokens invalid")
    if not isinstance(input_details, Mapping):
        raise JudgeProductionError("usage.input_tokens_details missing")
    cached_tokens = input_details.get("cached_tokens")
    cache_write_tokens = input_details.get("cache_write_tokens")
    if type(cached_tokens) is not int or cached_tokens < 0:
        raise JudgeProductionError("usage.cached_tokens invalid")
    if type(cache_write_tokens) is not int or cache_write_tokens < 0:
        raise JudgeProductionError("usage.cache_write_tokens invalid")
    if cached_tokens + cache_write_tokens > input_tokens:
        raise JudgeProductionError("cached + cache-write tokens exceed input tokens")
    reasoning_tokens = 0
    if isinstance(output_details, Mapping):
        value = output_details.get("reasoning_tokens")
        if value is not None:
            if type(value) is not int or value < 0:
                raise JudgeProductionError("usage.reasoning_tokens invalid")
            reasoning_tokens = value
    return input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens


def validate_production_judge_proposal(
    proposal: JudgeDecisionProposalDraft,
    *,
    context: JudgeProductionContext,
) -> None:
    if proposal.outcome not in EXPECTED_ALLOWED_OUTCOMES:
        raise JudgeProductionError("current frozen event forbids production Judge INVEST")
    if proposal.primary_candidate_id is not None:
        raise JudgeProductionError("current research-reopen event forbids a primary candidate")
    if proposal.judge_input_hash != context.judge_input_hash:
        raise JudgeProductionError("production Judge input hash lineage mismatch")
    if proposal.mandate_version != context.mandate_version:
        raise JudgeProductionError("production Judge mandate lineage mismatch")
    if proposal.deep_comparison_id != context.deep_comparison_id:
        raise JudgeProductionError("production Judge deep-comparison lineage mismatch")
    if proposal.research_reopen_required is not True:
        raise JudgeProductionError("production Judge suppressed required research reopen")
    if proposal.next_directive != JudgeNextDirective.RESEARCH_REOPEN_REQUEST:
        raise JudgeProductionError("production Judge must request research reopen")
    if set(proposal.material_unknown_refs) != set(EXPECTED_REQUIRED_UNKNOWN_REFS):
        raise JudgeProductionError("production Judge lost frozen material unknown")
    if not proposal.research_reopen_reason_codes:
        raise JudgeProductionError("production Judge research reopen lacks reason codes")
    if not set(proposal.watch_candidate_ids).issubset(context.candidate_ids):
        raise JudgeProductionError("production Judge watch candidate outside frozen top three")
    if not set(proposal.selected_candidate_basis_claim_ids).issubset(context.allowed_claim_ids):
        raise JudgeProductionError("production Judge basis ref outside canonical claim graph")
    for row in proposal.why_not_other_candidates:
        if row.candidate_id not in context.candidate_ids:
            raise JudgeProductionError("production Judge why-not candidate outside frozen top three")
        if not set(row.claim_ids).issubset(context.allowed_claim_ids):
            raise JudgeProductionError("production Judge why-not ref outside canonical claim graph")
    if not set(proposal.unresolved_dispute_refs).issubset(context.allowed_dispute_refs):
        raise JudgeProductionError("production Judge dispute ref outside frozen input")
    if not set(proposal.material_conflict_refs).issubset(context.allowed_conflict_refs):
        raise JudgeProductionError("production Judge conflict ref outside frozen input")
    if not set(proposal.material_unknown_refs).issubset(context.allowed_unknown_refs):
        raise JudgeProductionError("production Judge unknown ref outside frozen input")
    for condition in proposal.what_would_change_decision:
        if not set(condition.source_or_claim_refs).issubset(context.allowed_condition_refs):
            raise JudgeProductionError("production Judge change-condition ref outside frozen input")
    if proposal.execution_authority is not False:
        raise JudgeProductionError("production Judge execution authority violation")


def build_research_reopen_request(
    proposal: JudgeDecisionProposalDraft,
    *,
    parent_run_id: str,
    judge_proposal_hash: str,
    requested_at: datetime,
) -> Any:
    source_refs = tuple(
        dict.fromkeys(
            (
                *proposal.material_unknown_refs,
                *proposal.material_conflict_refs,
                *proposal.unresolved_dispute_refs,
                *proposal.selected_candidate_basis_claim_ids,
                *(
                    ref
                    for row in proposal.why_not_other_candidates
                    for ref in row.claim_ids
                ),
                *(
                    ref
                    for condition in proposal.what_would_change_decision
                    for ref in condition.source_or_claim_refs
                ),
            )
        )
    )
    if not source_refs:
        raise JudgeProductionError("research reopen requires source-ref lineage")
    seed = canonical_sha256(
        {
            "parent_run_id": parent_run_id,
            "judge_proposal_hash": judge_proposal_hash,
            "reason_codes": list(proposal.research_reopen_reason_codes),
            "source_ref_ids": list(source_refs),
        }
    )
    reopen_id = f"B4_RESEARCH_REOPEN_{seed[:24]}"
    values = {
        "reopen_request_id": reopen_id,
        "parent_run_id": parent_run_id,
        "parent_decision_id": None,
        "trigger_bundle_id": None,
        "reason_codes": list(proposal.research_reopen_reason_codes),
        "source_ref_ids": list(source_refs),
        "requested_at": requested_at.astimezone(UTC),
        "new_run_start_state": "S00",
    }
    provisional = RESEARCH_REOPEN_REQUEST_V1.model_construct(**values, request_hash="0" * 64)
    request_hash = canonical_sha256(provisional, exclude_fields=("request_hash",))
    return RESEARCH_REOPEN_REQUEST_V1.model_validate({**values, "request_hash": request_hash})


def execute_judge_production_once(
    *,
    request: CouncilRequestEnvelope,
    context: JudgeProductionContext,
    api_key: str,
    transport: Any,
    pricing: Mapping[str, Any],
    parent_run_id: str,
) -> JudgeProductionCallRun:
    started = perf_counter_ns()
    raw: Mapping[str, Any] | None = None
    response_id: str | None = None
    effective_model: str | None = None
    output_hash: str | None = None
    structured_output: Mapping[str, Any] | None = None
    structured_output_hash: str | None = None
    judge_proposal_hash: str | None = None
    reopen_payload: Mapping[str, Any] | None = None
    reopen_hash: str | None = None
    usage: tuple[int, int, int, int, int] | None = None
    cost: Decimal | None = None
    cost_status = "INCOMPLETE"
    validation_error: str | None = None

    try:
        raw_value = transport.post(payload=request.request_payload, api_key=api_key)
        if not isinstance(raw_value, Mapping):
            raise JudgeProductionError("Responses payload must be an object")
        raw = raw_value
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        call, proposal = parse_council_responses_payload(
            raw,
            request=request,
            latency_ms=latency_ms,
        )
        response_id = call.response_id
        effective_model = call.effective_model
        output_hash = call.output_hash
        if not isinstance(proposal, JudgeDecisionProposalDraft):
            raise JudgeProductionError("production Judge produced wrong DTO type")
        structured_output = proposal.model_dump(mode="json", exclude_none=False)
        structured_output_hash = canonical_sha256(structured_output)
        validate_production_judge_proposal(proposal, context=context)
        frozen = FrozenJudgeDecisionProposal.from_draft(proposal)
        judge_proposal_hash = frozen.judge_proposal_hash
        usage = _usage_counts(raw)
        model = request.request_payload.get("model")
        if not isinstance(model, str) or not model:
            raise JudgeProductionError("production Judge request model missing")
        cost = actual_cost_usd(raw, model=model, pricing=pricing)
        cost_status = "COMPLETE"
        requested_at = datetime.now(UTC)
        reopen = build_research_reopen_request(
            proposal,
            parent_run_id=parent_run_id,
            judge_proposal_hash=judge_proposal_hash,
            requested_at=requested_at,
        )
        reopen_payload = reopen.model_dump(mode="json", exclude_none=False, warnings=False)
        reopen_hash = reopen.request_hash
        if reopen_hash != canonical_sha256(reopen_payload, exclude_fields=("request_hash",)):
            raise JudgeProductionError("research reopen canonical hash mismatch")
    except Exception as exc:
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        validation_error = f"{type(exc).__name__}: {exc}"
        if raw is not None and cost_status != "COMPLETE":
            try:
                usage = _usage_counts(raw)
                model = request.request_payload.get("model")
                if not isinstance(model, str) or not model:
                    raise JudgeProductionError("production Judge request model missing")
                cost = actual_cost_usd(raw, model=model, pricing=pricing)
                cost_status = "COMPLETE"
            except Exception as cost_exc:
                validation_error += f"; cost receipt: {type(cost_exc).__name__}: {cost_exc}"

    if usage is None:
        input_tokens = cached_tokens = cache_write_tokens = None
        output_tokens = reasoning_tokens = None
    else:
        input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens = usage
    return JudgeProductionCallRun(
        request_hash=request.request_hash,
        response_id=response_id,
        effective_model=effective_model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        actual_cost_usd=cost,
        cost_receipt_status=cost_status,
        output_hash=output_hash,
        structured_output=structured_output,
        structured_output_hash=structured_output_hash,
        judge_proposal_hash=judge_proposal_hash,
        research_reopen_request=reopen_payload,
        research_reopen_request_hash=reopen_hash,
        validation_status="PASS" if reopen_payload is not None else "FAIL",
        validation_error=validation_error,
        model_calls=1 if raw is not None else 0,
    )


def build_judge_production_success_artifact(
    *,
    run_id: str,
    code_commit_sha: str,
    context: JudgeProductionContext,
    request_preflight_hash: str,
    request_manifest_hash: str,
    cost_preflight_hash: str,
    runner_dry_hash: str,
    selected_model_authority_hash: str,
    paid_authorization_hash: str,
    paid_receipt_hash: str,
    receipt_manifest_hash: str,
    approved_cost_ceiling_usd: Decimal,
    run: JudgeProductionCallRun,
) -> dict[str, Any]:
    if run.validation_status != "PASS" or run.research_reopen_request is None:
        raise JudgeProductionError("cannot freeze failed production Judge result")
    if run.actual_cost_usd is None or run.cost_receipt_status != "COMPLETE":
        raise JudgeProductionError("cannot freeze production Judge without complete cost receipt")
    proposal = JudgeDecisionProposalDraft.model_validate(dict(run.structured_output or {}))
    validate_production_judge_proposal(proposal, context=context)
    artifact: dict[str, Any] = {
        "artifact_version": JUDGE_PRODUCTION_RESULT_VERSION,
        "runtime_version": JUDGE_PRODUCTION_RUNTIME_VERSION,
        "status": JUDGE_PRODUCTION_SUCCESS_STATUS,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "judge_selected_model_authority_hash": selected_model_authority_hash,
        "selected_candidate": dict(EXPECTED_SELECTED_JUDGE),
        "judge_input_hash": context.judge_input_hash,
        "judge_context_hash": context.context_hash,
        "request_preflight_artifact_hash": request_preflight_hash,
        "request_manifest_hash": request_manifest_hash,
        "cost_preflight_artifact_hash": cost_preflight_hash,
        "runner_dry_artifact_hash": runner_dry_hash,
        "paid_authorization_artifact_hash": paid_authorization_hash,
        "paid_call_receipt_hash": paid_receipt_hash,
        "receipt_manifest_hash": receipt_manifest_hash,
        "approved_cost_ceiling_usd": str(approved_cost_ceiling_usd),
        "actual_cost_usd": str(run.actual_cost_usd),
        "cost_receipt_status": "COMPLETE",
        "response_id": run.response_id,
        "effective_model": run.effective_model,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "output_hash": run.output_hash,
        "structured_output": dict(run.structured_output or {}),
        "structured_output_hash": run.structured_output_hash,
        "judge_proposal_hash": run.judge_proposal_hash,
        "judge_outcome": proposal.outcome.value,
        "research_reopen_required": True,
        "research_reopen_request": dict(run.research_reopen_request),
        "research_reopen_request_hash": run.research_reopen_request_hash,
        "new_run_start_state": "S00",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "b4_complete": True,
        "next_lifecycle": "B3_RESEARCH_REOPEN_LINKED_S00",
        "dispatch_attempts": 1,
        "model_calls": 1,
        "automatic_repair_calls": 0,
        "judge_authorization_consumed": True,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_judge_production_success_artifact(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeProductionError("production Judge result self-hash mismatch")
    if payload.get("artifact_version") != JUDGE_PRODUCTION_RESULT_VERSION:
        raise JudgeProductionError("production Judge result version drift")
    if payload.get("status") != JUDGE_PRODUCTION_SUCCESS_STATUS:
        raise JudgeProductionError("production Judge result is not complete")
    if payload.get("selected_candidate") != EXPECTED_SELECTED_JUDGE:
        raise JudgeProductionError("production Judge selected model drift")
    if payload.get("judge_outcome") not in {item.value for item in EXPECTED_ALLOWED_OUTCOMES}:
        raise JudgeProductionError("production Judge result contains forbidden outcome")
    if payload.get("research_reopen_required") is not True:
        raise JudgeProductionError("production Judge result lost research reopen")
    reopen = payload.get("research_reopen_request")
    if not isinstance(reopen, Mapping):
        raise JudgeProductionError("production Judge reopen request missing")
    validated_reopen = RESEARCH_REOPEN_REQUEST_V1.model_validate(dict(reopen))
    if payload.get("research_reopen_request_hash") != validated_reopen.request_hash:
        raise JudgeProductionError("production Judge reopen request hash drift")
    if payload.get("final_decision_created") is not False or payload.get("b5_handoff_created") is not False:
        raise JudgeProductionError("research-reopen B4 result unexpectedly created FinalDecision/B5 handoff")
    if payload.get("new_run_start_state") != "S00" or payload.get("next_lifecycle") != "B3_RESEARCH_REOPEN_LINKED_S00":
        raise JudgeProductionError("production Judge reopen lifecycle drift")
    if payload.get("dispatch_attempts") != 1 or payload.get("model_calls") != 1:
        raise JudgeProductionError("production Judge call count drift")
    if payload.get("cost_receipt_status") != "COMPLETE":
        raise JudgeProductionError("production Judge cost receipt incomplete")
    if payload.get("automatic_repair_calls") != 0 or payload.get("rerun_authorized") is not False:
        raise JudgeProductionError("production Judge repair/rerun invariant drift")
    if payload.get("broker_writes") != 0 or payload.get("alpaca_orders") != 0 or payload.get("live_money") != "PROHIBITED":
        raise JudgeProductionError("production Judge side-effect invariant drift")
    return observed
