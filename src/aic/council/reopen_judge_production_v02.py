from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1

from .bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from .initial_runtime_cost_v02 import (
    EXPECTED_CACHE_WRITE_MULTIPLIER,
    EXPECTED_CACHE_WRITE_USAGE_FIELD,
    EXPECTED_RUNTIME_PRICING_VERSION,
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from .judge_model_selection_v01 import (
    EXPECTED_SELECTED_JUDGE,
    build_judge_selected_model_authority,
    verify_judge_selected_model_authority,
)
from .model_policy import JUDGE_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .proposal import JudgeDecisionProposalDraft, JudgeNextDirective, JudgeOutcome, RebuttalResponseType
from .rebuttal_runtime_execution import validate_rebuttal_processed_record
from .reopen_initial_runtime import ReopenInitialRuntimePlanItem
from .request import CouncilRequestEnvelope


ENTRY_VERSION = "B4_REOPEN_JUDGE_ENTRY_PREFLIGHT_v0_2"
ENTRY_STATUS = "PASS_ZERO_CALL_B4_REOPEN_JUDGE_ENTRY_V02"
CONTEXT_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_CONTEXT_v0_2"
EVENT_CONSTRAINT_VERSION = "B4_REOPEN_JUDGE_EVENT_CONSTRAINT_v0_2"
REQUEST_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_REQUEST_PREFLIGHT_v0_2"
REQUEST_STATUS = "PASS_ZERO_CALL_B4_REOPEN_JUDGE_PRODUCTION_REQUEST_PREFLIGHT"
COST_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_COST_PREFLIGHT_v0_2"
COST_STATUS = "REQUIRES_EXPLICIT_OWNER_B4_REOPEN_JUDGE_APPROVAL_V02"
DRY_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_RUNNER_DRY_v0_2"
DRY_STATUS = "READY_FOR_EXPLICIT_OWNER_B4_REOPEN_JUDGE_AUTHORIZATION_V02"
NEXT_GATE = "EXPLICIT_OWNER_B4_REOPEN_JUDGE_PRODUCTION_AUTHORIZATION_V02"
MODEL_RUN_REF = "B4_REOPEN_JUDGE_PRODUCTION_V02"

EXPECTED_REBUTTAL_FREEZE_HASH = "75f2ac76e0f4478e71447871b0b284cbe74e0e8378fb6171fff855ab1ce1ade4"
EXPECTED_REBUTTAL_RUN_ID = "AIC-B4-REOPEN-REBUTTAL-20260831T052118480747Z-0d8230e1e170"
EXPECTED_REBUTTAL_AUTH_HASH = "93b72f1edb0de423afd8ec56c940560c2f416385aedeb6e663ab4c3e6cf12b38"
EXPECTED_REBUTTAL_RECEIPT_MANIFEST_HASH = "0f450ffbf6429bfc0bf7968a5430b31c1209e2c3464d209ede7f7a982e45aea7"
EXPECTED_REBUTTAL_RECEIPTS = (
    "c47c296221db87874a55df99d79109f2e7339ee6110e34e9f6205a59885aebdd",
    "8123260ef996bbae1da55e0f2794aad609ddd3de92193c3249d4ef9b99568d50",
    "d3f0f82b683d6986eb55024d52067285bdf8fb1c8c5518136093e50afb1fab74",
)
EXPECTED_REBUTTAL_BUNDLES = (
    "678ee32cc016eea8d2927be2d0a9de2fd895b645585681b08282787bf12e3c20",
    "87fd561ac942dd1fe763e21daa41f07cfc77dace15a81b384464acbdb0501d1c",
    "f91a19b2559931be8716d891ee49b937a3a14085daa506aa6737583a22a75f52",
)
EXPECTED_RECOVERED_INITIAL_HASH = "b98a3fbb2ce43cd9cab0d97b28ec62c1819ea5c777d8ff0a0dc36eb7628e8440"
EXPECTED_REBUTTAL_REQUEST_MANIFEST = "ff423f97dc2398befa25dd8bedbfd92bc46562e56c302caa67ddb2e1c8f50693"
EXPECTED_CREDENTIAL_PROBE_RESULT_HASH = "219b234df60fbcd9d0ae5cd2c8ef23f2da495051cfb7db4641d55e62ae01eb1b"
EXPECTED_CREDENTIAL_SHA256 = "64735f921810c6c9c54240fe403702a485376dbf60863615a2b036d6954b6958"
EXPECTED_PRICING_HASH = "13b67bf92f56b2962694f463850e0a0e289fc08f0c4a3d3cafe8eb928d0ee336"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_REOPEN = ("NVDA", "MSFT")
EXPECTED_INVEST_ELIGIBLE = ("META",)
EXPECTED_INVEST_BLOCKED = ("NVDA", "MSFT")
EXPECTED_ALLOWED = (JudgeOutcome.INVEST, JudgeOutcome.WATCH, JudgeOutcome.ABSTAIN)
EXPECTED_MAX_OUTPUT_TOKENS = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.JUDGE]


