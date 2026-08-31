from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_verdict_preflight_v01 import (
    PostResearchReopenVerdictPreflightError,
    load_and_build_verdict_preflight,
    verify_verdict_preflight,
)


FINAL_CLOSURE = Path(".aic-runtime/b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
S00 = Path(".aic-runtime/b3_research_reopen_s00_scope_zero_call_v0_3.json")
LOCAL_REPLAY = Path(".aic-runtime/b3_research_reopen_local_replay_zero_call_v0_1.json")
ORIGINAL_RESULT = Path(".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json")
WIRE_V02_RESULT = Path(".aic-runtime/b3_research_reopen_continuation_wire_repair_result_v0_2.json")
REPAIR_RESULT = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_result_v0_1.json")
REPAIR_AUTH = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_authorization_v0_1.json")
REPAIR_RAW = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_raw_v0_1")
INITIAL_SELECTED_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
OUTPUT = Path(".aic-runtime/b4_post_research_reopen_verdict_preflight_zero_call_v0_1.json")
EXPECTED_BRANCH = "hackathon/alpaca-2026"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    if path.exists():
        raise PostResearchReopenVerdictPreflightError(
            f"output already exists: {path}; do not rerun"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    try:
        branch = _git("branch", "--show-current")
        if branch != EXPECTED_BRANCH:
            raise PostResearchReopenVerdictPreflightError(
                f"runner requires {EXPECTED_BRANCH}"
            )
        if _git("status", "--porcelain"):
            raise PostResearchReopenVerdictPreflightError(
                "runner requires clean worktree"
            )
        if OUTPUT.exists():
            raise PostResearchReopenVerdictPreflightError(
                f"output already exists: {OUTPUT}; do not delete or rerun"
            )

        for path in (
            FINAL_CLOSURE,
            S00,
            LOCAL_REPLAY,
            ORIGINAL_RESULT,
            WIRE_V02_RESULT,
            REPAIR_RESULT,
            REPAIR_AUTH,
            INITIAL_SELECTED_AUTHORITY,
        ):
            if not path.is_file():
                raise PostResearchReopenVerdictPreflightError(
                    f"required immutable input missing: {path}"
                )
        if not REPAIR_RAW.is_dir():
            raise PostResearchReopenVerdictPreflightError(
                f"required raw evidence directory missing: {REPAIR_RAW}"
            )

        head = _git("rev-parse", "HEAD")
        artifact = load_and_build_verdict_preflight(
            code_commit_sha=head,
            final_closure_path=FINAL_CLOSURE,
            s00_path=S00,
            local_replay_path=LOCAL_REPLAY,
            original_result_path=ORIGINAL_RESULT,
            wire_v02_result_path=WIRE_V02_RESULT,
            repair_result_path=REPAIR_RESULT,
            repair_authorization_path=REPAIR_AUTH,
            repair_raw_dir=REPAIR_RAW,
            initial_selected_model_authority_path=INITIAL_SELECTED_AUTHORITY,
        )
        verify_verdict_preflight(artifact, expected_code_commit_sha=head)
        _write_exclusive(OUTPUT, artifact)

        public = {
            "status": artifact["status"],
            "artifact_hash": artifact["artifact_hash"],
            "code_commit_sha": artifact["code_commit_sha"],
            "source_b3_final_closure_hash": artifact["source_b3_final_closure_hash"],
            "canonical_research_reopen_closed": artifact["canonical_research_reopen_closed"],
            "remaining_canonical_reopen_requirement_ids": artifact["remaining_canonical_reopen_requirement_ids"],
            "additional_provider_read_required_before_b4": artifact["additional_provider_read_required_before_b4"],
            "candidate_order": artifact["candidate_order"],
            "evidence_source_manifest": artifact["evidence_source_manifest"],
            "post_research_reopen_decision_context": artifact["post_research_reopen_decision_context"],
            "known_transport_limitations": artifact["known_transport_limitations"],
            "model_selection_plan": artifact["model_selection_plan"],
            "planned_model_eval_calls": artifact["planned_model_eval_calls"],
            "fresh_production_stages": artifact["fresh_production_stages"],
            "planned_fresh_production_model_calls_max": artifact["planned_fresh_production_model_calls_max"],
            "initial_model_facing_materialization_required": artifact["initial_model_facing_materialization_required"],
            "cost_authority_mode": artifact["cost_authority_mode"],
            "owner_cost_approval_required": artifact["owner_cost_approval_required"],
            "model_calls_authorized": artifact["model_calls_authorized"],
            "provider_reads_authorized": artifact["provider_reads_authorized"],
            "final_decision_created": artifact["final_decision_created"],
            "b5_handoff_created": artifact["b5_handoff_created"],
            "next_gate": artifact["next_gate"],
            "output_path": str(OUTPUT),
        }
        print(json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True))
        print("VERDICT_PREFLIGHT_VERIFICATION=PASS")
        print("PROVIDER_READS=0")
        print("MODEL_CALLS=0")
        print("MODEL_EVAL_CALLS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD_THIS_STEP=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (
        PostResearchReopenVerdictPreflightError,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print(f"B4_VERDICT_PREFLIGHT_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
