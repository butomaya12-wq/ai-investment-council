from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from .initial_runtime_cost_v02 import load_initial_runtime_pricing
from .model_policy import REBUTTAL_MODEL_LADDER, STAGE_MAX_OUTPUT_TOKENS, CouncilModelStage
from .models import CouncilLane
from .rebuttal_model_selection_v02 import verify_rebuttal_selected_model_authority_v02
from .rebuttal_runtime import RebuttalRuntimePlanItem
from .rebuttal_runtime_execution import validate_rebuttal_processed_record
from .rebuttal_schema_repair_v01 import build_bounded_rebuttal_request_v01
from .reopen_initial_runtime import load_and_build_reopen_initial_runtime_plan
from . import reopen_rebuttal_production_cost_preflight as cost_v01
from . import reopen_rebuttal_production_cost_preflight_v02 as cost_v02


RUNTIME_VERSION = "B4_REOPEN_REBUTTAL_PRODUCTION_RUNTIME_v0_1"
DRY_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_DRY_v0_1"
DRY_STATUS = "READY_FOR_EXPLICIT_OWNER_B4_REOPEN_REBUTTAL_AUTHORIZATION"
AUTH_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_AUTHORIZATION_v0_1"
AUTH_STATUS = "AUTHORIZED_FOR_ONE_B4_REOPEN_REBUTTAL_RUN"
EVENT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_JOURNAL_EVENT_v0_1"
RECEIPT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_CALL_RECEIPT_v0_1"
FREEZE_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_1"
FREEZE_STATUS = "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN"
BLOCKED_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_BLOCKED_v0_1"
BLOCKED_STATUS = "B4_REOPEN_REBUTTAL_COUNCIL_NOT_FROZEN"
NEXT_GATE = "B4_REOPEN_JUDGE_PRODUCTION_COST_PREFLIGHT_ZERO_CALL"

EXPECTED_COST_PREFLIGHT_HASH = "7213763ddf0c0a5f6622819d278de194685a796abce39095440e6534217d8838"
EXPECTED_COST_SOURCE_HEAD = "03363bd3a56c497e2e38140c6710beb44c902b7a"
EXPECTED_REQUEST_MANIFEST_HASH = "ff423f97dc2398befa25dd8bedbfd92bc46562e56c302caa67ddb2e1c8f50693"
EXPECTED_RECOVERED_INITIAL_FREEZE_HASH = "b98a3fbb2ce43cd9cab0d97b28ec62c1819ea5c777d8ff0a0dc36eb7628e8440"
EXPECTED_SELECTION_HASH = "8db38779171e0dcfc2e0325581192116b17adf98a1140950ffcbe5ce4698a882"
EXPECTED_SELECTION_EVAL_HASH = "1533a224f9a0c85abb77f42526aeed24e76c7e0453bc85cc5c8f8881669ae414"
EXPECTED_SELECTED = {
    "candidate_key": "R3",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "medium",
    "ladder_position": 3,
}
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_CALLS = 3
EXPECTED_MAX_OUTPUT_TOKENS = 6144
EXPECTED_COST_CEILING_USD = Decimal("1.73851")
EXPECTED_INITIAL_KNOWN_COST_USD = Decimal("0.3595905")
EXPECTED_INITIAL_SPEND_UPPER_USD = Decimal("0.4963025")


class B4ReopenRebuttalRuntimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BoundReopenRebuttalRuntime:
    cost_preflight: Mapping[str, Any]
    selection_authority: Mapping[str, Any]
    recovered_initial_freeze: Mapping[str, Any]
    plan: tuple[RebuttalRuntimePlanItem, ...]
    pricing: Mapping[str, Any]


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalRuntimeError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalRuntimeError(f"{label} root must be object")
    return value


