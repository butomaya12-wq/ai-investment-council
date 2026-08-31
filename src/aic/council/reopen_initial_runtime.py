from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1
from aic.research.handoff import load_real_event_handoff

from .bounded_request import assert_bounded_request_invariants
from .initial_runtime_cost_v02 import actual_cost_usd, load_initial_runtime_pricing
from .initial_schema_repair_v05 import build_bounded_initial_request_v05
from .model_input import build_initial_model_inputs
from .model_selection import InitialSelectedModelAuthority
from .models import CouncilInputBundle, CouncilInputFreezeArtifact, CouncilLane
from .promotion import promote_initial_council_opinion
from .proposal import InitialCouncilOpinionProposal
from .reopen_production_cost_preflight import (
    EXPECTED_CANDIDATES,
    EXPECTED_CLOSURE_HASH,
    EXPECTED_INITIAL_SELECTION_HASH,
    EXPECTED_LIFECYCLE_HASH,
    EXPECTED_OVERLAY_HASH,
    _STAGE_LANE,
    _closure_supplemental,
    _effective_surface_by_candidate,
    build_reopen_model_input,
    derive_effective_bundle,
    load_and_build_b4_reopen_production_cost_preflight,
    materialize_supplemental_material_claim,
)
from .request import CouncilRequestEnvelope, CouncilRequestStage, parse_council_responses_payload


REOPEN_INITIAL_RUNTIME_VERSION = "B4_REOPEN_INITIAL_PRODUCTION_RUNTIME_v0_1"
REOPEN_INITIAL_DRY_ARTIFACT_VERSION = "B4_REOPEN_INITIAL_RUNTIME_DRY_v0_1"
REOPEN_INITIAL_DRY_STATUS = "READY_FOR_EXPLICIT_OWNER_B4_REOPEN_INITIAL_AUTHORIZATION"
REOPEN_INITIAL_AUTHORIZATION_VERSION = "B4_REOPEN_INITIAL_RUNTIME_PAID_AUTHORIZATION_v0_1"
REOPEN_INITIAL_AUTHORIZATION_STATUS = "AUTHORIZED_FOR_ONE_B4_REOPEN_INITIAL_RUN"
REOPEN_INITIAL_RECEIPT_VERSION = "B4_REOPEN_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_1"
REOPEN_INITIAL_JOURNAL_EVENT_VERSION = "B4_REOPEN_INITIAL_RUNTIME_JOURNAL_EVENT_v0_1"
REOPEN_INITIAL_FREEZE_VERSION = "B4_REOPEN_INITIAL_COUNCIL_FREEZE_v0_1"
REOPEN_INITIAL_FROZEN_STATUS = "B4_REOPEN_INITIAL_COUNCIL_FROZEN"
REOPEN_INITIAL_BLOCKED_VERSION = "B4_REOPEN_INITIAL_COUNCIL_BLOCKED_v0_1"
REOPEN_INITIAL_BLOCKED_STATUS = "B4_REOPEN_INITIAL_COUNCIL_NOT_FROZEN"
REOPEN_INITIAL_NEXT_GATE = "B4_REOPEN_REBUTTAL_PRODUCTION_COST_PREFLIGHT_ZERO_CALL"

EXPECTED_COST_PREFLIGHT_HASH = "2ce5d3bdc7a0ffb87d5389d2f174c7ae0bb07c5ffdd19de038ec98ec028cd44f"
EXPECTED_REQUEST_MANIFEST_HASH = "fb619d5682e89ded1a20926b3b6fdd9657cb56c0b76f97c12c88f8a4cd2cd7ff"
EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH = "0bce18a825bb0ac9ff8e576b783c4a46673ae6de2ecdfd7b95f19afe0e629257"
EXPECTED_COST_PREFLIGHT_SOURCE_HEAD = "76a2d72962af6e5c27baa8beded67dd133e115ba"
EXPECTED_COST_CEILING_USD = Decimal("1.1991255")
EXPECTED_CALLS = 9
EXPECTED_MAX_OUTPUT_TOKENS = 4096
EXPECTED_SELECTED_MODEL = {
    "candidate_key": "L2",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "low",
    "ladder_position": 2,
}


