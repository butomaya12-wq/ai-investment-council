from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns
from typing import Any, Mapping

from aic.council.reopen_initial_runtime import (
    EXPECTED_CALLS,
    EXPECTED_COST_CEILING_USD,
    EXPECTED_COST_PREFLIGHT_HASH,
    EXPECTED_REQUEST_MANIFEST_HASH,
    REOPEN_INITIAL_BLOCKED_STATUS,
    REOPEN_INITIAL_DRY_STATUS,
    B4ReopenInitialRuntimeError,
    build_dispatch_attempt_event,
    build_paid_call_receipt,
    build_reopen_initial_blocked_artifact,
    build_reopen_initial_council_freeze_artifact,
    build_reopen_initial_dry_artifact,
    build_reopen_initial_paid_authorization,
    load_and_build_reopen_initial_runtime_plan,
    process_reopen_initial_provider_response,
    verify_reopen_initial_dry_artifact,
)
from aic.domain.canonical import canonical_sha256


DEFAULT_LIFECYCLE = Path(".aic-runtime/b4_reopen_lifecycle_plan_zero_call_v0_2.json")
DEFAULT_OVERLAY = Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json")
DEFAULT_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
DEFAULT_PRICING = Path("config/event/openai_text_pricing_2026_08_30.json")
DEFAULT_COST_PREFLIGHT = Path(".aic-runtime/b4_reopen_production_cost_preflight_zero_call_v0_1.json")
DEFAULT_DRY_OUTPUT = Path(".aic-runtime/b4_reopen_initial_runtime_dry_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_initial_council_freeze_v0_1.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(
    ".aic-runtime/b4_reopen_initial_runtime_paid_authorization_v0_1.json"
)
DEFAULT_RECEIPT_JOURNAL = Path(
    ".aic-runtime/b4_reopen_initial_runtime_paid_receipts_v0_1.jsonl"
)
EXPECTED_BRANCH = "hackathon/alpaca-2026"


class ReopenInitialPaidRunnerError(ValueError):
    pass


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-reopen-initial", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-request-manifest-hash")
    parser.add_argument("--approve-runner-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--lifecycle", type=Path, default=DEFAULT_LIFECYCLE)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--initial-authority", type=Path, default=DEFAULT_INITIAL_AUTHORITY)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT)
    parser.add_argument("--dry-output", type=Path, default=DEFAULT_DRY_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION_OUTPUT)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_RECEIPT_JOURNAL)
    return parser.parse_args()


