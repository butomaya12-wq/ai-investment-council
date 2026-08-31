from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from aic.council.reopen_rebuttal_auth_rejection_recovery import (
    B4ReopenRebuttalAuthRejectionRecoveryError,
    load_and_build_recovery_plan,
)


EXPECTED_BRANCH = "hackathon/alpaca-2026"
DEFAULT_COST = Path(".aic-runtime/b4_reopen_rebuttal_production_cost_preflight_zero_call_v0_2.json")
DEFAULT_DRY = Path(".aic-runtime/b4_reopen_rebuttal_runtime_dry_v0_2.json")
DEFAULT_AUTH = Path(".aic-runtime/b4_reopen_rebuttal_runtime_paid_authorization_v0_2.json")
DEFAULT_JOURNAL = Path(".aic-runtime/b4_reopen_rebuttal_runtime_paid_receipts_v0_2.jsonl")
DEFAULT_BLOCKED = Path(".aic-runtime/b4_reopen_rebuttal_council_freeze_v0_2.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_rebuttal_auth_rejection_recovery_plan_zero_call_v0_1.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _git_context() -> str:
    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise B4ReopenRebuttalAuthRejectionRecoveryError(
            f"expected branch {EXPECTED_BRANCH}, got {branch}"
        )
    if _git("status", "--porcelain"):
        raise B4ReopenRebuttalAuthRejectionRecoveryError("recovery plan requires clean git worktree")
    head = _git("rev-parse", "HEAD")
    if len(head) != 40:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("exact git HEAD missing")
    return head


def _write_new(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    try:
        head = _git_context()
        if DEFAULT_OUTPUT.exists():
            raise B4ReopenRebuttalAuthRejectionRecoveryError(
                f"recovery output already exists: {DEFAULT_OUTPUT}"
            )
        artifact = load_and_build_recovery_plan(
            code_commit_sha=head,
            cost_path=DEFAULT_COST,
            dry_path=DEFAULT_DRY,
            authorization_path=DEFAULT_AUTH,
            journal_path=DEFAULT_JOURNAL,
            blocked_path=DEFAULT_BLOCKED,
        )
        _write_new(DEFAULT_OUTPUT, artifact)
        summary = {
            "status": artifact["status"],
            "artifact_version": artifact["artifact_version"],
            "artifact_hash": artifact["artifact_hash"],
            "code_commit_sha": artifact["code_commit_sha"],
            "source_authority_consumed": artifact["source_authority_consumed"],
            "source_model_calls_known_completed": artifact["source_model_calls_known_completed"],
            "source_successful_rebuttal_processed_records": artifact["source_successful_rebuttal_processed_records"],
            "forensic_classification": artifact["forensic_classification"],
            "forensic_http_status_code": artifact["forensic_http_status_code"],
            "forensic_error_code": artifact["forensic_error_code"],
            "historical_receipt_cost_status": artifact["historical_receipt_cost_status"],
            "rejected_attempt_billing_resolution": artifact["rejected_attempt_billing_resolution"],
            "fresh_rebuttal_outputs_required": artifact["fresh_rebuttal_outputs_required"],
            "fresh_generation_cost_ceiling_usd_if_later_approved": artifact["fresh_generation_cost_ceiling_usd_if_later_approved"],
            "credential_probe_required": artifact["credential_probe_required"],
            "credential_probe_provider_reads_max_if_later_approved": artifact["credential_probe_provider_reads_max_if_later_approved"],
            "generation_dispatch_authorized": artifact["generation_dispatch_authorized"],
            "model_calls": artifact["model_calls"],
            "provider_reads": artifact["provider_reads"],
            "broker_writes": artifact["broker_writes"],
            "alpaca_orders": artifact["alpaca_orders"],
            "live_money": artifact["live_money"],
            "next_gate": artifact["next_gate"],
            "output_path": str(DEFAULT_OUTPUT),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            f"B4 reopen Rebuttal auth-rejection recovery failed closed: {type(exc).__name__}: {exc}"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
