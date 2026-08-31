from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_execute_production_v01 import CONTEXT_CAPABILITY_PATH, _read_object, _write_exclusive, load_context_capability
from aic.council.post_research_reopen_initial_full_recovery_v01 import FullInitialRecoveryError, build_full_recovery_readiness, verify_full_recovery_readiness
from aic.council.post_research_reopen_initial_paid_failure_recovery_preflight_v01 import file_sha256
from aic.council.post_research_reopen_initial_production_dispatch_v01 import EXPECTED_BRANCH


COST = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
PREFLIGHT = Path(".aic-runtime/b4_post_research_reopen_initial_paid_failure_recovery_preflight_v0_1.json")
HISTORICAL_LEDGER = Path(".aic-runtime/b4_post_research_reopen_initial_paid_dispatch_ledger_v0_1.json")
HISTORICAL_RAW = Path(".aic-runtime/b4_post_research_reopen_initial_paid_raw_responses_v0_1")
FRESH_RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_full_recovery_council_freeze_v0_1.json")
ORIGINAL_RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_council_freeze_v0_1.json")
OUT = Path(".aic-runtime/b4_post_research_reopen_initial_full_recovery_readiness_zero_call_v0_1.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    try:
        for key in ("OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET"):
            os.environ[key] = ""
        if _git("branch", "--show-current") != EXPECTED_BRANCH or _git("status", "--porcelain"):
            raise FullInitialRecoveryError("full recovery readiness requires clean target branch")
        if OUT.exists() or FRESH_RESULT.exists() or ORIGINAL_RESULT.exists():
            raise FullInitialRecoveryError("exclusive readiness output or fresh recovery result already exists")
        raw_count = len(list(HISTORICAL_RAW.glob("*.json"))) if HISTORICAL_RAW.exists() else 0
        head = _git("rev-parse", "HEAD")
        cost = _read_object(COST, "cost preflight")
        historical = _read_object(HISTORICAL_LEDGER, "historical failed paid ledger")
        preflight = _read_object(PREFLIGHT, "full recovery preflight")
        capability = load_context_capability(CONTEXT_CAPABILITY_PATH)
        artifact = build_full_recovery_readiness(
            code_commit_sha=head,
            cost_preflight=cost,
            context_capability=capability,
            historical_ledger=historical,
            historical_ledger_file_sha256=file_sha256(HISTORICAL_LEDGER),
            recovery_preflight=preflight,
            historical_raw_response_dir_exists=HISTORICAL_RAW.exists(),
            historical_raw_response_file_count=raw_count,
            fresh_initial_result_exists=FRESH_RESULT.exists() or ORIGINAL_RESULT.exists(),
        )
        verify_full_recovery_readiness(
            artifact,
            code_commit_sha=head,
            cost_preflight=cost,
            context_capability=capability,
            historical_ledger=historical,
            historical_ledger_file_sha256=file_sha256(HISTORICAL_LEDGER),
            recovery_preflight=preflight,
        )
        _write_exclusive(OUT, artifact)
        print(f"FULL_RECOVERY_READINESS_HASH={artifact['artifact_hash']}")
        print("CONTEXT_ADMISSIBILITY=PASS\nMODEL_CALLS_THIS_STEP=0\nPROVIDER_READS_THIS_STEP=0\nBROKER_WRITES=0\nALPACA_ORDERS=0\nCOST_USD_THIS_STEP=0")
        return 0
    except (FullInitialRecoveryError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_READINESS_STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
