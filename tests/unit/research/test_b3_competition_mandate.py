from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.mandate import (
    COMPETITION_LIFECYCLE_POLICY_HASH,
    COMPETITION_MANDATE_HASH,
    COMPETITION_MANDATE_VERSION,
    COMPETITION_OPTIONS_POLICY_HASH,
    CompetitionPolicyError,
    load_competition_decision_lifecycle_policy,
    load_competition_investment_mandate,
    load_competition_options_policy,
)
from aic.research.model_policy import MODEL_CANDIDATE_LADDER
from aic.research.promotion import bind_mandate_version, build_model_run_receipt_from_synthesis_record
from aic.research.models import ResearchEvidenceStatus
from aic.research.synthesize import SynthesisInputEnvelope


def test_owner_frozen_competition_authority_loads_and_self_hashes() -> None:
    options = load_competition_options_policy()
    mandate = load_competition_investment_mandate()
    lifecycle = load_competition_decision_lifecycle_policy()

    assert options["policy_hash"] == COMPETITION_OPTIONS_POLICY_HASH
    assert canonical_sha256(options, exclude_fields=("policy_hash",)) == COMPETITION_OPTIONS_POLICY_HASH
    assert mandate.version == COMPETITION_MANDATE_VERSION
    assert mandate.mandate_hash == COMPETITION_MANDATE_HASH
    assert lifecycle.policy_hash == COMPETITION_LIFECYCLE_POLICY_HASH
    assert lifecycle.decision_ttl_seconds == 7200
    assert mandate.execution_mode == "PAPER_APPROVAL_REQUIRED"
    assert mandate.live_execution is False


def test_options_policy_drift_is_rejected(tmp_path: Path) -> None:
    payload = load_competition_options_policy()
    payload["selector"]["delta_max"] = "0.61"
    payload["policy_hash"] = canonical_sha256(payload, exclude_fields=("policy_hash",))
    path = tmp_path / "mutated_options.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CompetitionPolicyError, match="owner freeze"):
        load_competition_options_policy(path)


def _legacy_input() -> SynthesisInputEnvelope:
    return SynthesisInputEnvelope(
        candidate_id="NVDA",
        symbol="NVDA",
        issuer_id="SEC_CIK_0001045810",
        b2_snapshot_id="B2_TEST",
        research_snapshot_id="B3_TEST",
        mandate_version=None,
        deep_comparison_id="DEEP_TEST",
        research_policy_version="RESEARCH_POLICY_vB3_0_1",
        model_policy_version="MODEL_POLICY_vB3_0_1",
        research_cutoff=datetime(2026, 8, 28, 17, 34, tzinfo=UTC),
        evidence_bundle_hash="1" * 64,
        evidence_status=ResearchEvidenceStatus.COMPLETE,
        evidence_items=(),
        computed_values=(),
        conflict_ids=(),
        research_questions=(),
        application_source_gaps=(),
    )


def test_mandate_binding_changes_only_lineage_field() -> None:
    legacy = _legacy_input()
    promoted = bind_mandate_version(legacy, mandate_version=COMPETITION_MANDATE_VERSION)

    assert legacy.mandate_version is None
    assert promoted.mandate_version == COMPETITION_MANDATE_VERSION
    left = legacy.model_dump(mode="json")
    right = promoted.model_dump(mode="json")
    left.pop("mandate_version")
    right.pop("mandate_version")
    assert left == right


def test_model_run_receipt_promotes_existing_observability_without_new_call() -> None:
    record = {
        "status": "DRAFT_VALIDATED",
        "repair_attempts": 0,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "reasoning_tokens": 10,
            "cached_tokens": 0,
        },
        "validator_results": [{"check_id": "B3-P1-P3", "status": "PASS", "detail": "ok"}],
        "synthesis_input_hash": "2" * 64,
        "output_hash": "3" * 64,
        "response_id": "resp_test",
        "requested_model": "gpt-5.6-terra",
        "effective_model": "gpt-5.6-terra",
        "latency_ms": 1234,
        "initial_validator_error": None,
    }
    receipt = build_model_run_receipt_from_synthesis_record(
        candidate="NVDA",
        record=record,
        model_candidate=MODEL_CANDIDATE_LADDER[0],
        research_policy_version="RESEARCH_POLICY_vB3_0_1",
        research_snapshot_hash="4" * 64,
        synthesis_artifact_hash="5" * 64,
    )

    assert receipt.stage == "SYNTHESIS"
    assert receipt.openai_response_id == "resp_test"
    assert receipt.store is False
    assert receipt.tools_enabled is False
    assert receipt.receipt_hash == canonical_sha256(receipt, exclude_fields=("receipt_hash",))
