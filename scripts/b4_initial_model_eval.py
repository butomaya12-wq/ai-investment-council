from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from aic.council.initial_eval_runtime import (
    EXPECTED_INITIAL_CASE_IDS,
    INITIAL_EVAL_VERSION,
    build_initial_eval_cases,
    dry_run_manifest,
    execute_case_once,
)
from aic.council.model_policy import (
    INITIAL_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelStage,
    StageModelEvalResult,
    select_stage_model_from_eval,
)
from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B4_INITIAL_MODEL_EVAL_ARTIFACT_v0_1"
DEFAULT_COST_PREFLIGHT = Path(".aic-runtime/b4_initial_eval_cost_preflight.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_model_eval.json")
EXPECTED_COST_STATUS = "REQUIRES_OWNER_COST_APPROVAL_BEFORE_INITIAL_MODEL_EVAL"
EXPECTED_COST_ARTIFACT_VERSION = "B4_INITIAL_MODEL_EVAL_COST_PREFLIGHT_ARTIFACT_v0_1"
EXPECTED_PLANNED_CALLS = 36
EXPECTED_MAX_OUTPUT_TOKENS = 4096


class B4InitialEvalAuthorizationError(ValueError):
    pass


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or execute the frozen B4 Initial-stage model eval. "
            "Paid execution is impossible without exact owner cost-artifact approval."
        )
    )
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute-paid-eval", action="store_true")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-max-usd")
    return parser.parse_args()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4InitialEvalAuthorizationError(f"unable to read cost preflight: {path}") from exc
    if not isinstance(value, dict):
        raise B4InitialEvalAuthorizationError("cost preflight root must be an object")
    return value


def _verify_cost_preflight(cost: Mapping[str, Any]) -> None:
    actual_hash = cost.get("artifact_hash")
    if not isinstance(actual_hash, str) or len(actual_hash) != 64:
        raise B4InitialEvalAuthorizationError("cost preflight artifact_hash missing")
    expected_hash = canonical_sha256(cost, exclude_fields=("artifact_hash",))
    if actual_hash != expected_hash:
        raise B4InitialEvalAuthorizationError("cost preflight artifact_hash mismatch")
    if cost.get("artifact_version") != EXPECTED_COST_ARTIFACT_VERSION:
        raise B4InitialEvalAuthorizationError("unexpected cost preflight artifact version")
    if cost.get("status") != EXPECTED_COST_STATUS:
        raise B4InitialEvalAuthorizationError("cost preflight is not owner-approval ready")
    if cost.get("planned_paid_calls_max") != EXPECTED_PLANNED_CALLS:
        raise B4InitialEvalAuthorizationError("Initial paid-call ceiling drift")
    if cost.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4InitialEvalAuthorizationError("Initial output-token ceiling drift")
    if tuple(cost.get("eval_case_ids", ())) != EXPECTED_INITIAL_CASE_IDS:
        raise B4InitialEvalAuthorizationError("Initial eval case surface drift")
    if cost.get("owner_cost_approval_required") is not True:
        raise B4InitialEvalAuthorizationError("cost preflight must require owner approval")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if cost.get(field) != 0:
            raise B4InitialEvalAuthorizationError(f"cost preflight {field} must be zero")
    if cost.get("live_money") != "PROHIBITED":
        raise B4InitialEvalAuthorizationError("live money must remain prohibited")


def _decimal_text(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise B4InitialEvalAuthorizationError(f"{field_name} must be decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise B4InitialEvalAuthorizationError(f"{field_name} invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise B4InitialEvalAuthorizationError(f"{field_name} invalid")
    return result


def validate_paid_execution_authorization(
    cost: Mapping[str, Any],
    *,
    approve_cost_artifact_hash: str | None,
    approve_max_usd: str | None,
) -> Decimal:
    _verify_cost_preflight(cost)
    artifact_hash = cost["artifact_hash"]
    ceiling = _decimal_text(
        cost.get("total_initial_model_eval_cost_upper_bound_usd"),
        field_name="total_initial_model_eval_cost_upper_bound_usd",
    )
    if approve_cost_artifact_hash != artifact_hash:
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires exact approved cost artifact hash"
        )
    approved = _decimal_text(approve_max_usd, field_name="approve_max_usd")
    if approved != ceiling:
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires exact approval of frozen cost ceiling"
        )
    return ceiling


