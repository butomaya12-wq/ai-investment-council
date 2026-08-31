from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.council.rebuttal_runtime_execution import execute_rebuttal_runtime_item_once
from aic.council.reopen_rebuttal_runtime import (
    AUTH_STATUS,
    AUTH_VERSION,
    BLOCKED_STATUS,
    DRY_STATUS,
    DRY_VERSION,
    EXPECTED_CALLS,
    EXPECTED_COST_CEILING_USD,
    EXPECTED_COST_PREFLIGHT_HASH,
    EXPECTED_MAX_OUTPUT_TOKENS,
    EXPECTED_REQUEST_MANIFEST_HASH,
    EXPECTED_SELECTION_HASH,
    B4ReopenRebuttalRuntimeError,
    append_jsonl_fsync,
    build_attempt_event,
    build_blocked_artifact,
    build_dry_artifact,
    build_freeze_artifact,
    build_paid_authorization,
    build_result_receipt,
    durable_finalize_inputs_from_journal,
    load_and_build_reopen_rebuttal_runtime_plan,
    read_jsonl,
    verify_dry_artifact,
    write_json_fsync_new,
)


EXPECTED_BRANCH = "hackathon/alpaca-2026"

DEFAULT_COST = Path(".aic-runtime/b4_reopen_rebuttal_production_cost_preflight_zero_call_v0_2.json")
DEFAULT_RECOVERED_INITIAL = Path(".aic-runtime/b4_reopen_initial_council_freeze_recovered_v0_2.json")
DEFAULT_SELECTION = Path(".aic-runtime/b4_rebuttal_selected_model_authority_v0_2.json")
DEFAULT_LIFECYCLE = Path(".aic-runtime/b4_reopen_lifecycle_plan_zero_call_v0_2.json")
DEFAULT_INITIAL_COST = Path(".aic-runtime/b4_reopen_production_cost_preflight_zero_call_v0_1.json")
DEFAULT_OVERLAY = Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json")
DEFAULT_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
DEFAULT_PRICING = Path("config/event/openai_text_pricing_2026_08_30.json")

DEFAULT_DRY = Path(".aic-runtime/b4_reopen_rebuttal_runtime_dry_v0_1.json")
DEFAULT_PAID_AUTH = Path(".aic-runtime/b4_reopen_rebuttal_runtime_paid_authorization_v0_1.json")
DEFAULT_PAID_JOURNAL = Path(".aic-runtime/b4_reopen_rebuttal_runtime_paid_receipts_v0_1.jsonl")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_rebuttal_council_freeze_v0_1.json")


