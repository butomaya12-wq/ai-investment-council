from __future__ import annotations

from types import SimpleNamespace

import pytest

from aic.domain.canonical import canonical_sha256
from aic.council import reopen_rebuttal_credential_probe_v02 as probe_v02
from aic.council import reopen_rebuttal_runtime as base
from aic.council import reopen_rebuttal_runtime_v02 as v02
from aic.council import reopen_rebuttal_runtime_v03 as v03


def _source_dry() -> dict:
    value = {
        "artifact_version": v02.DRY_VERSION,
        "runtime_version": v02.RUNTIME_VERSION,
        "status": base.DRY_STATUS,
        "code_commit_sha": v03.SOURCE_FRESH_DRY_HEAD,
        "source_cost_preflight_artifact_hash": base.EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": base.EXPECTED_REQUEST_MANIFEST_HASH,
        "planned_paid_calls_max": base.EXPECTED_CALLS,
        "max_output_tokens_per_call": base.EXPECTED_MAX_OUTPUT_TOKENS,
        "cost_ceiling_usd": str(base.EXPECTED_COST_CEILING_USD),
        "owner_approval_required": True,
        "paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "judge_authorized": False,
        "rebuttal_rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value)
    return value


def _probe_result(key: str) -> dict:
    value = {
        "artifact_version": probe_v02.FINAL_VERSION,
        "status": probe_v02.PASS_STATUS,
        "probe_model_id": probe_v02.MODEL_ID,
        "http_response_received": True,
        "http_status_code": 200,
        "error_type": None,
        "error_code": None,
        "returned_model_id": probe_v02.MODEL_ID,
        "provider_reads": 1,
        "model_calls": 0,
        "responses_generation_calls": 0,
        "credential_probe_authority_consumed": True,
        "fresh_generation_dispatch_authorized": False,
        "new_generation_owner_approval_required": True,
        "automatic_retries": 0,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": probe_v02.NEXT_GATE_PASS,
        "replacement_credential_fingerprint_sha256": probe_v02.credential_fingerprint_sha256(key),
    }
    value["artifact_hash"] = canonical_sha256(value)
    return value


def _item(index: int, candidate: str, request_hash: str) -> SimpleNamespace:
    request = SimpleNamespace(
        request_hash=request_hash,
        request_payload={
            "model": "gpt-5.6-sol",
            "reasoning": {"effort": "medium"},
            "max_output_tokens": 6144,
        },
    )
    return SimpleNamespace(
        dispatch_index=index,
        candidate_id=candidate,
        context_hash=(str(index) * 64)[:64],
        request=request,
        request_body_utf8_bytes=80000 + index,
    )


def _base_dry(head: str, items: list[SimpleNamespace]) -> dict:
    rows = [
        {
            "dispatch_index": item.dispatch_index,
            "candidate_id": item.candidate_id,
            "context_hash": item.context_hash,
            "request_hash": item.request.request_hash,
            "request_body_utf8_bytes": item.request_body_utf8_bytes,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "max_output_tokens": 6144,
        }
        for item in items
    ]
    value = {
        "artifact_version": v03.DRY_VERSION,
        "runtime_version": v03.RUNTIME_VERSION,
        "status": base.DRY_STATUS,
        "code_commit_sha": head,
        "source_cost_preflight_artifact_hash": base.EXPECTED_COST_PREFLIGHT_HASH,
        "source_recovered_initial_freeze_hash": base.EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": base.EXPECTED_SELECTION_HASH,
        "request_manifest_hash": base.EXPECTED_REQUEST_MANIFEST_HASH,
        "planned_paid_calls_max": base.EXPECTED_CALLS,
        "max_output_tokens_per_call": base.EXPECTED_MAX_OUTPUT_TOKENS,
        "cost_ceiling_usd": str(base.EXPECTED_COST_CEILING_USD),
        "request_rows": rows,
        "owner_approval_required": True,
        "paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "judge_authorized": False,
        "rebuttal_rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value)
    return value


def test_v03_source_dry_and_probe_are_self_hash_bound() -> None:
    source = _source_dry()
    key = "sk-" + "k" * 100
    probe = _probe_result(key)
    assert v03.verify_source_fresh_recovery_dry(
        source, expected_hash=source["artifact_hash"]
    ) == source["artifact_hash"]
    assert v03.verify_successful_probe_result(
        probe, expected_hash=probe["artifact_hash"]
    ) == probe["artifact_hash"]


def test_v03_dry_binds_credential_without_persisting_secret() -> None:
    source = _source_dry()
    key = "sk-" + "q" * 100
    probe = _probe_result(key)
    base_dry = {"artifact_hash": "0" * 64, "status": base.DRY_STATUS}
    dry = v03.add_credential_lineage_to_dry(
        base_dry,
        source_fresh_dry=source,
        probe_result=probe,
        api_key=key,
        expected_source_dry_hash=source["artifact_hash"],
        expected_probe_result_hash=probe["artifact_hash"],
    )
    assert dry["replacement_credential_fingerprint_sha256"] == probe[
        "replacement_credential_fingerprint_sha256"
    ]
    assert dry["replacement_credential_secret_persisted"] is False
    assert key not in repr(dry)
    assert dry["credential_lineage_enforced_before_paid_dispatch"] is True
    assert dry["artifact_hash"] == canonical_sha256(dry, exclude_fields=("artifact_hash",))


def test_v03_rejects_different_credential_before_authority_or_dispatch() -> None:
    source = _source_dry()
    probe = _probe_result("sk-" + "a" * 100)
    with pytest.raises(v03.B4ReopenRebuttalRuntimeV03Error, match="differs"):
        v03.add_credential_lineage_to_dry(
            {"artifact_hash": "0" * 64},
            source_fresh_dry=source,
            probe_result=probe,
            api_key="sk-" + "b" * 100,
            expected_source_dry_hash=source["artifact_hash"],
            expected_probe_result_hash=probe["artifact_hash"],
        )


def test_v03_authorization_and_attempt_preserve_credential_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    head = "c" * 40
    key = "sk-" + "z" * 100
    source = _source_dry()
    probe = _probe_result(key)
    items = [
        _item(1, "NVDA", "1" * 64),
        _item(2, "MSFT", "2" * 64),
        _item(3, "META", "3" * 64),
    ]
    bound = SimpleNamespace(plan=tuple(items))
    dry = v03.add_credential_lineage_to_dry(
        _base_dry(head, items),
        source_fresh_dry=source,
        probe_result=probe,
        api_key=key,
        expected_source_dry_hash=source["artifact_hash"],
        expected_probe_result_hash=probe["artifact_hash"],
    )
    monkeypatch.setattr(v03, "_source_lineage", lambda: (source, probe))
    monkeypatch.setattr(v03, "load_openai_api_key", lambda: key)
    auth = v03.build_paid_authorization(
        code_commit_sha=head,
        git_worktree_clean=True,
        created_at_utc="2026-08-31T05:10:01Z",
        run_id="AIC-B4-REOPEN-REBUTTAL-V03-TEST",
        owner_approval_id="OWNER-B4-REBUTTAL-V03-TEST",
        owner_approval_at_utc="2026-08-31T05:10:00Z",
        approve_cost_artifact_hash=base.EXPECTED_COST_PREFLIGHT_HASH,
        approve_request_manifest_hash=base.EXPECTED_REQUEST_MANIFEST_HASH,
        approve_dry_artifact_hash=dry["artifact_hash"],
        approve_max_usd=str(base.EXPECTED_COST_CEILING_USD),
        dry_artifact=dry,
        bound=bound,
        receipt_journal_path="v03.jsonl",
    )
    fingerprint = probe["replacement_credential_fingerprint_sha256"]
    assert auth["replacement_credential_fingerprint_sha256"] == fingerprint
    assert auth["runner_dry_artifact_hash"] == dry["artifact_hash"]
    attempt = v03.build_attempt_event(
        run_id=auth["run_id"],
        item=items[0],
        authorization_hash=auth["artifact_hash"],
        started_at_utc="2026-08-31T05:11:00Z",
    )
    assert attempt["replacement_credential_fingerprint_sha256"] == fingerprint
    assert attempt["authorization_consumed_by_this_attempt"] is True
    assert attempt["event_hash"] == canonical_sha256(attempt, exclude_fields=("event_hash",))


def test_canonical_hash_normalizes_deep_frozen_tuple_like_json_array() -> None:
    assert canonical_sha256({"refs": ["a", "b"]}) == canonical_sha256(
        {"refs": ("a", "b")}
    )
