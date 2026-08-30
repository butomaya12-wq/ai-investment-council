from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from time import perf_counter_ns
import uuid

from aic.domain.canonical import canonical_sha256
from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key
from aic.council.reopen_initial_recovery_runtime import (
    AUTH_STATUS,
    DRY_STATUS,
    RECOVERY_PLAN_HASH,
    append_jsonl_fsync,
    build_attempt_event,
    build_dry_artifact,
    build_paid_authorization,
    build_recovered_freeze,
    build_recovery_blocked,
    build_result_receipt,
    load_recovery_context,
    process_reopen_initial_provider_response,
    verify_dry_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / ".aic-runtime"
RECOVERY_PLAN = RUNTIME / "b4_reopen_initial_unknown_dispatch_recovery_plan_zero_call_v0_1.json"
SOURCE_BLOCKED = RUNTIME / "b4_reopen_initial_council_freeze_v0_1.json"
SOURCE_JOURNAL = RUNTIME / "b4_reopen_initial_runtime_paid_receipts_v0_1.jsonl"
SOURCE_AUTH = RUNTIME / "b4_reopen_initial_runtime_paid_authorization_v0_1.json"
COST = RUNTIME / "b4_reopen_production_cost_preflight_zero_call_v0_1.json"
LIFECYCLE = RUNTIME / "b4_reopen_lifecycle_plan_zero_call_v0_2.json"
OVERLAY = RUNTIME / "b4_reopen_input_overlay_zero_call_v0_1.json"
CLOSURE = RUNTIME / "b3_reopen_remaining_gaps_closure_zero_call_v0_2.json"
FREEZE = RUNTIME / "b4_council_input_freeze.json"
RECON = RUNTIME / "b3_selected_model_reconciliation.json"
HANDOFF = REPO_ROOT / "config/event/b2_real_event_handoff_v0_1.json"
INITIAL_AUTHORITY = REPO_ROOT / "config/event/b4_initial_selected_model_v1.json"
PRICING = REPO_ROOT / "config/event/openai_text_pricing_2026_08_30.json"
DRY = RUNTIME / "b4_reopen_initial_recovery_runtime_dry_v0_1.json"
PAID_AUTH = RUNTIME / "b4_reopen_initial_recovery_paid_authorization_v0_1.json"
PAID_JOURNAL = RUNTIME / "b4_reopen_initial_recovery_paid_receipts_v0_1.jsonl"
OUTPUT = RUNTIME / "b4_reopen_initial_council_freeze_recovered_v0_1.json"


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def current_head() -> str:
    import subprocess
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def worktree_clean() -> bool:
    import subprocess
    return subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True) == ""


