from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aic.council import rebuttal_runtime_preflight as runtime
from aic.council.model_policy import CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from aic.council.rebuttal_schema_repair_v01 import (
    REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
    REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
    REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
    REBUTTAL_SCHEMA_REPAIR_VERSION,
    REBUTTAL_SCHEMA_VERSION,
)
from aic.domain.canonical import canonical_sha256


def _source(head: str) -> dict:
    variants = []
    for candidate_id in ("NVDA", "MSFT", "META"):
        for key, model, effort in (
            ("R1", "gpt-5.6-terra", "low"),
            ("R2", "gpt-5.6-terra", "medium"),
            ("R3", "gpt-5.6-sol", "medium"),
        ):
            variants.append({
                "candidate": candidate_id,
                "candidate_key": key,
                "model": model,
                "reasoning_effort": effort,
                "request_hash": canonical_sha256({"candidate": candidate_id, "key": key}),
                "request_body_utf8_bytes": 70000 + len(variants),
                "schema_hash": "1" * 64,
                "prompt_contract_version": "P-B4-PROMPTS-v0.2",
                "prompt_version": "REBUTTAL_v0.2",
                "prompt_hash": "2" * 64,
                "schema_version": REBUTTAL_SCHEMA_VERSION,
                "input_hash": "3" * 64,
                "max_output_tokens": STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL],
            })
    artifact = {
        "artifact_version": "B4_REBUTTAL_SOURCE_REQUEST_PREFLIGHT_v0_1",
        "status": "PASS_ZERO_CALL_REBUTTAL_SOURCE_REQUEST_PREFLIGHT",
        "code_commit_sha": head,
        "initial_council_freeze_artifact_hash": runtime.EXPECTED_INITIAL_FREEZE_HASH,
        "schema_repair_version": REBUTTAL_SCHEMA_REPAIR_VERSION,
        "schema_version": REBUTTAL_SCHEMA_VERSION,
        "promotion_semantics_contract_version": REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION,
        "opposing_lane_contract_version": REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION,
        "claim_type_contract_version": REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION,
        "candidate_order": ["NVDA", "MSFT", "META"],
        "production_rebuttal_calls_after_selection": 3,
        "request_variants": variants,
        "request_variant_count": 9,
        "request_manifest_hash": runtime.EXPECTED_SOURCE_REQUEST_MANIFEST,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _selection() -> dict:
    return {
        "selection_hash": runtime.EXPECTED_SELECTION_HASH,
        "selected_candidate": dict(runtime.EXPECTED_SELECTED),
        "model_eval_artifact_hash": "1533a224f9a0c85abb77f42526aeed24e76c7e0453bc85cc5c8f8881669ae414",
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
    }


def test_runtime_request_preflight_selects_exact_three_r3_requests(monkeypatch) -> None:
    head = "a" * 40
    monkeypatch.setattr(
        runtime,
        "verify_rebuttal_selected_model_authority_v02",
        lambda payload: runtime.EXPECTED_SELECTION_HASH,
    )
    artifact = runtime.build_rebuttal_runtime_request_preflight(
        source_preflight=_source(head),
        selection_authority=_selection(),
        code_commit_sha=head,
    )
    assert artifact["status"] == runtime.REBUTTAL_RUNTIME_REQUEST_PREFLIGHT_STATUS
    assert artifact["selected_candidate"] == runtime.EXPECTED_SELECTED
    assert artifact["candidate_order"] == ["NVDA", "MSFT", "META"]
    assert artifact["planned_paid_calls_max"] == 3
    assert artifact["automatic_repair_calls_authorized"] is False
    assert [(row["candidate"], row["candidate_key"]) for row in artifact["selected_request_variants"]] == [
        ("NVDA", "R3"),
        ("MSFT", "R3"),
        ("META", "R3"),
    ]
    assert all(row["model"] == "gpt-5.6-sol" for row in artifact["selected_request_variants"])
    assert all(row["reasoning_effort"] == "medium" for row in artifact["selected_request_variants"])
    assert runtime.verify_rebuttal_runtime_request_preflight(artifact) == artifact["artifact_hash"]
    assert artifact["model_calls"] == 0
    assert artifact["production_rebuttal_authorized"] is False
    assert artifact["judge_authorized"] is False
    assert artifact["live_money"] == "PROHIBITED"


def test_runtime_request_preflight_rejects_non_r3_authority(monkeypatch) -> None:
    head = "b" * 40
    monkeypatch.setattr(
        runtime,
        "verify_rebuttal_selected_model_authority_v02",
        lambda payload: runtime.EXPECTED_SELECTION_HASH,
    )
    selection = _selection()
    selection["selected_candidate"] = {
        "candidate_key": "R2",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "ladder_position": 2,
    }
    with pytest.raises(runtime.RebuttalRuntimePreflightError):
        runtime.build_rebuttal_runtime_request_preflight(
            source_preflight=_source(head),
            selection_authority=selection,
            code_commit_sha=head,
        )


def test_runtime_cost_preflight_prices_only_three_sol_requests(monkeypatch) -> None:
    head = "c" * 40
    monkeypatch.setattr(
        runtime,
        "verify_rebuttal_selected_model_authority_v02",
        lambda payload: runtime.EXPECTED_SELECTION_HASH,
    )
    request = runtime.build_rebuttal_runtime_request_preflight(
        source_preflight=_source(head),
        selection_authority=_selection(),
        code_commit_sha=head,
    )
    cost = runtime.build_rebuttal_runtime_cost_preflight(request)
    assert cost["status"] == runtime.REBUTTAL_RUNTIME_COST_PREFLIGHT_STATUS
    assert cost["planned_paid_calls_max"] == 3
    assert len(cost["per_call_cost_upper_bounds"]) == 3
    assert all(row["model"] == "gpt-5.6-sol" for row in cost["per_call_cost_upper_bounds"])
    ceiling = Decimal(cost["total_rebuttal_runtime_cost_upper_bound_usd"])
    assert Decimal("0") < ceiling < Decimal("3")
    assert cost["owner_cost_approval_required"] is True
    assert cost["automatic_repair_calls_authorized"] is False
    assert cost["cache_write_input_rate_multiplier"] == "1.25"
    assert runtime.verify_rebuttal_runtime_cost_preflight(cost) == cost["artifact_hash"]
    assert cost["model_calls"] == 0
    assert cost["provider_reads"] == 0
    assert cost["production_rebuttal_authorized"] is False
    assert cost["judge_authorized"] is False
    assert cost["rerun_authorized"] is False


def test_production_preflight_scripts_are_zero_call() -> None:
    for path in (
        Path("scripts/b4_rebuttal_runtime_request_preflight_v01.py"),
        Path("scripts/b4_rebuttal_runtime_cost_preflight_v01.py"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY" not in text
        assert "StdlibResponsesTransport" not in text
        assert '"model_calls": 0' in text
        assert '"production_rebuttal_authorized": False' in text
        assert '"judge_authorized": False' in text
        assert '"rerun_authorized": False' in text
