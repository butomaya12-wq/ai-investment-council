from __future__ import annotations

from types import SimpleNamespace

import aic.council.reopen_initial_recovery_runtime as rr


def _item():
    return SimpleNamespace(
        candidate_id="META",
        lane=SimpleNamespace(value="RED_TEAM"),
        request=SimpleNamespace(request_hash=rr.EXPECTED_UNKNOWN_REQUEST_HASH),
        request_body_utf8_bytes=35024,
    )


def test_recovery_dry_is_one_call_only_and_zero_authority():
    item = _item()
    dry = rr.build_dry_artifact(
        code_commit_sha="a" * 40,
        recovery_plan={"artifact_hash": rr.RECOVERY_PLAN_HASH},
        item=item,
    )
    assert dry["status"] == rr.DRY_STATUS
    assert dry["recovery_paid_calls_max"] == 1
    assert dry["recovery_request_hash"] == rr.EXPECTED_UNKNOWN_REQUEST_HASH
    assert dry["recovery_cost_ceiling_usd"] == "0.136712"
    assert dry["recovery_paid_dispatch_authorized"] is False
    assert dry["recovery_rerun_authorized"] is False
    assert dry["rebuttal_authorized"] is False
    assert dry["judge_authorized"] is False
    assert rr.verify_dry_artifact(dry, code_commit_sha="a" * 40, item=item) == dry["artifact_hash"]


def test_recovery_authorization_binds_exact_one_call_scope():
    item = _item()
    dry = rr.build_dry_artifact(
        code_commit_sha="b" * 40,
        recovery_plan={"artifact_hash": rr.RECOVERY_PLAN_HASH},
        item=item,
    )
    auth = rr.build_paid_authorization(
        code_commit_sha="b" * 40,
        dry_artifact=dry,
        item=item,
        owner_approval_id="OWNER-B4-REOPEN-INITIAL-RECOVERY-V01",
        owner_approval_at_utc="2026-08-30T20:00:00Z",
        approve_recovery_plan_hash=rr.RECOVERY_PLAN_HASH,
        approve_dry_hash=dry["artifact_hash"],
        approve_max_usd="0.136712",
        created_at_utc="2026-08-30T20:01:00Z",
        run_id="RECOVERY-RUN-1",
        journal_path=".aic-runtime/recovery.jsonl",
    )
    assert auth["status"] == rr.AUTH_STATUS
    assert auth["recovery_paid_calls_max"] == 1
    assert auth["authorization_consumption_rule"] == "CONSUMED_ON_FIRST_RECOVERY_PROVIDER_DISPATCH_ATTEMPT"
    assert auth["owner_approval"]["scope"] == "ONE_FRESH_META_RED_TEAM_INITIAL_RECOVERY_CALL_ONLY"
    assert auth["owner_approval"]["recovery_rerun_authorized"] is False
    assert auth["owner_approval"]["rebuttal_authorized"] is False
    assert auth["owner_approval"]["judge_authorized"] is False


def test_recovered_freeze_preserves_historical_unknown_cost(monkeypatch):
    monkeypatch.setattr(
        rr,
        "_validate_processed_record",
        lambda record: (record["oid"], record["ohash"]),
    )
    identities = [
        ("NVDA", "BULL"), ("NVDA", "BEAR"), ("NVDA", "RED_TEAM"),
        ("MSFT", "BULL"), ("MSFT", "BEAR"), ("MSFT", "RED_TEAM"),
        ("META", "BULL"), ("META", "BEAR"),
    ]
    source_records = [
        {"candidate_id": c, "lane": l, "oid": f"O{i}", "ohash": f"H{i}"}
        for i, (c, l) in enumerate(identities, start=1)
    ]
    source_blocked = {
        "processed_records": source_records,
        "dispatch_attempt_hashes": [f"A{i}" for i in range(1, 10)],
        "paid_call_receipt_hashes": [f"R{i}" for i in range(1, 10)],
    }
    recovery_record = {
        "candidate_id": "META",
        "lane": "RED_TEAM",
        "oid": "O9",
        "ohash": "H9",
        "actual_cost_usd": "0.0400000",
    }
    frozen = rr.build_recovered_freeze(
        code_commit_sha="c" * 40,
        recovery_run_id="RECOVERY-RUN",
        recovery_authorization_hash="AUTH",
        recovery_dry_hash="DRY",
        source_blocked=source_blocked,
        source_events=[{}] * 18,
        recovery_attempt_hash="REC_ATTEMPT",
        recovery_receipt_hash="REC_RECEIPT",
        recovery_processed_record=recovery_record,
    )
    assert frozen["status"] == rr.FREEZE_STATUS
    assert frozen["initial_opinion_count"] == 9
    assert frozen["aggregate_provider_dispatch_attempts"] == 10
    assert frozen["model_calls_known_completed"] == 9
    assert frozen["source_unknown_dispatch_cost_remains_unknown"] is True
    assert frozen["aggregate_cost_receipt_status"] == "PARTIAL_UNKNOWN_HISTORICAL_DISPATCH"
    assert frozen["known_actual_cost_usd"] == "0.3655090"
    assert frozen["aggregate_initial_spend_upper_bound_usd"] == "0.5022210"
    assert frozen["initial_freeze_barrier"] is True
    assert frozen["next_gate"] == rr.NEXT_GATE
    assert frozen["recovery_rerun_authorized"] is False