class TrackingTransport:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST)
    parser.add_argument("--recovered-initial-freeze", type=Path, default=DEFAULT_RECOVERED_INITIAL)
    parser.add_argument("--selection-authority", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--lifecycle", type=Path, default=DEFAULT_LIFECYCLE)
    parser.add_argument("--initial-cost-preflight", type=Path, default=DEFAULT_INITIAL_COST)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--initial-authority", type=Path, default=DEFAULT_INITIAL_AUTHORITY)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--dry-output", type=Path, default=DEFAULT_DRY)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_PAID_AUTH)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_PAID_JOURNAL)
    parser.add_argument("--paid-output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute-paid-rebuttal", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-request-manifest-hash")
    parser.add_argument("--approve-runner-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    return parser.parse_args()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _git_context() -> tuple[str, bool]:
    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise B4ReopenRebuttalRuntimeError(f"expected branch {EXPECTED_BRANCH}, got {branch}")
    if _git("status", "--porcelain"):
        raise B4ReopenRebuttalRuntimeError("Rebuttal runtime requires clean git worktree")
    head = _git("rev-parse", "HEAD")
    if len(head) != 40:
        raise B4ReopenRebuttalRuntimeError("exact git HEAD missing")
    return head, True


def _read(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalRuntimeError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalRuntimeError(f"{label} root must be object")
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _run_id(started: str, dry_hash: str) -> str:
    suffix = canonical_sha256({
        "started_at_utc": started,
        "cost_preflight_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "dry_hash": dry_hash,
    })[:12]
    compact = started.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-REOPEN-REBUTTAL-{compact}-{suffix}"


def _load_bound(args: argparse.Namespace):
    return load_and_build_reopen_rebuttal_runtime_plan(
        cost_preflight_path=args.cost_preflight,
        recovered_initial_freeze_path=args.recovered_initial_freeze,
        selection_authority_path=args.selection_authority,
        lifecycle_path=args.lifecycle,
        initial_cost_preflight_path=args.initial_cost_preflight,
        overlay_path=args.overlay,
        closure_path=args.closure,
        freeze_path=args.freeze,
        reconciliation_path=args.reconciliation,
        handoff_path=args.handoff,
        initial_authority_path=args.initial_authority,
        pricing_path=args.pricing,
    )


def _verify_existing_auth(auth: Mapping[str, Any], *, head: str, dry_hash: str, args: argparse.Namespace) -> str:
    observed = auth.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(auth, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalRuntimeError("existing Rebuttal authorization self-hash mismatch")
    if auth.get("artifact_version") != AUTH_VERSION or auth.get("status") != AUTH_STATUS:
        raise B4ReopenRebuttalRuntimeError("existing Rebuttal authorization version/status drift")
    exact = {
        "code_commit_sha": head,
        "owner_approval_id": args.owner_approval_id,
        "owner_approval_at_utc": args.owner_approval_at_utc,
        "source_cost_preflight_artifact_hash": EXPECTED_COST_PREFLIGHT_HASH,
        "selected_model_authority_selection_hash": EXPECTED_SELECTION_HASH,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_hash,
        "planned_paid_calls_max": EXPECTED_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_cost_ceiling_usd": str(EXPECTED_COST_CEILING_USD),
    }
    for key, expected in exact.items():
        if auth.get(key) != expected:
            raise B4ReopenRebuttalRuntimeError(f"existing Rebuttal authorization drift: {key}")
    return observed


def _journal_block_reason(events: list[dict[str, Any]]) -> str:
    attempts = [row for row in events if row.get("event_type") == "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT"]
    results = [row for row in events if row.get("event_type") == "REBUTTAL_PROVIDER_DISPATCH_RESULT"]
    if len(attempts) > len(results):
        return "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH"
    for result in results:
        if result.get("provider_response_received") is not True:
            return "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH"
        if result.get("cost_receipt_status") != "COMPLETE":
            return "INCOMPLETE_COST_RECEIPT"
        if result.get("validation_status") != "PASS":
            return "REBUTTAL_VALIDATION_FAILED"
    if len(attempts) < EXPECTED_CALLS:
        return "PARTIAL_CONSUMED_AUTHORITY_NO_RERUN"
    return "DURABLE_JOURNAL_NOT_FINALIZABLE"


def _blocked_from_existing_journal(*, args: argparse.Namespace, head: str, auth: Mapping[str, Any], dry_hash: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [row for row in events if row.get("event_type") == "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT"]
    results = [row for row in events if row.get("event_type") == "REBUTTAL_PROVIDER_DISPATCH_RESULT"]
    known_cost = Decimal("0")
    records: list[Mapping[str, Any]] = []
    receipt_hashes: list[str] = []
    model_calls = 0
    for row in results:
        if row.get("provider_response_received") is True:
            model_calls += 1
        value = row.get("actual_cost_usd")
        if isinstance(value, str) and row.get("cost_receipt_status") == "COMPLETE":
            known_cost += Decimal(value)
        receipt_hash = row.get("receipt_hash")
        if isinstance(receipt_hash, str):
            receipt_hashes.append(receipt_hash)
        record = row.get("processed_record")
        if row.get("validation_status") == "PASS" and isinstance(record, Mapping):
            records.append(record)
    return build_blocked_artifact(
        code_commit_sha=head,
        run_id=str(auth["run_id"]),
        authorization_hash=str(auth["artifact_hash"]),
        dry_hash=dry_hash,
        reason=_journal_block_reason(events),
        dispatch_attempts=len(attempts),
        known_model_calls=model_calls,
        known_rebuttal_cost_usd=known_cost,
        receipt_hashes=receipt_hashes,
        successful_processed_records=records,
    )


def run_dry(args: argparse.Namespace) -> int:
    head, _ = _git_context()
    bound = _load_bound(args)
    dry = build_dry_artifact(code_commit_sha=head, bound=bound)
    if args.dry_output.exists():
        raise B4ReopenRebuttalRuntimeError(f"dry output already exists: {args.dry_output}")
    write_json_fsync_new(args.dry_output, dry)
    print(json.dumps({
        "status": dry["status"],
        "artifact_version": dry["artifact_version"],
        "artifact_hash": dry["artifact_hash"],
        "code_commit_sha": dry["code_commit_sha"],
        "source_cost_preflight_artifact_hash": dry["source_cost_preflight_artifact_hash"],
        "selected_model_authority_selection_hash": dry["selected_model_authority_selection_hash"],
        "request_manifest_hash": dry["request_manifest_hash"],
        "planned_paid_calls_max": dry["planned_paid_calls_max"],
        "max_output_tokens_per_call": dry["max_output_tokens_per_call"],
        "cost_ceiling_usd": dry["cost_ceiling_usd"],
        "historical_rebuttal_selection_authority_revalidated": dry["historical_rebuttal_selection_authority_revalidated"],
        "crash_safe_local_finalize_supported_when_all_three_pass_receipts_exist": dry["crash_safe_local_finalize_supported_when_all_three_pass_receipts_exist"],
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "dry_output_path": str(args.dry_output),
    }, ensure_ascii=False, indent=2))
    return 0


def run_paid(args: argparse.Namespace) -> int:
    head, clean = _git_context()
    bound = _load_bound(args)
    dry = _read(args.dry_output, label="Rebuttal runtime dry artifact")
    dry_hash = verify_dry_artifact(dry, expected_code_commit_sha=head, bound=bound)
    if args.paid_output.exists():
        raise B4ReopenRebuttalRuntimeError(f"paid Rebuttal output already exists: {args.paid_output}")

    auth: dict[str, Any]
    if args.authorization_output.exists():
        auth = _read(args.authorization_output, label="existing Rebuttal paid authorization")
        _verify_existing_auth(auth, head=head, dry_hash=dry_hash, args=args)
    else:
        created = _utc_now()
        run_id = _run_id(created, dry_hash)
        auth = build_paid_authorization(
            code_commit_sha=head,
            git_worktree_clean=clean,
            created_at_utc=created,
            run_id=run_id,
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            approve_cost_artifact_hash=args.approve_cost_artifact_hash,
            approve_request_manifest_hash=args.approve_request_manifest_hash,
            approve_dry_artifact_hash=args.approve_runner_dry_artifact_hash,
            approve_max_usd=args.approve_max_usd,
            dry_artifact=dry,
            bound=bound,
            receipt_journal_path=str(args.receipt_journal),
        )
        write_json_fsync_new(args.authorization_output, auth)

    auth_hash = str(auth["artifact_hash"])
    events = read_jsonl(args.receipt_journal)
    if events:
        finalize = durable_finalize_inputs_from_journal(
            events=events,
            bound=bound,
            authorization_hash=auth_hash,
        )
        if finalize is not None:
            receipt_hashes, records, actual_cost = finalize
            freeze = build_freeze_artifact(
                code_commit_sha=head,
                run_id=str(auth["run_id"]),
                authorization_hash=auth_hash,
                dry_hash=dry_hash,
                receipt_hashes=receipt_hashes,
                processed_records=records,
                actual_rebuttal_cost_usd=actual_cost,
                finalized_from_durable_receipts_without_provider_dispatch=True,
                bound=bound,
            )
            write_json_fsync_new(args.paid_output, freeze)
            print(json.dumps(freeze, ensure_ascii=False, indent=2))
            print("LOCAL_FINALIZE_ONLY=YES")
            print("NEW_PROVIDER_DISPATCHES=0")
            return 0
        blocked = _blocked_from_existing_journal(
            args=args,
            head=head,
            auth=auth,
            dry_hash=dry_hash,
            events=events,
        )
        write_json_fsync_new(args.paid_output, blocked)
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
        print("EXISTING_CONSUMED_JOURNAL=YES")
        print("NEW_PROVIDER_DISPATCHES=0")
        return 2

    if args.authorization_output.exists() and not args.receipt_journal.exists():
        print("PAID_AUTHORIZATION_EXISTS_WITH_ZERO_DISPATCH_ATTEMPTS=YES", file=sys.stderr, flush=True)

    from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

    api_key = load_openai_api_key()
    cumulative_cost = Decimal("0")
    receipt_hashes: list[str] = []
    records: list[Mapping[str, Any]] = []
    model_calls = 0

    for item in bound.plan:
        started = _utc_now()
        attempt = build_attempt_event(
            run_id=str(auth["run_id"]),
            item=item,
            authorization_hash=auth_hash,
            started_at_utc=started,
        )
        append_jsonl_fsync(args.receipt_journal, attempt)
        print(
            f"[B4 REOPEN REBUTTAL] PROVIDER_DISPATCH_ATTEMPT index={item.dispatch_index} candidate={item.candidate_id}",
            file=sys.stderr,
            flush=True,
        )
        tracker = TrackingTransport(StdlibResponsesTransport())
        run = execute_rebuttal_runtime_item_once(
            item,
            initial_freeze=bound.recovered_initial_freeze,
            api_key=api_key,
            transport=tracker,
            pricing=bound.pricing,
            frozen_at=datetime.now(UTC),
        )
        finished = _utc_now()
        provider_received = tracker.provider_responses == 1 and run.model_calls == 1
        receipt = build_result_receipt(
            run_id=str(auth["run_id"]),
            item=item,
            authorization_hash=auth_hash,
            attempt_hash=str(attempt["event_hash"]),
            started_at_utc=started,
            finished_at_utc=finished,
            provider_response_received=provider_received,
            run=run,
        )
        append_jsonl_fsync(args.receipt_journal, receipt)
        receipt_hashes.append(str(receipt["receipt_hash"]))
        if provider_received:
            model_calls += 1
        if run.actual_cost_usd is not None and run.cost_receipt_status == "COMPLETE":
            cumulative_cost += run.actual_cost_usd

        reason: str | None = None
        if not provider_received:
            reason = "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH"
        elif run.cost_receipt_status != "COMPLETE" or run.actual_cost_usd is None:
            reason = "INCOMPLETE_COST_RECEIPT"
        elif cumulative_cost > EXPECTED_COST_CEILING_USD:
            reason = "APPROVED_COST_CEILING_EXCEEDED"
        elif run.validation_status != "PASS" or run.processed_record is None:
            reason = "REBUTTAL_VALIDATION_FAILED"

        if reason is not None:
            blocked = build_blocked_artifact(
                code_commit_sha=head,
                run_id=str(auth["run_id"]),
                authorization_hash=auth_hash,
                dry_hash=dry_hash,
                reason=reason + ("; " + str(run.validation_error) if run.validation_error else ""),
                dispatch_attempts=item.dispatch_index,
                known_model_calls=model_calls,
                known_rebuttal_cost_usd=cumulative_cost,
                receipt_hashes=receipt_hashes,
                successful_processed_records=records,
            )
            write_json_fsync_new(args.paid_output, blocked)
            print(json.dumps(blocked, ensure_ascii=False, indent=2))
            return 2
        records.append(run.processed_record)

    if len(receipt_hashes) != EXPECTED_CALLS or len(records) != EXPECTED_CALLS or model_calls != EXPECTED_CALLS:
        raise B4ReopenRebuttalRuntimeError("successful Rebuttal runtime must complete exactly three calls/receipts/records")
    freeze = build_freeze_artifact(
        code_commit_sha=head,
        run_id=str(auth["run_id"]),
        authorization_hash=auth_hash,
        dry_hash=dry_hash,
        receipt_hashes=receipt_hashes,
        processed_records=records,
        actual_rebuttal_cost_usd=cumulative_cost,
        finalized_from_durable_receipts_without_provider_dispatch=False,
        bound=bound,
    )
    write_json_fsync_new(args.paid_output, freeze)
    print(json.dumps(freeze, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = _args()
    try:
        if args.execute_paid_rebuttal:
            return run_paid(args)
        return run_dry(args)
    except Exception as exc:
        print(
            f"B4 reopen Rebuttal runtime failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
