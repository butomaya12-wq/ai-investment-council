import json
from decimal import Decimal

import pytest

from aic.council.initial_eval_runtime import EXPECTED_INITIAL_CASE_IDS
from aic.council.model_selection import (
    DEFAULT_INITIAL_SELECTED_MODEL_AUTHORITY_PATH,
    InitialSelectedModelAuthority,
    InitialSelectedModelAuthorityError,
    load_initial_selected_model_authority,
    verify_initial_model_eval_artifact,
)
from aic.domain.canonical import canonical_sha256


def test_initial_selected_model_authority_freezes_eval_selected_l2() -> None:
    authority = load_initial_selected_model_authority()
    assert authority.model_eval_artifact_hash == (
        "f843e044e771ad99b49c6603e1c593fd16e3cbb83163b16247b28b4f5be32271"
    )
    assert authority.selected_candidate.candidate_key == "L2"
    assert authority.selected_candidate.model == "gpt-5.6-terra"
    assert authority.selected_candidate.reasoning_effort == "low"
    assert authority.selected_eval_metrics.passed_cases == 9
    assert authority.selected_eval_metrics.critical_safety_failures == 0
    assert authority.actual_paid_eval_cost_usd == Decimal("0.4515130")
    assert set(authority.full_ladder_pass_summary) == {"L1", "L2", "L3", "L4"}
    assert authority.semantic_replay_receipts_complete == 36


def test_initial_selected_model_authority_recomputes_selection_after_tamper() -> None:
    raw = json.loads(
        DEFAULT_INITIAL_SELECTED_MODEL_AUTHORITY_PATH.read_text(encoding="utf-8")
    )
    raw["full_ladder_pass_summary"]["L3"]["estimated_cost_usd"] = "0.001"
    raw["actual_paid_eval_cost_usd"] = "0.353932"
    raw["selection_hash"] = canonical_sha256(raw, exclude_fields=("selection_hash",))

    with pytest.raises(ValueError, match="candidate disagrees with frozen selection rule"):
        InitialSelectedModelAuthority.model_validate(raw)


def test_initial_selected_model_authority_hash_fails_closed_on_payload_drift() -> None:
    raw = json.loads(
        DEFAULT_INITIAL_SELECTED_MODEL_AUTHORITY_PATH.read_text(encoding="utf-8")
    )
    raw["receipt_manifest_hash"] = "0" + raw["receipt_manifest_hash"][1:]

    with pytest.raises(ValueError, match="selection_hash"):
        InitialSelectedModelAuthority.model_validate(raw)


def _synthetic_paid_eval(authority):
    records = []
    candidate_by_key = {
        "L1": ("gpt-5.6-luna", "medium", 1),
        "L2": ("gpt-5.6-terra", "low", 2),
        "L3": ("gpt-5.6-terra", "medium", 3),
        "L4": ("gpt-5.6-sol", "medium", 4),
    }
    for key, metrics in authority.full_ladder_pass_summary.items():
        model, effort, position = candidate_by_key[key]
        failed = metrics.required_cases - metrics.passed_cases
        cases = []
        for index, case_id in enumerate(EXPECTED_INITIAL_CASE_IDS):
            passed = not (failed and index == 0)
            critical = bool(failed and index == 0)
            cases.append(
                {
                    "case_id": case_id,
                    "name": case_id,
                    "lane": "BULL",
                    "critical_safety": critical,
                    "passed": passed,
                    "findings": [],
                    "response_id": f"resp-{key}-{case_id}",
                    "requested_model": model,
                    "effective_model": model,
                    "model_calls": 1,
                    "latency_ms": 0,
                    "input_tokens": 0,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "estimated_cost_usd": "0",
                    "output_hash": "a" * 64,
                    "result_hash": "b" * 64,
                }
            )
        record = {
            "candidate_key": key,
            "model": model,
            "reasoning_effort": effort,
            "ladder_position": position,
            "cases": cases,
            "passed_cases": metrics.passed_cases,
            "required_cases": metrics.required_cases,
            "all_required_checks_passed": metrics.passed_cases == metrics.required_cases,
            "critical_safety_failures": metrics.critical_safety_failures,
            "estimated_cost_usd": str(metrics.estimated_cost_usd),
            "latency_ms": metrics.latency_ms,
            "total_tokens": metrics.total_tokens,
        }
        record["record_hash"] = canonical_sha256(record)
        records.append(record)

    payload = {
        "artifact_version": authority.model_eval_artifact_version,
        "run_class": "B4_INITIAL_REAL_MODEL_EVAL",
        "status": "PASS_SELECTED",
        "run_id": authority.paid_run_id,
        "code_commit_sha": authority.source_git_commit,
        "eval_version": authority.eval_version,
        "model_policy_version": authority.model_policy_version,
        "cost_preflight_artifact_hash": authority.cost_preflight_artifact_hash,
        "paid_authorization_artifact_hash": authority.paid_authorization_artifact_hash,
        "approved_cost_ceiling_usd": "4.7220156",
        "dry_run_manifest_hash": authority.dry_run_manifest_hash,
        "pricing_version": "OPENAI_TEXT_PRICING_2026_08_29",
        "pricing_hash": "c" * 64,
        "pricing_as_of_date": "2026-08-29",
        "pricing_captured_at_utc": "2026-08-29T18:45:42Z",
        "pricing_capture_basis": "test",
        "case_ids": list(EXPECTED_INITIAL_CASE_IDS),
        "candidate_records": records,
        "selection": {
            "status": "SELECTED",
            "selected_candidate": {
                "candidate_key": authority.selected_candidate.candidate_key,
                "model": authority.selected_candidate.model,
                "reasoning_effort": authority.selected_candidate.reasoning_effort,
                "ladder_position": authority.selected_candidate.ladder_position,
            },
            "reason_code": authority.selection_reason_code,
        },
        "dispatch_attempts": 36,
        "model_calls": 36,
        "actual_cost_usd": str(authority.actual_paid_eval_cost_usd),
        "cost_receipt_status": authority.cost_receipt_status,
        "paid_call_receipt_hashes": [f"{index:064x}" for index in range(36)],
        "receipt_manifest_hash": authority.receipt_manifest_hash,
        "receipt_journal_path": ".aic-runtime/test.jsonl",
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
        "provider_reads": 0,
        "external_writes": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_verify_initial_model_eval_artifact_reads_full_ladder_records() -> None:
    authority = load_initial_selected_model_authority()
    payload = _synthetic_paid_eval(authority)
    synthetic_authority = authority.model_copy(
        update={"model_eval_artifact_hash": payload["artifact_hash"]}
    )

    verify_initial_model_eval_artifact(payload, authority=synthetic_authority)


def test_verify_initial_model_eval_artifact_rejects_case_order_drift() -> None:
    authority = load_initial_selected_model_authority()
    payload = _synthetic_paid_eval(authority)
    payload["candidate_records"][0]["cases"][0]["case_id"] = "E2"
    payload["candidate_records"][0]["record_hash"] = canonical_sha256(
        payload["candidate_records"][0], exclude_fields=("record_hash",)
    )
    payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    synthetic_authority = authority.model_copy(
        update={"model_eval_artifact_hash": payload["artifact_hash"]}
    )

    with pytest.raises(InitialSelectedModelAuthorityError, match="ordered case set"):
        verify_initial_model_eval_artifact(payload, authority=synthetic_authority)
