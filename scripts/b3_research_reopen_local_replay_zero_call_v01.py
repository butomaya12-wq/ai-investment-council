from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_judge_local_replay_v01 import (
    JudgeReopenLocalReplayError,
    build_local_replay,
    verify_local_replay,
)


DEFAULT_INVENTORY = Path(".aic-runtime/b3_research_reopen_existing_evidence_inventory_zero_call_v0_1.json")
DEFAULT_HISTORICAL_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_JUDGE = Path(".aic-runtime/b4_reopen_judge_production_result_v0_2.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_research_reopen_local_replay_zero_call_v0_1.json")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read(path: Path, *, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JudgeReopenLocalReplayError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise JudgeReopenLocalReplayError(f"{label} root must be object")
    return value


def _write_exclusive(path: Path, payload: dict) -> None:
    if path.exists():
        existing = _read(path, label="existing local replay output")
        if existing == payload:
            raise JudgeReopenLocalReplayError("local replay output already exists and matches; do not rerun")
        raise JudgeReopenLocalReplayError("local replay output already exists with different content; do not overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    try:
        head = _git("rev-parse", "HEAD")
        branch = _git("branch", "--show-current")
        if branch != "hackathon/alpaca-2026":
            raise JudgeReopenLocalReplayError("runner requires hackathon/alpaca-2026 branch")
        if _git("status", "--porcelain"):
            raise JudgeReopenLocalReplayError("runner requires clean worktree")
        if DEFAULT_OUTPUT.exists():
            raise JudgeReopenLocalReplayError("local replay output already exists; do not rerun")

        artifact = build_local_replay(
            inventory=_read(DEFAULT_INVENTORY, label="existing-evidence inventory"),
            historical_closure=_read(DEFAULT_HISTORICAL_CLOSURE, label="historical closure"),
            handoff=_read(DEFAULT_HANDOFF, label="B2 handoff"),
            judge_result=_read(DEFAULT_JUDGE, label="Judge result"),
            code_commit_sha=head,
        )
        artifact["output_path"] = str(DEFAULT_OUTPUT)
        artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
        verify_local_replay(artifact, expected_code_commit_sha=head)
        _write_exclusive(DEFAULT_OUTPUT, artifact)

        safe = {
            "status": artifact["status"],
            "artifact_hash": artifact["artifact_hash"],
            "code_commit_sha": artifact["code_commit_sha"],
            "source_existing_evidence_inventory_hash": artifact["source_existing_evidence_inventory_hash"],
            "local_replay_target_count": artifact["local_replay_target_count"],
            "local_replay_target_ids": artifact["local_replay_target_ids"],
            "local_replay_partial_target_count": artifact["local_replay_partial_target_count"],
            "local_replay_partial_target_ids": artifact["local_replay_partial_target_ids"],
            "local_replay_resolved_target_count": artifact["local_replay_resolved_target_count"],
            "newly_escalated_external_read_target_count": artifact["newly_escalated_external_read_target_count"],
            "newly_escalated_external_read_target_ids": artifact["newly_escalated_external_read_target_ids"],
            "residual_external_read_target_count": artifact["residual_external_read_target_count"],
            "residual_external_read_target_ids": artifact["residual_external_read_target_ids"],
            "msft_point_in_time_pe": artifact["deterministic_context"]["valuation_comparison"]["msft"]["price_to_reported_annual_gaap_diluted_eps"],
            "msft_earnings_yield": artifact["deterministic_context"]["valuation_comparison"]["msft"]["earnings_yield_from_same_multiple"],
            "meta_point_in_time_pe": artifact["deterministic_context"]["valuation_comparison"]["meta"]["price_to_reported_annual_gaap_diluted_eps"],
            "meta_earnings_yield": artifact["deterministic_context"]["valuation_comparison"]["meta"]["earnings_yield_from_same_multiple"],
            "msft_pe_premium_vs_meta_ratio": artifact["deterministic_context"]["valuation_comparison"]["derived_relative_view"]["msft_pe_premium_vs_meta_ratio"],
            "meta_direct_position_exposure_at_b2_cutoff": artifact["deterministic_context"]["meta_portfolio_context"]["direct_position_exposure"],
            "provider_reads_authorized": artifact["provider_reads_authorized"],
            "model_calls_authorized": artifact["model_calls_authorized"],
            "broad_b3_rerun_authorized": artifact["broad_b3_rerun_authorized"],
            "final_decision_created": artifact["final_decision_created"],
            "b5_handoff_created": artifact["b5_handoff_created"],
            "next_gate": artifact["next_gate"],
            "write_state": "CREATED",
        }
        print(json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (JudgeReopenLocalReplayError, subprocess.CalledProcessError) as exc:
        print(f"JudgeReopenLocalReplayError: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
