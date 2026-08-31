from __future__ import annotations

from decimal import Decimal

import pytest

from aic.council import reopen_judge_paid_runtime_v02 as paid
from aic.council import reopen_judge_production_v02 as gate
from aic.domain.canonical import canonical_sha256


def _authorization() -> dict[str, object]:
    return paid.build_paid_authorization(
        run_id="AIC-B4-REOPEN-JUDGE-TEST",
        created_at_utc="2026-08-31T06:00:00Z",
        code_commit_sha="a" * 40,
        git_worktree_clean=True,
        owner_approval_id="OWNER-JUDGE-V02-TEST",
        owner_approval_at_utc="2026-08-31T05:59:00Z",
        selection_hash="1" * 64,
        entry_hash="2" * 64,
        request_preflight_hash="3" * 64,
        request_manifest_hash="4" * 64,
        request_hash="5" * 64,
        cost_preflight_hash="6" * 64,
        runner_dry_hash="7" * 64,
        approved_cost_ceiling_usd=Decimal("0.5"),
        receipt_journal_path=".aic-runtime/test.jsonl",
    )


def test_paid_authorization_is_bound_to_credential_and_current_freeze() -> None:
    artifact = _authorization()
    assert paid.verify_paid_authorization(artifact) == artifact["artifact_hash"]
    assert artifact["rebuttal_council_freeze_artifact_hash"] == gate.EXPECTED_REBUTTAL_FREEZE_HASH
    assert artifact["replacement_credential_fingerprint_sha256"] == gate.EXPECTED_CREDENTIAL_SHA256
    assert artifact["planned_paid_calls_max"] == 1
    assert artifact["automatic_retries"] == 0
    assert artifact["automatic_repair_calls_authorized"] == 0
    assert artifact["judge_execution_authority"] is False


def test_paid_authorization_rejects_credential_drift_even_if_rehashed() -> None:
    artifact = _authorization()
    artifact["replacement_credential_fingerprint_sha256"] = "0" * 64
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    with pytest.raises(paid.ReopenJudgePaidRuntimeError, match="authorization drift"):
        paid.verify_paid_authorization(artifact)


def test_attempt_event_consumes_authority_before_provider_dispatch() -> None:
    auth = _authorization()
    event = paid.build_attempt_event(
        run_id=str(auth["run_id"]),
        started_at_utc="2026-08-31T06:01:00Z",
        authorization_hash=str(auth["artifact_hash"]),
        request_hash="5" * 64,
        request_manifest_hash="4" * 64,
    )
    assert event["event_type"] == "JUDGE_PROVIDER_DISPATCH_ATTEMPT"
    assert event["authorization_consumed_by_this_attempt"] is True
    assert event["automatic_retry"] is False
    assert event["automatic_repair_attempted"] is False
    assert event["event_hash"] == canonical_sha256(event, exclude_fields=("event_hash",))


def test_blocked_artifact_never_authorizes_rerun_or_execution() -> None:
    artifact = paid.build_blocked_artifact(
        status="BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
        reason="network state unknown",
        run_id="AIC-B4-REOPEN-JUDGE-TEST",
        code_commit_sha="a" * 40,
        authorization_hash="1" * 64,
        attempt_event_hash="2" * 64,
        receipt_hash=None,
        runner_dry_hash="3" * 64,
        approved_cost_ceiling_usd=Decimal("0.5"),
        run=None,
    )
    assert artifact["judge_authorization_consumed"] is True
    assert artifact["rerun_authorized"] is False
    assert artifact["execution_authority"] is False
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
