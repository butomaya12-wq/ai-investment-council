#!/usr/bin/env python3
"""Verify the inactive TTL-only reevaluation policy proposal without calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, TextIO

from aic.domain.canonical import canonical_sha256


POLICY_PATH = Path("config/event/decision_ttl_reevaluation_policy_competition_v1.json")
POLICY_HASH = "0b9128d8b19505daa19ef556b50ae7c1435ad02d04db34cff7730e8235eb3c7a"
POLICY_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"


class ProposalBlocked(ValueError):
    """Fail-closed inactive-policy verification rejection."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ProposalBlocked(reason)


def load_policy(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposalBlocked("BLOCK_POLICY_LOAD") from exc
    _need(isinstance(value, Mapping), "BLOCK_POLICY_ROOT")
    return value


def verify_inactive_proposal(policy: Mapping[str, Any]) -> str:
    expected = {
        "active": False,
        "artifact_version": "ALPACA_COMPETITION_DECISION_TTL_REEVALUATION_POLICY_V1",
        "automatic_retries": 0,
        "broker_write_authority": False,
        "cost_preflight_required": True,
        "fresh_b5_required_after_fresh_invest": True,
        "fresh_semantic_decision_required": True,
        "fresh_watch_or_abstain_b5_started": False,
        "historical_b3_input_lineage_allowed": True,
        "historical_initial_input_lineage_allowed": True,
        "historical_judge_reactivation_allowed": False,
        "historical_judge_semantic_input_allowed": False,
        "historical_lineage_authorizes_stage_skip": False,
        "historical_rebuttal_input_lineage_allowed": True,
        "historical_ttl_refresh_allowed": False,
        "live_execution": False,
        "model_calls_authorized": False,
        "owner_activation_required": True,
        "owner_paid_approval_required": True,
        "policy_id": "ALPACA_2026_COMPETITION_DECISION_TTL_REEVALUATION",
        "policy_name": "DECISION_TTL_REEVALUATION",
        "proposed_max_fresh_judge_calls": 1,
        "proposed_model_stage_scope": "FRESH_JUDGE_ONLY",
        "provider_reads_authorized": False,
        "provider_refresh_required_before_model": False,
        "status": "DRAFT_NOT_AUTHORITY",
        "trigger": "TTL_EXPIRY",
        "ttl_only_exclusion_conditions": [
            "RESEARCH_REOPEN_TRIGGER",
            "MATERIAL_EVIDENCE_CHANGE_TRIGGER",
            "MANDATE_POLICY_CHANGE_TRIGGER",
            "THESIS_INVALIDATION_TRIGGER",
        ],
        "version": POLICY_VERSION,
    }
    _need(policy.get("policy_hash") == POLICY_HASH, "BLOCK_POLICY_HASH")
    _need(canonical_sha256(policy, exclude_fields=("policy_hash",)) == POLICY_HASH, "BLOCK_POLICY_SELF_HASH")
    _need(set(policy) == set(expected) | {"policy_hash"}, "BLOCK_POLICY_FIELDS")
    for field, value in expected.items():
        _need(policy.get(field) == value, f"BLOCK_POLICY_{field.upper()}")
    return POLICY_HASH


def verify_policy_at(repository: Path) -> str:
    return verify_inactive_proposal(load_policy(repository / POLICY_PATH))


def main(argv: Sequence[str] | None = None, *, output: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args(argv)
    destination = output if output is not None else sys.stdout
    try:
        policy = load_policy(arguments.repository / POLICY_PATH)
        policy_hash = verify_inactive_proposal(policy)
    except ProposalBlocked as exc:
        print(f"TTL_REEVALUATION_POLICY_PROPOSAL_STATUS={exc}", file=destination)
        return 1
    print("PROPOSAL_ACTIVE=false", file=destination)
    print("PROPOSAL_STATUS=DRAFT_NOT_AUTHORITY", file=destination)
    print("PROPOSED_MODEL_STAGE_SCOPE=FRESH_JUDGE_ONLY", file=destination)
    print("PROVIDER_REFRESH_REQUIRED_BEFORE_MODEL=false", file=destination)
    print("MODEL_CALLS_AUTHORIZED=false", file=destination)
    print("PROVIDER_READS_AUTHORIZED=false", file=destination)
    print("BROKER_WRITE_AUTHORITY=false", file=destination)
    print("LIVE_EXECUTION=false", file=destination)
    print(f"POLICY_HASH={policy_hash}", file=destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
