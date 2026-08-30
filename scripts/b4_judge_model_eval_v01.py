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

from aic.council.judge_eval_preflight import (
    EXPECTED_JUDGE_ENTRY_HASH,
    EXPECTED_JUDGE_EVAL_CASE_IDS,
    EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
    EXPECTED_REBUTTAL_FREEZE_HASH,
    JUDGE_EVAL_COST_PREFLIGHT_STATUS,
    JUDGE_EVAL_VERSION,
    build_judge_eval_cases,
    verify_judge_eval_cost_preflight,
    verify_judge_eval_request_preflight,
)
from aic.council.judge_eval_runtime import (
    JUDGE_EVAL_RUNTIME_VERSION,
    build_judge_eval_case_request,
    dry_run_manifest,
    execute_judge_eval_case_once,
)
from aic.council.model_policy import (
    JUDGE_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelStage,
    StageModelEvalResult,
    select_stage_model_from_eval,
)
from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B4_JUDGE_MODEL_EVAL_ARTIFACT_v0_1"
RUNNER_DRY_VERSION = "B4_JUDGE_MODEL_EVAL_PAID_RUNNER_DRY_v0_1"
PAID_AUTHORIZATION_ARTIFACT_VERSION = (
    "B4_JUDGE_MODEL_EVAL_PAID_AUTHORIZATION_v0_1"
)
PAID_CALL_RECEIPT_VERSION = "B4_JUDGE_MODEL_EVAL_PAID_CALL_RECEIPT_v0_1"
DEFAULT_REQUEST_PREFLIGHT = Path(
    ".aic-runtime/b4_judge_model_eval_request_preflight_v0_1.json"
)
DEFAULT_COST_PREFLIGHT = Path(
    ".aic-runtime/b4_judge_model_eval_cost_preflight_v0_1.json"
)
DEFAULT_RUNNER_DRY_OUTPUT = Path(
    ".aic-runtime/b4_judge_model_eval_paid_runner_dry_v0_1.json"
)
DEFAULT_PAID_OUTPUT = Path(".aic-runtime/b4_judge_model_eval_v0_1.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(
    ".aic-runtime/b4_judge_model_eval_paid_authorization_v0_1.json"
)
DEFAULT_RECEIPT_JOURNAL = Path(
    ".aic-runtime/b4_judge_model_eval_paid_receipts_v0_1.jsonl"
)
EXPECTED_MAX_OUTPUT_TOKENS = 8192


class JudgeEvalAuthorizationError(ValueError):
    pass