class B4ReopenInitialRuntimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReopenInitialRuntimePlanItem:
    dispatch_index: int
    candidate_id: str
    lane: CouncilLane
    stage: CouncilRequestStage
    bundle: CouncilInputBundle
    model_input: Mapping[str, Any]
    request: CouncilRequestEnvelope
    request_body_utf8_bytes: int
    frozen_row: Mapping[str, Any]


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenInitialRuntimeError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenInitialRuntimeError(f"{label} root must be object")
    return value


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise B4ReopenInitialRuntimeError(f"{field_name} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise B4ReopenInitialRuntimeError(f"{field_name} invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise B4ReopenInitialRuntimeError(f"{field_name} must be finite and non-negative")
    return parsed


def _utc_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B4ReopenInitialRuntimeError(f"{field_name} missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B4ReopenInitialRuntimeError(f"{field_name} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B4ReopenInitialRuntimeError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _request_body_utf8_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def verify_reopen_initial_cost_preflight(cost: Mapping[str, Any]) -> str:
    observed_hash = cost.get("artifact_hash")
    if observed_hash != EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenInitialRuntimeError("reopen Initial cost-preflight hash drift")
    if observed_hash != canonical_sha256(cost, exclude_fields=("artifact_hash",)):
        raise B4ReopenInitialRuntimeError("reopen Initial cost-preflight self-hash mismatch")
    if cost.get("artifact_version") != "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_v0_1":
        raise B4ReopenInitialRuntimeError("reopen Initial cost-preflight version drift")
    if cost.get("status") != "B4_REOPEN_PRODUCTION_COST_PREFLIGHT_ZERO_CALL_PASS":
        raise B4ReopenInitialRuntimeError("reopen Initial cost-preflight is not PASS")
    if cost.get("code_commit_sha") != EXPECTED_COST_PREFLIGHT_SOURCE_HEAD:
        raise B4ReopenInitialRuntimeError("reopen Initial cost-preflight source HEAD drift")
    if cost.get("request_manifest_hash") != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenInitialRuntimeError("reopen Initial request-manifest drift")
    if cost.get("effective_input_manifest_hash") != EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH:
        raise B4ReopenInitialRuntimeError("reopen Initial effective-input manifest drift")
    if cost.get("source_b4_reopen_lifecycle_plan_hash") != EXPECTED_LIFECYCLE_HASH:
        raise B4ReopenInitialRuntimeError("reopen lifecycle lineage drift")
    if cost.get("source_b4_reopen_input_overlay_hash") != EXPECTED_OVERLAY_HASH:
        raise B4ReopenInitialRuntimeError("reopen overlay lineage drift")
    if cost.get("source_b3_reopen_closure_hash") != EXPECTED_CLOSURE_HASH:
        raise B4ReopenInitialRuntimeError("B3 reopen closure lineage drift")
    if cost.get("source_initial_selected_model_selection_hash") != EXPECTED_INITIAL_SELECTION_HASH:
        raise B4ReopenInitialRuntimeError("Initial selected-model authority lineage drift")
    if cost.get("cost_authority_mode") != "STAGED_EXACT":
        raise B4ReopenInitialRuntimeError("reopen cost authority mode drift")
    if cost.get("exactly_costed_now_stage") != "INITIAL" or cost.get("exactly_costed_now_calls") != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("reopen Initial exact cost scope drift")
    if cost.get("deferred_exact_costing_calls") != 4 or cost.get("all_13_owner_approval_ready") is not False:
        raise B4ReopenInitialRuntimeError("deferred Rebuttal/Judge cost boundary drift")
    if cost.get("next_owner_approval_scope") != "INITIAL_ONLY":
        raise B4ReopenInitialRuntimeError("owner approval scope must remain Initial-only")
    if cost.get("selected_initial_model") != EXPECTED_SELECTED_MODEL:
        raise B4ReopenInitialRuntimeError("reopen Initial selected-model identity drift")
    if cost.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4ReopenInitialRuntimeError("reopen Initial output-token cap drift")
    if _decimal(cost.get("initial_exact_cost_upper_bound_usd"), field_name="Initial cost ceiling") != EXPECTED_COST_CEILING_USD:
        raise B4ReopenInitialRuntimeError("reopen Initial cost ceiling drift")
    if cost.get("owner_cost_approval_required") is not True:
        raise B4ReopenInitialRuntimeError("owner cost approval requirement missing")
    for field_name in (
        "initial_paid_dispatch_authorized",
        "rebuttal_paid_dispatch_authorized",
        "judge_paid_dispatch_authorized",
        "model_calls_authorized",
        "provider_reads_authorized",
        "rerun_authorized",
        "final_decision_created",
        "b5_handoff_created",
    ):
        if cost.get(field_name) is not False:
            raise B4ReopenInitialRuntimeError(f"cost preflight unexpectedly authorizes {field_name}")
    if cost.get("automatic_repair_calls_authorized") != 0 or cost.get("automatic_retries") != 0:
        raise B4ReopenInitialRuntimeError("automatic repair/retry authority drift")
    if cost.get("broker_writes_authorized") != 0 or cost.get("alpaca_orders_authorized") != 0:
        raise B4ReopenInitialRuntimeError("broker/order authority drift")
    if cost.get("live_money") != "PROHIBITED":
        raise B4ReopenInitialRuntimeError("live-money boundary drift")
    if cost.get("effective_unresolved_data_gap_refs") != [] or cost.get("effective_unresolved_reopen_reason_codes") != []:
        raise B4ReopenInitialRuntimeError("effective reopen gaps are not closed")

    rows = cost.get("initial_request_rows")
    if not isinstance(rows, list) or len(rows) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("reopen Initial request rows must be exact 9")
    expected_identity = tuple(
        (candidate, lane.value, stage.value)
        for candidate in EXPECTED_CANDIDATES
        for stage, lane in _STAGE_LANE
    )
    observed_identity: list[tuple[str, str, str]] = []
    manifest_rows: list[dict[str, Any]] = []
    total = Decimal("0")
    for row in rows:
        if not isinstance(row, Mapping):
            raise B4ReopenInitialRuntimeError("reopen Initial request row malformed")
        candidate = row.get("candidate_id")
        lane = row.get("lane")
        stage = row.get("stage")
        if not all(isinstance(value, str) for value in (candidate, lane, stage)):
            raise B4ReopenInitialRuntimeError("reopen Initial request identity missing")
        observed_identity.append((str(candidate), str(lane), str(stage)))
        if row.get("model") != "gpt-5.6-terra" or row.get("reasoning_effort") != "low":
            raise B4ReopenInitialRuntimeError("reopen Initial request model drift")
        if row.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
            raise B4ReopenInitialRuntimeError("reopen Initial row output cap drift")
        byte_count = row.get("request_body_utf8_bytes")
        if type(byte_count) is not int or byte_count <= 0 or row.get("input_tokens_upper_bound") != byte_count:
            raise B4ReopenInitialRuntimeError("reopen Initial row byte/token bound invalid")
        if row.get("effective_data_gap_refs") != []:
            raise B4ReopenInitialRuntimeError("reopen Initial row unexpectedly carries effective gap")
        if row.get("schema_allows_all_effective_material_claim_ids") is not True:
            raise B4ReopenInitialRuntimeError("reopen Initial schema allowlist proof missing")
        total += _decimal(row.get("per_call_cost_upper_bound_usd"), field_name="per-call cost ceiling")
        manifest_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "lane": row["lane"],
                "model_input_hash": row["model_input_hash"],
                "effective_bundle_hash": row["effective_bundle_hash"],
                "request_hash": row["request_hash"],
                "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                "max_output_tokens": row["max_output_tokens"],
            }
        )
    if tuple(observed_identity) != expected_identity:
        raise B4ReopenInitialRuntimeError("reopen Initial request order drift")
    if canonical_sha256({"rows": manifest_rows}) != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenInitialRuntimeError("reopen Initial request-manifest recomputation mismatch")
    if total != EXPECTED_COST_CEILING_USD:
        raise B4ReopenInitialRuntimeError("reopen Initial per-call cost sum drift")
    return str(observed_hash)


