from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from aic.domain.canonical import canonical_sha256
import aic.council.reopen_initial_recovery_runtime_v02 as rr


def _item():
    return SimpleNamespace(
        candidate_id="META",
        lane=SimpleNamespace(value="RED_TEAM"),
        request=SimpleNamespace(request_hash=rr.EXPECTED_UNKNOWN_REQUEST_HASH),
        request_body_utf8_bytes=35024,
    )


def test_v02_dry_advertises_crash_safe_local_finalize_and_zero_paid_authority():
    dry = rr.build_dry_artifact(
        code_commit_sha="d" * 40,
        recovery_plan={"artifact_hash": rr.RECOVERY_PLAN_HASH},
        item=_item(),
    )
    assert dry["artifact_version"] == rr.DRY_VERSION
    assert dry["runtime_version"] == rr.RUNTIME_VERSION
    assert dry["status"] == rr.DRY_STATUS
    assert dry["recovery_paid_calls_max"] == 1
    assert dry["recovery_paid_dispatch_authorized"] is False
    assert dry["recovery_rerun_authorized"] is False
    assert dry["validated_processed_record_persisted_in_recovery_receipt"] is True
    assert dry["crash_safe_local_finalize_supported"] is True
    assert dry["local_finalize_requires_no_provider_dispatch"] is True
    assert rr.verify_dry_artifact(dry, code_commit_sha="d" * 40, item=_item()) == dry["artifact_hash"]


def test_v02_paid_authorization_binds_v02_dry_and_one_call_only():
    dry = rr.build_dry_artifact(
        code_commit_sha="e" * 40,
        recovery_plan={"artifact_hash": rr.RECOVERY_PLAN_HASH},
        item=_item(),
    )
    auth = rr.build_paid_authorization(
        code_commit_sha="e" * 40,
        dry_artifact=dry,
        item=_item(),
        owner_approval_id="OWNER-B4-REOPEN-INITIAL-RECOVERY-V01",
        owner_approval_at_utc="2026-08-30T20:00:00Z",
        approve_recovery_plan_hash=rr.RECOVERY_PLAN_HASH,
        approve_dry_hash=dry["artifact_hash"],
        approve_max_usd="0.136712",
        created_at_utc="2026-08-30T20:01:00Z",
        run_id="RECOVERY-V02-RUN",
        journal_path=".aic-runtime/recovery-v02.jsonl",
    )
    assert auth["artifact_version"] == rr.AUTH_VERSION
    assert auth["runtime_version"] == rr.RUNTIME_VERSION
    assert auth["recovery_paid_calls_max"] == 1
    assert auth["owner_approval"]["approved_recovery_dry_artifact_hash"] == dry["artifact_hash"]
    assert auth["owner_approval"]["scope"] == "ONE_FRESH_META_RED_TEAM_INITIAL_RECOVERY_CALL_ONLY"
    assert auth["owner_approval"]["recovery_rerun_authorized"] is False
    assert auth["validated_processed_record_persisted_in_recovery_receipt"] is True
    assert auth["crash_safe_local_finalize_supported"] is True


def test_v02_pass_receipt_persists_processed_record_for_zero_call_finalize(monkeypatch):
    record = {
        "candidate_id": "META",
        "lane": "RED_TEAM",
        "record_hash": "REC-HASH",
        "actual_cost_usd": "0.0400000",
    }
    receipt = rr.build_result_receipt(
        run_id="RECOVERY-V02-RUN",
        item=_item(),
        authorization_hash="AUTH-HASH",
        attempt_hash="ATTEMPT-HASH",
        started_at_utc="2026-08-30T20:00:00Z",
        finished_at_utc="2026-08-30T20:00:02Z",
        provider_response_received=True,
        raw_response={"usage": {"input_tokens": 100, "output_tokens": 20}},
        processed_record=record,
        validation_error=None,
    )
    assert receipt["receipt_version"] == rr.RECEIPT_VERSION
    assert receipt["event_version"] == rr.EVENT_VERSION
    assert receipt["processed_record"] == record
    assert receipt["validated_processed_record_persisted"] is True
    assert receipt["local_finalize_replayable"] is True
    assert receipt["receipt_hash"] == canonical_sha256(receipt, exclude_fields=("receipt_hash",))

    monkeypatch.setattr(rr, "_validate_processed_record", lambda raw: ("OID", "OHASH"))
    recovered = rr.processed_record_from_recovery_receipt(
        receipt,
        expected_authorization_hash="AUTH-HASH",
        expected_attempt_hash="ATTEMPT-HASH",
    )
    assert recovered == record


def test_v02_finalize_rejects_tampered_persisted_record(monkeypatch):
    record = {
        "candidate_id": "META",
        "lane": "RED_TEAM",
        "record_hash": "REC-HASH",
        "actual_cost_usd": "0.0400000",
    }
    receipt = rr.build_result_receipt(
        run_id="RECOVERY-V02-RUN",
        item=_item(),
        authorization_hash="AUTH-HASH",
        attempt_hash="ATTEMPT-HASH",
        started_at_utc="2026-08-30T20:00:00Z",
        finished_at_utc="2026-08-30T20:00:02Z",
        provider_response_received=True,
        raw_response={"usage": {"input_tokens": 100, "output_tokens": 20}},
        processed_record=record,
        validation_error=None,
    )
    receipt["processed_record"] = {**record, "actual_cost_usd": "0.999"}
    receipt["receipt_hash"] = canonical_sha256(receipt, exclude_fields=("receipt_hash",))
    monkeypatch.setattr(rr, "_validate_processed_record", lambda raw: ("OID", "OHASH"))
    try:
        rr.processed_record_from_recovery_receipt(
            receipt,
            expected_authorization_hash="AUTH-HASH",
            expected_attempt_hash="ATTEMPT-HASH",
        )
    except rr.B4ReopenInitialRecoveryRuntimeError as exc:
        assert "cost binding drift" in str(exc)
    else:
        raise AssertionError("tampered persisted processed record must fail closed")


def test_v02_runner_import_smoke():
    script = Path(__file__).resolve().parents[3] / "scripts" / "b4_reopen_run_initial_recovery_runtime_v02.py"
    spec = importlib.util.spec_from_file_location("b4_recovery_runner_v02_smoke", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    assert module.DRY.name.endswith("v0_2.json")
    assert module.PAID_JOURNAL.name.endswith("v0_2.jsonl")
