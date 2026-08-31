from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research.runtime import load_openai_api_key
from aic.council import reopen_rebuttal_credential_probe as v01
from aic.council import reopen_rebuttal_credential_probe_v02 as v02


EXPECTED_BRANCH = "hackathon/alpaca-2026"
DEFAULT_RECOVERY_PLAN = Path(".aic-runtime/b4_reopen_rebuttal_auth_rejection_recovery_plan_zero_call_v0_1.json")
DEFAULT_SOURCE_V01_RESULT = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_result_v0_1.json")
DEFAULT_DRY = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_dry_v0_2.json")
DEFAULT_AUTH = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_authorization_v0_2.json")
DEFAULT_JOURNAL = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_receipts_v0_2.jsonl")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_rebuttal_credential_probe_result_v0_2.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-plan", type=Path, default=DEFAULT_RECOVERY_PLAN)
    parser.add_argument("--source-v01-result", type=Path, default=DEFAULT_SOURCE_V01_RESULT)
    parser.add_argument("--expected-source-v01-result-hash", required=True)
    parser.add_argument("--dry-output", type=Path, default=DEFAULT_DRY)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute-probe", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--approve-recovery-plan-artifact-hash")
    parser.add_argument("--approve-source-v01-result-artifact-hash")
    parser.add_argument("--approve-dry-artifact-hash")
    return parser.parse_args()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _git_context() -> str:
    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(
            f"expected branch {EXPECTED_BRANCH}, got {branch}"
        )
    if _git("status", "--porcelain"):
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(
            "credential probe V02 requires clean git worktree"
        )
    head = _git("rev-parse", "HEAD")
    if len(head) != 40:
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("exact git HEAD missing")
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
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(f"{label} root must be object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise v02.B4ReopenRebuttalCredentialProbeV02Error(
                    "credential probe V02 journal row must be object"
                )
            rows.append(value)
    return rows