class DispatchTrackingTransport:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.dispatch_attempts = 0
        self.provider_responses = 0

    def post(
        self,
        *,
        payload: Mapping[str, Any],
        api_key: str,
    ) -> Mapping[str, Any]:
        self.dispatch_attempts += 1
        result = self.delegate.post(payload=payload, api_key=api_key)
        self.provider_responses += 1
        return result


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or execute frozen B4 Judge model evaluation. "
            "Paid dispatch requires exact owner approval."
        )
    )
    parser.add_argument(
        "--request-preflight", type=Path, default=DEFAULT_REQUEST_PREFLIGHT
    )
    parser.add_argument(
        "--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT
    )
    parser.add_argument(
        "--runner-dry-output", type=Path, default=DEFAULT_RUNNER_DRY_OUTPUT
    )
    parser.add_argument("--paid-output", type=Path, default=DEFAULT_PAID_OUTPUT)
    parser.add_argument(
        "--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION_OUTPUT
    )
    parser.add_argument(
        "--receipt-journal", type=Path, default=DEFAULT_RECEIPT_JOURNAL
    )
    parser.add_argument("--execute-paid-eval", action="store_true")
    parser.add_argument("--approve-request-preflight-hash")
    parser.add_argument("--approve-request-manifest-hash")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-runner-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser.parse_args()


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JudgeEvalAuthorizationError(
            f"unable to read {label}: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise JudgeEvalAuthorizationError(f"{label} root must be an object")
    return value


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise JudgeEvalAuthorizationError(f"{field_name} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise JudgeEvalAuthorizationError(f"{field_name} invalid decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise JudgeEvalAuthorizationError(f"{field_name} invalid")
    return parsed


def _verify_authorities(
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
) -> tuple[str, str, Decimal]:
    request_hash = verify_judge_eval_request_preflight(request_preflight)
    cost_hash = verify_judge_eval_cost_preflight(cost_preflight)
    if cost_preflight.get("status") != JUDGE_EVAL_COST_PREFLIGHT_STATUS:
        raise JudgeEvalAuthorizationError(
            "Judge eval cost preflight is not approval-ready"
        )
    if cost_preflight.get("eval_request_preflight_artifact_hash") != request_hash:
        raise JudgeEvalAuthorizationError(
            "cost preflight does not bind request preflight"
        )
    if cost_preflight.get("eval_request_manifest_hash") != request_preflight.get(
        "request_manifest_hash"
    ):
        raise JudgeEvalAuthorizationError("cost/request manifest binding mismatch")
    if request_preflight.get("judge_entry_preflight_artifact_hash") != EXPECTED_JUDGE_ENTRY_HASH:
        raise JudgeEvalAuthorizationError("Judge entry authority binding drift")
    if cost_preflight.get("judge_entry_preflight_artifact_hash") != EXPECTED_JUDGE_ENTRY_HASH:
        raise JudgeEvalAuthorizationError("Judge eval cost entry binding drift")
    if request_preflight.get("rebuttal_council_freeze_artifact_hash") != EXPECTED_REBUTTAL_FREEZE_HASH:
        raise JudgeEvalAuthorizationError("Rebuttal freeze binding drift")
    if cost_preflight.get("rebuttal_council_freeze_artifact_hash") != EXPECTED_REBUTTAL_FREEZE_HASH:
        raise JudgeEvalAuthorizationError("Judge eval cost Rebuttal binding drift")
    if request_preflight.get("planned_paid_calls_max") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeEvalAuthorizationError("Judge eval call ceiling drift")
    if cost_preflight.get("planned_paid_calls_max") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeEvalAuthorizationError("Judge eval cost call ceiling drift")
    if request_preflight.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise JudgeEvalAuthorizationError("Judge eval output-token bound drift")
    if cost_preflight.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise JudgeEvalAuthorizationError("Judge eval cost output-token bound drift")
    if tuple(request_preflight.get("case_ids", ())) != EXPECTED_JUDGE_EVAL_CASE_IDS:
        raise JudgeEvalAuthorizationError("Judge eval case surface drift")
    if tuple(request_preflight.get("candidate_keys", ())) != tuple(
        candidate.candidate_key for candidate in JUDGE_MODEL_LADDER
    ):
        raise JudgeEvalAuthorizationError("Judge model ladder drift")
    for obj, label in (
        (request_preflight, "request"),
        (cost_preflight, "cost"),
    ):
        if obj.get("automatic_repair_calls_authorized") is not False:
            raise JudgeEvalAuthorizationError(
                f"{label} unexpectedly authorizes automatic repair"
            )
        for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
            if obj.get(field) != 0:
                raise JudgeEvalAuthorizationError(
                    f"{label} preflight {field} must be zero"
                )
        if obj.get("live_money") != "PROHIBITED":
            raise JudgeEvalAuthorizationError(
                f"{label} live-money invariant drift"
            )
        for field in (
            "paid_eval_authorized",
            "production_judge_authorized",
            "rerun_authorized",
        ):
            if obj.get(field) is not False:
                raise JudgeEvalAuthorizationError(
                    f"{label} unexpectedly sets {field}"
                )
    ceiling = _decimal(
        cost_preflight.get("total_judge_eval_cost_upper_bound_usd"),
        field_name="total_judge_eval_cost_upper_bound_usd",
    )
    return request_hash, cost_hash, ceiling


def _dry_run(
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
) -> dict[str, Any]:
    request_hash, cost_hash, ceiling = _verify_authorities(
        request_preflight, cost_preflight
    )
    manifest = dry_run_manifest()
    if manifest.get("request_count") != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeEvalAuthorizationError(
            "dry-run does not contain exactly 21 requests"
        )
    expected = request_preflight.get("request_variants")
    observed = manifest.get("requests")
    if not isinstance(expected, list) or len(expected) != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
        raise JudgeEvalAuthorizationError("request-preflight variants missing")
    if not isinstance(observed, list) or len(observed) != len(expected):
        raise JudgeEvalAuthorizationError("dry-run request surface missing")
    for frozen, rebuilt in zip(expected, observed, strict=True):
        if not isinstance(frozen, Mapping) or not isinstance(rebuilt, Mapping):
            raise JudgeEvalAuthorizationError("dry-run request record malformed")
        for field in (
            "candidate_key",
            "model",
            "reasoning_effort",
            "case_id",
            "request_hash",
            "request_body_utf8_bytes",
            "max_output_tokens",
        ):
            if frozen.get(field) != rebuilt.get(field):
                raise JudgeEvalAuthorizationError(
                    f"dry-run request differs from preflight: {field}"
                )
    rebuilt_manifest_hash = canonical_sha256(
        {
            "variants": [
                {
                    "candidate_key": row["candidate_key"],
                    "case_id": row["case_id"],
                    "request_hash": row["request_hash"],
                    "request_body_utf8_bytes": row["request_body_utf8_bytes"],
                }
                for row in observed
            ]
        }
    )
    if rebuilt_manifest_hash != request_preflight.get("request_manifest_hash"):
        raise JudgeEvalAuthorizationError("dry-run request manifest hash drift")
    return {
        "manifest": manifest,
        "request_preflight_hash": request_hash,
        "request_manifest_hash": rebuilt_manifest_hash,
        "cost_preflight_hash": cost_hash,
        "cost_ceiling_usd": ceiling,
    }


def _runner_dry_artifact(
    *,
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    dry: Mapping[str, Any],
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": RUNNER_DRY_VERSION,
        "status": "READY_FOR_EXPLICIT_OWNER_B4_JUDGE_MODEL_EVAL_AUTHORIZATION",
        "code_commit_sha": request_preflight["code_commit_sha"],
        "eval_version": JUDGE_EVAL_VERSION,
        "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "request_preflight_artifact_hash": dry["request_preflight_hash"],
        "request_manifest_hash": dry["request_manifest_hash"],
        "cost_preflight_artifact_hash": dry["cost_preflight_hash"],
        "cost_ceiling_usd": str(dry["cost_ceiling_usd"]),
        "dry_run_manifest_hash": dry["manifest"]["manifest_hash"],
        "candidate_keys": [item.candidate_key for item in JUDGE_MODEL_LADDER],
        "case_ids": list(EXPECTED_JUDGE_EVAL_CASE_IDS),
        "planned_paid_calls_max": EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "paid_authorization_artifact_version": PAID_AUTHORIZATION_ARTIFACT_VERSION,
        "paid_call_receipt_version": PAID_CALL_RECEIPT_VERSION,
        "consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "unknown_dispatch_fail_closed": True,
        "semantic_fail_continues_ladder": True,
        "stop_on_incomplete_cost_receipt": True,
        "automatic_repair_calls_authorized": False,
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


def validate_paid_execution_authorization(
    *,
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    approve_request_preflight_hash: str | None,
    approve_request_manifest_hash: str | None,
    approve_cost_artifact_hash: str | None,
    approve_runner_dry_artifact_hash: str | None,
    approve_max_usd: str | None,
) -> Decimal:
    request_hash, cost_hash, ceiling = _verify_authorities(
        request_preflight, cost_preflight
    )
    expected_runner = _runner_dry_artifact(
        request_preflight=request_preflight,
        cost_preflight=cost_preflight,
        dry=_dry_run(request_preflight, cost_preflight),
    )
    if runner_dry != expected_runner:
        raise JudgeEvalAuthorizationError("runner dry artifact differs from deterministic rebuild")
    if approve_request_preflight_hash != request_hash:
        raise JudgeEvalAuthorizationError(
            "paid Judge eval requires exact request-preflight approval"
        )
    if approve_request_manifest_hash != request_preflight.get("request_manifest_hash"):
        raise JudgeEvalAuthorizationError(
            "paid Judge eval requires exact request-manifest approval"
        )
    if approve_cost_artifact_hash != cost_hash:
        raise JudgeEvalAuthorizationError(
            "paid Judge eval requires exact cost-artifact approval"
        )
    if approve_runner_dry_artifact_hash != runner_dry.get("artifact_hash"):
        raise JudgeEvalAuthorizationError(
            "paid Judge eval requires exact runner-dry approval"
        )
    if _decimal(approve_max_usd, field_name="approve_max_usd") != ceiling:
        raise JudgeEvalAuthorizationError(
            "paid Judge eval requires exact cost-ceiling approval"
        )
    return ceiling


def _canonical_owner_approval_time(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JudgeEvalAuthorizationError("owner approval timestamp is required")
    text = value.strip()
    parse_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise JudgeEvalAuthorizationError(
            "owner approval timestamp must be RFC3339"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise JudgeEvalAuthorizationError("owner approval timestamp must be UTC")
    if parsed > datetime.now(UTC):
        raise JudgeEvalAuthorizationError("owner approval timestamp cannot be future")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def validate_owner_approval_record(
    owner_approval_id: str | None,
    owner_approval_at_utc: str | None,
) -> tuple[str, str]:
    if not isinstance(owner_approval_id, str) or not owner_approval_id.strip():
        raise JudgeEvalAuthorizationError("owner approval ID is required")
    approval_id = owner_approval_id.strip()
    if len(approval_id) > 160 or any(ch.isspace() for ch in approval_id):
        raise JudgeEvalAuthorizationError(
            "owner approval ID must be <=160 chars without whitespace"
        )
    return approval_id, _canonical_owner_approval_time(owner_approval_at_utc)


def _git_execution_context(expected_head: str) -> dict[str, Any]:
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
        raise JudgeEvalAuthorizationError(
            "unable to prove local git execution context"
        ) from exc
    if head != expected_head:
        raise JudgeEvalAuthorizationError(
            "local HEAD differs from approved Judge eval HEAD"
        )
    if status.strip():
        raise JudgeEvalAuthorizationError(
            "paid Judge eval requires clean git worktree"
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
            raise JudgeEvalAuthorizationError(
                f"paid evidence path already exists; refusing overwrite: {path}"
            )


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _run_id(started: str, request_hash: str, cost_hash: str) -> str:
    suffix = canonical_sha256(
        {
            "started_at_utc": started,
            "request_preflight_artifact_hash": request_hash,
            "cost_preflight_artifact_hash": cost_hash,
        }
    )[:12]
    compact = started.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-JUDGE-EVAL-{compact}-{suffix}"


def _authorization_artifact(
    *,
    run_id: str,
    created_at: str,
    git_context: Mapping[str, Any],
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    ceiling: Decimal,
    owner_id: str,
    owner_at: str,
    receipt_journal: Path,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": PAID_AUTHORIZATION_ARTIFACT_VERSION,
        "run_class": "B4_JUDGE_MODEL_EVAL_PAID_PRE_DISPATCH_AUTHORIZATION",
        "status": "AUTHORIZED_UNCONSUMED_BEFORE_DISPATCH",
        "consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "created_at_utc": created_at,
        "code_commit_sha": git_context["code_commit_sha"],
        "git_worktree_clean": git_context["git_worktree_clean"],
        "eval_version": JUDGE_EVAL_VERSION,
        "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "request_preflight_artifact_hash": request_preflight["artifact_hash"],
        "request_manifest_hash": request_preflight["request_manifest_hash"],
        "cost_preflight_artifact_hash": cost_preflight["artifact_hash"],
        "runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "approved_cost_ceiling_usd": str(ceiling),
        "dry_run_manifest_hash": runner_dry["dry_run_manifest_hash"],
        "candidate_keys": [item.candidate_key for item in JUDGE_MODEL_LADDER],
        "case_ids": list(EXPECTED_JUDGE_EVAL_CASE_IDS),
        "planned_paid_calls_max": EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": False,
        "pricing_version": cost_preflight["pricing_version"],
        "pricing_hash": cost_preflight["pricing_hash"],
        "owner_approval": {
            "owner_approval_id": owner_id,
            "owner_approval_at_utc": owner_at,
            "approved_request_preflight_hash": request_preflight["artifact_hash"],
            "approved_request_manifest_hash": request_preflight["request_manifest_hash"],
            "approved_cost_artifact_hash": cost_preflight["artifact_hash"],
            "approved_runner_dry_artifact_hash": runner_dry["artifact_hash"],
            "approved_cost_ceiling_usd": str(ceiling),
        },
        "receipt_contract_version": PAID_CALL_RECEIPT_VERSION,
        "receipt_journal_path": str(receipt_journal),
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
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


def _receipt(
    *,
    run_id: str,
    dispatch_index: int,
    started_at: str,
    finished_at: str,
    authorization_hash: str,
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    ceiling: Decimal,
    owner_id: str,
    owner_at: str,
    code_commit_sha: str,
    candidate: Any,
    case: Any,
    request: Any,
    run: Any,
    tracker: DispatchTrackingTransport,
) -> dict[str, Any]:
    provider_received = tracker.provider_responses == 1 and run.model_calls == 1
    if not provider_received:
        case_result = "BLOCKED_UNKNOWN_PROVIDER_DISPATCH"
    elif run.passed:
        case_result = "PASS"
    else:
        case_result = "FAIL"
    receipt: dict[str, Any] = {
        "receipt_version": PAID_CALL_RECEIPT_VERSION,
        "run_id": run_id,
        "dispatch_index": dispatch_index,
        "dispatch_started_at_utc": started_at,
        "dispatch_finished_at_utc": finished_at,
        "code_commit_sha": code_commit_sha,
        "stage": "JUDGE",
        "run_class": "MODEL_EVAL",
        "candidate_key": candidate.candidate_key,
        "case_id": case.case_id,
        "case_name": case.name,
        "critical_safety": case.critical_safety,
        "requested_model": candidate.model,
        "effective_model": run.effective_model,
        "reasoning_effort": candidate.reasoning_effort,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "request_hash": request.request_hash,
        "request_body_utf8_bytes": len(
            json.dumps(
                request.request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ),
        "max_output_tokens": request.request_payload["max_output_tokens"],
        "judge_input_hash": case.judge_input_hash,
        "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "request_preflight_artifact_hash": request_preflight["artifact_hash"],
        "request_manifest_hash": request_preflight["request_manifest_hash"],
        "cost_preflight_artifact_hash": cost_preflight["artifact_hash"],
        "runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_hash,
        "approved_cost_ceiling_usd": str(ceiling),
        "owner_approval_id": owner_id,
        "owner_approval_at_utc": owner_at,
        "pricing_version": cost_preflight["pricing_version"],
        "pricing_hash": cost_preflight["pricing_hash"],
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
        "case_result": case_result,
        "findings": list(run.findings),
        "output_hash": run.output_hash,
        "structured_output": None
        if run.structured_output is None
        else dict(run.structured_output),
        "structured_output_hash": run.structured_output_hash,
        "result_hash": run.result_hash,
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _case_record(run: Any) -> dict[str, Any]:
    return {
        "case_id": run.case_id,
        "name": run.name,
        "critical_safety": run.critical_safety,
        "passed": run.passed,
        "findings": list(run.findings),
        "response_id": run.response_id,
        "requested_model": run.requested_model,
        "effective_model": run.effective_model,
        "model_calls": run.model_calls,
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "actual_cost_usd": None
        if run.actual_cost_usd is None
        else str(run.actual_cost_usd),
        "cost_receipt_status": run.cost_receipt_status,
        "output_hash": run.output_hash,
        "structured_output_hash": run.structured_output_hash,
        "result_hash": run.result_hash,
    }


def _candidate_record(
    candidate: Any,
    runs: tuple[Any, ...],
) -> tuple[dict[str, Any], StageModelEvalResult]:
    passed = all(run.passed for run in runs)
    critical_failures = sum(
        1 for run in runs if run.critical_safety and not run.passed
    )
    cost = sum(
        (run.actual_cost_usd or Decimal("0") for run in runs),
        Decimal("0"),
    )
    latency = sum(run.latency_ms for run in runs)
    total_tokens = sum(
        (run.input_tokens or 0) + (run.output_tokens or 0) for run in runs
    )
    result = StageModelEvalResult(
        candidate_key=candidate.candidate_key,
        all_required_checks_passed=passed,
        critical_safety_failures=critical_failures,
        estimated_cost_usd=cost,
        latency_ms=latency,
        total_tokens=total_tokens,
    )
    record: dict[str, Any] = {
        "candidate_key": candidate.candidate_key,
        "model": candidate.model,
        "reasoning_effort": candidate.reasoning_effort,
        "ladder_position": candidate.ladder_position,
        "cases": [_case_record(run) for run in runs],
        "passed_cases": sum(1 for run in runs if run.passed),
        "required_cases": len(runs),
        "all_required_checks_passed": passed,
        "critical_safety_failures": critical_failures,
        "estimated_cost_usd": str(cost),
        "latency_ms": latency,
        "total_tokens": total_tokens,
    }
    record["record_hash"] = canonical_sha256(record)
    return record, result


def _blocked_artifact(
    *,
    status: str,
    reason: str,
    run_id: str,
    code_commit_sha: str,
    request_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    authorization_hash: str,
    ceiling: Decimal,
    dispatch_attempts: int,
    model_calls: int,
    known_cost: Decimal,
    receipt_hashes: list[str],
    receipt_journal: Path,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "run_class": "B4_JUDGE_MODEL_EVAL",
        "status": status,
        "blocked_reason": reason,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "eval_version": JUDGE_EVAL_VERSION,
        "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "request_preflight_artifact_hash": request_preflight["artifact_hash"],
        "request_manifest_hash": request_preflight["request_manifest_hash"],
        "cost_preflight_artifact_hash": cost_preflight["artifact_hash"],
        "runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_hash,
        "approved_cost_ceiling_usd": str(ceiling),
        "dispatch_attempts": dispatch_attempts,
        "model_calls": model_calls,
        "known_cost_usd": str(known_cost),
        "actual_cost_usd": None,
        "cost_receipt_status": "INCOMPLETE",
        "paid_call_receipt_hashes": receipt_hashes,
        "receipt_manifest_hash": canonical_sha256(
            {"receipt_hashes": receipt_hashes}
        ),
        "receipt_journal_path": str(receipt_journal),
        "judge_eval_authorization_consumed": dispatch_attempts > 0,
        "automatic_repair_calls": 0,
        "production_judge_authorized": False,
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
        request_preflight = _read_object(
            args.request_preflight, label="request preflight"
        )
        cost_preflight = _read_object(
            args.cost_preflight, label="cost preflight"
        )
        dry = _dry_run(request_preflight, cost_preflight)
        runner_dry = _runner_dry_artifact(
            request_preflight=request_preflight,
            cost_preflight=cost_preflight,
            dry=dry,
        )

        if not args.execute_paid_eval:
            _write(args.runner_dry_output, runner_dry)
            print(json.dumps(runner_dry, ensure_ascii=False, indent=2))
            return 0

        on_disk_runner_dry = _read_object(
            args.runner_dry_output, label="runner dry artifact"
        )
        ceiling = validate_paid_execution_authorization(
            request_preflight=request_preflight,
            cost_preflight=cost_preflight,
            runner_dry=on_disk_runner_dry,
            approve_request_preflight_hash=args.approve_request_preflight_hash,
            approve_request_manifest_hash=args.approve_request_manifest_hash,
            approve_cost_artifact_hash=args.approve_cost_artifact_hash,
            approve_runner_dry_artifact_hash=args.approve_runner_dry_artifact_hash,
            approve_max_usd=args.approve_max_usd,
        )
        owner_id, owner_at = validate_owner_approval_record(
            args.owner_approval_id,
            args.owner_approval_at_utc,
        )
        git_context = _git_execution_context(request_preflight["code_commit_sha"])
        _require_fresh_paid_paths(
            args.paid_output,
            args.authorization_output,
            args.receipt_journal,
        )

        run_started = _utc_now_text()
        run_id = _run_id(
            run_started,
            request_preflight["artifact_hash"],
            cost_preflight["artifact_hash"],
        )
        authorization = _authorization_artifact(
            run_id=run_id,
            created_at=run_started,
            git_context=git_context,
            request_preflight=request_preflight,
            cost_preflight=cost_preflight,
            runner_dry=on_disk_runner_dry,
            ceiling=ceiling,
            owner_id=owner_id,
            owner_at=owner_at,
            receipt_journal=args.receipt_journal,
        )
        _write_durable_new(args.authorization_output, authorization)

        from aic.research.runtime import (
            StdlibResponsesTransport,
            load_openai_api_key,
        )

        api_key = load_openai_api_key()
        cases = build_judge_eval_cases()
        frozen_variants = {
            (row["candidate_key"], row["case_id"]): row
            for row in request_preflight["request_variants"]
        }
        if len(frozen_variants) != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
            raise JudgeEvalAuthorizationError("frozen request lookup count mismatch")

        dispatch_attempts = 0
        completed_model_responses = 0
        cumulative_cost = Decimal("0")
        receipt_hashes: list[str] = []
        candidate_records: list[dict[str, Any]] = []
        eval_results: list[StageModelEvalResult] = []

        for candidate in JUDGE_MODEL_LADDER:
            runs = []
            for case in cases:
                if dispatch_attempts >= EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
                    raise JudgeEvalAuthorizationError(
                        "paid Judge eval dispatch ceiling exhausted"
                    )
                request = build_judge_eval_case_request(case, candidate)
                frozen = frozen_variants[(candidate.candidate_key, case.case_id)]
                request_bytes = len(
                    json.dumps(
                        request.request_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if frozen.get("request_hash") != request.request_hash:
                    raise JudgeEvalAuthorizationError(
                        "paid Judge request hash differs from preflight"
                    )
                if frozen.get("request_body_utf8_bytes") != request_bytes:
                    raise JudgeEvalAuthorizationError(
                        "paid Judge request bytes differ from preflight"
                    )
                if request.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
                    raise JudgeEvalAuthorizationError(
                        "paid Judge request output cap drift"
                    )

                print(
                    f"[B4 JUDGE EVAL] {candidate.candidate_key} {case.case_id} "
                    f"{candidate.model}/{candidate.reasoning_effort}",
                    file=sys.stderr,
                    flush=True,
                )
                started_at = _utc_now_text()
                tracker = DispatchTrackingTransport(StdlibResponsesTransport())
                run = execute_judge_eval_case_once(
                    case,
                    model_candidate=candidate,
                    api_key=api_key,
                    transport=tracker,
                )
                finished_at = _utc_now_text()
                if tracker.dispatch_attempts != 1:
                    raise JudgeEvalAuthorizationError(
                        "each Judge eval case must attempt exactly one provider dispatch"
                    )
                dispatch_attempts += 1
                completed_model_responses += run.model_calls

                receipt = _receipt(
                    run_id=run_id,
                    dispatch_index=dispatch_attempts,
                    started_at=started_at,
                    finished_at=finished_at,
                    authorization_hash=authorization["artifact_hash"],
                    request_preflight=request_preflight,
                    cost_preflight=cost_preflight,
                    runner_dry=on_disk_runner_dry,
                    ceiling=ceiling,
                    owner_id=owner_id,
                    owner_at=owner_at,
                    code_commit_sha=git_context["code_commit_sha"],
                    candidate=candidate,
                    case=case,
                    request=request,
                    run=run,
                    tracker=tracker,
                )
                _append_receipt(args.receipt_journal, receipt)
                receipt_hashes.append(receipt["receipt_hash"])

                if tracker.provider_responses != 1 or run.model_calls != 1:
                    artifact = _blocked_artifact(
                        status="BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
                        reason=run.findings[0]
                        if run.findings
                        else "provider response unavailable",
                        run_id=run_id,
                        code_commit_sha=git_context["code_commit_sha"],
                        request_preflight=request_preflight,
                        cost_preflight=cost_preflight,
                        runner_dry=on_disk_runner_dry,
                        authorization_hash=authorization["artifact_hash"],
                        ceiling=ceiling,
                        dispatch_attempts=dispatch_attempts,
                        model_calls=completed_model_responses,
                        known_cost=cumulative_cost,
                        receipt_hashes=receipt_hashes,
                        receipt_journal=args.receipt_journal,
                    )
                    _write(args.paid_output, artifact)
                    print(json.dumps(artifact, ensure_ascii=False, indent=2))
                    return 2

                if run.cost_receipt_status != "COMPLETE" or run.actual_cost_usd is None:
                    artifact = _blocked_artifact(
                        status="BLOCKED_INCOMPLETE_COST_RECEIPT",
                        reason=run.findings[-1]
                        if run.findings
                        else "cost receipt incomplete",
                        run_id=run_id,
                        code_commit_sha=git_context["code_commit_sha"],
                        request_preflight=request_preflight,
                        cost_preflight=cost_preflight,
                        runner_dry=on_disk_runner_dry,
                        authorization_hash=authorization["artifact_hash"],
                        ceiling=ceiling,
                        dispatch_attempts=dispatch_attempts,
                        model_calls=completed_model_responses,
                        known_cost=cumulative_cost,
                        receipt_hashes=receipt_hashes,
                        receipt_journal=args.receipt_journal,
                    )
                    _write(args.paid_output, artifact)
                    print(json.dumps(artifact, ensure_ascii=False, indent=2))
                    return 2

                cumulative_cost += run.actual_cost_usd
                if cumulative_cost > ceiling:
                    artifact = _blocked_artifact(
                        status="BLOCKED_APPROVED_COST_CEILING_EXCEEDED",
                        reason="known actual cost exceeded approved ceiling",
                        run_id=run_id,
                        code_commit_sha=git_context["code_commit_sha"],
                        request_preflight=request_preflight,
                        cost_preflight=cost_preflight,
                        runner_dry=on_disk_runner_dry,
                        authorization_hash=authorization["artifact_hash"],
                        ceiling=ceiling,
                        dispatch_attempts=dispatch_attempts,
                        model_calls=completed_model_responses,
                        known_cost=cumulative_cost,
                        receipt_hashes=receipt_hashes,
                        receipt_journal=args.receipt_journal,
                    )
                    _write(args.paid_output, artifact)
                    print(json.dumps(artifact, ensure_ascii=False, indent=2))
                    return 2

                runs.append(run)

            record, eval_result = _candidate_record(candidate, tuple(runs))
            candidate_records.append(record)
            eval_results.append(eval_result)

        if dispatch_attempts != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
            raise JudgeEvalAuthorizationError(
                "paid Judge eval dispatch count is not 21"
            )
        if completed_model_responses != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
            raise JudgeEvalAuthorizationError(
                "paid Judge eval provider-response count is not 21"
            )
        if len(receipt_hashes) != EXPECTED_JUDGE_EVAL_PAID_CALLS_MAX:
            raise JudgeEvalAuthorizationError(
                "paid Judge eval receipt count is not 21"
            )

        selection = select_stage_model_from_eval(
            CouncilModelStage.JUDGE,
            tuple(eval_results),
        )
        selected = None
        if selection.selected_candidate is not None:
            selected = {
                "candidate_key": selection.selected_candidate.candidate_key,
                "model": selection.selected_candidate.model,
                "reasoning_effort": selection.selected_candidate.reasoning_effort,
                "ladder_position": selection.selected_candidate.ladder_position,
            }

        artifact: dict[str, Any] = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": "B4_JUDGE_MODEL_EVAL",
            "status": "PASS_SELECTED"
            if selected is not None
            else "COMPLETE_NO_PASSING_MODEL",
            "run_id": run_id,
            "code_commit_sha": git_context["code_commit_sha"],
            "eval_version": JUDGE_EVAL_VERSION,
            "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
            "model_policy_version": MODEL_POLICY_VERSION,
            "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
            "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
            "request_preflight_artifact_hash": request_preflight["artifact_hash"],
            "request_manifest_hash": request_preflight["request_manifest_hash"],
            "cost_preflight_artifact_hash": cost_preflight["artifact_hash"],
            "runner_dry_artifact_hash": on_disk_runner_dry["artifact_hash"],
            "paid_authorization_artifact_hash": authorization["artifact_hash"],
            "approved_cost_ceiling_usd": str(ceiling),
            "dry_run_manifest_hash": on_disk_runner_dry["dry_run_manifest_hash"],
            "pricing_version": cost_preflight["pricing_version"],
            "pricing_hash": cost_preflight["pricing_hash"],
            "case_ids": list(EXPECTED_JUDGE_EVAL_CASE_IDS),
            "candidate_records": candidate_records,
            "selection": {
                "status": selection.status.value,
                "selected_candidate": selected,
                "reason_code": selection.reason_code,
            },
            "dispatch_attempts": dispatch_attempts,
            "model_calls": completed_model_responses,
            "actual_cost_usd": str(cumulative_cost),
            "cost_receipt_status": "COMPLETE",
            "paid_call_receipt_hashes": receipt_hashes,
            "receipt_manifest_hash": canonical_sha256(
                {"receipt_hashes": receipt_hashes}
            ),
            "receipt_journal_path": str(args.receipt_journal),
            "judge_eval_authorization_consumed": True,
            "automatic_repair_calls": 0,
            "production_judge_authorized": False,
            "rerun_authorized": False,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
        }
        artifact["artifact_hash"] = canonical_sha256(artifact)
        _write(args.paid_output, artifact)
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
