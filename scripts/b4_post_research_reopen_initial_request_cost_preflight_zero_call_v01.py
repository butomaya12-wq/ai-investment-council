from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_request_cost_preflight_v01 import (
    PostResearchReopenInitialRequestCostPreflightError,
    load_and_build_initial_request_cost_preflight,
    verify_initial_request_cost_preflight,
)


SOURCE_VERDICT = Path(".aic-runtime/b4_post_research_reopen_verdict_preflight_zero_call_v0_1.json")
FINAL_CLOSURE = Path(".aic-runtime/b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
S00 = Path(".aic-runtime/b3_research_reopen_s00_scope_zero_call_v0_3.json")
LOCAL_REPLAY = Path(".aic-runtime/b3_research_reopen_local_replay_zero_call_v0_1.json")
ORIGINAL_NVDA = Path(".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json")
WIRE_V02_MSFT = Path(".aic-runtime/b3_research_reopen_continuation_wire_repair_result_v0_2.json")
REPAIR_RESULT = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_result_v0_1.json")
REPAIR_AUTHORIZATION = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_authorization_v0_1.json")
REPAIR_RAW = Path(".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_raw_v0_1")
FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
PRICING = Path("config/event/openai_text_pricing_2026_08_30.json")
OUTPUT = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
EXPECTED_BRANCH = "hackathon/alpaca-2026"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    if path.exists():
        raise PostResearchReopenInitialRequestCostPreflightError(
            f"output already exists: {path}; do not delete or rerun"
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
        for name in (
            "OPENAI_API_KEY",
            "APCA_API_KEY_ID",
            "APCA_API_SECRET_KEY",
            "ALPACA_API_KEY",
            "ALPACA_API_SECRET",
        ):
            os.environ[name] = ""
        _require_branch = _git("branch", "--show-current")
        if _require_branch != EXPECTED_BRANCH:
            raise PostResearchReopenInitialRequestCostPreflightError(
                f"runner requires {EXPECTED_BRANCH}"
            )
        if _git("status", "--porcelain"):
            raise PostResearchReopenInitialRequestCostPreflightError(
                "runner requires clean worktree"
            )
        if OUTPUT.exists():
            raise PostResearchReopenInitialRequestCostPreflightError(
                f"output already exists: {OUTPUT}; do not delete or rerun"
            )
        required_files = (
            SOURCE_VERDICT, FINAL_CLOSURE, S00, LOCAL_REPLAY, ORIGINAL_NVDA,
            WIRE_V02_MSFT, REPAIR_RESULT, REPAIR_AUTHORIZATION, FREEZE,
            RECONCILIATION, HANDOFF, INITIAL_AUTHORITY, PRICING,
        )
        for path in required_files:
            if not path.is_file():
                raise PostResearchReopenInitialRequestCostPreflightError(
                    f"required immutable input missing: {path}"
                )
        if not REPAIR_RAW.is_dir():
            raise PostResearchReopenInitialRequestCostPreflightError(
                f"required immutable raw evidence directory missing: {REPAIR_RAW}"
            )
        head = _git("rev-parse", "HEAD")
        artifact = load_and_build_initial_request_cost_preflight(
            code_commit_sha=head,
            source_verdict_preflight_path=SOURCE_VERDICT,
            final_closure_path=FINAL_CLOSURE,
            s00_path=S00,
            local_replay_path=LOCAL_REPLAY,
            original_result_path=ORIGINAL_NVDA,
            wire_v02_result_path=WIRE_V02_MSFT,
            repair_result_path=REPAIR_RESULT,
            repair_authorization_path=REPAIR_AUTHORIZATION,
            repair_raw_dir=REPAIR_RAW,
            freeze_path=FREEZE,
            reconciliation_path=RECONCILIATION,
            handoff_path=HANDOFF,
            initial_authority_path=INITIAL_AUTHORITY,
            pricing_path=PRICING,
        )
        verify_initial_request_cost_preflight(artifact, expected_code_commit_sha=head)
        _write_exclusive(OUTPUT, artifact)
        print(f"SOURCE_VERDICT_PREFLIGHT_HASH={artifact['source_verdict_preflight_hash']}")
        print(f"SOURCE_B3_CLOSURE_HASH={artifact['source_b3_closure_hash']}")
        print(f"MODEL={artifact['model']}")
        print(f"REASONING_EFFORT={artifact['reasoning_effort']}")
        print(f"CALL_COUNT_PLANNED={artifact['call_count_planned']}")
        print(f"CALL_COUNT_CEILING={artifact['call_count_ceiling']}")
        print(f"ESTIMATED_INPUT_TOKENS={artifact['estimated_input_tokens_upper_bound_total']}")
        print(f"MAX_OR_EXPECTED_OUTPUT_TOKENS={artifact['maximum_output_tokens_per_request']}")
        print(f"ESTIMATED_MAX_COST_USD={artifact['estimated_max_cost_usd']}")
        print(f"OWNER_APPROVAL_STATUS={artifact['owner_approval_status']}")
        print("MODEL_CALLS_AUTHORIZED=FALSE")
        print("PROVIDER_READS_AUTHORIZED=FALSE")
        print("MODEL_CALLS_THIS_STEP=0")
        print("PROVIDER_READS_THIS_STEP=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD_THIS_STEP=0")
        print("LIVE_MONEY=PROHIBITED")
        print("B4_POST_RESEARCH_REOPEN_INITIAL_REQUEST_COST_PREFLIGHT_ZERO_CALL_PASS")
        return 0
    except (PostResearchReopenInitialRequestCostPreflightError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_REQUEST_COST_PREFLIGHT_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