class ReopenJudgeV02Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReopenJudgeContext:
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
    allowed_reopen_reason_codes: tuple[str, ...]
    context_hash: str


def _require(value: bool, message: str) -> None:
    if not value:
        raise ReopenJudgeV02Error(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _require(
        isinstance(observed, str)
        and observed == canonical_sha256(payload, exclude_fields=(field,)),
        f"{field} self-hash mismatch",
    )
    return observed


def _decimal(value: object, *, field: str) -> Decimal:
    _require(isinstance(value, str), f"{field} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ReopenJudgeV02Error(f"{field} invalid") from exc
    _require(parsed.is_finite() and parsed >= 0, f"{field} invalid")
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def request_body_utf8_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def verify_current_rebuttal_freeze(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _require(observed == EXPECTED_REBUTTAL_FREEZE_HASH, "current Rebuttal freeze hash drift")
    exact = {
        "artifact_version": "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3",
        "runtime_version": "B4_REOPEN_REBUTTAL_PRODUCTION_RUNTIME_v0_3",
        "status": "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN",
        "run_id": EXPECTED_REBUTTAL_RUN_ID,
        "source_recovered_initial_freeze_hash": EXPECTED_RECOVERED_INITIAL_HASH,
        "request_manifest_hash": EXPECTED_REBUTTAL_REQUEST_MANIFEST,
        "paid_authorization_artifact_hash": EXPECTED_REBUTTAL_AUTH_HASH,
        "receipt_manifest_hash": EXPECTED_REBUTTAL_RECEIPT_MANIFEST_HASH,
        "paid_call_receipt_hashes": list(EXPECTED_REBUTTAL_RECEIPTS),
        "rebuttal_bundle_hashes": list(EXPECTED_REBUTTAL_BUNDLES),
        "candidate_order": list(EXPECTED_CANDIDATES),
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "dispatch_attempts": 3,
        "model_calls": 3,
        "automatic_repair_calls": 0,
        "automatic_retries": 0,
        "rebuttal_actual_cost_usd": "0.622971",
        "rebuttal_cost_receipt_status": "COMPLETE",
        "rebuttal_freeze_barrier": True,
        "judge_model_calls": 0,
        "judge_authorized": False,
        "rebuttal_rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": "B4_REOPEN_JUDGE_PRODUCTION_COST_PREFLIGHT_ZERO_CALL",
        "source_successful_credential_probe_v02_result_artifact_hash": EXPECTED_CREDENTIAL_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"current Rebuttal freeze drift: {key}")
    rows = payload.get("processed_records")
    _require(isinstance(rows, list) and len(rows) == 3, "current Rebuttal freeze requires three records")
    _require(
        tuple(row.get("candidate_id") for row in rows if isinstance(row, Mapping)) == EXPECTED_CANDIDATES,
        "current Rebuttal candidate order drift",
    )
    flags: list[bool] = []
    for row in rows:
        _require(isinstance(row, Mapping), "current Rebuttal record malformed")
        validate_rebuttal_processed_record(row)
        flags.append(bool(row.get("research_reopen_required")))
    _require(tuple(flags) == (True, True, False), "current Rebuttal per-candidate reopen state drift")
    return observed


def rebuild_and_verify_judge_selection(
    eval_artifact: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    selection: Mapping[str, Any],
) -> str:
    rebuilt = build_judge_selected_model_authority(eval_artifact, receipts)
    _require(dict(selection) == rebuilt, "Judge authority differs from durable 21-receipt replay")
    observed = verify_judge_selected_model_authority(selection)
    _require(selection.get("selected_candidate") == EXPECTED_SELECTED_JUDGE, "Judge J1 selection drift")
    return observed


def build_entry(rebuttal_freeze: Mapping[str, Any], *, code_commit_sha: str) -> dict[str, Any]:
    _require(
        isinstance(code_commit_sha, str)
        and len(code_commit_sha) == 40
        and all(ch in "0123456789abcdef" for ch in code_commit_sha),
        "Judge V02 entry requires exact git SHA",
    )
    freeze_hash = verify_current_rebuttal_freeze(rebuttal_freeze)
    reason_map = {
        str(row["candidate_id"]): list(row["research_reopen_reason_codes"])
        for row in rebuttal_freeze["processed_records"]
        if row["research_reopen_required"]
    }
    artifact = {
        "artifact_version": ENTRY_VERSION,
        "status": ENTRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "rebuttal_council_freeze_artifact_hash": freeze_hash,
        "rebuttal_run_id": EXPECTED_REBUTTAL_RUN_ID,
        "paid_rebuttal_authorization_artifact_hash": EXPECTED_REBUTTAL_AUTH_HASH,
        "rebuttal_receipt_manifest_hash": EXPECTED_REBUTTAL_RECEIPT_MANIFEST_HASH,
        "candidate_order": list(EXPECTED_CANDIDATES),
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "research_reopen_reason_codes_by_candidate": reason_map,
        "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
        "allowed_judge_outcomes_for_current_frozen_run": [x.value for x in EXPECTED_ALLOWED],
        "invest_constraint": "INVEST_REQUIRES_PRIMARY_CANDIDATE_META_WHILE_NVDA_MSFT_REOPEN_ACTIVE",
        "research_reopen_must_remain_visible_to_judge": True,
        "historical_judge_model_selection_replay_required": True,
        "new_research_inside_b4_allowed": False,
        "judge_execution_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_entry(payload: Mapping[str, Any], *, head: str | None = None) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": ENTRY_VERSION,
        "status": ENTRY_STATUS,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
        "allowed_judge_outcomes_for_current_frozen_run": [x.value for x in EXPECTED_ALLOWED],
        "research_reopen_must_remain_visible_to_judge": True,
        "judge_execution_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "rerun_authorized": False,
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"Judge V02 entry drift: {key}")
    if head is not None:
        _require(payload.get("code_commit_sha") == head, "Judge V02 entry HEAD drift")
    return observed


def _claims(
    effective_inputs: Sequence[Mapping[str, Any]],
    recovered: Mapping[str, Any],
    rebuttal: Mapping[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    by_id: dict[str, Any] = {}
    conflicts: list[str] = []

    def add(raw: Mapping[str, Any]) -> None:
        claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
        prior = by_id.get(claim.claim_id)
        _require(
            prior is None or canonical_sha256(prior) == canonical_sha256(claim),
            "MaterialClaim ID collision",
        )
        by_id[claim.claim_id] = claim
        for ref in claim.conflict_ids:
            if ref not in conflicts:
                conflicts.append(ref)

    for model_input in effective_inputs:
        _require(model_input.get("data_gap_refs") == [], "effective reopen input carries active data gap")
        raw_claims = model_input.get("material_claims")
        _require(isinstance(raw_claims, list), "effective material claims missing")
        for raw in raw_claims:
            _require(isinstance(raw, Mapping), "effective claim malformed")
            add(raw)

    initial_rows = recovered.get("processed_records")
    _require(isinstance(initial_rows, list) and len(initial_rows) == 9, "recovered Initial records missing")
    for row in initial_rows:
        _require(isinstance(row, Mapping), "recovered Initial record malformed")
        for raw in row.get("material_claims", ()):
            add(raw)
        opinion = COUNCIL_OPINION_V1.model_validate(row["council_opinion"])
        _require(not opinion.data_gap_refs, "effective recovered Initial opinion still has data gaps")

    for row in rebuttal["processed_records"]:
        for raw in row["material_claims"]:
            add(raw)
        _require(row["required_unknown_refs"] == [], "Rebuttal preserved active unknown unexpectedly")
        for item in row["frozen_rebuttal_bundle"]["draft"]["items"]:
            _require(item["remaining_uncertainty_refs"] == [], "Rebuttal remaining uncertainty drift")
    return (
        tuple(x.model_dump(mode="json", exclude_none=False, warnings=False) for x in by_id.values()),
        tuple(conflicts),
    )


def build_context(
    *,
    initial_plan: Sequence[ReopenInitialRuntimePlanItem],
    recovered_initial_freeze: Mapping[str, Any],
    rebuttal_freeze: Mapping[str, Any],
    entry: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> ReopenJudgeContext:
    verify_current_rebuttal_freeze(rebuttal_freeze)
    verify_entry(entry)
    verify_judge_selected_model_authority(selection)
    _require(selection.get("selected_candidate") == EXPECTED_SELECTED_JUDGE, "Judge V02 requires J1")
    _require(recovered_initial_freeze.get("artifact_hash") == EXPECTED_RECOVERED_INITIAL_HASH, "recovered Initial hash drift")
    _require(len(initial_plan) == 9, "Judge V02 requires nine effective Initial plan items")

    reps: list[ReopenInitialRuntimePlanItem] = []
    for offset, candidate in zip((0, 3, 6), EXPECTED_CANDIDATES, strict=True):
        group = initial_plan[offset : offset + 3]
        _require(len(group) == 3 and all(x.candidate_id == candidate for x in group), "Initial plan order drift")
        _require(len({x.bundle.bundle_hash for x in group}) == 1, "effective bundle lane drift")
        _require(len({canonical_sha256(dict(x.model_input)) for x in group}) == 1, "effective model-input lane drift")
        reps.append(group[0])

    inputs = tuple(dict(x.model_input) for x in reps)
    packets: list[dict[str, Any]] = []
    computed: list[dict[str, Any]] = []
    overlays: list[dict[str, Any]] = []
    for model_input in inputs:
        packet, values, overlay = (
            model_input.get("candidate_packet"),
            model_input.get("computed_values"),
            model_input.get("reopen_overlay"),
        )
        _require(isinstance(packet, Mapping) and isinstance(values, list) and isinstance(overlay, Mapping), "effective input shape incomplete")
        packets.append(dict(packet))
        computed.extend(dict(v) for v in values if isinstance(v, Mapping))
        overlays.append(dict(overlay))
        _require(model_input.get("data_gap_refs") == [], "effective data gaps are not closed")

    lineage_sets = (
        {x.bundle.mandate_version for x in reps},
        {x.bundle.deep_comparison_id for x in reps},
        {x.bundle.council_policy_version for x in reps},
        {x.bundle.judge_policy_version for x in reps},
        {x.bundle.model_policy_version for x in reps},
    )
    _require(all(len(x) == 1 for x in lineage_sets), "effective Judge policy lineage drift")
    mandate, deep, council_policy, judge_policy, model_policy = (
        str(next(iter(x))) for x in lineage_sets
    )

    claims, conflicts = _claims(inputs, recovered_initial_freeze, rebuttal_freeze)
    claim_ids = tuple(x["claim_id"] for x in claims)

    initial_views = [
        COUNCIL_OPINION_V1.model_validate(row["council_opinion"]).model_dump(
            mode="json", exclude_none=False, warnings=False
        )
        for row in recovered_initial_freeze["processed_records"]
    ]
    rebuttal_views: list[dict[str, Any]] = []
    disputes: list[str] = []
    reason_map: dict[str, list[str]] = {}
    for row in rebuttal_freeze["processed_records"]:
        items = []
        for item in row["frozen_rebuttal_bundle"]["draft"]["items"]:
            if RebuttalResponseType(item["response_type"]) == RebuttalResponseType.UNRESOLVED:
                for ref in item["opposing_finding_ids"]:
                    if ref not in disputes:
                        disputes.append(ref)
            items.append(
                {
                    "responding_lane": item["responding_lane"],
                    "opposing_finding_ids": list(item["opposing_finding_ids"]),
                    "response_type": item["response_type"],
                }
            )
        if row["research_reopen_required"]:
            reason_map[str(row["candidate_id"])] = list(row["research_reopen_reason_codes"])
        rebuttal_views.append(
            {
                "candidate_id": row["candidate_id"],
                "rebuttal_bundle_id": row["rebuttal_bundle_id"],
                "rebuttal_bundle_hash": row["rebuttal_bundle_hash"],
                "rebuttal_material_claim_ids": [x["claim_id"] for x in row["material_claims"]],
                "items": items,
                "research_reopen_required": row["research_reopen_required"],
                "research_reopen_reason_codes": list(row["research_reopen_reason_codes"]),
            }
        )

    allowed_reasons = tuple(
        dict.fromkeys(reason for candidate in EXPECTED_REOPEN for reason in reason_map.get(candidate, ()))
    )
    _require(bool(allowed_reasons), "active reopen lacks frozen reason codes")
    condition_refs = tuple(
        dict.fromkeys((*claim_ids, *disputes, *conflicts, *allowed_reasons))
    )

    base = {
        "production_context_version": CONTEXT_VERSION,
        "candidate_order": list(EXPECTED_CANDIDATES),
        "candidate_packets": packets,
        "computed_values": computed,
        "mandate_version": mandate,
        "deep_comparison_id": deep,
        "council_policy_version": council_policy,
        "judge_policy_version": judge_policy,
        "model_policy_version": model_policy,
        "material_claims": list(claims),
        "initial_role_views": initial_views,
        "rebuttal_bundles": rebuttal_views,
        "effective_gap_state": {
            "effective_data_gap_refs": [],
            "historical_candidate_packet_source_gaps_are_immutable_provenance_only": True,
            "reopen_overlays": overlays,
        },
        "event_outcome_constraints": {
            "constraint_version": EVENT_CONSTRAINT_VERSION,
            "allowed_outcomes": [x.value for x in EXPECTED_ALLOWED],
            "research_reopen_required_candidates": list(EXPECTED_REOPEN),
            "research_reopen_reason_codes_by_candidate": reason_map,
            "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
            "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
            "invest_rule": "INVEST is permitted only when primary_candidate_id is META.",
            "non_invest_rule": (
                "WATCH/ABSTAIN must preserve active NVDA/MSFT reopen via "
                "research_reopen_required=true, frozen reason codes, and RESEARCH_REOPEN_REQUEST."
            ),
            "new_research_inside_b4_allowed": False,
        },
        "source_lineage": {
            "recovered_initial_freeze_artifact_hash": EXPECTED_RECOVERED_INITIAL_HASH,
            "rebuttal_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
            "judge_entry_artifact_hash": entry["artifact_hash"],
            "judge_selected_model_authority_hash": selection["artifact_hash"],
        },
    }
    judge_input_hash = canonical_sha256(base)
    model_input = dict(base)
    model_input["judge_input_hash"] = judge_input_hash
    return ReopenJudgeContext(
        candidate_ids=EXPECTED_CANDIDATES,
        mandate_version=mandate,
        deep_comparison_id=deep,
        judge_input_hash=judge_input_hash,
        model_input=model_input,
        allowed_claim_ids=claim_ids,
        allowed_dispute_refs=tuple(disputes),
        allowed_conflict_refs=conflicts,
        allowed_unknown_refs=(),
        allowed_condition_refs=condition_refs,
        allowed_reopen_reason_codes=allowed_reasons,
        context_hash=canonical_sha256(model_input),
    )


def build_request(context: ReopenJudgeContext, selection: Mapping[str, Any]) -> CouncilRequestEnvelope:
    verify_judge_selected_model_authority(selection)
    _require(selection.get("selected_candidate") == EXPECTED_SELECTED_JUDGE, "Judge V02 request requires J1")
    matches = [x for x in JUDGE_MODEL_LADDER if x.candidate_key == "J1"]
    _require(len(matches) == 1, "J1 missing from frozen Judge ladder")
    request = build_bounded_judge_request(
        model_candidate=matches[0],
        model_input=context.model_input,
        candidate_ids=context.candidate_ids,
        mandate_version=context.mandate_version,
        deep_comparison_id=context.deep_comparison_id,
        judge_input_hash=context.judge_input_hash,
        council_policy_version=str(context.model_input["council_policy_version"]),
        judge_policy_version=str(context.model_input["judge_policy_version"]),
        model_policy_version=str(context.model_input["model_policy_version"]),
        model_run_ref=MODEL_RUN_REF,
        allowed_claim_ids=context.allowed_claim_ids,
        allowed_dispute_refs=context.allowed_dispute_refs,
        allowed_conflict_refs=context.allowed_conflict_refs,
        allowed_unknown_refs=context.allowed_unknown_refs,
        allowed_condition_refs=context.allowed_condition_refs,
    )
    assert_bounded_request_invariants(request)
    _require(request.request_payload.get("model") == "gpt-5.6-terra", "Judge V02 model drift")
    _require(request.request_payload.get("reasoning") == {"effort": "medium"}, "Judge V02 reasoning drift")
    _require(request.request_payload.get("max_output_tokens") == EXPECTED_MAX_OUTPUT_TOKENS, "Judge V02 output cap drift")
    return request


def validate_event_proposal(proposal: JudgeDecisionProposalDraft, *, context: ReopenJudgeContext) -> None:
    _require(proposal.outcome in EXPECTED_ALLOWED, "Judge V02 outcome outside allowed surface")
    _require(proposal.judge_input_hash == context.judge_input_hash, "Judge V02 input hash mismatch")
    _require(proposal.mandate_version == context.mandate_version, "Judge V02 mandate mismatch")
    _require(proposal.deep_comparison_id == context.deep_comparison_id, "Judge V02 deep-comparison mismatch")
    _require(proposal.council_policy_version == context.model_input["council_policy_version"], "Judge V02 council policy mismatch")
    _require(proposal.judge_policy_version == context.model_input["judge_policy_version"], "Judge V02 judge policy mismatch")
    _require(proposal.model_policy_version == context.model_input["model_policy_version"], "Judge V02 model policy mismatch")
    _require(proposal.model_run_ref == MODEL_RUN_REF, "Judge V02 model_run_ref mismatch")
    _require(
        proposal.primary_candidate_id is None or proposal.primary_candidate_id in context.candidate_ids,
        "Judge V02 primary candidate outside top three",
    )
    _require(set(proposal.watch_candidate_ids).issubset(context.candidate_ids), "Judge V02 watch candidate outside top three")
    _require(set(proposal.selected_candidate_basis_claim_ids).issubset(context.allowed_claim_ids), "Judge V02 basis claim outside graph")
    for row in proposal.why_not_other_candidates:
        _require(row.candidate_id in context.candidate_ids, "Judge V02 why-not candidate outside top three")
        _require(set(row.claim_ids).issubset(context.allowed_claim_ids), "Judge V02 why-not claim outside graph")
    _require(set(proposal.unresolved_dispute_refs).issubset(context.allowed_dispute_refs), "Judge V02 dispute ref outside input")
    _require(set(proposal.material_conflict_refs).issubset(context.allowed_conflict_refs), "Judge V02 conflict ref outside input")
    _require(set(proposal.material_unknown_refs).issubset(context.allowed_unknown_refs), "Judge V02 unknown ref outside input")
    for condition in proposal.what_would_change_decision:
        _require(set(condition.source_or_claim_refs).issubset(context.allowed_condition_refs), "Judge V02 condition ref outside input")
    _require(set(proposal.invalidation_condition_refs).issubset(context.allowed_condition_refs), "Judge V02 invalidation ref outside input")
    _require(proposal.execution_authority is False, "Judge V02 execution authority violation")

    if proposal.outcome == JudgeOutcome.INVEST:
        _require(proposal.primary_candidate_id == "META", "Judge V02 INVEST is allowed only for META")
        _require(proposal.research_reopen_required is False, "Judge V02 INVEST/META cannot carry selected-candidate reopen")
    else:
        _require(proposal.research_reopen_required is True, "Judge V02 WATCH/ABSTAIN must preserve active reopen")
        _require(bool(proposal.research_reopen_reason_codes), "Judge V02 non-INVEST reopen lacks reasons")
        _require(
            set(proposal.research_reopen_reason_codes).issubset(context.allowed_reopen_reason_codes),
            "Judge V02 reopen reason outside frozen Rebuttal reasons",
        )
        _require(
            proposal.next_directive == JudgeNextDirective.RESEARCH_REOPEN_REQUEST,
            "Judge V02 non-INVEST must request research reopen",
        )


def build_request_preflight(
    *,
    code_commit_sha: str,
    entry: Mapping[str, Any],
    context: ReopenJudgeContext,
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    entry_hash = verify_entry(entry, head=code_commit_sha)
    selection_hash = verify_judge_selected_model_authority(selection)
    request = build_request(context, selection)
    nbytes = request_body_utf8_bytes(request.request_payload)
    schema = request.request_payload["text"]["format"]["schema"]
    manifest = canonical_sha256(
        {
            "rebuttal_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
            "entry_artifact_hash": entry_hash,
            "selected_model_authority_hash": selection_hash,
            "judge_input_hash": context.judge_input_hash,
            "judge_context_hash": context.context_hash,
            "request_hash": request.request_hash,
            "request_body_utf8_bytes": nbytes,
        }
    )
    artifact = {
        "artifact_version": REQUEST_VERSION,
        "status": REQUEST_STATUS,
        "code_commit_sha": code_commit_sha,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "recovered_initial_freeze_artifact_hash": EXPECTED_RECOVERED_INITIAL_HASH,
        "judge_entry_preflight_artifact_hash": entry_hash,
        "judge_selected_model_authority_hash": selection_hash,
        "selected_candidate": dict(EXPECTED_SELECTED_JUDGE),
        "judge_input_hash": context.judge_input_hash,
        "judge_context_hash": context.context_hash,
        "event_constraint_version": EVENT_CONSTRAINT_VERSION,
        "allowed_outcomes": [x.value for x in EXPECTED_ALLOWED],
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
        "request_hash": request.request_hash,
        "request_body_utf8_bytes": nbytes,
        "request_manifest_hash": manifest,
        "schema_hash": canonical_sha256(schema),
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "owner_approval_required": True,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_request_preflight(payload: Mapping[str, Any], *, head: str | None = None) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": REQUEST_VERSION,
        "status": REQUEST_STATUS,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "recovered_initial_freeze_artifact_hash": EXPECTED_RECOVERED_INITIAL_HASH,
        "selected_candidate": EXPECTED_SELECTED_JUDGE,
        "event_constraint_version": EVENT_CONSTRAINT_VERSION,
        "allowed_outcomes": [x.value for x in EXPECTED_ALLOWED],
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "owner_approval_required": True,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"Judge V02 request preflight drift: {key}")
    _require(type(payload.get("request_body_utf8_bytes")) is int and payload["request_body_utf8_bytes"] > 0, "Judge V02 request byte count invalid")
    if head is not None:
        _require(payload.get("code_commit_sha") == head, "Judge V02 request HEAD drift")
    return observed


def build_cost_preflight(request_preflight: Mapping[str, Any], *, pricing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request_hash = verify_request_preflight(request_preflight)
    pricing = dict(pricing or load_initial_runtime_pricing())
    _require(pricing.get("pricing_version") == EXPECTED_RUNTIME_PRICING_VERSION, "Judge V02 pricing version drift")
    _require(
        pricing.get("pricing_hash") == EXPECTED_PRICING_HASH
        and pricing["pricing_hash"] == canonical_sha256(pricing, exclude_fields=("pricing_hash",)),
        "Judge V02 pricing hash drift",
    )
    nbytes = request_preflight["request_body_utf8_bytes"]
    cost = runtime_cost_upper_bound_usd(
        model="gpt-5.6-terra",
        input_tokens_upper_bound=nbytes,
        output_tokens_upper_bound=EXPECTED_MAX_OUTPUT_TOKENS,
        call_count=1,
        pricing=pricing,
    )
    cache, long_ctx = pricing["cache_write"], pricing["long_context"]
    artifact = {
        "artifact_version": COST_VERSION,
        "status": COST_STATUS,
        "code_commit_sha": request_preflight["code_commit_sha"],
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "judge_selected_model_authority_hash": request_preflight["judge_selected_model_authority_hash"],
        "judge_production_request_preflight_artifact_hash": request_hash,
        "judge_production_request_manifest_hash": request_preflight["request_manifest_hash"],
        "request_hash": request_preflight["request_hash"],
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "request_body_utf8_bytes": nbytes,
        "input_tokens_upper_bound": nbytes,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "planned_paid_calls_max": 1,
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing["pricing_hash"],
        "pricing_as_of_date": pricing["as_of_date"],
        "cache_write_input_rate_multiplier": cache["input_rate_multiplier"],
        "cache_write_usage_field": cache["usage_field"],
        "long_context_threshold_input_tokens_exclusive": long_ctx["threshold_input_tokens_exclusive"],
        "long_context_input_multiplier": long_ctx["input_multiplier"],
        "long_context_output_multiplier": long_ctx["output_multiplier"],
        "long_context_surcharge_assumed": nbytes > long_ctx["threshold_input_tokens_exclusive"],
        "worst_case_all_input_tokens_as_cache_write_assumed": True,
        "cached_input_discount_assumed_for_upper_bound": False,
        "production_judge_cost_upper_bound_usd": _decimal_text(cost),
        "owner_cost_approval_required": True,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    _require(Decimal(str(cache["input_rate_multiplier"])) == EXPECTED_CACHE_WRITE_MULTIPLIER, "Judge V02 cache-write multiplier drift")
    _require(cache["usage_field"] == EXPECTED_CACHE_WRITE_USAGE_FIELD, "Judge V02 cache-write usage field drift")
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_cost_preflight(payload: Mapping[str, Any], *, head: str | None = None) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": COST_VERSION,
        "status": COST_STATUS,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "planned_paid_calls_max": 1,
        "pricing_version": EXPECTED_RUNTIME_PRICING_VERSION,
        "pricing_hash": EXPECTED_PRICING_HASH,
        "owner_cost_approval_required": True,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"Judge V02 cost preflight drift: {key}")
    _require(_decimal(payload.get("production_judge_cost_upper_bound_usd"), field="Judge ceiling") > 0, "Judge V02 ceiling invalid")
    if head is not None:
        _require(payload.get("code_commit_sha") == head, "Judge V02 cost HEAD drift")
    return observed


def build_dry(
    *,
    code_commit_sha: str,
    entry: Mapping[str, Any],
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = {
        "artifact_version": DRY_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "judge_entry_preflight_artifact_hash": verify_entry(entry, head=code_commit_sha),
        "judge_selected_model_authority_hash": verify_judge_selected_model_authority(selection),
        "selected_candidate": dict(EXPECTED_SELECTED_JUDGE),
        "judge_input_hash": request_preflight["judge_input_hash"],
        "judge_context_hash": request_preflight["judge_context_hash"],
        "request_preflight_artifact_hash": verify_request_preflight(request_preflight, head=code_commit_sha),
        "request_manifest_hash": request_preflight["request_manifest_hash"],
        "request_hash": request_preflight["request_hash"],
        "cost_preflight_artifact_hash": verify_cost_preflight(cost_preflight, head=code_commit_sha),
        "cost_ceiling_usd": cost_preflight["production_judge_cost_upper_bound_usd"],
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "allowed_outcomes": [x.value for x in EXPECTED_ALLOWED],
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
        "source_successful_credential_probe_v02_result_artifact_hash": EXPECTED_CREDENTIAL_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
        "paid_authorization_requires_current_credential_sha256_match": True,
        "owner_approval_required": True,
        "consumption_rule": "CONSUMED_ON_FIRST_DURABLE_JUDGE_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry(payload: Mapping[str, Any], *, head: str | None = None) -> str:
    observed = _self_hash(payload)
    exact = {
        "artifact_version": DRY_VERSION,
        "status": DRY_STATUS,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "selected_candidate": EXPECTED_SELECTED_JUDGE,
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "allowed_outcomes": [x.value for x in EXPECTED_ALLOWED],
        "research_reopen_required_candidates": list(EXPECTED_REOPEN),
        "invest_eligible_candidates": list(EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(EXPECTED_INVEST_BLOCKED),
        "source_successful_credential_probe_v02_result_artifact_hash": EXPECTED_CREDENTIAL_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
        "paid_authorization_requires_current_credential_sha256_match": True,
        "owner_approval_required": True,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"Judge V02 dry drift: {key}")
    if head is not None:
        _require(payload.get("code_commit_sha") == head, "Judge V02 dry HEAD drift")
    return observed