def _write_durable_fresh(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_event(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReopenInitialPaidRunnerError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ReopenInitialPaidRunnerError(f"{label} root must be object")
    return value


def _git_context() -> tuple[str, bool]:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
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
        raise ReopenInitialPaidRunnerError("unable to prove git execution context") from exc
    if branch != EXPECTED_BRANCH:
        raise ReopenInitialPaidRunnerError(f"expected branch {EXPECTED_BRANCH}, got {branch}")
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise ReopenInitialPaidRunnerError("git HEAD is not canonical SHA")
    return head, not bool(status.strip())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _run_id(created_at: str, head: str) -> str:
    suffix = canonical_sha256(
        {
            "created_at_utc": created_at,
            "runner_code_commit_sha": head,
            "cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
            "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        }
    )[:12]
    compact = created_at.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-REOPEN-INITIAL-RUNTIME-{compact}-{suffix}"


def _require_paths(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.is_file():
            raise ReopenInitialPaidRunnerError(f"required immutable runtime input missing: {path}")


def _require_fresh_paths(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if path.exists():
            raise ReopenInitialPaidRunnerError(
                f"paid reopen Initial evidence path already exists: {path}; do not overwrite or rerun"
            )


def _load_plan(args: argparse.Namespace):
    _require_paths(
        (
            args.lifecycle,
            args.overlay,
            args.closure,
            args.freeze,
            args.reconciliation,
            args.handoff,
            args.initial_authority,
            args.pricing,
            args.cost_preflight,
        )
    )
    return load_and_build_reopen_initial_runtime_plan(
        cost_preflight_path=args.cost_preflight,
        lifecycle_path=args.lifecycle,
        overlay_path=args.overlay,
        closure_path=args.closure,
        freeze_path=args.freeze,
        reconciliation_path=args.reconciliation,
        handoff_path=args.handoff,
        initial_authority_path=args.initial_authority,
        pricing_path=args.pricing,
    )


def _blocked(
    *,
    args: argparse.Namespace,
    run_id: str,
    head: str,
    authorization_hash: str,
    dry_hash: str,
    processed_records: list[Mapping[str, Any]],
    dispatch_attempt_hashes: list[str],
    receipt_hashes: list[str],
    dispatch_attempts: int,
    provider_responses: int,
    cumulative_cost: Decimal,
    cost_status: str,
    reason: str,
) -> int:
    artifact = build_reopen_initial_blocked_artifact(
        run_id=run_id,
        code_commit_sha=head,
        authorization_hash=authorization_hash,
        dry_artifact_hash=dry_hash,
        processed_records=processed_records,
        dispatch_attempt_hashes=dispatch_attempt_hashes,
        receipt_hashes=receipt_hashes,
        receipt_journal_path=str(args.receipt_journal),
        dispatch_attempts=dispatch_attempts,
        provider_responses=provider_responses,
        actual_cost_usd_known=cumulative_cost,
        cost_receipt_status=cost_status,
        blocked_reason=reason,
    )
    _write_durable_fresh(args.output, artifact)
    print(
        json.dumps(
            {
                "status": artifact["status"],
                "run_id": artifact["run_id"],
                "authorization_consumed": artifact["authorization_consumed"],
                "provider_dispatch_attempts": artifact["provider_dispatch_attempts"],
                "model_calls_known_completed": artifact["model_calls_known_completed"],
                "processed_opinion_count": artifact["processed_opinion_count"],
                "actual_cost_usd_known": artifact["actual_cost_usd_known"],
                "cost_receipt_status": artifact["cost_receipt_status"],
                "blocked_reason": artifact["blocked_reason"],
                "initial_freeze_barrier": False,
                "rebuttal_authorized": False,
                "judge_authorized": False,
                "rerun_authorized": False,
                "broker_writes": 0,
                "alpaca_orders": 0,
                "live_money": "PROHIBITED",
                "artifact_hash": artifact["artifact_hash"],
                "output_path": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2


def main() -> int:
    args = _args()
    try:
        head, clean = _git_context()
        if not clean:
            raise ReopenInitialPaidRunnerError("worktree must be clean")
        cost, plan, _authority, pricing = _load_plan(args)
        if len(plan) != EXPECTED_CALLS:
            raise ReopenInitialPaidRunnerError("reopen Initial plan is not exact nine calls")

        if not args.execute_paid_reopen_initial:
            if args.dry_output.exists():
                raise ReopenInitialPaidRunnerError(
                    f"dry output already exists: {args.dry_output}; inspect it instead of overwriting"
                )
            dry = build_reopen_initial_dry_artifact(
                code_commit_sha=head,
                cost_preflight=cost,
                plan=plan,
            )
            _write_durable_fresh(args.dry_output, dry)
            print(
                json.dumps(
                    {
                        "status": dry["status"],
                        "code_commit_sha": dry["code_commit_sha"],
                        "source_cost_preflight_artifact_hash": dry[
                            "source_cost_preflight_artifact_hash"
                        ],
                        "request_manifest_hash": dry["request_manifest_hash"],
                        "effective_input_manifest_hash": dry[
                            "effective_input_manifest_hash"
                        ],
                        "runner_dry_artifact_hash": dry["artifact_hash"],
                        "selected_model": dry["selected_model"],
                        "planned_paid_calls_max": EXPECTED_CALLS,
                        "automatic_repair_calls_authorized": False,
                        "automatic_retries": 0,
                        "cost_ceiling_usd": str(EXPECTED_COST_CEILING_USD),
                        "model_calls": 0,
                        "provider_reads": 0,
                        "broker_writes": 0,
                        "alpaca_orders": 0,
                        "live_money": "PROHIBITED",
                        "dry_output_path": str(args.dry_output),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        required = {
            "owner_approval_id": args.owner_approval_id,
            "owner_approval_at_utc": args.owner_approval_at_utc,
            "approve_cost_artifact_hash": args.approve_cost_artifact_hash,
            "approve_request_manifest_hash": args.approve_request_manifest_hash,
            "approve_runner_dry_artifact_hash": args.approve_runner_dry_artifact_hash,
            "approve_max_usd": args.approve_max_usd,
        }
        missing = [key for key, value in required.items() if not isinstance(value, str) or not value]
        if missing:
            raise ReopenInitialPaidRunnerError(
                "paid reopen Initial execution missing explicit owner authorization fields: "
                + ", ".join(missing)
            )
        if args.approve_cost_artifact_hash != EXPECTED_COST_PREFLIGHT_HASH:
            raise ReopenInitialPaidRunnerError("approved cost artifact hash mismatch")
        if args.approve_request_manifest_hash != EXPECTED_REQUEST_MANIFEST_HASH:
            raise ReopenInitialPaidRunnerError("approved request manifest hash mismatch")
        if Decimal(args.approve_max_usd) != EXPECTED_COST_CEILING_USD:
            raise ReopenInitialPaidRunnerError("approved max USD mismatch")
        if not args.dry_output.is_file():
            raise ReopenInitialPaidRunnerError("required zero-call runner dry artifact missing")
        dry = _read_json(args.dry_output, label="reopen Initial runner dry artifact")
        dry_hash = verify_reopen_initial_dry_artifact(
            dry,
            expected_code_commit_sha=head,
            plan=plan,
        )
        if args.approve_runner_dry_artifact_hash != dry_hash:
            raise ReopenInitialPaidRunnerError("approved runner dry artifact hash mismatch")

        _require_fresh_paths(
            (
                args.output,
                args.authorization_output,
                args.receipt_journal,
            )
        )
        created_at = _now()
        run_id = _run_id(created_at, head)
        authorization = build_reopen_initial_paid_authorization(
            cost_preflight=cost,
            dry_artifact=dry,
            plan=plan,
            approve_cost_artifact_hash=args.approve_cost_artifact_hash,
            approve_max_usd=args.approve_max_usd,
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            code_commit_sha=head,
            git_worktree_clean=clean,
            created_at_utc=created_at,
            run_id=run_id,
            receipt_journal_path=str(args.receipt_journal),
        )
        _write_durable_fresh(args.authorization_output, authorization)

        # Credentials and provider transport are loaded only after all deterministic
        # authority checks pass and the immutable authorization is durable.
        from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

        api_key = load_openai_api_key()
        transport = StdlibResponsesTransport()
        processed_records: list[Mapping[str, Any]] = []
        dispatch_attempt_hashes: list[str] = []
        receipt_hashes: list[str] = []
        dispatch_attempts = 0
        provider_responses = 0
        cumulative_cost = Decimal("0")

        for item in plan:
            if dispatch_attempts >= EXPECTED_CALLS:
                raise ReopenInitialPaidRunnerError("reopen Initial paid dispatch ceiling exhausted")
            print(
                f"[B4 REOPEN INITIAL] {item.dispatch_index}/9 {item.candidate_id} "
                f"{item.lane.value} gpt-5.6-terra/low",
                file=sys.stderr,
                flush=True,
            )
            started_at = _now()
            attempt = build_dispatch_attempt_event(
                run_id=run_id,
                item=item,
                authorization_hash=authorization["artifact_hash"],
                started_at_utc=started_at,
            )
            _append_event(args.receipt_journal, attempt)
            dispatch_attempt_hashes.append(attempt["event_hash"])
            dispatch_attempts += 1

            raw: Mapping[str, Any] | None = None
            processed_record: Mapping[str, Any] | None = None
            validation_error: str | None = None
            provider_response_received = False
            started_ns = perf_counter_ns()
            try:
                raw = transport.post(payload=item.request.request_payload, api_key=api_key)
                provider_response_received = True
                provider_responses += 1
                latency_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
                processed_record = process_reopen_initial_provider_response(
                    item,
                    raw_response=raw,
                    latency_ms=latency_ms,
                    frozen_at=datetime.now(UTC),
                    pricing=pricing,
                )
            except Exception as exc:
                latency_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
                if provider_response_received:
                    validation_error = f"{type(exc).__name__}: {exc}"
                else:
                    # Do not persist provider error bodies/messages. Once the durable
                    # attempt event exists, authorization is consumed and dispatch state
                    # is treated as unknown.
                    validation_error = f"UNKNOWN_PROVIDER_DISPATCH:{type(exc).__name__}"

            finished_at = _now()
            receipt = build_paid_call_receipt(
                run_id=run_id,
                item=item,
                authorization_hash=authorization["artifact_hash"],
                attempt_event_hash=attempt["event_hash"],
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                provider_response_received=provider_response_received,
                raw_response=raw,
                latency_ms=latency_ms,
                processed_record=processed_record,
                validation_error=validation_error,
                pricing=pricing,
            )
            _append_event(args.receipt_journal, receipt)
            receipt_hashes.append(receipt["receipt_hash"])

            if receipt.get("actual_cost_usd") is not None:
                cumulative_cost += Decimal(str(receipt["actual_cost_usd"]))
            if receipt.get("cost_receipt_status") != "COMPLETE":
                return _blocked(
                    args=args,
                    run_id=run_id,
                    head=head,
                    authorization_hash=authorization["artifact_hash"],
                    dry_hash=dry_hash,
                    processed_records=processed_records,
                    dispatch_attempt_hashes=dispatch_attempt_hashes,
                    receipt_hashes=receipt_hashes,
                    dispatch_attempts=dispatch_attempts,
                    provider_responses=provider_responses,
                    cumulative_cost=cumulative_cost,
                    cost_status=str(receipt.get("cost_receipt_status")),
                    reason=validation_error or "incomplete paid cost receipt",
                )
            if cumulative_cost > EXPECTED_COST_CEILING_USD:
                return _blocked(
                    args=args,
                    run_id=run_id,
                    head=head,
                    authorization_hash=authorization["artifact_hash"],
                    dry_hash=dry_hash,
                    processed_records=processed_records,
                    dispatch_attempt_hashes=dispatch_attempt_hashes,
                    receipt_hashes=receipt_hashes,
                    dispatch_attempts=dispatch_attempts,
                    provider_responses=provider_responses,
                    cumulative_cost=cumulative_cost,
                    cost_status="COMPLETE_COST_CEILING_BREACH",
                    reason="cumulative reopen Initial paid cost exceeded approved ceiling",
                )
            if processed_record is None:
                return _blocked(
                    args=args,
                    run_id=run_id,
                    head=head,
                    authorization_hash=authorization["artifact_hash"],
                    dry_hash=dry_hash,
                    processed_records=processed_records,
                    dispatch_attempt_hashes=dispatch_attempt_hashes,
                    receipt_hashes=receipt_hashes,
                    dispatch_attempts=dispatch_attempts,
                    provider_responses=provider_responses,
                    cumulative_cost=cumulative_cost,
                    cost_status="COMPLETE",
                    reason=validation_error or "reopen Initial provider response failed deterministic validation/promotion",
                )
            processed_records.append(processed_record)

        if (
            dispatch_attempts != EXPECTED_CALLS
            or provider_responses != EXPECTED_CALLS
            or len(processed_records) != EXPECTED_CALLS
            or len(dispatch_attempt_hashes) != EXPECTED_CALLS
            or len(receipt_hashes) != EXPECTED_CALLS
        ):
            raise ReopenInitialPaidRunnerError("reopen Initial run did not complete exact 9/9 surface")

        artifact = build_reopen_initial_council_freeze_artifact(
            run_id=run_id,
            code_commit_sha=head,
            authorization_hash=authorization["artifact_hash"],
            dry_artifact_hash=dry_hash,
            processed_records=tuple(processed_records),
            dispatch_attempt_hashes=dispatch_attempt_hashes,
            receipt_hashes=receipt_hashes,
            receipt_journal_path=str(args.receipt_journal),
            actual_cost_usd_total=cumulative_cost,
        )
        _write_durable_fresh(args.output, artifact)
        print(
            json.dumps(
                {
                    "status": artifact["status"],
                    "run_id": artifact["run_id"],
                    "authorization_consumed": True,
                    "selected_model": artifact["selected_model"],
                    "initial_opinion_count": artifact["initial_opinion_count"],
                    "provider_dispatch_attempts": artifact["provider_dispatch_attempts"],
                    "model_calls_known_completed": artifact["model_calls_known_completed"],
                    "automatic_repair_calls": 0,
                    "actual_cost_usd": artifact["actual_cost_usd"],
                    "approved_cost_ceiling_usd": artifact["approved_cost_ceiling_usd"],
                    "cost_receipt_status": artifact["cost_receipt_status"],
                    "receipt_manifest_hash": artifact["receipt_manifest_hash"],
                    "initial_freeze_barrier": True,
                    "rebuttal_authorized": False,
                    "judge_authorized": False,
                    "rerun_authorized": False,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                    "next_gate": artifact["next_gate"],
                    "artifact_hash": artifact["artifact_hash"],
                    "output_path": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (B4ReopenInitialRuntimeError, ReopenInitialPaidRunnerError, ValueError) as exc:
        print(
            f"B4 reopen Initial runtime failed closed before/around dispatch: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            f"B4 reopen Initial runtime unexpected fail-closed error: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
