from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.council.reopen_rebuttal_credential_probe import (
    AUTH_STATUS,
    AUTH_VERSION,
    B4ReopenRebuttalCredentialProbeError,
    DRY_STATUS,
    DRY_VERSION,
    ENDPOINT,
    EVENT_VERSION,
    FINAL_VERSION,
    MODEL_ID,
    RECEIPT_VERSION,
    build_attempt_event,
    build_authorization,
    build_dry_artifact,
    build_final_artifact,
    build_result_receipt,
    load_recovery_plan,
    probe_model_metadata,
    verify_dry_artifact,
    verify_recovery_plan,
)


EXPECTED_BRANCH = "hackathon/alpaca-2026"
DEFAULT_RECOVERY_PLAN = Path(".aic-runtime/b4_reopen_rebuttal_auth_rejection_recovery_plan_zero_call_v0_1.json")
DEFAULT_DRY = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_dry_v0_1.json")
DEFAULT_AUTH = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_authorization_v0_1.json")
DEFAULT_JOURNAL = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_receipts_v0_1.jsonl")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_result_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-plan", type=Path, default=DEFAULT_RECOVERY_PLAN)
    parser.add_argument("--dry-output", type=Path, default=DEFAULT_DRY)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute-probe", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--approve-recovery-plan-artifact-hash")
    parser.add_argument("--approve-dry-artifact-hash")
    return parser.parse_args()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _git_context() -> str:
    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise B4ReopenRebuttalCredentialProbeError(f"expected branch {EXPECTED_BRANCH}, got {branch}")
    if _git("status", "--porcelain"):
        raise B4ReopenRebuttalCredentialProbeError("credential probe requires clean git worktree")
    head = _git("rev-parse", "HEAD")
    if len(head) != 40:
        raise B4ReopenRebuttalCredentialProbeError("exact git HEAD missing")
    return head


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _write_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalCredentialProbeError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalCredentialProbeError(f"{label} root must be object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise B4ReopenRebuttalCredentialProbeError("credential probe journal row must be object")
            rows.append(value)
    return rows


def _verify_existing_auth(
    auth: Mapping[str, Any], *, head: str, plan_hash: str, dry_hash: str, args: argparse.Namespace
) -> str:
    observed = auth.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(auth, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalCredentialProbeError("existing credential probe authorization self-hash mismatch")
    exact = {
        "artifact_version": AUTH_VERSION,
        "status": AUTH_STATUS,
        "code_commit_sha": head,
        "owner_approval_id": args.owner_approval_id,
        "owner_approval_at_utc": args.owner_approval_at_utc,
        "source_recovery_plan_artifact_hash": plan_hash,
        "runner_dry_artifact_hash": dry_hash,
        "probe_endpoint": ENDPOINT,
        "probe_model_id": MODEL_ID,
        "provider_reads_max": 1,
        "model_calls_max": 0,
        "responses_generation_calls_max": 0,
        "generation_dispatch_authorized": False,
        "judge_authorized": False,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if auth.get(field) != expected:
            raise B4ReopenRebuttalCredentialProbeError(f"existing credential probe authorization drift: {field}")
    return observed


def _finalize_existing_journal(
    *,
    head: str,
    plan_hash: str,
    dry_hash: str,
    auth_hash: str,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(events) != 2:
        return None
    attempt, receipt = events
    attempt_hash = attempt.get("event_hash")
    if not isinstance(attempt_hash, str) or attempt_hash != canonical_sha256(attempt, exclude_fields=("event_hash",)):
        raise B4ReopenRebuttalCredentialProbeError("credential probe attempt self-hash mismatch")
    if attempt.get("event_version") != EVENT_VERSION or attempt.get("event_type") != "CREDENTIAL_PROBE_HTTP_ATTEMPT":
        raise B4ReopenRebuttalCredentialProbeError("credential probe attempt version/type drift")
    if attempt.get("paid_authorization_artifact_hash") != auth_hash:
        raise B4ReopenRebuttalCredentialProbeError("credential probe attempt authorization drift")
    receipt_hash = receipt.get("receipt_hash")
    if not isinstance(receipt_hash, str) or receipt_hash != canonical_sha256(receipt, exclude_fields=("receipt_hash",)):
        raise B4ReopenRebuttalCredentialProbeError("credential probe receipt self-hash mismatch")
    if receipt.get("receipt_version") != RECEIPT_VERSION or receipt.get("event_type") != "CREDENTIAL_PROBE_HTTP_RESULT":
        raise B4ReopenRebuttalCredentialProbeError("credential probe receipt version/type drift")
    if receipt.get("paid_authorization_artifact_hash") != auth_hash or receipt.get("attempt_event_hash") != attempt_hash:
        raise B4ReopenRebuttalCredentialProbeError("credential probe receipt lineage drift")
    return build_final_artifact(
        code_commit_sha=head,
        recovery_plan_hash=plan_hash,
        dry_hash=dry_hash,
        authorization_hash=auth_hash,
        attempt_hash=attempt_hash,
        receipt=receipt,
    )


def run_dry(args: argparse.Namespace) -> int:
    head = _git_context()
    plan = load_recovery_plan(args.recovery_plan)
    if args.dry_output.exists():
        raise B4ReopenRebuttalCredentialProbeError(f"credential probe dry already exists: {args.dry_output}")
    dry = build_dry_artifact(code_commit_sha=head, recovery_plan=plan)
    _write_json_new(args.dry_output, dry)
    print(json.dumps({
        "status": dry["status"],
        "artifact_version": dry["artifact_version"],
        "artifact_hash": dry["artifact_hash"],
        "code_commit_sha": dry["code_commit_sha"],
        "source_recovery_plan_artifact_hash": dry["source_recovery_plan_artifact_hash"],
        "probe_http_method": dry["probe_http_method"],
        "probe_endpoint": dry["probe_endpoint"],
        "probe_model_id": dry["probe_model_id"],
        "provider_reads_max_if_later_approved": dry["provider_reads_max_if_later_approved"],
        "model_calls_max": 0,
        "responses_generation_calls_max": 0,
        "probe_provider_read_authorized": False,
        "generation_dispatch_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "dry_output_path": str(args.dry_output),
    }, ensure_ascii=False, indent=2))
    return 0


def run_probe(args: argparse.Namespace) -> int:
    head = _git_context()
    plan = load_recovery_plan(args.recovery_plan)
    plan_hash = verify_recovery_plan(plan)
    dry = _read_object(args.dry_output, label="credential probe dry")
    dry_hash = verify_dry_artifact(dry, expected_code_commit_sha=head, recovery_plan=plan)
    if args.output.exists():
        raise B4ReopenRebuttalCredentialProbeError(f"credential probe output already exists: {args.output}")

    if args.authorization_output.exists():
        auth = _read_object(args.authorization_output, label="existing credential probe authorization")
        auth_hash = _verify_existing_auth(
            auth, head=head, plan_hash=plan_hash, dry_hash=dry_hash, args=args
        )
    else:
        auth = build_authorization(
            code_commit_sha=head,
            created_at_utc=_utc_now(),
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            approve_recovery_plan_hash=args.approve_recovery_plan_artifact_hash,
            approve_dry_hash=args.approve_dry_artifact_hash,
            recovery_plan=plan,
            dry_artifact=dry,
            journal_path=str(args.receipt_journal),
        )
        _write_json_new(args.authorization_output, auth)
        auth_hash = str(auth["artifact_hash"])

    events = _read_jsonl(args.receipt_journal)
    if events:
        final = _finalize_existing_journal(
            head=head,
            plan_hash=plan_hash,
            dry_hash=dry_hash,
            auth_hash=auth_hash,
            events=events,
        )
        if final is None:
            raise B4ReopenRebuttalCredentialProbeError(
                "existing credential probe journal is incomplete; no new provider read authorized"
            )
        _write_json_new(args.output, final)
        print(json.dumps(final, ensure_ascii=False, indent=2))
        print("LOCAL_FINALIZE_ONLY=YES")
        print("NEW_PROVIDER_READS=0")
        return 0 if final["status"].endswith("_PASS") else 2

    from aic.research.runtime import load_openai_api_key

    api_key = load_openai_api_key()
    attempt = build_attempt_event(authorization_hash=auth_hash, started_at_utc=_utc_now())
    _append_jsonl(args.receipt_journal, attempt)
    print("CREDENTIAL_PROBE_HTTP_ATTEMPT=1", flush=True)
    probe_result = probe_model_metadata(api_key=api_key)
    receipt = build_result_receipt(
        authorization_hash=auth_hash,
        attempt_hash=str(attempt["event_hash"]),
        finished_at_utc=_utc_now(),
        probe_result=probe_result,
    )
    _append_jsonl(args.receipt_journal, receipt)
    final = build_final_artifact(
        code_commit_sha=head,
        recovery_plan_hash=plan_hash,
        dry_hash=dry_hash,
        authorization_hash=auth_hash,
        attempt_hash=str(attempt["event_hash"]),
        receipt=receipt,
    )
    _write_json_new(args.output, final)
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0 if final["status"].endswith("_PASS") else 2


def main() -> int:
    args = _args()
    try:
        return run_probe(args) if args.execute_probe else run_dry(args)
    except Exception as exc:
        print(f"B4 reopen Rebuttal credential probe failed closed: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
