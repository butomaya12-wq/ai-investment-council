from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_paid_failure_recovery_preflight_v01 import (
    PaidFailureRecoveryPreflightError, build_failure_recovery_preflight, file_sha256, verify_failure_recovery_preflight,
)
from aic.council.post_research_reopen_initial_request_cost_preflight_v01 import _read_object
from aic.council.post_research_reopen_initial_production_dispatch_v01 import EXPECTED_BRANCH

LEDGER = Path(".aic-runtime/b4_post_research_reopen_initial_paid_dispatch_ledger_v0_1.json")
COST = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
RAW_DIR = Path(".aic-runtime/b4_post_research_reopen_initial_paid_raw_responses_v0_1")
RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_council_freeze_v0_1.json")
OUT = Path(".aic-runtime/b4_post_research_reopen_initial_paid_failure_recovery_preflight_v0_1.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _write_exclusive(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise PaidFailureRecoveryPreflightError(f"exclusive output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    try:
        for key in ("OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET"):
            os.environ[key] = ""
        if _git("branch", "--show-current") != EXPECTED_BRANCH or _git("status", "--porcelain"):
            raise PaidFailureRecoveryPreflightError("recovery preflight requires clean target branch")
        ledger = _read_object(LEDGER, label="immutable paid failure ledger")
        raw_count = len(list(RAW_DIR.glob("*.json"))) if RAW_DIR.exists() else 0
        artifact = build_failure_recovery_preflight(ledger=ledger, ledger_file_sha256=file_sha256(LEDGER), cost_preflight=_read_object(COST, label="frozen cost preflight"), raw_response_dir_exists=RAW_DIR.exists(), raw_response_file_count=raw_count, fresh_result_exists=RESULT.exists())
        verify_failure_recovery_preflight(artifact, ledger=ledger, ledger_file_sha256=file_sha256(LEDGER), cost_preflight=_read_object(COST, label="frozen cost preflight"))
        _write_exclusive(OUT, artifact)
        print(f"RECOVERY_PREFLIGHT_HASH={artifact['artifact_hash']}")
        print("MODEL_CALLS_THIS_STEP=0\nPROVIDER_READS_THIS_STEP=0\nBROKER_WRITES=0\nALPACA_ORDERS=0\nCOST_USD_THIS_STEP=0")
        return 0
    except (PaidFailureRecoveryPreflightError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_PREFLIGHT_STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
