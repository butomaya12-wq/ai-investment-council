from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from aic.council.initial_runtime_cost_v02 import load_initial_runtime_pricing
from aic.council.model_input import build_initial_model_inputs
from aic.council.models import CouncilInputFreezeArtifact
from aic.council.rebuttal_model_selection_v02 import (
    verify_rebuttal_selected_model_authority_v02,
)
from aic.council.rebuttal_preflight import build_rebuttal_frozen_contexts
from aic.council.rebuttal_runtime import (
    EXPECTED_PRODUCTION_CALLS,
    REBUTTAL_RUNTIME_VERSION,
    build_rebuttal_runtime_plan,
)
from aic.council.rebuttal_runtime_execution import (
    REBUTTAL_COUNCIL_BLOCKED_STATUS,
    REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
    build_rebuttal_council_freeze_artifact,
    execute_rebuttal_runtime_item_once,
    verify_rebuttal_council_freeze_artifact,
)
from aic.council.rebuttal_runtime_preflight import (
    EXPECTED_INITIAL_FREEZE_HASH,
    EXPECTED_PRICING_HASH,
    EXPECTED_PRICING_VERSION,
    EXPECTED_SELECTED,
    EXPECTED_SELECTION_HASH,
    EXPECTED_SOURCE_REQUEST_MANIFEST,
    REBUTTAL_RUNTIME_COST_PREFLIGHT_STATUS,
    verify_rebuttal_runtime_cost_preflight,
    verify_rebuttal_runtime_request_preflight,
)
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


PAID_AUTHORIZATION_VERSION = "B4_REBUTTAL_RUNTIME_PAID_AUTHORIZATION_v0_1"
PAID_RECEIPT_VERSION = "B4_REBUTTAL_RUNTIME_PAID_CALL_RECEIPT_v0_1"
RUNNER_DRY_VERSION = "B4_REBUTTAL_PRODUCTION_RUNNER_DRY_v0_1"
EXPECTED_MAX_OUTPUT_TOKENS = 6144

DEFAULT_INITIAL_FREEZE = Path(".aic-runtime/b4_initial_council_freeze_v0_5.json")
DEFAULT_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_SOURCE_PREFLIGHT = Path(".aic-runtime/b4_rebuttal_source_preflight_v0_1.json")
DEFAULT_RUNTIME_PREFLIGHT = Path(".aic-runtime/b4_rebuttal_runtime_request_preflight_v0_1.json")
DEFAULT_COST_PREFLIGHT = Path(".aic-runtime/b4_rebuttal_runtime_cost_preflight_v0_1.json")
DEFAULT_SELECTION = Path(".aic-runtime/b4_rebuttal_selected_model_authority_v0_2.json")
DEFAULT_DRY_OUTPUT = Path(".aic-runtime/b4_rebuttal_production_runner_dry_v0_1.json")
DEFAULT_PAID_OUTPUT = Path(".aic-runtime/b4_rebuttal_council_freeze_v0_1.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(
    ".aic-runtime/b4_rebuttal_runtime_paid_authorization_v0_1.json"
)
DEFAULT_RECEIPT_JOURNAL = Path(
    ".aic-runtime/b4_rebuttal_runtime_paid_receipts_v0_1.jsonl"
)


class RebuttalRuntimeAuthorizationError(ValueError):
    pass