def _decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise B4ReopenRebuttalRuntimeError(f"{field} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise B4ReopenRebuttalRuntimeError(f"{field} invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise B4ReopenRebuttalRuntimeError(f"{field} must be finite and non-negative")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise B4ReopenRebuttalRuntimeError("cost must be finite and non-negative")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _utc(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B4ReopenRebuttalRuntimeError(f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B4ReopenRebuttalRuntimeError(f"{field} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B4ReopenRebuttalRuntimeError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _selected_model() -> Any:
    matches = [item for item in REBUTTAL_MODEL_LADDER if item.candidate_key == "R3"]
    if len(matches) != 1:
        raise B4ReopenRebuttalRuntimeError("R3 model-policy candidate missing")
    selected = matches[0]
    observed = {
        "candidate_key": selected.candidate_key,
        "model": selected.model,
        "reasoning_effort": selected.reasoning_effort,
        "ladder_position": selected.ladder_position,
    }
    if observed != EXPECTED_SELECTED:
        raise B4ReopenRebuttalRuntimeError("R3 model-policy identity drift")
    if STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL] != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal output-token policy drift")
    return selected


def verify_cost_preflight(cost: Mapping[str, Any]) -> str:
    observed = cost.get("artifact_hash")
    if observed != EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenRebuttalRuntimeError("reopen Rebuttal cost-preflight hash drift")
    if observed != canonical_sha256(cost, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalRuntimeError("reopen Rebuttal cost-preflight self-hash mismatch")
    if cost.get("artifact_version") != cost_v02.ARTIFACT_VERSION or cost.get("status") != cost_v02.PASS_STATUS:
        raise B4ReopenRebuttalRuntimeError("reopen Rebuttal cost-preflight version/status drift")
    if cost.get("code_commit_sha") != EXPECTED_COST_SOURCE_HEAD:
        raise B4ReopenRebuttalRuntimeError("reopen Rebuttal cost source HEAD drift")
    if cost.get("source_recovered_initial_freeze_hash") != EXPECTED_RECOVERED_INITIAL_FREEZE_HASH:
        raise B4ReopenRebuttalRuntimeError("recovered Initial freeze lineage drift")
    if cost.get("request_manifest_hash") != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalRuntimeError("Rebuttal request-manifest drift")
    if cost.get("selected_rebuttal_model") != dict(cost_v01.EXPECTED_REBUTTAL_SELECTED):
        raise B4ReopenRebuttalRuntimeError("selected Rebuttal model drift")
    if cost.get("candidate_order") != list(EXPECTED_CANDIDATES):
        raise B4ReopenRebuttalRuntimeError("Rebuttal candidate order drift")
    if cost.get("planned_paid_calls_max") != EXPECTED_CALLS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal paid-call ceiling drift")
    if cost.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal max_output_tokens drift")
    if _decimal(cost.get("rebuttal_exact_cost_upper_bound_usd"), field="Rebuttal cost ceiling") != EXPECTED_COST_CEILING_USD:
        raise B4ReopenRebuttalRuntimeError("Rebuttal exact cost ceiling drift")
    if _decimal(cost.get("source_initial_known_actual_cost_usd"), field="Initial known cost") != EXPECTED_INITIAL_KNOWN_COST_USD:
        raise B4ReopenRebuttalRuntimeError("Initial known cost lineage drift")
    if _decimal(cost.get("source_initial_spend_upper_bound_usd"), field="Initial spend upper") != EXPECTED_INITIAL_SPEND_UPPER_USD:
        raise B4ReopenRebuttalRuntimeError("Initial spend upper lineage drift")
    if cost.get("owner_cost_approval_required") is not True:
        raise B4ReopenRebuttalRuntimeError("owner cost approval requirement missing")
    for field in (
        "rebuttal_paid_dispatch_authorized",
        "judge_paid_dispatch_authorized",
        "model_calls_authorized",
        "provider_reads_authorized",
        "rerun_authorized",
    ):
        if cost.get(field) is not False:
            raise B4ReopenRebuttalRuntimeError(f"cost preflight unexpectedly authorizes {field}")
    if cost.get("automatic_repair_calls_authorized") != 0 or cost.get("automatic_retries") != 0:
        raise B4ReopenRebuttalRuntimeError("repair/retry authority drift")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if cost.get(field) != 0:
            raise B4ReopenRebuttalRuntimeError(f"cost preflight zero-call invariant drift: {field}")
    if cost.get("live_money") != "PROHIBITED":
        raise B4ReopenRebuttalRuntimeError("live-money boundary drift")
    rows = cost.get("request_rows")
    if not isinstance(rows, list) or len(rows) != EXPECTED_CALLS:
        raise B4ReopenRebuttalRuntimeError("cost preflight must contain exactly three request rows")
    manifest = canonical_sha256({
        "rows": [
            {
                "candidate_id": row["candidate_id"],
                "candidate_key": row["candidate_key"],
                "context_hash": row["context_hash"],
                "request_hash": row["request_hash"],
                "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                "max_output_tokens": row["max_output_tokens"],
            }
            for row in rows
        ]
    })
    if manifest != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalRuntimeError("request-manifest recomputation mismatch")
    return str(observed)


def verify_selection_authority(selection: Mapping[str, Any]) -> str:
    try:
        observed = verify_rebuttal_selected_model_authority_v02(selection)
    except Exception as exc:
        raise B4ReopenRebuttalRuntimeError(f"historical Rebuttal selection authority invalid: {exc}") from exc
    if observed != EXPECTED_SELECTION_HASH:
        raise B4ReopenRebuttalRuntimeError("historical Rebuttal selection hash drift")
    if selection.get("selected_candidate") != EXPECTED_SELECTED:
        raise B4ReopenRebuttalRuntimeError("historical Rebuttal selection is not frozen R3")
    if selection.get("model_eval_artifact_hash") != EXPECTED_SELECTION_EVAL_HASH:
        raise B4ReopenRebuttalRuntimeError("historical Rebuttal eval binding drift")
    if selection.get("cost_receipt_status") != "COMPLETE" or selection.get("semantic_replay_receipts_complete") != 12:
        raise B4ReopenRebuttalRuntimeError("historical Rebuttal selection receipts incomplete")
    if selection.get("production_rebuttal_authorized") is not False or selection.get("judge_authorized") is not False:
        raise B4ReopenRebuttalRuntimeError("historical selection unexpectedly grants production/Judge")
    if selection.get("rerun_authorized") is not False:
        raise B4ReopenRebuttalRuntimeError("historical selection rerun boundary drift")
    return observed


def load_and_build_reopen_rebuttal_runtime_plan(
    *,
    cost_preflight_path: str | Path,
    recovered_initial_freeze_path: str | Path,
    selection_authority_path: str | Path,
    lifecycle_path: str | Path,
    initial_cost_preflight_path: str | Path,
    overlay_path: str | Path,
    closure_path: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
) -> BoundReopenRebuttalRuntime:
    cost = _read_object(cost_preflight_path, label="B4 reopen Rebuttal cost preflight")
    verify_cost_preflight(cost)
    recomputed = cost_v02.load_and_build_b4_reopen_rebuttal_production_cost_preflight(
        code_commit_sha=EXPECTED_COST_SOURCE_HEAD,
        recovered_initial_freeze_path=recovered_initial_freeze_path,
        lifecycle_path=lifecycle_path,
        initial_cost_preflight_path=initial_cost_preflight_path,
        overlay_path=overlay_path,
        closure_path=closure_path,
        freeze_path=freeze_path,
        reconciliation_path=reconciliation_path,
        handoff_path=handoff_path,
        initial_authority_path=initial_authority_path,
        pricing_path=pricing_path,
    )
    if recomputed != cost:
        raise B4ReopenRebuttalRuntimeError("current deterministic code/source artifacts do not reproduce frozen Rebuttal cost preflight")

    selection = _read_object(selection_authority_path, label="historical Rebuttal selected-model authority")
    verify_selection_authority(selection)
    recovered = _read_object(recovered_initial_freeze_path, label="recovered Initial freeze")
    _, initial_plan, _, pricing = load_and_build_reopen_initial_runtime_plan(
        cost_preflight_path=initial_cost_preflight_path,
        lifecycle_path=lifecycle_path,
        overlay_path=overlay_path,
        closure_path=closure_path,
        freeze_path=freeze_path,
        reconciliation_path=reconciliation_path,
        handoff_path=handoff_path,
        initial_authority_path=initial_authority_path,
        pricing_path=pricing_path,
    )
    cost_v02.verify_recovered_initial_freeze(recovered, initial_plan=initial_plan)
    selected = _selected_model()
    bundles = {item.candidate_id: item.bundle for item in initial_plan[::3]}
    contexts = [
        cost_v01._candidate_context(
            candidate_id=candidate,
            recovered=recovered,
            initial_plan=initial_plan,
        )
        for candidate in EXPECTED_CANDIDATES
    ]
    rows = {row["candidate_id"]: row for row in cost["request_rows"]}
    if tuple(rows) != EXPECTED_CANDIDATES:
        raise B4ReopenRebuttalRuntimeError("frozen Rebuttal request row order drift")
    plan: list[RebuttalRuntimePlanItem] = []
    for index, context in enumerate(contexts, start=1):
        candidate = str(context["candidate_id"])
        opposing = {
            CouncilLane(key): tuple(value)
            for key, value in context["opposing_claim_ids_by_lane"].items()
        }
        request = build_bounded_rebuttal_request_v01(
            model_candidate=selected,
            bundle=bundles[candidate],
            model_input=context["model_input"],
            initial_opinion_ids=tuple(context["initial_opinion_ids"]),
            initial_opinion_hashes=tuple(context["initial_opinion_hashes"]),
            opposing_claim_ids_by_lane=opposing,
            allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]),
        )
        byte_count = cost_v01._request_body_utf8_bytes(request.request_payload)
        frozen = rows[candidate]
        expected_fields = {
            "candidate_key": "R3",
            "model": selected.model,
            "reasoning_effort": selected.reasoning_effort,
            "context_hash": context["context_hash"],
            "effective_bundle_hash": bundles[candidate].bundle_hash,
            "effective_model_input_hash": context["effective_model_input_hash"],
            "rebuttal_model_input_hash": context["rebuttal_model_input_hash"],
            "request_hash": request.request_hash,
            "request_body_utf8_bytes": byte_count,
            "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        }
        for key, expected in expected_fields.items():
            if frozen.get(key) != expected:
                raise B4ReopenRebuttalRuntimeError(f"{candidate} reconstructed Rebuttal request differs from cost preflight: {key}")
        if request.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
            raise B4ReopenRebuttalRuntimeError("reconstructed Rebuttal output cap drift")
        plan.append(
            RebuttalRuntimePlanItem(
                dispatch_index=index,
                candidate_id=candidate,
                context_hash=str(context["context_hash"]),
                bundle=bundles[candidate],
                model_input=context["model_input"],
                initial_opinion_ids=tuple(context["initial_opinion_ids"]),
                initial_opinion_hashes=tuple(context["initial_opinion_hashes"]),
                opposing_claim_ids_by_lane=opposing,
                allowed_uncertainty_refs=tuple(context["allowed_uncertainty_refs"]),
                required_unknown_refs=tuple(context["required_unknown_refs"]),
                request=request,
                request_body_utf8_bytes=byte_count,
            )
        )
    if len(plan) != EXPECTED_CALLS or tuple(item.candidate_id for item in plan) != EXPECTED_CANDIDATES:
        raise B4ReopenRebuttalRuntimeError("reopen Rebuttal runtime plan must contain NVDA/MSFT/META exactly once")
    loaded_pricing = load_initial_runtime_pricing(Path(pricing_path))
    if loaded_pricing != pricing or pricing.get("pricing_hash") != cost.get("pricing_hash"):
        raise B4ReopenRebuttalRuntimeError("runtime pricing differs from cost preflight")
    return BoundReopenRebuttalRuntime(
        cost_preflight=cost,
        selection_authority=selection,
        recovered_initial_freeze=recovered,
        plan=tuple(plan),
        pricing=pricing,
    )


def build_dry_artifact(*, code_commit_sha: str, bound: BoundReopenRebuttalRuntime) -> dict[str, Any]:
    verify_cost_preflight(bound.cost_preflight)
    verify_selection_authority(bound.selection_authority)
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenRebuttalRuntimeError("dry-run exact git SHA invalid")
    if len(bound.plan) != EXPECTED_CALLS:
        raise B4ReopenRebuttalRuntimeError("dry-run plan must contain exact three calls")
    rows = [
        {
            "dispatch_index": item.dispatch_index,
            "candidate_id": item.candidate_id,
            "context_hash": item.context_hash,
            "request_hash": item.request.request_hash,
            "request_body_utf8_bytes": item.request_body_utf8_bytes,
            "model": item.request.request_payload["model"],
            "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
            "max_output_tokens": item.request.request_payload["max_output_tokens"],
        }
        for item in bound.plan
    ]
    artifact: dict[str, Any] = {
        "artifact_version": DRY_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "source_cost_preflight_code_commit_sha": EXPECTED_COST_SOURCE_HEAD,
        "source_recovered_initial_freeze_hash": EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "historical_rebuttal_selection_authority_revalidated": True,
        "selected_model_authority_selection_hash": EXPECTED_SELECTION_HASH,
        "selected_rebuttal_model": dict(cost_v01.EXPECTED_REBUTTAL_SELECTED),
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "planned_paid_calls_max": EXPECTED_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "cost_ceiling_usd": _decimal_text(EXPECTED_COST_CEILING_USD),
        "request_rows": rows,
        "plan_manifest_hash": canonical_sha256({"rows": rows}),
        "owner_approval_required": True,
        "paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "consumption_rule": "CONSUMED_ON_FIRST_REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "validated_processed_record_persisted_in_receipt": True,
        "crash_safe_local_finalize_supported_when_all_three_pass_receipts_exist": True,
        "partial_or_unknown_journal_fail_closed_without_provider_dispatch": True,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry_artifact(dry: Mapping[str, Any], *, expected_code_commit_sha: str, bound: BoundReopenRebuttalRuntime) -> str:
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(dry, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry artifact self-hash mismatch")
    if dry.get("artifact_version") != DRY_VERSION or dry.get("status") != DRY_STATUS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry artifact version/status drift")
    if dry.get("code_commit_sha") != expected_code_commit_sha:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry HEAD drift")
    if dry.get("source_cost_preflight_artifact_hash") != EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry cost binding drift")
    if dry.get("request_manifest_hash") != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry request manifest drift")
    if dry.get("planned_paid_calls_max") != EXPECTED_CALLS or dry.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry call/token ceiling drift")
    if _decimal(dry.get("cost_ceiling_usd"), field="dry cost ceiling") != EXPECTED_COST_CEILING_USD:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry cost ceiling drift")
    if dry.get("request_rows") != [
        {
            "dispatch_index": item.dispatch_index,
            "candidate_id": item.candidate_id,
            "context_hash": item.context_hash,
            "request_hash": item.request.request_hash,
            "request_body_utf8_bytes": item.request_body_utf8_bytes,
            "model": item.request.request_payload["model"],
            "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
            "max_output_tokens": item.request.request_payload["max_output_tokens"],
        }
        for item in bound.plan
    ]:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry request rows drift")
    if dry.get("paid_dispatch_authorized") is not False or dry.get("judge_authorized") is not False or dry.get("rebuttal_rerun_authorized") is not False:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry unexpectedly grants authority")
    if dry.get("automatic_repair_calls_authorized") != 0 or dry.get("automatic_retries") != 0:
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry repair/retry boundary drift")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if dry.get(field) != 0:
            raise B4ReopenRebuttalRuntimeError(f"Rebuttal dry zero-call invariant drift: {field}")
    if dry.get("live_money") != "PROHIBITED":
        raise B4ReopenRebuttalRuntimeError("Rebuttal dry live-money boundary drift")
    return observed


def build_paid_authorization(
    *,
    code_commit_sha: str,
    git_worktree_clean: bool,
    created_at_utc: str,
    run_id: str,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    approve_cost_artifact_hash: str,
    approve_request_manifest_hash: str,
    approve_dry_artifact_hash: str,
    approve_max_usd: str,
    dry_artifact: Mapping[str, Any],
    bound: BoundReopenRebuttalRuntime,
    receipt_journal_path: str,
) -> dict[str, Any]:
    if approve_cost_artifact_hash != EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenRebuttalRuntimeError("owner approval cost artifact hash mismatch")
    if approve_request_manifest_hash != EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalRuntimeError("owner approval request manifest mismatch")
    dry_hash = verify_dry_artifact(dry_artifact, expected_code_commit_sha=code_commit_sha, bound=bound)
    if approve_dry_artifact_hash != dry_hash:
        raise B4ReopenRebuttalRuntimeError("owner approval dry artifact hash mismatch")
    if _decimal(approve_max_usd, field="approved max USD") != EXPECTED_COST_CEILING_USD:
        raise B4ReopenRebuttalRuntimeError("approved max USD must equal frozen Rebuttal ceiling")
    if not isinstance(owner_approval_id, str) or not owner_approval_id or owner_approval_id != owner_approval_id.strip() or any(ch.isspace() for ch in owner_approval_id):
        raise B4ReopenRebuttalRuntimeError("owner approval ID invalid")
    created = _utc(created_at_utc, field="authorization created_at")
    owner_at = _utc(owner_approval_at_utc, field="owner approval time")
    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    owner_dt = datetime.fromisoformat(owner_at.replace("Z", "+00:00"))
    if owner_dt > created_dt:
        raise B4ReopenRebuttalRuntimeError("owner approval cannot postdate authorization")
    artifact: dict[str, Any] = {
        "artifact_version": AUTH_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": AUTH_STATUS,
        "run_id": run_id,
        "created_at_utc": created,
        "code_commit_sha": code_commit_sha,
        "git_worktree_clean": git_worktree_clean,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_at,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "source_recovered_initial_freeze_hash": EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": EXPECTED_SELECTION_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_hash,
        "selected_rebuttal_model": dict(cost_v01.EXPECTED_REBUTTAL_SELECTED),
        "planned_paid_calls_max": EXPECTED_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_cost_ceiling_usd": _decimal_text(EXPECTED_COST_CEILING_USD),
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "receipt_journal_path": receipt_journal_path,
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_attempt_event(*, run_id: str, item: RebuttalRuntimePlanItem, authorization_hash: str, started_at_utc: str) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_version": EVENT_VERSION,
        "event_type": "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": _utc(started_at_utc, field="dispatch start"),
        "candidate_id": item.candidate_id,
        "stage": "REBUTTAL",
        "context_hash": item.context_hash,
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "requested_model": item.request.request_payload["model"],
        "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
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


def build_result_receipt(
    *,
    run_id: str,
    item: RebuttalRuntimePlanItem,
    authorization_hash: str,
    attempt_hash: str,
    started_at_utc: str,
    finished_at_utc: str,
    provider_response_received: bool,
    run: Any,
) -> dict[str, Any]:
    processed = run.processed_record if isinstance(run.processed_record, Mapping) else None
    actual_cost = None if run.actual_cost_usd is None else str(run.actual_cost_usd)
    receipt: dict[str, Any] = {
        "receipt_version": RECEIPT_VERSION,
        "event_version": EVENT_VERSION,
        "event_type": "REBUTTAL_PROVIDER_DISPATCH_RESULT",
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": _utc(started_at_utc, field="dispatch start"),
        "dispatch_finished_at_utc": _utc(finished_at_utc, field="dispatch finish"),
        "candidate_id": item.candidate_id,
        "stage": "REBUTTAL",
        "context_hash": item.context_hash,
        "request_hash": item.request.request_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "dispatch_attempt_event_hash": attempt_hash,
        "provider_response_received": provider_response_received,
        "provider_dispatch_state_unknown": not provider_response_received,
        "response_id": run.response_id,
        "effective_model": run.effective_model,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "latency_ms": run.latency_ms,
        "actual_cost_usd": actual_cost,
        "cost_receipt_status": run.cost_receipt_status if provider_response_received else "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "validation_status": run.validation_status,
        "validation_error": run.validation_error,
        "output_hash": run.output_hash,
        "structured_output_hash": run.structured_output_hash,
        "processed_record_hash": None if processed is None else processed.get("record_hash"),
        "processed_record": None if processed is None else dict(processed),
        "validated_processed_record_persisted": processed is not None and run.validation_status == "PASS",
        "local_finalize_replayable": processed is not None and run.validation_status == "PASS" and run.cost_receipt_status == "COMPLETE",
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _validate_receipt_processed_record(receipt: Mapping[str, Any], *, item: RebuttalRuntimePlanItem) -> Mapping[str, Any]:
    observed_hash = receipt.get("receipt_hash")
    if not isinstance(observed_hash, str) or observed_hash != canonical_sha256(receipt, exclude_fields=("receipt_hash",)):
        raise B4ReopenRebuttalRuntimeError("Rebuttal receipt self-hash mismatch")
    if receipt.get("event_type") != "REBUTTAL_PROVIDER_DISPATCH_RESULT" or receipt.get("validation_status") != "PASS":
        raise B4ReopenRebuttalRuntimeError("Rebuttal receipt is not PASS")
    if receipt.get("provider_response_received") is not True or receipt.get("cost_receipt_status") != "COMPLETE":
        raise B4ReopenRebuttalRuntimeError("Rebuttal PASS receipt lacks complete provider/cost state")
    if receipt.get("candidate_id") != item.candidate_id or receipt.get("request_hash") != item.request.request_hash or receipt.get("context_hash") != item.context_hash:
        raise B4ReopenRebuttalRuntimeError("Rebuttal receipt request/context identity drift")
    raw = receipt.get("processed_record")
    if not isinstance(raw, Mapping):
        raise B4ReopenRebuttalRuntimeError("Rebuttal PASS receipt lacks durable processed record")
    validate_rebuttal_processed_record(raw)
    if raw.get("candidate_id") != item.candidate_id or raw.get("request_hash") != item.request.request_hash or raw.get("context_hash") != item.context_hash:
        raise B4ReopenRebuttalRuntimeError("durable Rebuttal processed-record lineage drift")
    if raw.get("record_hash") != receipt.get("processed_record_hash"):
        raise B4ReopenRebuttalRuntimeError("durable Rebuttal processed-record hash drift")
    return raw


def build_freeze_artifact(
    *,
    code_commit_sha: str,
    run_id: str,
    authorization_hash: str,
    dry_hash: str,
    receipt_hashes: Sequence[str],
    processed_records: Sequence[Mapping[str, Any]],
    actual_rebuttal_cost_usd: Decimal,
    finalized_from_durable_receipts_without_provider_dispatch: bool,
    bound: BoundReopenRebuttalRuntime,
) -> dict[str, Any]:
    if len(receipt_hashes) != EXPECTED_CALLS or len(processed_records) != EXPECTED_CALLS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal freeze requires exactly three receipts and records")
    bundle_ids: list[str] = []
    bundle_hashes: list[str] = []
    research_reopen_candidates: list[str] = []
    for raw, item in zip(processed_records, bound.plan, strict=True):
        validate_rebuttal_processed_record(raw)
        if raw.get("candidate_id") != item.candidate_id or raw.get("request_hash") != item.request.request_hash or raw.get("context_hash") != item.context_hash:
            raise B4ReopenRebuttalRuntimeError("Rebuttal freeze processed-record lineage drift")
        bundle_ids.append(str(raw["rebuttal_bundle_id"]))
        bundle_hashes.append(str(raw["rebuttal_bundle_hash"]))
        if raw.get("research_reopen_required") is True:
            research_reopen_candidates.append(item.candidate_id)
    if len(set(bundle_ids)) != EXPECTED_CALLS or len(set(bundle_hashes)) != EXPECTED_CALLS:
        raise B4ReopenRebuttalRuntimeError("Rebuttal freeze bundles must be unique")
    if actual_rebuttal_cost_usd > EXPECTED_COST_CEILING_USD:
        raise B4ReopenRebuttalRuntimeError("Rebuttal actual cost exceeds approved ceiling")
    known_actual = EXPECTED_INITIAL_KNOWN_COST_USD + actual_rebuttal_cost_usd
    aggregate_upper = EXPECTED_INITIAL_SPEND_UPPER_USD + actual_rebuttal_cost_usd
    artifact: dict[str, Any] = {
        "artifact_version": FREEZE_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": FREEZE_STATUS,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "source_recovered_initial_freeze_hash": EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "source_cost_preflight_code_commit_sha": EXPECTED_COST_SOURCE_HEAD,
        "selected_model_authority_selection_hash": EXPECTED_SELECTION_HASH,
        "selected_rebuttal_model": dict(cost_v01.EXPECTED_REBUTTAL_SELECTED),
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "candidate_order": list(EXPECTED_CANDIDATES),
        "rebuttal_bundle_count": EXPECTED_CALLS,
        "rebuttal_bundle_ids": bundle_ids,
        "rebuttal_bundle_hashes": bundle_hashes,
        "processed_records": [dict(row) for row in processed_records],
        "research_reopen_required_candidates": research_reopen_candidates,
        "dispatch_attempts": EXPECTED_CALLS,
        "model_calls": EXPECTED_CALLS,
        "automatic_repair_calls": 0,
        "automatic_retries": 0,
        "rebuttal_actual_cost_usd": _decimal_text(actual_rebuttal_cost_usd),
        "rebuttal_cost_receipt_status": "COMPLETE",
        "source_initial_known_actual_cost_usd": _decimal_text(EXPECTED_INITIAL_KNOWN_COST_USD),
        "source_initial_spend_upper_bound_usd": _decimal_text(EXPECTED_INITIAL_SPEND_UPPER_USD),
        "historical_unknown_initial_dispatch_cost_remains_unknown": True,
        "aggregate_known_actual_cost_usd": _decimal_text(known_actual),
        "aggregate_spend_upper_bound_usd": _decimal_text(aggregate_upper),
        "aggregate_cost_receipt_status": "PARTIAL_UNKNOWN_HISTORICAL_INITIAL_DISPATCH",
        "receipt_manifest_hash": canonical_sha256({"receipt_hashes": list(receipt_hashes)}),
        "paid_call_receipt_hashes": list(receipt_hashes),
        "rebuttal_freeze_barrier": True,
        "finalized_from_durable_receipts_without_provider_dispatch": finalized_from_durable_receipts_without_provider_dispatch,
        "judge_model_calls": 0,
        "judge_authorized": False,
        "rebuttal_rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_blocked_artifact(
    *,
    code_commit_sha: str,
    run_id: str,
    authorization_hash: str,
    dry_hash: str,
    reason: str,
    dispatch_attempts: int,
    known_model_calls: int,
    known_rebuttal_cost_usd: Decimal,
    receipt_hashes: Sequence[str],
    successful_processed_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": BLOCKED_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": BLOCKED_STATUS,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "source_recovered_initial_freeze_hash": EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "authorization_consumed": dispatch_attempts > 0,
        "dispatch_attempts": dispatch_attempts,
        "model_calls_known_completed": known_model_calls,
        "known_rebuttal_cost_usd": _decimal_text(known_rebuttal_cost_usd),
        "receipt_hashes": list(receipt_hashes),
        "successful_processed_records": [dict(row) for row in successful_processed_records],
        "blocked_reason": reason,
        "rebuttal_freeze_barrier": False,
        "automatic_repair_calls": 0,
        "automatic_retries": 0,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def append_jsonl_fsync(path: str | Path, payload: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_fsync_new(path: str | Path, payload: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(p, flags, 0o600)
    try:
        data = (json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise B4ReopenRebuttalRuntimeError("Rebuttal journal line root must be object")
        events.append(value)
    return events


def durable_finalize_inputs_from_journal(
    *,
    events: Sequence[Mapping[str, Any]],
    bound: BoundReopenRebuttalRuntime,
    authorization_hash: str,
) -> tuple[list[str], list[Mapping[str, Any]], Decimal] | None:
    attempts = [row for row in events if row.get("event_type") == "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT"]
    results = [row for row in events if row.get("event_type") == "REBUTTAL_PROVIDER_DISPATCH_RESULT"]
    if not attempts and not results:
        return None
    if len(attempts) != EXPECTED_CALLS or len(results) != EXPECTED_CALLS:
        return None
    if [row.get("dispatch_index") for row in attempts] != [1, 2, 3] or [row.get("dispatch_index") for row in results] != [1, 2, 3]:
        return None
    receipt_hashes: list[str] = []
    records: list[Mapping[str, Any]] = []
    total = Decimal("0")
    for attempt, receipt, item in zip(attempts, results, bound.plan, strict=True):
        attempt_hash = attempt.get("event_hash")
        if not isinstance(attempt_hash, str) or attempt_hash != canonical_sha256(attempt, exclude_fields=("event_hash",)):
            raise B4ReopenRebuttalRuntimeError("Rebuttal attempt event self-hash mismatch")
        if attempt.get("paid_authorization_artifact_hash") != authorization_hash or receipt.get("paid_authorization_artifact_hash") != authorization_hash:
            raise B4ReopenRebuttalRuntimeError("Rebuttal journal authorization lineage drift")
        if receipt.get("dispatch_attempt_event_hash") != attempt_hash:
            raise B4ReopenRebuttalRuntimeError("Rebuttal result does not bind attempt event")
        record = _validate_receipt_processed_record(receipt, item=item)
        cost = _decimal(receipt.get("actual_cost_usd"), field="receipt actual cost")
        total += cost
        receipt_hashes.append(str(receipt["receipt_hash"]))
        records.append(record)
    if total > EXPECTED_COST_CEILING_USD:
        raise B4ReopenRebuttalRuntimeError("durable Rebuttal receipts exceed cost ceiling")
    return receipt_hashes, records, total