def write_json_fsync(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} root must be object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-recovery", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--approve-recovery-plan-artifact-hash")
    parser.add_argument("--approve-recovery-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    return parser.parse_args()


def load_context(head: str):
    return load_recovery_context(
        code_commit_sha=head,
        recovery_plan_path=RECOVERY_PLAN,
        source_blocked_path=SOURCE_BLOCKED,
        source_journal_path=SOURCE_JOURNAL,
        cost_preflight_path=COST,
        lifecycle_path=LIFECYCLE,
        overlay_path=OVERLAY,
        closure_path=CLOSURE,
        freeze_path=FREEZE,
        reconciliation_path=RECON,
        handoff_path=HANDOFF,
        initial_authority_path=INITIAL_AUTHORITY,
        pricing_path=PRICING,
        source_authorization_path=SOURCE_AUTH,
    )


def run_dry(head: str) -> int:
    if DRY.exists():
        raise RuntimeError(f"recovery dry artifact already exists: {DRY}")
    if PAID_AUTH.exists() or PAID_JOURNAL.exists() or OUTPUT.exists():
        raise RuntimeError("recovery paid evidence already exists; do not regenerate dry artifact")
    recovery_plan, _blocked, plan, _pricing, _events = load_context(head)
    item = plan[8]
    artifact = build_dry_artifact(code_commit_sha=head, recovery_plan=recovery_plan, item=item)
    write_json_fsync(DRY, artifact)
    print(json.dumps({
        "status": artifact["status"],
        "artifact_hash": artifact["artifact_hash"],
        "source_recovery_plan_artifact_hash": artifact["source_recovery_plan_artifact_hash"],
        "recovery_request_hash": artifact["recovery_request_hash"],
        "recovery_paid_calls_max": 1,
        "recovery_cost_ceiling_usd": artifact["recovery_cost_ceiling_usd"],
        "aggregate_initial_spend_upper_bound_after_one_recovery_usd": artifact["aggregate_initial_spend_upper_bound_after_one_recovery_usd"],
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "dry_output_path": str(DRY.relative_to(REPO_ROOT)),
    }, indent=2))
    return 0


def verify_existing_auth(auth: dict, *, head: str, dry_hash: str, args: argparse.Namespace) -> None:
    observed = auth.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(auth, exclude_fields=("artifact_hash",)):
        raise RuntimeError("existing recovery authorization self-hash mismatch")
    if auth.get("status") != AUTH_STATUS or auth.get("runner_code_commit_sha") != head:
        raise RuntimeError("existing recovery authorization identity drift")
    approval = auth.get("owner_approval")
    if not isinstance(approval, dict):
        raise RuntimeError("existing recovery authorization approval missing")
    expected = {
        "owner_approval_id": args.owner_approval_id,
        "owner_approval_at_utc": args.owner_approval_at_utc,
        "approved_recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
        "approved_recovery_dry_artifact_hash": dry_hash,
        "approved_cost_ceiling_usd": args.approve_max_usd,
    }
    for key, value in expected.items():
        if approval.get(key) != value:
            raise RuntimeError(f"existing recovery authorization drift: {key}")


def run_paid(head: str, args: argparse.Namespace) -> int:
    if not worktree_clean():
        raise RuntimeError("paid recovery requires clean worktree")
    if not DRY.exists():
        raise RuntimeError("recovery dry artifact missing")
    if PAID_JOURNAL.exists() or OUTPUT.exists():
        raise RuntimeError("recovery dispatch evidence/output already exists; DO NOT RERUN")
    recovery_plan, source_blocked, plan, pricing, source_events = load_context(head)
    item = plan[8]
    dry = load_json(DRY)
    dry_hash = verify_dry_artifact(dry, code_commit_sha=head, item=item)
    required = (
        args.owner_approval_id,
        args.owner_approval_at_utc,
        args.approve_recovery_plan_artifact_hash,
        args.approve_recovery_dry_artifact_hash,
        args.approve_max_usd,
    )
    if any(value is None for value in required):
        raise RuntimeError("all paid recovery approval arguments are required")

    if PAID_AUTH.exists():
        auth = load_json(PAID_AUTH)
        verify_existing_auth(auth, head=head, dry_hash=dry_hash, args=args)
    else:
        run_id = "AIC-B4-REOPEN-INITIAL-RECOVERY-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
        auth = build_paid_authorization(
            code_commit_sha=head,
            dry_artifact=dry,
            item=item,
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            approve_recovery_plan_hash=args.approve_recovery_plan_artifact_hash,
            approve_dry_hash=args.approve_recovery_dry_artifact_hash,
            approve_max_usd=args.approve_max_usd,
            created_at_utc=now_utc(),
            run_id=run_id,
            journal_path=str(PAID_JOURNAL.relative_to(REPO_ROOT)),
        )
        write_json_fsync(PAID_AUTH, auth)
    run_id = str(auth["run_id"])
    auth_hash = str(auth["artifact_hash"])

    # Key is intentionally loaded only after durable authorization exists.
    api_key = load_openai_api_key()
    started = now_utc()
    attempt = build_attempt_event(run_id=run_id, item=item, authorization_hash=auth_hash, started_at_utc=started)
    append_jsonl_fsync(PAID_JOURNAL, attempt)
    attempt_hash = str(attempt["event_hash"])

    raw_response = None
    processed = None
    provider_received = False
    validation_error = None
    start_ns = perf_counter_ns()
    try:
        raw_response = StdlibResponsesTransport().post(payload=item.request.request_payload, api_key=api_key)
        provider_received = True
        latency_ms = max(0, (perf_counter_ns() - start_ns) // 1_000_000)
        processed = process_reopen_initial_provider_response(
            item,
            raw_response=raw_response,
            latency_ms=latency_ms,
            frozen_at=datetime.now(UTC),
            pricing=pricing,
        )
    except Exception as exc:
        latency_ms = max(0, (perf_counter_ns() - start_ns) // 1_000_000)
        validation_error = ("LOCAL_VALIDATION:" if provider_received else "UNKNOWN_PROVIDER_DISPATCH:") + type(exc).__name__

    finished = now_utc()
    receipt = build_result_receipt(
        run_id=run_id,
        item=item,
        authorization_hash=auth_hash,
        attempt_hash=attempt_hash,
        started_at_utc=started,
        finished_at_utc=finished,
        provider_response_received=provider_received,
        raw_response=raw_response,
        processed_record=processed,
        validation_error=validation_error,
    )
    append_jsonl_fsync(PAID_JOURNAL, receipt)
    receipt_hash = str(receipt["receipt_hash"])

    if processed is None:
        blocked = build_recovery_blocked(
            code_commit_sha=head,
            run_id=run_id,
            auth_hash=auth_hash,
            dry_hash=dry_hash,
            attempt_hash=attempt_hash,
            receipt_hash=receipt_hash,
            reason=str(validation_error),
        )
        write_json_fsync(OUTPUT, blocked)
        print(json.dumps(blocked, indent=2))
        return 2

    frozen = build_recovered_freeze(
        code_commit_sha=head,
        recovery_run_id=run_id,
        recovery_authorization_hash=auth_hash,
        recovery_dry_hash=dry_hash,
        source_blocked=source_blocked,
        source_events=source_events,
        recovery_attempt_hash=attempt_hash,
        recovery_receipt_hash=receipt_hash,
        recovery_processed_record=processed,
    )
    write_json_fsync(OUTPUT, frozen)
    print(json.dumps(frozen, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    os.chdir(REPO_ROOT)
    head = current_head()
    if args.execute_paid_recovery:
        return run_paid(head, args)
    return run_dry(head)


if __name__ == "__main__":
    raise SystemExit(main())