def _load_typed_freeze(path: str | Path) -> CouncilInputFreezeArtifact:
    try:
        return CouncilInputFreezeArtifact.model_validate(_read_object(path, label="historical B4 input freeze"))
    except Exception as exc:
        raise B4ReopenInitialRuntimeError("historical B4 input freeze invalid") from exc


def _load_initial_authority(path: str | Path) -> InitialSelectedModelAuthority:
    try:
        authority = InitialSelectedModelAuthority.model_validate(
            _read_object(path, label="Initial selected-model authority")
        )
    except Exception as exc:
        raise B4ReopenInitialRuntimeError("Initial selected-model authority invalid") from exc
    if authority.selection_hash != EXPECTED_INITIAL_SELECTION_HASH:
        raise B4ReopenInitialRuntimeError("Initial selected-model selection hash drift")
    selected = authority.selected_candidate
    if {
        "candidate_key": selected.candidate_key,
        "model": selected.model,
        "reasoning_effort": selected.reasoning_effort,
        "ladder_position": selected.ladder_position,
    } != EXPECTED_SELECTED_MODEL:
        raise B4ReopenInitialRuntimeError("Initial selected-model identity drift")
    return authority


def load_and_build_reopen_initial_runtime_plan(
    *,
    cost_preflight_path: str | Path,
    lifecycle_path: str | Path,
    overlay_path: str | Path,
    closure_path: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
) -> tuple[dict[str, Any], tuple[ReopenInitialRuntimePlanItem, ...], InitialSelectedModelAuthority, dict[str, Any]]:
    cost = _read_object(cost_preflight_path, label="B4 reopen production cost preflight")
    verify_reopen_initial_cost_preflight(cost)

    recomputed = load_and_build_b4_reopen_production_cost_preflight(
        code_commit_sha=EXPECTED_COST_PREFLIGHT_SOURCE_HEAD,
        lifecycle_path=lifecycle_path,
        overlay_path=overlay_path,
        closure_path=closure_path,
        freeze_path=freeze_path,
        reconciliation_path=reconciliation_path,
        handoff_path=handoff_path,
        initial_authority_path=initial_authority_path,
        pricing_path=pricing_path,
    )
    if recomputed != cost:
        raise B4ReopenInitialRuntimeError(
            "current deterministic code/source artifacts do not reproduce approved reopen Initial cost preflight"
        )

    overlay = _read_object(overlay_path, label="B4 reopen input overlay")
    closure = _read_object(closure_path, label="B3 reopen closure")
    reconciliation = _read_object(reconciliation_path, label="selected B3 reconciliation")
    freeze = _load_typed_freeze(freeze_path)
    authority = _load_initial_authority(initial_authority_path)
    handoff = load_real_event_handoff(Path(handoff_path))
    pricing = load_initial_runtime_pricing(Path(pricing_path))
    if pricing.get("pricing_hash") != cost.get("pricing_hash"):
        raise B4ReopenInitialRuntimeError("runtime pricing authority differs from cost preflight")

    legacy_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
    if tuple(item.candidate_id for item in legacy_inputs) != EXPECTED_CANDIDATES:
        raise B4ReopenInitialRuntimeError("legacy Initial model-input candidate order drift")
    supplemental_claims, supplemental_evidence = _closure_supplemental(closure)
    effective_ids_by_candidate, portfolio_refs_by_candidate = _effective_surface_by_candidate(
        overlay=overlay,
        supplemental_claims=supplemental_claims,
    )
    supplemental_views = {
        str(item["claim_id"]): materialize_supplemental_material_claim(item)
        for item in supplemental_claims
    }
    supplemental_evidence_by_candidate: dict[str, list[Mapping[str, Any]]] = {
        candidate: [] for candidate in EXPECTED_CANDIDATES
    }
    for item in supplemental_evidence:
        candidate = item.get("candidate_id")
        if candidate not in supplemental_evidence_by_candidate:
            raise B4ReopenInitialRuntimeError("supplemental evidence candidate drift")
        supplemental_evidence_by_candidate[str(candidate)].append(item)

    bundles: dict[str, CouncilInputBundle] = {}
    reopen_inputs: dict[str, dict[str, Any]] = {}
    for legacy_input, historical_bundle in zip(legacy_inputs, freeze.bundles, strict=True):
        candidate = legacy_input.candidate_id
        legacy_claims = [dict(item) for item in legacy_input.material_claims]
        legacy_ids = tuple(str(item["claim_id"]) for item in legacy_claims)
        effective_ids = effective_ids_by_candidate[candidate]
        if effective_ids[: len(legacy_ids)] != legacy_ids:
            raise B4ReopenInitialRuntimeError("effective claim surface does not preserve legacy prefix")
        appended = tuple(item for item in effective_ids if item not in set(legacy_ids))
        if any(item not in supplemental_views for item in appended):
            raise B4ReopenInitialRuntimeError("effective claim surface contains unknown supplemental claim")
        effective_claims = tuple([*legacy_claims, *(supplemental_views[item] for item in appended)])
        bundle = derive_effective_bundle(
            historical_bundle,
            effective_claim_ids=effective_ids,
            supplemental_portfolio_context_refs=portfolio_refs_by_candidate[candidate],
        )
        model_input = build_reopen_model_input(
            legacy_model_input=legacy_input,
            effective_bundle=bundle,
            effective_material_claims=effective_claims,
            supplemental_evidence_units=tuple(supplemental_evidence_by_candidate[candidate]),
        )
        bundles[candidate] = bundle
        reopen_inputs[candidate] = model_input

    rows = cost["initial_request_rows"]
    row_index = 0
    plan: list[ReopenInitialRuntimePlanItem] = []
    selected = authority.selected_candidate
    dispatch_index = 0
    for candidate in EXPECTED_CANDIDATES:
        bundle = bundles[candidate]
        model_input = reopen_inputs[candidate]
        for stage, lane in _STAGE_LANE:
            dispatch_index += 1
            row = rows[row_index]
            row_index += 1
            model_run_ref = (
                f"B4_REOPEN_INITIAL_{candidate}_{lane.value}_L2_"
                f"{str(model_input['model_input_hash'])[:12]}"
            )
            request = build_bounded_initial_request_v05(
                stage=stage,
                model_candidate=selected,
                bundle=bundle,
                model_run_ref=model_run_ref,
                model_input=model_input,
                allowed_data_gap_refs=(),
            )
            assert_bounded_request_invariants(request)
            byte_count = _request_body_utf8_bytes(request.request_payload)
            expected = {
                "candidate_id": candidate,
                "lane": lane.value,
                "stage": stage.value,
                "model_run_ref": model_run_ref,
                "model": selected.model,
                "reasoning_effort": selected.reasoning_effort,
                "model_input_hash": model_input["model_input_hash"],
                "effective_bundle_hash": bundle.bundle_hash,
                "request_hash": request.request_hash,
                "request_body_utf8_bytes": byte_count,
                "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
            }
            for key, value in expected.items():
                if row.get(key) != value:
                    raise B4ReopenInitialRuntimeError(
                        f"reconstructed reopen Initial request differs from cost preflight: {candidate}/{lane.value}/{key}"
                    )
            if request.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
                raise B4ReopenInitialRuntimeError("reconstructed reopen Initial output cap drift")
            plan.append(
                ReopenInitialRuntimePlanItem(
                    dispatch_index=dispatch_index,
                    candidate_id=candidate,
                    lane=lane,
                    stage=stage,
                    bundle=bundle,
                    model_input=model_input,
                    request=request,
                    request_body_utf8_bytes=byte_count,
                    frozen_row=row,
                )
            )
    if len(plan) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("reopen Initial runtime plan must contain exactly nine calls")
    return cost, tuple(plan), authority, pricing