class DispatchTrackingTransport:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.dispatch_attempts = 0
        self.provider_responses = 0

    def post(self, *, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        self.dispatch_attempts += 1
        result = self.delegate.post(payload=payload, api_key=api_key)
        self.provider_responses += 1
        return result


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or execute exactly one owner-approved B4 production Rebuttal. "
            "Paid execution is exactly three R3 calls and has no automatic repair."
        )
    )
    parser.add_argument("--initial-freeze", type=Path, default=DEFAULT_INITIAL_FREEZE)
    parser.add_argument("--input-freeze", type=Path, default=DEFAULT_INPUT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--source-preflight", type=Path, default=DEFAULT_SOURCE_PREFLIGHT)
    parser.add_argument("--runtime-preflight", type=Path, default=DEFAULT_RUNTIME_PREFLIGHT)
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--dry-output", type=Path, default=DEFAULT_DRY_OUTPUT)
    parser.add_argument("--paid-output", type=Path, default=DEFAULT_PAID_OUTPUT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION_OUTPUT)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_RECEIPT_JOURNAL)
    parser.add_argument("--execute-paid-rebuttal", action="store_true")
    parser.add_argument("--approve-initial-freeze-hash")
    parser.add_argument("--approve-selection-hash")
    parser.add_argument("--approve-source-preflight-hash")
    parser.add_argument("--approve-source-request-manifest-hash")
    parser.add_argument("--approve-runtime-request-preflight-hash")
    parser.add_argument("--approve-runtime-request-manifest-hash")
    parser.add_argument("--approve-runtime-cost-artifact-hash")
    parser.add_argument("--approve-runner-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser.parse_args()


def _read(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RebuttalRuntimeAuthorizationError(
            f"unable to read {label}: {path}"
        ) from exc
    if not isinstance(raw, dict):
        raise RebuttalRuntimeAuthorizationError(f"{label} root must be object")
    return raw


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise RebuttalRuntimeAuthorizationError(
            f"{field_name} must be decimal string"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise RebuttalRuntimeAuthorizationError(
            f"{field_name} invalid decimal"
        ) from exc
    if not parsed.is_finite() or parsed < 0:
        raise RebuttalRuntimeAuthorizationError(f"{field_name} invalid")
    return parsed


def _canonical_owner_approval_time(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RebuttalRuntimeAuthorizationError(
            "owner approval timestamp is required"
        )
    text = value.strip()
    parse_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise RebuttalRuntimeAuthorizationError(
            "owner approval timestamp must be RFC3339"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise RebuttalRuntimeAuthorizationError(
            "owner approval timestamp must be UTC"
        )
    if parsed > datetime.now(UTC):
        raise RebuttalRuntimeAuthorizationError(
            "owner approval timestamp cannot be future"
        )
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _owner_record(
    owner_approval_id: str | None,
    owner_approval_at_utc: str | None,
) -> tuple[str, str]:
    if not isinstance(owner_approval_id, str) or not owner_approval_id.strip():
        raise RebuttalRuntimeAuthorizationError("owner approval ID is required")
    approval_id = owner_approval_id.strip()
    if len(approval_id) > 160 or any(ch.isspace() for ch in approval_id):
        raise RebuttalRuntimeAuthorizationError(
            "owner approval ID must be <=160 chars without whitespace"
        )
    return approval_id, _canonical_owner_approval_time(owner_approval_at_utc)


def _git_context(*, expected_head: str | None = None) -> dict[str, Any]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RebuttalRuntimeAuthorizationError(
            "unable to prove local git execution context"
        ) from exc
    if expected_head is not None and head != expected_head:
        raise RebuttalRuntimeAuthorizationError(
            "local HEAD differs from approved production Rebuttal HEAD"
        )
    if status.strip():
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal requires clean git worktree"
        )
    return {"code_commit_sha": head, "git_worktree_clean": True}


def _write(path: Path, artifact: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_durable_new(path: Path, artifact: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _require_fresh_paid_paths(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            raise RebuttalRuntimeAuthorizationError(
                f"paid evidence path already exists; refusing overwrite: {path}"
            )


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _run_id(started_at: str, request_hash: str, cost_hash: str) -> str:
    suffix = canonical_sha256(
        {
            "started_at_utc": started_at,
            "runtime_request_preflight_artifact_hash": request_hash,
            "runtime_cost_preflight_artifact_hash": cost_hash,
        }
    )[:12]
    compact = started_at.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-REBUTTAL-RUNTIME-{compact}-{suffix}"


def _load_bound_runtime(args: argparse.Namespace) -> dict[str, Any]:
    git_context = _git_context()
    head = git_context["code_commit_sha"]
    initial_freeze = _read(args.initial_freeze, label="Initial Council freeze")
    freeze = CouncilInputFreezeArtifact.model_validate(
        _read(args.input_freeze, label="B4 input freeze")
    )
    reconciliation = _read(args.reconciliation, label="B3 reconciliation")
    handoff = load_real_event_handoff(args.handoff)
    source = _read(args.source_preflight, label="Rebuttal source preflight")
    runtime_preflight = _read(
        args.runtime_preflight, label="Rebuttal runtime request preflight"
    )
    cost_preflight = _read(
        args.cost_preflight, label="Rebuttal runtime cost preflight"
    )
    selection = _read(args.selection, label="Rebuttal selected-model authority")

    source_hash = source.get("artifact_hash")
    if not isinstance(source_hash, str) or source_hash != canonical_sha256(
        source, exclude_fields=("artifact_hash",)
    ):
        raise RebuttalRuntimeAuthorizationError(
            "Rebuttal source-preflight canonical hash mismatch"
        )
    if source.get("code_commit_sha") != head:
        raise RebuttalRuntimeAuthorizationError(
            "Rebuttal source preflight is not bound to current HEAD"
        )
    if source.get("initial_council_freeze_artifact_hash") != EXPECTED_INITIAL_FREEZE_HASH:
        raise RebuttalRuntimeAuthorizationError(
            "Rebuttal source preflight Initial freeze drift"
        )
    if source.get("request_manifest_hash") != EXPECTED_SOURCE_REQUEST_MANIFEST:
        raise RebuttalRuntimeAuthorizationError(
            "Rebuttal source request manifest drift"
        )

    request_hash = verify_rebuttal_runtime_request_preflight(runtime_preflight)
    cost_hash = verify_rebuttal_runtime_cost_preflight(cost_preflight)
    selection_hash = verify_rebuttal_selected_model_authority_v02(selection)
    if selection_hash != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimeAuthorizationError(
            "selected-model authority hash drift"
        )
    if selection.get("selected_candidate") != EXPECTED_SELECTED:
        raise RebuttalRuntimeAuthorizationError(
            "selected-model authority is not frozen R3"
        )
    if runtime_preflight.get("code_commit_sha") != head:
        raise RebuttalRuntimeAuthorizationError(
            "runtime request preflight is not bound to current HEAD"
        )
    if cost_preflight.get("code_commit_sha") != head:
        raise RebuttalRuntimeAuthorizationError(
            "runtime cost preflight is not bound to current HEAD"
        )
    if cost_preflight.get("status") != REBUTTAL_RUNTIME_COST_PREFLIGHT_STATUS:
        raise RebuttalRuntimeAuthorizationError(
            "runtime cost preflight is not approval-ready"
        )
    if cost_preflight.get("runtime_request_preflight_artifact_hash") != request_hash:
        raise RebuttalRuntimeAuthorizationError(
            "runtime cost preflight does not bind request preflight"
        )
    if cost_preflight.get("runtime_request_manifest_hash") != runtime_preflight.get(
        "request_manifest_hash"
    ):
        raise RebuttalRuntimeAuthorizationError(
            "runtime request/cost manifest binding mismatch"
        )
    if runtime_preflight.get("source_request_preflight_artifact_hash") != source_hash:
        raise RebuttalRuntimeAuthorizationError(
            "runtime request preflight does not bind source artifact"
        )
    if cost_preflight.get("source_request_preflight_artifact_hash") != source_hash:
        raise RebuttalRuntimeAuthorizationError(
            "runtime cost preflight does not bind source artifact"
        )
    if runtime_preflight.get("planned_paid_calls_max") != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal request call ceiling drift"
        )
    if cost_preflight.get("planned_paid_calls_max") != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal cost call ceiling drift"
        )
    if runtime_preflight.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal output-token cap drift"
        )
    if cost_preflight.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal cost output-token cap drift"
        )
    if cost_preflight.get("pricing_version") != EXPECTED_PRICING_VERSION:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal pricing version drift"
        )
    if cost_preflight.get("pricing_hash") != EXPECTED_PRICING_HASH:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal pricing hash drift"
        )
    for obj, label in (
        (source, "source"),
        (runtime_preflight, "request"),
        (cost_preflight, "cost"),
    ):
        if obj.get("automatic_repair_calls_authorized", False) is not False:
            raise RebuttalRuntimeAuthorizationError(
                f"{label} unexpectedly authorizes automatic repair"
            )
        for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
            if obj.get(field) != 0:
                raise RebuttalRuntimeAuthorizationError(
                    f"{label} preflight {field} must be zero"
                )
        if obj.get("live_money") != "PROHIBITED":
            raise RebuttalRuntimeAuthorizationError(
                f"{label} live-money invariant drift"
            )
        if obj.get("production_rebuttal_authorized") is not False:
            raise RebuttalRuntimeAuthorizationError(
                f"{label} unexpectedly pre-authorizes production Rebuttal"
            )
        if obj.get("judge_authorized") is not False:
            raise RebuttalRuntimeAuthorizationError(
                f"{label} unexpectedly authorizes Judge"
            )
        if "rerun_authorized" in obj and obj.get("rerun_authorized") is not False:
            raise RebuttalRuntimeAuthorizationError(
                f"{label} unexpectedly authorizes rerun"
            )

    initial_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
    contexts = build_rebuttal_frozen_contexts(
        initial_freeze=initial_freeze,
        freeze=freeze,
        initial_model_inputs=initial_inputs,
        expected_initial_freeze_hash=EXPECTED_INITIAL_FREEZE_HASH,
    )
    plan = build_rebuttal_runtime_plan(
        freeze=freeze,
        contexts=contexts,
        runtime_preflight=runtime_preflight,
        selection_authority=selection,
    )
    if len(plan) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeAuthorizationError(
            "production Rebuttal runtime plan is not exactly three calls"
        )
    ceiling = _decimal(
        cost_preflight.get("total_rebuttal_runtime_cost_upper_bound_usd"),
        field_name="total_rebuttal_runtime_cost_upper_bound_usd",
    )
    return {
        "git_context": git_context,
        "initial_freeze": initial_freeze,
        "freeze": freeze,
        "source": source,
        "runtime_preflight": runtime_preflight,
        "cost_preflight": cost_preflight,
        "selection": selection,
        "plan": plan,
        "source_hash": source_hash,
        "request_hash": request_hash,
        "cost_hash": cost_hash,
        "selection_hash": selection_hash,
        "ceiling": ceiling,
    }


def _dry_artifact(bound: Mapping[str, Any]) -> dict[str, Any]:
    runtime_preflight = bound["runtime_preflight"]
    cost_preflight = bound["cost_preflight"]
    plan = bound["plan"]
    plan_rows = [
        {
            "dispatch_index": item.dispatch_index,
            "candidate_id": item.candidate_id,
            "context_hash": item.context_hash,
            "request_hash": item.request.request_hash,
            "request_body_utf8_bytes": item.request_body_utf8_bytes,
            "model": item.request.request_payload["model"],
            "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
            "max_output_tokens": item.request.request_payload["max_output_tokens"],
            "required_unknown_refs": list(item.required_unknown_refs),
        }
        for item in plan
    ]
    artifact: dict[str, Any] = {
        "artifact_version": RUNNER_DRY_VERSION,
        "status": "READY_FOR_EXPLICIT_OWNER_B4_REBUTTAL_PRODUCTION_AUTHORIZATION",
        "runtime_version": REBUTTAL_RUNTIME_VERSION,
        "code_commit_sha": bound["git_context"]["code_commit_sha"],
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": bound["selection_hash"],
        "selected_candidate": dict(EXPECTED_SELECTED),
        "source_request_preflight_artifact_hash": bound["source_hash"],
        "source_request_manifest_hash": EXPECTED_SOURCE_REQUEST_MANIFEST,
        "runtime_request_preflight_artifact_hash": bound["request_hash"],
        "runtime_request_manifest_hash": runtime_preflight["request_manifest_hash"],
        "runtime_cost_preflight_artifact_hash": bound["cost_hash"],
        "cost_ceiling_usd": str(bound["ceiling"]),
        "pricing_version": cost_preflight["pricing_version"],
        "pricing_hash": cost_preflight["pricing_hash"],
        "candidate_order": list(runtime_preflight["candidate_order"]),
        "planned_paid_calls_max": EXPECTED_PRODUCTION_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": False,
        "paid_authorization_artifact_version": PAID_AUTHORIZATION_VERSION,
        "paid_call_receipt_version": PAID_RECEIPT_VERSION,
        "freeze_artifact_version": REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
        "consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "stop_on_first_failed_provider_validation_or_cost_receipt": True,
        "unknown_dispatch_fail_closed": True,
        "plan": plan_rows,
        "plan_manifest_hash": canonical_sha256({"plan": plan_rows}),
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _validate_paid_approval(
    args: argparse.Namespace,
    *,
    bound: Mapping[str, Any],
    dry: Mapping[str, Any],
) -> Decimal:
    exact = (
        (args.approve_initial_freeze_hash, EXPECTED_INITIAL_FREEZE_HASH, "Initial freeze"),
        (args.approve_selection_hash, bound["selection_hash"], "selection"),
        (args.approve_source_preflight_hash, bound["source_hash"], "source preflight"),
        (
            args.approve_source_request_manifest_hash,
            EXPECTED_SOURCE_REQUEST_MANIFEST,
            "source request manifest",
        ),
        (
            args.approve_runtime_request_preflight_hash,
            bound["request_hash"],
            "runtime request preflight",
        ),
        (
            args.approve_runtime_request_manifest_hash,
            bound["runtime_preflight"]["request_manifest_hash"],
            "runtime request manifest",
        ),
        (
            args.approve_runtime_cost_artifact_hash,
            bound["cost_hash"],
            "runtime cost artifact",
        ),
        (
            args.approve_runner_dry_artifact_hash,
            dry["artifact_hash"],
            "runner dry artifact",
        ),
    )
    for observed, expected, label in exact:
        if observed != expected:
            raise RebuttalRuntimeAuthorizationError(
                f"paid production Rebuttal requires exact {label} approval"
            )
    ceiling = _decimal(args.approve_max_usd, field_name="approve_max_usd")
    if ceiling != bound["ceiling"]:
        raise RebuttalRuntimeAuthorizationError(
            "paid production Rebuttal requires exact cost-ceiling approval"
        )
    return ceiling


def _authorization_artifact(
    *,
    run_id: str,
    created_at: str,
    bound: Mapping[str, Any],
    dry: Mapping[str, Any],
    ceiling: Decimal,
    owner_id: str,
    owner_at: str,
    receipt_journal: Path,
) -> dict[str, Any]:
    runtime_preflight = bound["runtime_preflight"]
    cost_preflight = bound["cost_preflight"]
    artifact: dict[str, Any] = {
        "artifact_version": PAID_AUTHORIZATION_VERSION,
        "run_class": "B4_PRODUCTION_REBUTTAL_PAID_PRE_DISPATCH_AUTHORIZATION",
        "status": "AUTHORIZED_UNCONSUMED_BEFORE_DISPATCH",
        "consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "scope_exhausts_after_dispatch_attempts": EXPECTED_PRODUCTION_CALLS,
        "run_id": run_id,
        "created_at_utc": created_at,
        "code_commit_sha": bound["git_context"]["code_commit_sha"],
        "git_worktree_clean": bound["git_context"]["git_worktree_clean"],
        "runtime_version": REBUTTAL_RUNTIME_VERSION,
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": bound["selection_hash"],
        "selected_candidate": dict(EXPECTED_SELECTED),
        "source_request_preflight_artifact_hash": bound["source_hash"],
        "source_request_manifest_hash": EXPECTED_SOURCE_REQUEST_MANIFEST,
        "runtime_request_preflight_artifact_hash": bound["request_hash"],
        "runtime_request_manifest_hash": runtime_preflight["request_manifest_hash"],
        "runtime_cost_preflight_artifact_hash": bound["cost_hash"],
        "runner_dry_artifact_hash": dry["artifact_hash"],
        "runner_dry_plan_manifest_hash": dry["plan_manifest_hash"],
        "approved_cost_ceiling_usd": str(ceiling),
        "pricing_version": cost_preflight["pricing_version"],
        "pricing_hash": cost_preflight["pricing_hash"],
        "candidate_order": list(runtime_preflight["candidate_order"]),
        "planned_paid_calls_max": EXPECTED_PRODUCTION_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": False,
        "owner_approval": {
            "owner_approval_id": owner_id,
            "owner_approval_at_utc": owner_at,
            "approved_initial_council_freeze_hash": EXPECTED_INITIAL_FREEZE_HASH,
            "approved_selection_hash": bound["selection_hash"],
            "approved_source_preflight_hash": bound["source_hash"],
            "approved_source_request_manifest_hash": EXPECTED_SOURCE_REQUEST_MANIFEST,
            "approved_runtime_request_preflight_hash": bound["request_hash"],
            "approved_runtime_request_manifest_hash": runtime_preflight[
                "request_manifest_hash"
            ],
            "approved_runtime_cost_artifact_hash": bound["cost_hash"],
            "approved_runner_dry_artifact_hash": dry["artifact_hash"],
            "approved_cost_ceiling_usd": str(ceiling),
        },
        "receipt_contract_version": PAID_RECEIPT_VERSION,
        "receipt_journal_path": str(receipt_journal),
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
        "production_rebuttal_authorized": True,
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


def _receipt(
    *,
    run_id: str,
    item: Any,
    started_at: str,
    finished_at: str,
    authorization: Mapping[str, Any],
    bound: Mapping[str, Any],
    ceiling: Decimal,
    owner_id: str,
    owner_at: str,
    run: Any,
    tracker: DispatchTrackingTransport,
) -> dict[str, Any]:
    provider_received = tracker.provider_responses == 1 and run.model_calls == 1
    if not provider_received:
        result = "BLOCKED_UNKNOWN_PROVIDER_DISPATCH"
    elif run.cost_receipt_status != "COMPLETE":
        result = "BLOCKED_INCOMPLETE_COST_RECEIPT"
    elif run.validation_status != "PASS":
        result = "BLOCKED_REBUTTAL_VALIDATION_FAILED"
    else:
        result = "PASS"
    processed_hash = None
    if isinstance(run.processed_record, Mapping):
        processed_hash = run.processed_record.get("record_hash")
    receipt: dict[str, Any] = {
        "receipt_version": PAID_RECEIPT_VERSION,
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": started_at,
        "dispatch_finished_at_utc": finished_at,
        "code_commit_sha": bound["git_context"]["code_commit_sha"],
        "stage": "REBUTTAL",
        "run_class": "PRODUCTION_REBUTTAL",
        "candidate_id": item.candidate_id,
        "context_hash": item.context_hash,
        "requested_model": item.request.request_payload["model"],
        "effective_model": run.effective_model,
        "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
        "prompt_version": item.request.prompt_version,
        "prompt_hash": item.request.prompt_hash,
        "schema_version": item.request.schema_version,
        "input_hash": item.request.input_hash,
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "max_output_tokens": item.request.request_payload["max_output_tokens"],
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": bound["selection_hash"],
        "source_request_preflight_artifact_hash": bound["source_hash"],
        "source_request_manifest_hash": EXPECTED_SOURCE_REQUEST_MANIFEST,
        "runtime_request_preflight_artifact_hash": bound["request_hash"],
        "runtime_request_manifest_hash": bound["runtime_preflight"][
            "request_manifest_hash"
        ],
        "runtime_cost_preflight_artifact_hash": bound["cost_hash"],
        "paid_authorization_artifact_hash": authorization["artifact_hash"],
        "approved_cost_ceiling_usd": str(ceiling),
        "owner_approval_id": owner_id,
        "owner_approval_at_utc": owner_at,
        "pricing_version": bound["cost_preflight"]["pricing_version"],
        "pricing_hash": bound["cost_preflight"]["pricing_hash"],
        "dispatch_attempted": tracker.dispatch_attempts == 1,
        "provider_response_received": provider_received,
        "response_id": run.response_id,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "latency_ms": run.latency_ms,
        "actual_cost_usd": None
        if run.actual_cost_usd is None
        else str(run.actual_cost_usd),
        "cost_receipt_status": run.cost_receipt_status,
        "validation_status": run.validation_status,
        "validation_error": run.validation_error,
        "call_result": result,
        "output_hash": run.output_hash,
        "structured_output": None
        if run.structured_output is None
        else dict(run.structured_output),
        "structured_output_hash": run.structured_output_hash,
        "processed_record_hash": processed_hash,
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _blocked_artifact(
    *,
    status: str,
    reason: str,
    run_id: str,
    bound: Mapping[str, Any],
    authorization_hash: str,
    ceiling: Decimal,
    dispatch_attempts: int,
    model_calls: int,
    known_cost: Decimal,
    receipt_hashes: list[str],
    receipt_journal: Path,
    successful_records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
        "runtime_version": REBUTTAL_RUNTIME_VERSION,
        "run_class": "B4_REAL_SELECTED_MODEL_REBUTTAL_COUNCIL",
        "status": status,
        "blocked_reason": reason,
        "run_id": run_id,
        "code_commit_sha": bound["git_context"]["code_commit_sha"],
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": bound["selection_hash"],
        "selected_candidate": dict(EXPECTED_SELECTED),
        "source_request_preflight_artifact_hash": bound["source_hash"],
        "runtime_request_preflight_artifact_hash": bound["request_hash"],
        "runtime_request_manifest_hash": bound["runtime_preflight"][
            "request_manifest_hash"
        ],
        "runtime_cost_preflight_artifact_hash": bound["cost_hash"],
        "paid_authorization_artifact_hash": authorization_hash,
        "approved_cost_ceiling_usd": str(ceiling),
        "dispatch_attempts": dispatch_attempts,
        "model_calls": model_calls,
        "known_cost_usd": str(known_cost),
        "actual_cost_usd": None,
        "cost_receipt_status": "INCOMPLETE"
        if status == "BLOCKED_INCOMPLETE_COST_RECEIPT"
        else "PARTIAL",
        "paid_call_receipt_hashes": receipt_hashes,
        "receipt_manifest_hash": canonical_sha256(
            {"receipt_hashes": receipt_hashes}
        ),
        "receipt_journal_path": str(receipt_journal),
        "successful_processed_records": [dict(row) for row in successful_records],
        "automatic_repair_calls": 0,
        "rebuttal_freeze_barrier": False,
        "production_rebuttal_authorization_consumed": dispatch_attempts > 0,
        "judge_model_calls": 0,
        "judge_authorized": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def main() -> int:
    args = _args()
    try:
        bound = _load_bound_runtime(args)
        dry = _dry_artifact(bound)

        if not args.execute_paid_rebuttal:
            _write(args.dry_output, dry)
            print(json.dumps(dry, ensure_ascii=False, indent=2))
            return 0

        ceiling = _validate_paid_approval(args, bound=bound, dry=dry)
        owner_id, owner_at = _owner_record(
            args.owner_approval_id,
            args.owner_approval_at_utc,
        )
        _git_context(expected_head=bound["git_context"]["code_commit_sha"])
        _require_fresh_paid_paths(
            args.paid_output,
            args.authorization_output,
            args.receipt_journal,
        )

        run_started = _utc_now_text()
        run_id = _run_id(run_started, bound["request_hash"], bound["cost_hash"])
        authorization = _authorization_artifact(
            run_id=run_id,
            created_at=run_started,
            bound=bound,
            dry=dry,
            ceiling=ceiling,
            owner_id=owner_id,
            owner_at=owner_at,
            receipt_journal=args.receipt_journal,
        )
        _write_durable_new(args.authorization_output, authorization)

        from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

        api_key = load_openai_api_key()
        pricing = load_initial_runtime_pricing()
        dispatch_attempts = 0
        model_calls = 0
        cumulative_cost = Decimal("0")
        receipt_hashes: list[str] = []
        processed_records: list[Mapping[str, Any]] = []

        variants = {
            row["candidate"]: row
            for row in bound["runtime_preflight"]["selected_request_variants"]
        }
        if len(variants) != EXPECTED_PRODUCTION_CALLS:
            raise RebuttalRuntimeAuthorizationError(
                "frozen production request lookup count mismatch"
            )

        for item in bound["plan"]:
            if dispatch_attempts >= EXPECTED_PRODUCTION_CALLS:
                raise RebuttalRuntimeAuthorizationError(
                    "production Rebuttal dispatch ceiling exhausted"
                )
            frozen = variants.get(item.candidate_id)
            if not isinstance(frozen, Mapping):
                raise RebuttalRuntimeAuthorizationError(
                    "production frozen request missing"
                )
            if frozen.get("request_hash") != item.request.request_hash:
                raise RebuttalRuntimeAuthorizationError(
                    "production request hash differs from approved preflight"
                )
            if frozen.get("request_body_utf8_bytes") != item.request_body_utf8_bytes:
                raise RebuttalRuntimeAuthorizationError(
                    "production request bytes differ from approved preflight"
                )
            if item.request.request_payload.get("model") != EXPECTED_SELECTED["model"]:
                raise RebuttalRuntimeAuthorizationError(
                    "production request model differs from frozen R3"
                )
            if item.request.request_payload.get("reasoning", {}).get(
                "effort"
            ) != EXPECTED_SELECTED["reasoning_effort"]:
                raise RebuttalRuntimeAuthorizationError(
                    "production request reasoning differs from frozen R3"
                )
            if item.request.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
                raise RebuttalRuntimeAuthorizationError(
                    "production request output-token cap drift"
                )

            print(
                f"[B4 REBUTTAL PRODUCTION] {item.candidate_id} "
                f"{EXPECTED_SELECTED['model']}/{EXPECTED_SELECTED['reasoning_effort']}",
                file=sys.stderr,
                flush=True,
            )
            started_at = _utc_now_text()
            tracker = DispatchTrackingTransport(StdlibResponsesTransport())
            run = execute_rebuttal_runtime_item_once(
                item,
                initial_freeze=bound["initial_freeze"],
                api_key=api_key,
                transport=tracker,
                pricing=pricing,
                frozen_at=datetime.now(UTC),
            )
            finished_at = _utc_now_text()
            if tracker.dispatch_attempts != 1:
                raise RebuttalRuntimeAuthorizationError(
                    "each production Rebuttal item must attempt exactly one provider dispatch"
                )
            dispatch_attempts += 1
            model_calls += run.model_calls

            receipt = _receipt(
                run_id=run_id,
                item=item,
                started_at=started_at,
                finished_at=finished_at,
                authorization=authorization,
                bound=bound,
                ceiling=ceiling,
                owner_id=owner_id,
                owner_at=owner_at,
                run=run,
                tracker=tracker,
            )
            _append_receipt(args.receipt_journal, receipt)
            receipt_hashes.append(receipt["receipt_hash"])

            if run.actual_cost_usd is not None and run.cost_receipt_status == "COMPLETE":
                cumulative_cost += run.actual_cost_usd

            if tracker.provider_responses != 1 or run.model_calls != 1:
                artifact = _blocked_artifact(
                    status="BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
                    reason=run.validation_error or "provider response unavailable",
                    run_id=run_id,
                    bound=bound,
                    authorization_hash=authorization["artifact_hash"],
                    ceiling=ceiling,
                    dispatch_attempts=dispatch_attempts,
                    model_calls=model_calls,
                    known_cost=cumulative_cost,
                    receipt_hashes=receipt_hashes,
                    receipt_journal=args.receipt_journal,
                    successful_records=processed_records,
                )
                _write(args.paid_output, artifact)
                print(json.dumps(artifact, ensure_ascii=False, indent=2))
                return 2

            if run.cost_receipt_status != "COMPLETE" or run.actual_cost_usd is None:
                artifact = _blocked_artifact(
                    status="BLOCKED_INCOMPLETE_COST_RECEIPT",
                    reason=run.validation_error or "cost receipt incomplete",
                    run_id=run_id,
                    bound=bound,
                    authorization_hash=authorization["artifact_hash"],
                    ceiling=ceiling,
                    dispatch_attempts=dispatch_attempts,
                    model_calls=model_calls,
                    known_cost=cumulative_cost,
                    receipt_hashes=receipt_hashes,
                    receipt_journal=args.receipt_journal,
                    successful_records=processed_records,
                )
                _write(args.paid_output, artifact)
                print(json.dumps(artifact, ensure_ascii=False, indent=2))
                return 2

            if cumulative_cost > ceiling:
                artifact = _blocked_artifact(
                    status="BLOCKED_APPROVED_COST_CEILING_EXCEEDED",
                    reason="known actual cost exceeded approved ceiling",
                    run_id=run_id,
                    bound=bound,
                    authorization_hash=authorization["artifact_hash"],
                    ceiling=ceiling,
                    dispatch_attempts=dispatch_attempts,
                    model_calls=model_calls,
                    known_cost=cumulative_cost,
                    receipt_hashes=receipt_hashes,
                    receipt_journal=args.receipt_journal,
                    successful_records=processed_records,
                )
                _write(args.paid_output, artifact)
                print(json.dumps(artifact, ensure_ascii=False, indent=2))
                return 2

            if run.validation_status != "PASS" or run.processed_record is None:
                artifact = _blocked_artifact(
                    status="BLOCKED_REBUTTAL_VALIDATION_FAILED",
                    reason=run.validation_error or "deterministic Rebuttal validation failed",
                    run_id=run_id,
                    bound=bound,
                    authorization_hash=authorization["artifact_hash"],
                    ceiling=ceiling,
                    dispatch_attempts=dispatch_attempts,
                    model_calls=model_calls,
                    known_cost=cumulative_cost,
                    receipt_hashes=receipt_hashes,
                    receipt_journal=args.receipt_journal,
                    successful_records=processed_records,
                )
                _write(args.paid_output, artifact)
                print(json.dumps(artifact, ensure_ascii=False, indent=2))
                return 2

            processed_records.append(run.processed_record)

        if dispatch_attempts != EXPECTED_PRODUCTION_CALLS:
            raise RebuttalRuntimeAuthorizationError(
                "production Rebuttal dispatch count is not exactly three"
            )
        if model_calls != EXPECTED_PRODUCTION_CALLS:
            raise RebuttalRuntimeAuthorizationError(
                "production Rebuttal model-response count is not exactly three"
            )
        if len(receipt_hashes) != EXPECTED_PRODUCTION_CALLS:
            raise RebuttalRuntimeAuthorizationError(
                "production Rebuttal receipt count is not exactly three"
            )
        if len(processed_records) != EXPECTED_PRODUCTION_CALLS:
            raise RebuttalRuntimeAuthorizationError(
                "production Rebuttal validated record count is not exactly three"
            )

        receipt_manifest_hash = canonical_sha256(
            {"receipt_hashes": receipt_hashes}
        )
        freeze_artifact = build_rebuttal_council_freeze_artifact(
            processed_records=tuple(processed_records),
            freeze=bound["freeze"],
            runtime_preflight=bound["runtime_preflight"],
            cost_preflight=bound["cost_preflight"],
            selection_authority=bound["selection"],
            run_id=run_id,
            paid_authorization_artifact_hash=authorization["artifact_hash"],
            receipt_manifest_hash=receipt_manifest_hash,
            actual_cost_usd_total=cumulative_cost,
        )
        freeze_artifact["paid_call_receipt_hashes"] = receipt_hashes
        freeze_artifact["receipt_journal_path"] = str(args.receipt_journal)
        freeze_artifact["approved_cost_ceiling_usd"] = str(ceiling)
        freeze_artifact["pricing_version"] = bound["cost_preflight"]["pricing_version"]
        freeze_artifact["pricing_hash"] = bound["cost_preflight"]["pricing_hash"]
        freeze_artifact["production_rebuttal_authorization_consumed"] = True
        freeze_artifact.pop("artifact_hash", None)
        freeze_artifact["artifact_hash"] = canonical_sha256(freeze_artifact)
        verify_rebuttal_council_freeze_artifact(freeze_artifact)
        _write(args.paid_output, freeze_artifact)
        print(
            json.dumps(
                {
                    "artifact_version": freeze_artifact["artifact_version"],
                    "status": freeze_artifact["status"],
                    "run_id": freeze_artifact["run_id"],
                    "candidate_order": freeze_artifact["candidate_order"],
                    "rebuttal_bundle_count": freeze_artifact["rebuttal_bundle_count"],
                    "rebuttal_bundle_hashes": freeze_artifact["rebuttal_bundle_hashes"],
                    "research_reopen_required_candidates": freeze_artifact[
                        "research_reopen_required_candidates"
                    ],
                    "dispatch_attempts": freeze_artifact["dispatch_attempts"],
                    "model_calls": freeze_artifact["model_calls"],
                    "actual_cost_usd": freeze_artifact["actual_cost_usd"],
                    "approved_cost_ceiling_usd": freeze_artifact[
                        "approved_cost_ceiling_usd"
                    ],
                    "cost_receipt_status": freeze_artifact["cost_receipt_status"],
                    "paid_authorization_artifact_hash": freeze_artifact[
                        "paid_authorization_artifact_hash"
                    ],
                    "receipt_manifest_hash": freeze_artifact[
                        "receipt_manifest_hash"
                    ],
                    "rebuttal_freeze_barrier": True,
                    "production_rebuttal_authorization_consumed": True,
                    "judge_authorized": False,
                    "rerun_authorized": False,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                    "artifact_hash": freeze_artifact["artifact_hash"],
                    "output_path": str(args.paid_output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    except Exception as exc:
        print(
            f"B4 production Rebuttal failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
