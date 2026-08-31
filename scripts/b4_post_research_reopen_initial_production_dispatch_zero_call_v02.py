from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_execute_production_v01 import (
    CONTEXT_CAPABILITY_PATH, PostResearchInitialExecutionError, _read_object, _write_exclusive,
    build_readiness, load_context_capability, verify_readiness,
)
from aic.council.post_research_reopen_initial_production_dispatch_v01 import EXPECTED_BRANCH

COST = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
OLD = Path(".aic-runtime/b4_post_research_reopen_initial_production_dispatch_zero_call_preflight_v0_1.json")
OUT = Path(".aic-runtime/b4_post_research_reopen_initial_production_dispatch_zero_call_preflight_v0_2.json")
RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_council_freeze_v0_1.json")
OLD_HASH = "269ea362c5903cc0cd06e2e2ab84f8e7d841a2353f7ed965ad6b573067dbe83e"

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

def main() -> int:
    try:
        for key in ("OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET"):
            os.environ[key] = ""
        if _git("branch", "--show-current") != EXPECTED_BRANCH or _git("status", "--porcelain"):
            raise PostResearchInitialExecutionError("v0.2 readiness requires clean target branch")
        if OUT.exists() or RESULT.exists():
            raise PostResearchInitialExecutionError("exclusive output or fresh result already exists")
        if _read_object(OLD, "immutable v0.1 readiness").get("artifact_hash") != OLD_HASH:
            raise PostResearchInitialExecutionError("immutable v0.1 readiness drift")
        head = _git("rev-parse", "HEAD")
        cost, capability = _read_object(COST, "cost preflight"), load_context_capability(CONTEXT_CAPABILITY_PATH)
        artifact = build_readiness(code_commit_sha=head, cost_preflight=cost, context_capability=capability)
        verify_readiness(artifact, code_commit_sha=head, cost_preflight=cost, context_capability=capability)
        _write_exclusive(OUT, artifact)
        print(f"NEW_DISPATCH_PREFLIGHT_HASH={artifact['artifact_hash']}")
        print("CONTEXT_ADMISSIBILITY=PASS\nMODEL_CALLS_THIS_STEP=0\nPROVIDER_READS_THIS_STEP=0\nBROKER_WRITES=0\nALPACA_ORDERS=0\nCOST_USD_THIS_STEP=0")
        return 0
    except (PostResearchInitialExecutionError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_V02_READINESS_STOP: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