def build_reopen_initial_dry_artifact(
    *,
    code_commit_sha: str,
    cost_preflight: Mapping[str, Any],
    plan: tuple[ReopenInitialRuntimePlanItem, ...],
) -> dict[str, Any]:
    verify_reopen_initial_cost_preflight(cost_preflight)
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenInitialRuntimeError("dry-run exact git SHA invalid")
    if len(plan) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("dry-run plan must contain exact nine calls")
    artifact: dict[str, Any] = {
        "artifact_version": REOPEN_INITIAL_DRY_ARTIFACT_VERSION,
        "runtime_version": REOPEN_INITIAL_RUNTIME_VERSION,
        "status": REOPEN_INITIAL_DRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "effective_input_manifest_hash": EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
        "source_cost_preflight_code_commit_sha": EXPECTED_COST_PREFLIGHT_SOURCE_HEAD,
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "planned_paid_calls_max": EXPECTED_CALLS,
        "automatic_repair_calls_authorized": False,
        "automatic_retries": 0,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_cost_ceiling_not_yet_granted_usd": str(EXPECTED_COST_CEILING_USD),
        "request_hashes": [item.request.request_hash for item in plan],
        "request_body_utf8_bytes": [item.request_body_utf8_bytes for item in plan],
        "owner_approval_required": True,
        "paid_dispatch_authorized": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_reopen_initial_dry_artifact(
    dry: Mapping[str, Any],
    *,
    expected_code_commit_sha: str,
    plan: tuple[ReopenInitialRuntimePlanItem, ...],
) -> str:
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(dry, exclude_fields=("artifact_hash",)):
        raise B4ReopenInitialRuntimeError("reopen Initial dry artifact self-hash mismatch")
    if dry.get("artifact_version") != REOPEN_INITIAL_DRY_ARTIFACT_VERSION or dry.get("status") != REOPEN_INITIAL_DRY_STATUS:
        raise B4ReopenInitialRuntimeError("reopen Initial dry artifact version/status drift")
    if dry.get("code_commit_sha") != expected_code_commit_sha:
        raise B4ReopenInitialRuntimeError("reopen Initial dry artifact HEAD drift")
    if dry.get("source_cost_preflight_artifact_hash") != EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenInitialRuntimeError("reopen Initial dry cost binding drift")
    if dry.get("request_manifest_hash") != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenInitialRuntimeError("reopen Initial dry request-manifest drift")
    if dry.get("request_hashes") != [item.request.request_hash for item in plan]:
        raise B4ReopenInitialRuntimeError("reopen Initial dry request hashes drift")
    if dry.get("request_body_utf8_bytes") != [item.request_body_utf8_bytes for item in plan]:
        raise B4ReopenInitialRuntimeError("reopen Initial dry request sizes drift")
    if dry.get("planned_paid_calls_max") != EXPECTED_CALLS or dry.get("automatic_repair_calls_authorized") is not False:
        raise B4ReopenInitialRuntimeError("reopen Initial dry paid-call boundary drift")
    if dry.get("paid_dispatch_authorized") is not False or dry.get("rebuttal_authorized") is not False or dry.get("judge_authorized") is not False or dry.get("rerun_authorized") is not False:
        raise B4ReopenInitialRuntimeError("reopen Initial dry artifact unexpectedly grants authority")
    if dry.get("model_calls") != 0 or dry.get("provider_reads") != 0 or dry.get("broker_writes") != 0 or dry.get("alpaca_orders") != 0 or dry.get("live_money") != "PROHIBITED":
        raise B4ReopenInitialRuntimeError("reopen Initial dry safety counters drift")
    return observed


def build_reopen_initial_paid_authorization(
    *,
    cost_preflight: Mapping[str, Any],
    dry_artifact: Mapping[str, Any],
    plan: tuple[ReopenInitialRuntimePlanItem, ...],
    approve_cost_artifact_hash: str,
    approve_max_usd: str,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    code_commit_sha: str,
    git_worktree_clean: bool,
    created_at_utc: str,
    run_id: str,
    receipt_journal_path: str,
) -> dict[str, Any]:
    cost_hash = verify_reopen_initial_cost_preflight(cost_preflight)
    dry_hash = verify_reopen_initial_dry_artifact(
        dry_artifact,
        expected_code_commit_sha=code_commit_sha,
        plan=plan,
    )
    if approve_cost_artifact_hash != cost_hash:
        raise B4ReopenInitialRuntimeError("owner approval cost artifact hash mismatch")
    approved_ceiling = _decimal(approve_max_usd, field_name="approved max USD")
    if approved_ceiling != EXPECTED_COST_CEILING_USD:
        raise B4ReopenInitialRuntimeError("approved max USD must exactly equal frozen Initial ceiling")
    if git_worktree_clean is not True:
        raise B4ReopenInitialRuntimeError("paid reopen Initial authorization requires clean worktree")
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenInitialRuntimeError("paid reopen Initial exact git SHA invalid")
    if not isinstance(owner_approval_id, str) or not owner_approval_id or owner_approval_id != owner_approval_id.strip():
        raise B4ReopenInitialRuntimeError("owner approval ID missing")
    approval_at = _utc_text(owner_approval_at_utc, field_name="owner approval timestamp")
    created_at = _utc_text(created_at_utc, field_name="authorization creation timestamp")
    if datetime.fromisoformat(approval_at.replace("Z", "+00:00")) > datetime.fromisoformat(created_at.replace("Z", "+00:00")):
        raise B4ReopenInitialRuntimeError("owner approval timestamp cannot be after authorization creation")
    if not isinstance(run_id, str) or not run_id or run_id != run_id.strip():
        raise B4ReopenInitialRuntimeError("reopen Initial run_id missing")
    if not isinstance(receipt_journal_path, str) or not receipt_journal_path:
        raise B4ReopenInitialRuntimeError("reopen Initial receipt journal path missing")
    if len(plan) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("paid reopen Initial plan must be exact 9 calls")

    artifact: dict[str, Any] = {
        "artifact_version": REOPEN_INITIAL_AUTHORIZATION_VERSION,
        "runtime_version": REOPEN_INITIAL_RUNTIME_VERSION,
        "status": REOPEN_INITIAL_AUTHORIZATION_STATUS,
        "run_id": run_id,
        "created_at_utc": created_at,
        "runner_code_commit_sha": code_commit_sha,
        "git_worktree_clean": True,
        "source_cost_preflight_artifact_hash": cost_hash,
        "source_cost_preflight_code_commit_sha": EXPECTED_COST_PREFLIGHT_SOURCE_HEAD,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "effective_input_manifest_hash": EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_hash,
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "planned_paid_calls_max": EXPECTED_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": False,
        "automatic_retries": 0,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "authorization_consumed_before_dispatch": False,
        "owner_approval": {
            "owner_approval_id": owner_approval_id,
            "owner_approval_at_utc": approval_at,
            "approved_cost_artifact_hash": cost_hash,
            "approved_cost_ceiling_usd": str(approved_ceiling),
            "approved_request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
            "approved_runner_code_commit_sha": code_commit_sha,
            "approved_runner_dry_artifact_hash": dry_hash,
            "scope": "ONE_B4_REOPEN_INITIAL_PRODUCTION_RUN_EXACTLY_NINE_BASELINE_CALLS_ONLY",
            "rebuttal_authorized": False,
            "judge_authorized": False,
            "rerun_authorized": False,
        },
        "receipt_contract_version": REOPEN_INITIAL_RECEIPT_VERSION,
        "journal_event_version": REOPEN_INITIAL_JOURNAL_EVENT_VERSION,
        "receipt_journal_path": receipt_journal_path,
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
        "model_calls_known_completed": 0,
        "provider_dispatch_attempts": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_dispatch_attempt_event(
    *,
    run_id: str,
    item: ReopenInitialRuntimePlanItem,
    authorization_hash: str,
    started_at_utc: str,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_version": REOPEN_INITIAL_JOURNAL_EVENT_VERSION,
        "event_type": "PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": _utc_text(started_at_utc, field_name="dispatch start"),
        "candidate_id": item.candidate_id,
        "lane": item.lane.value,
        "stage": item.stage.value,
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "requested_model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "paid_authorization_artifact_hash": authorization_hash,
        "authorization_consumed_by_this_attempt": True,
        "automatic_repair_attempted": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    event["event_hash"] = canonical_sha256(event)
    return event


def _strict_usage(raw_response: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    usage = raw_response.get("usage")
    if not isinstance(usage, Mapping):
        raise B4ReopenInitialRuntimeError("provider response lacks usage")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    if type(input_tokens) is not int or input_tokens < 0 or type(output_tokens) is not int or output_tokens < 0:
        raise B4ReopenInitialRuntimeError("provider usage token counters invalid")
    if not isinstance(input_details, Mapping):
        raise B4ReopenInitialRuntimeError("provider input token details missing")
    cached_tokens = input_details.get("cached_tokens")
    cache_write_tokens = input_details.get("cache_write_tokens")
    if type(cached_tokens) is not int or cached_tokens < 0:
        raise B4ReopenInitialRuntimeError("provider cached token count invalid")
    if type(cache_write_tokens) is not int or cache_write_tokens < 0:
        raise B4ReopenInitialRuntimeError("provider cache-write token count invalid")
    if cached_tokens + cache_write_tokens > input_tokens:
        raise B4ReopenInitialRuntimeError("cached + cache-write tokens exceed input tokens")
    reasoning_tokens = 0
    if isinstance(output_details, Mapping) and output_details.get("reasoning_tokens") is not None:
        reasoning_tokens = output_details.get("reasoning_tokens")
        if type(reasoning_tokens) is not int or reasoning_tokens < 0:
            raise B4ReopenInitialRuntimeError("provider reasoning token count invalid")
    return input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens


def _source_claims(model_input: Mapping[str, Any]) -> dict[str, Any]:
    raw_claims = model_input.get("material_claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise B4ReopenInitialRuntimeError("reopen Initial model input material claims missing")
    result: dict[str, Any] = {}
    for raw in raw_claims:
        claim = MATERIAL_CLAIM_V1.model_validate(raw)
        if claim.claim_id in result:
            raise B4ReopenInitialRuntimeError("reopen Initial model input duplicate source claim")
        result[claim.claim_id] = claim
    return result


def _computed_values(model_input: Mapping[str, Any]) -> dict[str, str]:
    raw_values = model_input.get("computed_values")
    if not isinstance(raw_values, list):
        raise B4ReopenInitialRuntimeError("reopen Initial model input computed values missing")
    result: dict[str, str] = {}
    for raw in raw_values:
        if not isinstance(raw, Mapping):
            raise B4ReopenInitialRuntimeError("reopen Initial computed value malformed")
        value_id = raw.get("computed_value_id")
        value = raw.get("value")
        if not isinstance(value_id, str) or not isinstance(value, str):
            raise B4ReopenInitialRuntimeError("reopen Initial computed value identity/value invalid")
        result[value_id] = value
    return result


def derive_initial_allowed_data_gap_refs_from_frozen_request(
    request: CouncilRequestEnvelope,
) -> tuple[str, ...]:
    """Recover only the gap authority encoded in the exact sent schema."""
    payload = request.request_payload
    try:
        schema = payload["text"]["format"]["schema"]
        properties = schema["properties"]
        field = properties["material_unknown_refs"]
    except (KeyError, TypeError) as exc:
        raise B4ReopenInitialRuntimeError("frozen Initial gap schema missing") from exc
    if not isinstance(schema, Mapping) or not isinstance(properties, Mapping) or not isinstance(field, Mapping):
        raise B4ReopenInitialRuntimeError("frozen Initial gap schema malformed")
    if field.get("type") != "array":
        raise B4ReopenInitialRuntimeError("frozen Initial gap schema must be array")
    items = field.get("items")
    if isinstance(items, Mapping) and "enum" in items:
        enum = items["enum"]
        if not isinstance(enum, list) or not enum or any(not isinstance(value, str) or not value for value in enum) or len(set(enum)) != len(enum):
            raise B4ReopenInitialRuntimeError("frozen Initial gap enum malformed")
        maximum = field.get("maxItems")
        if maximum is not None and (type(maximum) is not int or maximum < len(enum)):
            raise B4ReopenInitialRuntimeError("frozen Initial gap enum/maxItems conflict")
        return tuple(enum)
    if field.get("maxItems") == 0:
        if items not in (None, {}) and not isinstance(items, Mapping):
            raise B4ReopenInitialRuntimeError("frozen Initial empty gap schema malformed")
        return ()
    raise B4ReopenInitialRuntimeError("frozen Initial gap schema is unbounded or ambiguous")


def process_reopen_initial_provider_response(
    item: ReopenInitialRuntimePlanItem,
    *,
    raw_response: Mapping[str, Any],
    latency_ms: int,
    frozen_at: datetime,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    call, proposal = parse_council_responses_payload(
        raw_response,
        request=item.request,
        latency_ms=latency_ms,
    )
    if not isinstance(proposal, InitialCouncilOpinionProposal):
        raise B4ReopenInitialRuntimeError("reopen Initial response produced wrong DTO")
    if not proposal.proposed_claims:
        raise B4ReopenInitialRuntimeError("reopen Initial response contains no Council claims")
    promotion = promote_initial_council_opinion(
        proposal,
        bundle=item.bundle,
        expected_lane=item.lane,
        source_claims=_source_claims(item.model_input),
        computed_value_values=_computed_values(item.model_input),
        allowed_data_gap_refs=derive_initial_allowed_data_gap_refs_from_frozen_request(item.request),
        required_data_gap_refs=(),
        frozen_at=frozen_at,
    )
    opinion = COUNCIL_OPINION_V1.model_validate(promotion.council_opinion)
    structured = proposal.model_dump(mode="json", exclude_none=False)
    input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens = _strict_usage(raw_response)
    call_cost = actual_cost_usd(raw_response, model="gpt-5.6-terra", pricing=pricing)
    record: dict[str, Any] = {
        "candidate_id": item.candidate_id,
        "lane": item.lane.value,
        "stage": item.stage.value,
        "request_hash": item.request.request_hash,
        "model_run_ref": proposal.model_run_ref,
        "response_id": call.response_id,
        "effective_model": call.effective_model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "actual_cost_usd": str(call_cost),
        "output_hash": call.output_hash,
        "structured_output": structured,
        "structured_output_hash": canonical_sha256(structured),
        "material_claims": [
            claim.model_dump(mode="json", exclude_none=False, warnings=False)
            for claim in promotion.material_claims
        ],
        "claim_metadata": [
            metadata.model_dump(mode="json", exclude_none=False)
            for metadata in promotion.claim_metadata
        ],
        "council_opinion": opinion.model_dump(mode="json", exclude_none=False, warnings=False),
        "council_opinion_hash": canonical_sha256(opinion),
        "validator_results": [dict(value) for value in promotion.validator_results],
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def build_paid_call_receipt(
    *,
    run_id: str,
    item: ReopenInitialRuntimePlanItem,
    authorization_hash: str,
    attempt_event_hash: str,
    started_at_utc: str,
    finished_at_utc: str,
    provider_response_received: bool,
    raw_response: Mapping[str, Any] | None,
    latency_ms: int,
    processed_record: Mapping[str, Any] | None,
    validation_error: str | None,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    response_id = effective_model = output_hash = None
    structured_output: Mapping[str, Any] | None = None
    structured_output_hash = None
    input_tokens = cached_tokens = cache_write_tokens = output_tokens = reasoning_tokens = None
    actual_cost: Decimal | None = None
    cost_status = "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH"

    if provider_response_received and isinstance(raw_response, Mapping):
        try:
            call, proposal = parse_council_responses_payload(
                raw_response,
                request=item.request,
                latency_ms=latency_ms,
            )
            response_id = call.response_id
            effective_model = call.effective_model
            output_hash = call.output_hash
            if isinstance(proposal, InitialCouncilOpinionProposal):
                structured_output = proposal.model_dump(mode="json", exclude_none=False)
                structured_output_hash = canonical_sha256(structured_output)
        except Exception:
            # A malformed local DTO must not erase provider/cost evidence below.
            pass
        try:
            (
                input_tokens,
                cached_tokens,
                cache_write_tokens,
                output_tokens,
                reasoning_tokens,
            ) = _strict_usage(raw_response)
            actual_cost = actual_cost_usd(
                raw_response,
                model="gpt-5.6-terra",
                pricing=pricing,
            )
            cost_status = "COMPLETE"
        except Exception:
            cost_status = "INCOMPLETE_USAGE_OR_CACHE_WRITE"

    receipt: dict[str, Any] = {
        "receipt_version": REOPEN_INITIAL_RECEIPT_VERSION,
        "event_version": REOPEN_INITIAL_JOURNAL_EVENT_VERSION,
        "event_type": "PROVIDER_DISPATCH_RESULT",
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": _utc_text(started_at_utc, field_name="dispatch start"),
        "dispatch_finished_at_utc": _utc_text(finished_at_utc, field_name="dispatch finish"),
        "candidate_id": item.candidate_id,
        "lane": item.lane.value,
        "stage": item.stage.value,
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "requested_model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "paid_authorization_artifact_hash": authorization_hash,
        "dispatch_attempt_event_hash": attempt_event_hash,
        "dispatch_attempted": True,
        "provider_response_received": provider_response_received,
        "provider_dispatch_state_unknown": not provider_response_received,
        "response_id": response_id,
        "effective_model": effective_model,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "latency_ms": latency_ms,
        "actual_cost_usd": None if actual_cost is None else str(actual_cost),
        "cost_receipt_status": cost_status,
        "validation_status": "PASS" if processed_record is not None else "FAIL",
        "validation_error": validation_error,
        "output_hash": output_hash,
        "structured_output": None if structured_output is None else dict(structured_output),
        "structured_output_hash": structured_output_hash,
        "semantic_replay_status": "COMPLETE" if structured_output is not None else "INCOMPLETE",
        "processed_record_hash": None if processed_record is None else processed_record.get("record_hash"),
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "rerun_authorized": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _validate_processed_record(raw: Mapping[str, Any]) -> tuple[str, str]:
    record_hash = raw.get("record_hash")
    if not isinstance(record_hash, str) or record_hash != canonical_sha256(raw, exclude_fields=("record_hash",)):
        raise B4ReopenInitialRuntimeError("reopen Initial processed record self-hash mismatch")
    structured = raw.get("structured_output")
    if not isinstance(structured, Mapping) or raw.get("structured_output_hash") != canonical_sha256(structured):
        raise B4ReopenInitialRuntimeError("reopen Initial processed structured-output hash mismatch")
    claims = raw.get("material_claims")
    if not isinstance(claims, list) or not claims:
        raise B4ReopenInitialRuntimeError("reopen Initial processed claims missing")
    for claim in claims:
        MATERIAL_CLAIM_V1.model_validate(claim)
    opinion_raw = raw.get("council_opinion")
    if not isinstance(opinion_raw, Mapping):
        raise B4ReopenInitialRuntimeError("reopen Initial CouncilOpinion missing")
    opinion = COUNCIL_OPINION_V1.model_validate(opinion_raw)
    if raw.get("council_opinion_hash") != canonical_sha256(opinion):
        raise B4ReopenInitialRuntimeError("reopen Initial CouncilOpinion hash mismatch")
    if opinion.candidate_id != raw.get("candidate_id") or opinion.lane != raw.get("lane"):
        raise B4ReopenInitialRuntimeError("reopen Initial CouncilOpinion identity drift")
    return opinion.opinion_id, str(raw["council_opinion_hash"])


def receipt_manifest_hash(
    *,
    dispatch_attempt_hashes: list[str],
    paid_call_receipt_hashes: list[str],
) -> str:
    return canonical_sha256(
        {
            "dispatch_attempt_hashes": list(dispatch_attempt_hashes),
            "paid_call_receipt_hashes": list(paid_call_receipt_hashes),
        }
    )


def build_reopen_initial_blocked_artifact(
    *,
    run_id: str,
    code_commit_sha: str,
    authorization_hash: str,
    dry_artifact_hash: str,
    processed_records: list[Mapping[str, Any]],
    dispatch_attempt_hashes: list[str],
    receipt_hashes: list[str],
    receipt_journal_path: str,
    dispatch_attempts: int,
    provider_responses: int,
    actual_cost_usd_known: Decimal,
    cost_receipt_status: str,
    blocked_reason: str,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": REOPEN_INITIAL_BLOCKED_VERSION,
        "runtime_version": REOPEN_INITIAL_RUNTIME_VERSION,
        "status": REOPEN_INITIAL_BLOCKED_STATUS,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "effective_input_manifest_hash": EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_artifact_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "authorization_consumed": dispatch_attempts > 0,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "candidate_order": list(EXPECTED_CANDIDATES),
        "processed_opinion_count": len(processed_records),
        "processed_records": [dict(item) for item in processed_records],
        "provider_dispatch_attempts": dispatch_attempts,
        "model_calls_known_completed": provider_responses,
        "automatic_repair_calls": 0,
        "actual_cost_usd_known": str(actual_cost_usd_known),
        "approved_cost_ceiling_usd": str(EXPECTED_COST_CEILING_USD),
        "cost_receipt_status": cost_receipt_status,
        "dispatch_attempt_hashes": list(dispatch_attempt_hashes),
        "paid_call_receipt_hashes": list(receipt_hashes),
        "receipt_manifest_hash": receipt_manifest_hash(
            dispatch_attempt_hashes=dispatch_attempt_hashes,
            paid_call_receipt_hashes=receipt_hashes,
        ),
        "receipt_journal_path": receipt_journal_path,
        "blocked_reason": blocked_reason,
        "initial_freeze_barrier": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_reopen_initial_council_freeze_artifact(
    *,
    run_id: str,
    code_commit_sha: str,
    authorization_hash: str,
    dry_artifact_hash: str,
    processed_records: tuple[Mapping[str, Any], ...],
    dispatch_attempt_hashes: list[str],
    receipt_hashes: list[str],
    receipt_journal_path: str,
    actual_cost_usd_total: Decimal,
) -> dict[str, Any]:
    if len(processed_records) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("reopen Initial freeze requires exact nine processed records")
    if len(dispatch_attempt_hashes) != EXPECTED_CALLS or len(receipt_hashes) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("reopen Initial freeze requires exact nine attempts/receipts")
    expected_identity = tuple(
        (candidate, lane.value)
        for candidate in EXPECTED_CANDIDATES
        for _, lane in _STAGE_LANE
    )
    observed_identity: list[tuple[str, str]] = []
    opinion_ids: list[str] = []
    opinion_hashes: list[str] = []
    for raw in processed_records:
        opinion_id, opinion_hash = _validate_processed_record(raw)
        candidate = raw.get("candidate_id")
        lane = raw.get("lane")
        if not isinstance(candidate, str) or not isinstance(lane, str):
            raise B4ReopenInitialRuntimeError("reopen Initial processed identity missing")
        observed_identity.append((candidate, lane))
        opinion_ids.append(opinion_id)
        opinion_hashes.append(opinion_hash)
    if tuple(observed_identity) != expected_identity:
        raise B4ReopenInitialRuntimeError("reopen Initial opinion order/coverage drift")
    if len(set(opinion_ids)) != EXPECTED_CALLS or len(set(opinion_hashes)) != EXPECTED_CALLS:
        raise B4ReopenInitialRuntimeError("reopen Initial opinions must have unique IDs/hashes")
    if actual_cost_usd_total > EXPECTED_COST_CEILING_USD:
        raise B4ReopenInitialRuntimeError("reopen Initial actual cost exceeds approved ceiling")

    artifact: dict[str, Any] = {
        "artifact_version": REOPEN_INITIAL_FREEZE_VERSION,
        "runtime_version": REOPEN_INITIAL_RUNTIME_VERSION,
        "status": REOPEN_INITIAL_FROZEN_STATUS,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "effective_input_manifest_hash": EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
        "source_b4_reopen_lifecycle_plan_hash": EXPECTED_LIFECYCLE_HASH,
        "source_b4_reopen_input_overlay_hash": EXPECTED_OVERLAY_HASH,
        "source_b3_reopen_closure_hash": EXPECTED_CLOSURE_HASH,
        "runner_dry_artifact_hash": dry_artifact_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "authorization_consumed": True,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "candidate_order": list(EXPECTED_CANDIDATES),
        "initial_opinion_count": EXPECTED_CALLS,
        "initial_opinion_ids": opinion_ids,
        "initial_opinion_hashes": opinion_hashes,
        "processed_records": [dict(item) for item in processed_records],
        "provider_dispatch_attempts": EXPECTED_CALLS,
        "model_calls_known_completed": EXPECTED_CALLS,
        "automatic_repair_calls": 0,
        "actual_cost_usd": str(actual_cost_usd_total),
        "approved_cost_ceiling_usd": str(EXPECTED_COST_CEILING_USD),
        "cost_receipt_status": "COMPLETE",
        "dispatch_attempt_hashes": list(dispatch_attempt_hashes),
        "paid_call_receipt_hashes": list(receipt_hashes),
        "receipt_manifest_hash": receipt_manifest_hash(
            dispatch_attempt_hashes=dispatch_attempt_hashes,
            paid_call_receipt_hashes=receipt_hashes,
        ),
        "receipt_journal_path": receipt_journal_path,
        "initial_freeze_barrier": True,
        "rebuttal_cost_requires_this_fresh_initial_freeze": True,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": REOPEN_INITIAL_NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
