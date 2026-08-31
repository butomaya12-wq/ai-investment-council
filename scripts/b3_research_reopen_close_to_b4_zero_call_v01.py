from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_final_competition_closure_v01 import (
    FinalCompetitionClosureError,
    load_and_build_final_closure,
    verify_final_closure,
)


S00 = Path(".aic-runtime/b3_research_reopen_s00_scope_zero_call_v0_3.json")
LOCAL_REPLAY = Path(".aic-runtime/b3_research_reopen_local_replay_zero_call_v0_1.json")
ORIGINAL_RESULT = Path(".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json")
WIRE_V02_RESULT = Path(".aic-runtime/b3_research_reopen_continuation_wire_repair_result_v0_2.json")
REPAIR_RESULT = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_result_v0_1.json")
REPAIR_AUTH = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_authorization_v0_1.json")
REPAIR_RAW = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_raw_v0_1")
OUTPUT = Path(".aic-runtime/b3_research_reopen_final_competition_closure_zero_call_v0_1.json")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    if path.exists():
        raise FinalCompetitionClosureError(f"output already exists: {path}; do not rerun")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    try:
        branch = _git("branch", "--show-current")
        if branch != "hackathon/alpaca-2026":
            raise FinalCompetitionClosureError("runner requires hackathon/alpaca-2026")
        if _git("status", "--porcelain"):
            raise FinalCompetitionClosureError("runner requires clean worktree")
        head = _git("rev-parse", "HEAD")
        for path in (S00, LOCAL_REPLAY, ORIGINAL_RESULT, WIRE_V02_RESULT, REPAIR_RESULT, REPAIR_AUTH):
            if not path.is_file():
                raise FinalCompetitionClosureError(f"required immutable input missing: {path}")
        if not REPAIR_RAW.is_dir():
            raise FinalCompetitionClosureError("repair raw-response directory missing")
        if OUTPUT.exists():
            raise FinalCompetitionClosureError("closure output already exists; do not rerun")

        artifact = load_and_build_final_closure(
            code_commit_sha=head,
            s00_path=S00,
            local_replay_path=LOCAL_REPLAY,
            original_result_path=ORIGINAL_RESULT,
            wire_v02_result_path=WIRE_V02_RESULT,
            repair_result_path=REPAIR_RESULT,
            repair_authorization_path=REPAIR_AUTH,
            repair_raw_dir=REPAIR_RAW,
        )
        verify_final_closure(artifact, expected_code_commit_sha=head)
        _write_exclusive(OUTPUT, artifact)

        public = {
            "status": artifact["status"],
            "artifact_hash": artifact["artifact_hash"],
            "code_commit_sha": artifact["code_commit_sha"],
            "canonical_reopen_requirement_ids": artifact["canonical_reopen_requirement_ids"],
            "requirement_closures": artifact["requirement_closures"],
            "remaining_canonical_reopen_requirement_ids": artifact["remaining_canonical_reopen_requirement_ids"],
            "canonical_research_reopen_closed": artifact["canonical_research_reopen_closed"],
            "additional_provider_read_required_before_b4": artifact["additional_provider_read_required_before_b4"],
            "rr2_failure_class": artifact["rr2_failure_class"],
            "rr3_second_dispatch_failure_class": artifact["rr3_second_dispatch_failure_class"],
            "new_b4_verdict_required": artifact["new_b4_verdict_required"],
            "provider_reads_authorized": artifact["provider_reads_authorized"],
            "model_calls_authorized": artifact["model_calls_authorized"],
            "final_decision_created": artifact["final_decision_created"],
            "b5_handoff_created": artifact["b5_handoff_created"],
            "next_gate": artifact["next_gate"],
            "output_path": str(OUTPUT),
        }
        print(json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True))
        print("PROVIDER_READS=0")
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (FinalCompetitionClosureError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"FINAL_COMPETITION_CLOSURE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
