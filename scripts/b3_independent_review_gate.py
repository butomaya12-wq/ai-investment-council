from __future__ import annotations

import argparse
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


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the B3 independent review gate once, or deterministically verify an "
            "already-completed frozen review artifact without any model/provider call."
        )
    )
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="verify the existing review artifact only; never launch the reviewer subprocess",
    )
    return parser.parse_args()


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


def _public_pass_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    review = artifact.get("review")
    if not isinstance(review, dict):
        raise ValueError("PASS review artifact lacks review output")
    attack_results = review.get("attack_results")
    if not isinstance(attack_results, list):
        raise ValueError("PASS review artifact lacks attack results")
    return {
        "gate_version": GATE_VERSION,
        "verification_mode": "EXISTING_ARTIFACT_ONLY",
        "artifact_version": artifact.get("artifact_version"),
        "run_class": artifact.get("run_class"),
        "review_status": artifact.get("review_status"),
        "review_input_hash": artifact.get("review_input_hash"),
        "response_id": artifact.get("response_id"),
        "attack_class_count": len(attack_results),
        "attack_statuses": [
            {
                "attack_class": item.get("attack_class"),
                "status": item.get("status"),
            }
            for item in attack_results
            if isinstance(item, dict)
        ],
        "material_gap_summary": review.get("material_gap_summary"),
        "inconclusive_summary": review.get("inconclusive_summary"),
        "review_reconstructibility_status": artifact.get("review_reconstructibility_status"),
        "repair_attempts": artifact.get("repair_attempts"),
        "provider_reads": artifact.get("provider_reads"),
        "broker_writes": artifact.get("broker_writes"),
        "alpaca_orders": artifact.get("alpaca_orders"),
        "live_money": artifact.get("live_money"),
        "artifact_hash": artifact.get("artifact_hash"),
        "model_calls_performed_by_this_verification": 0,
    }


def _verify_existing() -> int:
    try:
        artifact = _read_artifact(REVIEW_ARTIFACT)
        _verify_pass_artifact(artifact)
        summary = _public_pass_summary(artifact)
    except Exception as exc:
        print(
            f"B3 existing independent review deterministic verification failed closed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = _args()
    if args.verify_existing:
        return _verify_existing()

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
