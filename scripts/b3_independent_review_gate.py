from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.research.independent_review import IndependentReviewDraft
from aic.research.review_refs import validate_review_evidence_refs


REVIEW_SCRIPT = Path("scripts/b3_independent_review.py")
REVIEW_ARTIFACT = Path(".aic-runtime/b3_independent_review.json")
GATE_VERSION = "B3_INDEPENDENT_REVIEW_GATE_v0_1"


def _read_artifact(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read independent-review artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("independent-review artifact root must be object")
    actual = value.get("artifact_hash")
    expected = canonical_sha256(value, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise ValueError("independent-review artifact hash mismatch")
    return value


def _verify_pass_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("review_status") != "PASS":
        raise ValueError("review gate PASS verification requires review_status=PASS")
    if artifact.get("review_reconstructibility_status") != "PASS":
        raise ValueError("independent review is not reconstructible")
    if artifact.get("repair_attempts") != 0:
        raise ValueError("independent review unexpectedly used repair attempts")
    if artifact.get("provider_reads") != 0:
        raise ValueError("independent review unexpectedly used provider reads")
    if artifact.get("broker_writes") != 0 or artifact.get("alpaca_orders") != 0:
        raise ValueError("independent review unexpectedly used broker/order writes")
    if artifact.get("live_money") != "PROHIBITED":
        raise ValueError("independent review live-money invariant drift")

    review_input = artifact.get("review_input")
    review_raw = artifact.get("review")
    if not isinstance(review_input, dict) or not isinstance(review_raw, dict):
        raise ValueError("PASS review artifact lacks review input/output")
    review = IndependentReviewDraft.model_validate(review_raw)
    validate_review_evidence_refs(review, review_input=review_input)


def main() -> int:
    completed = subprocess.run(
        [sys.executable, str(REVIEW_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="")
        return completed.returncode

    try:
        artifact = _read_artifact(REVIEW_ARTIFACT)
        _verify_pass_artifact(artifact)
    except Exception as exc:
        print(
            f"B3 independent review deterministic gate failed closed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    if completed.stdout:
        print(completed.stdout, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