def _dry_run(cost: Mapping[str, Any]) -> dict[str, Any]:
    _verify_cost_preflight(cost)
    manifest = dry_run_manifest()
    request_bound = cost.get("eval_request_body_utf8_bytes_upper_bound")
    if not isinstance(request_bound, int) or request_bound <= 0:
        raise B4InitialEvalAuthorizationError("eval request byte bound invalid")
    if manifest["request_count"] != EXPECTED_PLANNED_CALLS:
        raise B4InitialEvalAuthorizationError("dry-run request count mismatch")
    if max(item["request_body_utf8_bytes"] for item in manifest["requests"]) > request_bound:
        raise B4InitialEvalAuthorizationError("frozen Initial eval request exceeds approved byte bound")
    if any(item["max_output_tokens"] != EXPECTED_MAX_OUTPUT_TOKENS for item in manifest["requests"]):
        raise B4InitialEvalAuthorizationError("dry-run request lacks exact output-token cap")
    return manifest


def _case_record(run) -> dict[str, Any]:
    return {
        "case_id": run.case_id,
        "name": run.name,
        "lane": run.lane,
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
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "estimated_cost_usd": str(run.estimated_cost_usd),
        "output_hash": run.output_hash,
        "result_hash": run.result_hash,
    }


def _candidate_record(candidate, runs) -> tuple[dict[str, Any], StageModelEvalResult]:
    passed = all(run.passed for run in runs)
    critical_failures = sum(1 for run in runs if run.critical_safety and not run.passed)
    cost = sum((run.estimated_cost_usd for run in runs), Decimal("0"))
    latency = sum(run.latency_ms for run in runs)
    total_tokens = sum(run.input_tokens + run.output_tokens for run in runs)
    eval_result = StageModelEvalResult(
        candidate_key=candidate.candidate_key,
        all_required_checks_passed=passed,
        critical_safety_failures=critical_failures,
        estimated_cost_usd=cost,
        latency_ms=latency,
        total_tokens=total_tokens,
    )
    record = {
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
    return record, eval_result


def _write(output: Path, artifact: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    args = _args()
    try:
        cost = _read_object(args.cost_preflight)
        dry = _dry_run(cost)
        if not args.execute_paid_eval:
            artifact: dict[str, Any] = {
                "artifact_version": ARTIFACT_VERSION,
                "run_class": "B4_INITIAL_MODEL_EVAL_DRY_RUN",
                "status": "READY_FOR_OWNER_PAID_INITIAL_EVAL_AUTHORIZATION",
                "eval_version": INITIAL_EVAL_VERSION,
                "model_policy_version": MODEL_POLICY_VERSION,
                "cost_preflight_artifact_hash": cost["artifact_hash"],
                "approved_cost_ceiling_usd": None,
                "dry_run_manifest_hash": dry["manifest_hash"],
                "case_ids": list(EXPECTED_INITIAL_CASE_IDS),
                "candidate_keys": [item.candidate_key for item in INITIAL_MODEL_LADDER],
                "planned_paid_calls_max": EXPECTED_PLANNED_CALLS,
                "request_count": dry["request_count"],
                "max_request_body_utf8_bytes": max(item["request_body_utf8_bytes"] for item in dry["requests"]),
                "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
                "model_calls": 0,
                "provider_reads": 0,
                "broker_writes": 0,
                "alpaca_orders": 0,
                "live_money": "PROHIBITED",
            }
            artifact["artifact_hash"] = canonical_sha256(artifact)
            _write(args.output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 0

        approved_ceiling = validate_paid_execution_authorization(
            cost,
            approve_cost_artifact_hash=args.approve_cost_artifact_hash,
            approve_max_usd=args.approve_max_usd,
        )

        # Import/load the secret only after all deterministic approval and request gates pass.
        from aic.research.runtime import load_openai_api_key
        api_key = load_openai_api_key()
        cases = build_initial_eval_cases()
        candidate_records = []
        eval_results = []
        cumulative_cost = Decimal("0")
        actual_calls = 0
        provider_blocked: str | None = None

        for candidate in INITIAL_MODEL_LADDER:
            runs = []
            for case in cases:
                print(
                    f"[B4 INITIAL EVAL] {candidate.candidate_key} {case.case_id} "
                    f"{candidate.model}/{candidate.reasoning_effort}",
                    file=sys.stderr,
                    flush=True,
                )
                run = execute_case_once(case, model_candidate=candidate, api_key=api_key)
                runs.append(run)
                actual_calls += run.model_calls
                cumulative_cost += run.estimated_cost_usd
                if cumulative_cost > approved_ceiling:
                    raise B4InitialEvalAuthorizationError("actual eval cost exceeded approved ceiling")
                if run.model_calls == 0:
                    provider_blocked = run.findings[0] if run.findings else "provider call failed"
                    break
            if provider_blocked is not None:
                break
            if len(runs) != len(cases):
                raise B4InitialEvalAuthorizationError("candidate eval did not cover full case set")
            record, eval_result = _candidate_record(candidate, tuple(runs))
            candidate_records.append(record)
            eval_results.append(eval_result)

        if provider_blocked is not None:
            artifact = {
                "artifact_version": ARTIFACT_VERSION,
                "run_class": "B4_INITIAL_REAL_MODEL_EVAL",
                "status": "PROVIDER_BLOCKED_NO_MODEL_SELECTION",
                "eval_version": INITIAL_EVAL_VERSION,
                "model_policy_version": MODEL_POLICY_VERSION,
                "cost_preflight_artifact_hash": cost["artifact_hash"],
                "approved_cost_ceiling_usd": str(approved_ceiling),
                "dry_run_manifest_hash": dry["manifest_hash"],
                "provider_blocked_reason": provider_blocked,
                "model_calls": actual_calls,
                "actual_cost_usd": str(cumulative_cost),
                "broker_writes": 0,
                "alpaca_orders": 0,
                "live_money": "PROHIBITED",
            }
            artifact["artifact_hash"] = canonical_sha256(artifact)
            _write(args.output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 2

        if actual_calls != EXPECTED_PLANNED_CALLS:
            raise B4InitialEvalAuthorizationError("paid Initial eval must contain exactly 36 calls")
        if len(eval_results) != len(INITIAL_MODEL_LADDER):
            raise B4InitialEvalAuthorizationError("paid Initial eval did not cover full frozen ladder")

        selection = select_stage_model_from_eval(CouncilModelStage.INITIAL, tuple(eval_results))
        selected = None if selection.selected_candidate is None else {
            "candidate_key": selection.selected_candidate.candidate_key,
            "model": selection.selected_candidate.model,
            "reasoning_effort": selection.selected_candidate.reasoning_effort,
            "ladder_position": selection.selected_candidate.ladder_position,
        }
        artifact = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": "B4_INITIAL_REAL_MODEL_EVAL",
            "status": "PASS_SELECTED" if selected is not None else "BLOCKED_NO_PASSING_MODEL",
            "eval_version": INITIAL_EVAL_VERSION,
            "model_policy_version": MODEL_POLICY_VERSION,
            "cost_preflight_artifact_hash": cost["artifact_hash"],
            "approved_cost_ceiling_usd": str(approved_ceiling),
            "dry_run_manifest_hash": dry["manifest_hash"],
            "case_ids": list(EXPECTED_INITIAL_CASE_IDS),
            "candidate_records": candidate_records,
            "selection": {
                "status": selection.status.value,
                "selected_candidate": selected,
                "reason_code": selection.reason_code,
            },
            "model_calls": actual_calls,
            "actual_cost_usd": str(cumulative_cost),
            "network_manifest": {
                "openai_responses_api": True,
                "hosted_tools": False,
                "general_web_search": False,
                "remote_mcp": False,
                "broker_api": False,
            },
            "provider_reads": 0,
            "external_writes": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
        }
        artifact["artifact_hash"] = canonical_sha256(artifact)
        _write(args.output, artifact)
        print(json.dumps({
            "artifact_version": artifact["artifact_version"],
            "status": artifact["status"],
            "selection": artifact["selection"],
            "model_calls": artifact["model_calls"],
            "actual_cost_usd": artifact["actual_cost_usd"],
            "approved_cost_ceiling_usd": artifact["approved_cost_ceiling_usd"],
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "artifact_hash": artifact["artifact_hash"],
            "output_path": str(args.output),
        }, ensure_ascii=False, indent=2))
        return 0 if selected is not None else 1
    except Exception as exc:
        print(f"B4 Initial model eval failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