def _verify_existing_auth(
    auth: Mapping[str, Any],
    *,
    head: str,
    recovery_hash: str,
    source_hash: str,
    dry_hash: str,
    fingerprint: str,
    args: argparse.Namespace,
) -> str:
    observed = auth.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(auth, exclude_fields=("artifact_hash",)):
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(
            "existing credential probe V02 authorization self-hash mismatch"
        )
    exact = {
        "artifact_version": v02.AUTH_VERSION,
        "status": v02.AUTH_STATUS,
        "code_commit_sha": head,
        "owner_approval_id": args.owner_approval_id,
        "owner_approval_at_utc": args.owner_approval_at_utc,
        "source_recovery_plan_artifact_hash": recovery_hash,
        "source_failed_v01_result_artifact_hash": source_hash,
        "runner_dry_artifact_hash": dry_hash,
        "replacement_credential_fingerprint_sha256": fingerprint,
        "replacement_credential_secret_persisted": False,
        "probe_endpoint": v02.ENDPOINT,
        "probe_model_id": v02.MODEL_ID,
        "provider_reads_max": 1,
        "model_calls_max": 0,
        "responses_generation_calls_max": 0,
        "generation_dispatch_authorized": False,
        "judge_authorized": False,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if auth.get(field) != expected:
            raise v02.B4ReopenRebuttalCredentialProbeV02Error(
                f"existing credential probe V02 authorization drift: {field}"
            )
    return observed


def _finalize_existing_journal(
    *,
    head: str,
    recovery_hash: str,
    source_hash: str,
    dry_hash: str,
    auth_hash: str,
    fingerprint: str,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(events) != 2:
        return None
    attempt, receipt = events
    attempt_hash = attempt.get("event_hash")
    if not isinstance(attempt_hash, str) or attempt_hash != canonical_sha256(attempt, exclude_fields=("event_hash",)):
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 attempt self-hash mismatch")
    if attempt.get("event_version") != v02.EVENT_VERSION or attempt.get("event_type") != "CREDENTIAL_PROBE_HTTP_ATTEMPT":
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 attempt version/type drift")
    if attempt.get("paid_authorization_artifact_hash") != auth_hash:
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 attempt authorization drift")
    if attempt.get("replacement_credential_fingerprint_sha256") != fingerprint:
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 attempt credential drift")
    receipt_hash = receipt.get("receipt_hash")
    if not isinstance(receipt_hash, str) or receipt_hash != canonical_sha256(receipt, exclude_fields=("receipt_hash",)):
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 receipt self-hash mismatch")
    if receipt.get("receipt_version") != v02.RECEIPT_VERSION or receipt.get("event_type") != "CREDENTIAL_PROBE_HTTP_RESULT":
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 receipt version/type drift")
    if receipt.get("paid_authorization_artifact_hash") != auth_hash or receipt.get("attempt_event_hash") != attempt_hash:
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 receipt lineage drift")
    if receipt.get("replacement_credential_fingerprint_sha256") != fingerprint:
        raise v02.B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 receipt credential drift")
    return v02.build_final_artifact(
        code_commit_sha=head,
        recovery_plan_hash=recovery_hash,
        source_failed_result_hash=source_hash,
        dry_hash=dry_hash,
        authorization_hash=auth_hash,
        attempt_hash=attempt_hash,
        receipt=receipt,
    )


def run_dry(args: argparse.Namespace) -> int:
    head = _git_context()
    recovery = v01.load_recovery_plan(args.recovery_plan)
    source = _read_object(args.source_v01_result, label="source credential probe V01 result")
    api_key = load_openai_api_key()
    if args.dry_output.exists():
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(
            f"credential probe V02 dry already exists: {args.dry_output}"
        )
    dry = v02.build_dry_artifact(
        code_commit_sha=head,
        recovery_plan=recovery,
        source_failed_result=source,
        expected_source_failed_result_hash=args.expected_source_v01_result_hash,
        api_key=api_key,
    )
    _write_json_new(args.dry_output, dry)
    print(json.dumps({
        "status": dry["status"],
        "artifact_version": dry["artifact_version"],
        "artifact_hash": dry["artifact_hash"],
        "code_commit_sha": dry["code_commit_sha"],
        "source_recovery_plan_artifact_hash": dry["source_recovery_plan_artifact_hash"],
        "source_failed_v01_result_artifact_hash": dry["source_failed_v01_result_artifact_hash"],
        "replacement_credential_fingerprint_sha256": dry["replacement_credential_fingerprint_sha256"],
        "replacement_credential_secret_persisted": False,
        "credential_hygiene_status": dry["credential_hygiene_status"],
        "probe_http_method": dry["probe_http_method"],
        "probe_endpoint": dry["probe_endpoint"],
        "probe_model_id": dry["probe_model_id"],
        "provider_reads_max_if_later_approved": 1,
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
    recovery = v01.load_recovery_plan(args.recovery_plan)
    recovery_hash = v01.verify_recovery_plan(recovery)
    source = _read_object(args.source_v01_result, label="source credential probe V01 result")
    source_hash = v02.verify_source_failed_result(
        source,
        expected_artifact_hash=args.expected_source_v01_result_hash,
    )
    api_key = load_openai_api_key()
    fingerprint = v02.credential_fingerprint_sha256(api_key)
    dry = _read_object(args.dry_output, label="credential probe V02 dry")
    dry_hash = v02.verify_dry_artifact(
        dry,
        expected_code_commit_sha=head,
        recovery_plan=recovery,
        source_failed_result=source,
        expected_source_failed_result_hash=source_hash,
        api_key=api_key,
    )
    if args.output.exists():
        raise v02.B4ReopenRebuttalCredentialProbeV02Error(
            f"credential probe V02 output already exists: {args.output}"
        )

    if args.authorization_output.exists():
        auth = _read_object(args.authorization_output, label="existing credential probe V02 authorization")
        auth_hash = _verify_existing_auth(
            auth,
            head=head,
            recovery_hash=recovery_hash,
            source_hash=source_hash,
            dry_hash=dry_hash,
            fingerprint=fingerprint,
            args=args,
        )
    else:
        auth = v02.build_authorization(
            code_commit_sha=head,
            created_at_utc=_utc_now(),
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            approve_recovery_plan_hash=args.approve_recovery_plan_artifact_hash,
            approve_source_failed_result_hash=args.approve_source_v01_result_artifact_hash,
            approve_dry_hash=args.approve_dry_artifact_hash,
            recovery_plan=recovery,
            source_failed_result=source,
            dry_artifact=dry,
            api_key=api_key,
            journal_path=str(args.receipt_journal),
        )
        _write_json_new(args.authorization_output, auth)
        auth_hash = str(auth["artifact_hash"])

    events = _read_jsonl(args.receipt_journal)
    if events:
        final = _finalize_existing_journal(
            head=head,
            recovery_hash=recovery_hash,
            source_hash=source_hash,
            dry_hash=dry_hash,
            auth_hash=auth_hash,
            fingerprint=fingerprint,
            events=events,
        )
        if final is None:
            raise v02.B4ReopenRebuttalCredentialProbeV02Error(
                "existing credential probe V02 journal is incomplete; no new provider read authorized"
            )
        _write_json_new(args.output, final)
        print(json.dumps(final, ensure_ascii=False, indent=2))
        print("LOCAL_FINALIZE_ONLY=YES")
        print("NEW_PROVIDER_READS=0")
        return 0 if final["status"].endswith("_PASS") else 2

    attempt = v02.build_attempt_event(
        authorization_hash=auth_hash,
        credential_fingerprint=fingerprint,
        started_at_utc=_utc_now(),
    )
    _append_jsonl(args.receipt_journal, attempt)
    print("CREDENTIAL_PROBE_V02_HTTP_ATTEMPT=1", flush=True)
    probe_result = v02.probe_model_metadata(api_key=api_key)
    receipt = v02.build_result_receipt(
        authorization_hash=auth_hash,
        attempt_hash=str(attempt["event_hash"]),
        credential_fingerprint=fingerprint,
        finished_at_utc=_utc_now(),
        probe_result=probe_result,
    )
    _append_jsonl(args.receipt_journal, receipt)
    final = v02.build_final_artifact(
        code_commit_sha=head,
        recovery_plan_hash=recovery_hash,
        source_failed_result_hash=source_hash,
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
        print(f"B4 reopen Rebuttal credential probe V02 failed closed: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
