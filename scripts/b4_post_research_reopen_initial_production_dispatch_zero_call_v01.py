from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_production_dispatch_v01 import (
    EXPECTED_BRANCH,
    PostResearchReopenInitialProductionDispatchError,
    assert_exclusive_output,
    build_zero_call_dispatch_preflight,
    verify_zero_call_dispatch_preflight,
)
from aic.council.post_research_reopen_initial_request_cost_preflight_v01 import _read_object
from aic.council.model_selection import InitialSelectedModelAuthority


COST_PREFLIGHT = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
OUTPUT = Path(".aic-runtime/b4_post_research_reopen_initial_production_dispatch_zero_call_preflight_v0_1.json")
FRESH_INITIAL_RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_council_freeze_v0_1.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _write_exclusive(path: Path, payload: dict[str, object]) -> None:
    assert_exclusive_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    try:
        for name in ("OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET"):
            os.environ[name] = ""
        if _git("branch", "--show-current") != EXPECTED_BRANCH:
            raise PostResearchReopenInitialProductionDispatchError("runner branch mismatch")
        if _git("status", "--porcelain"):
            raise PostResearchReopenInitialProductionDispatchError("runner requires clean worktree")
        assert_exclusive_output(OUTPUT)
        assert_exclusive_output(FRESH_INITIAL_RESULT)
        if not COST_PREFLIGHT.is_file() or not INITIAL_AUTHORITY.is_file():
            raise PostResearchReopenInitialProductionDispatchError("required immutable dispatch input missing")
        head = _git("rev-parse", "HEAD")
        artifact = build_zero_call_dispatch_preflight(
            code_commit_sha=head,
            cost_preflight=_read_object(COST_PREFLIGHT, label="post-research Initial cost preflight"),
            authority=InitialSelectedModelAuthority.model_validate(
                _read_object(INITIAL_AUTHORITY, label="Initial selected-model authority")
            ),
        )
        verify_zero_call_dispatch_preflight(artifact, expected_code_commit_sha=head)
        _write_exclusive(OUTPUT, artifact)
        print(f"SOURCE_COST_PREFLIGHT_HASH={artifact['source_cost_preflight_hash']}")
        print(f"MODEL={artifact['model']}")
        print(f"REASONING_EFFORT={artifact['reasoning_effort']}")
        print(f"CALL_COUNT={artifact['call_count']}")
        print(f"MAX_OUTPUT_TOKENS={artifact['max_output_tokens_per_call']}")
        print(f"APPROVAL_MAX_COST_USD={artifact['approved_max_estimated_cost_usd_required']}")
        print("OWNER_APPROVAL_REQUIRED=TRUE")
        print("MODEL_CALLS_AUTHORIZED=FALSE")
        print("AUTOMATIC_RETRIES=0")
        print("PARTIAL_DISPATCH_FAIL_CLOSED=TRUE")
        print("PROVIDER_READS_THIS_STEP=0")
        print("MODEL_CALLS_THIS_STEP=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD_THIS_STEP=0")
        print("LIVE_MONEY=PROHIBITED")
        print("B4_POST_RESEARCH_REOPEN_INITIAL_PRODUCTION_DISPATCH_ZERO_CALL_PREFLIGHT_PASS")
        return 0
    except (PostResearchReopenInitialProductionDispatchError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_PRODUCTION_DISPATCH_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
