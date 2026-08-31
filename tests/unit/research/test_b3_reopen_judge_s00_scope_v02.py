from __future__ import annotations

from copy import deepcopy

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_s00_scope_v01 as v01
from aic.research import reopen_judge_s00_scope_v02 as v02


def _claim(claim_id: str, category: str, text: str) -> dict:
    return {
        "assumptions": [],
        "candidate_id": "MSFT",
        "category": category,
        "claim_hash": "0" * 64,
        "claim_id": claim_id,
        "claim_kind": "INFERENCE",
        "claim_text": text,
        "computed_value_ids": [],
        "conflict_ids": [],
        "evidence_ids": ["EVID_TEST"],
        "materiality": "MATERIAL",
        "support_status": "SUPPORTED",
        "uncertainty_note": None,
    }


def _production_shaped_freeze() -> dict:
    valuation = _claim(
        v01.EXPECTED_REOPEN_REASONS[1],
        "INTEGRITY_FINDING",
        "A single trailing price-to-latest-reported-annual-GAAP-diluted-EPS observation does not establish valuation attractiveness.",
    )
    durability = _claim(
        v01.EXPECTED_REOPEN_REASONS[2],
        "ASSUMPTION",
        "Forward cloud and AI monetization and investment returns remain an unverified assumption.",
    )
    payload = {
        "artifact_version": "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3",
        "status": "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN",
        "candidate_order": ["NVDA", "MSFT", "META"],
        "research_reopen_required_candidates": ["NVDA", "MSFT"],
        "processed_records": [
            {
                "candidate_id": "NVDA",
                "material_claims": [],
                "research_reopen_required": True,
                "research_reopen_reason_codes": [v01.EXPECTED_REOPEN_REASONS[0]],
            },
            {
                "candidate_id": "MSFT",
                "material_claims": [valuation, durability],
                "research_reopen_required": True,
                "research_reopen_reason_codes": [
                    v01.EXPECTED_REOPEN_REASONS[1],
                    v01.EXPECTED_REOPEN_REASONS[2],
                ],
            },
            {
                "candidate_id": "META",
                "material_claims": [],
                "research_reopen_required": False,
                "research_reopen_reason_codes": [],
            },
        ],
        "rebuttal_rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_v02_derives_reopen_reasons_from_real_processed_record_shape() -> None:
    freeze = _production_shaped_freeze()
    observed, valuation, durability = v02.verify_rebuttal_freeze(
        freeze,
        expected_hash=freeze["artifact_hash"],
    )
    assert observed == freeze["artifact_hash"]
    assert valuation["claim_id"] == v01.EXPECTED_REOPEN_REASONS[1]
    assert durability["claim_id"] == v01.EXPECTED_REOPEN_REASONS[2]
    assert "research_reopen_reason_codes_by_candidate" not in freeze


def test_v02_rejects_processed_record_reason_tamper_even_with_correct_top_level_map() -> None:
    freeze = _production_shaped_freeze()
    freeze["research_reopen_reason_codes_by_candidate"] = {
        "NVDA": [v01.EXPECTED_REOPEN_REASONS[0]],
        "MSFT": [v01.EXPECTED_REOPEN_REASONS[1], v01.EXPECTED_REOPEN_REASONS[2]],
    }
    freeze["processed_records"][1]["research_reopen_reason_codes"] = [
        v01.EXPECTED_REOPEN_REASONS[1]
    ]
    freeze["artifact_hash"] = canonical_sha256(
        freeze,
        exclude_fields=("artifact_hash",),
    )
    with pytest.raises(v02.JudgeReopenS00ScopeV02Error, match="MSFT Rebuttal reopen reason drift"):
        v02.verify_rebuttal_freeze(freeze, expected_hash=freeze["artifact_hash"])


def test_v02_rejects_top_level_reopen_candidate_list_inconsistent_with_records() -> None:
    freeze = _production_shaped_freeze()
    freeze["research_reopen_required_candidates"] = ["NVDA"]
    freeze["artifact_hash"] = canonical_sha256(
        freeze,
        exclude_fields=("artifact_hash",),
    )
    with pytest.raises(
        v02.JudgeReopenS00ScopeV02Error,
        match="top-level reopen candidates do not match processed records",
    ):
        v02.verify_rebuttal_freeze(freeze, expected_hash=freeze["artifact_hash"])


def test_v02_build_restores_v01_verifier_and_emits_v02_audit_fields(monkeypatch) -> None:
    valuation = _claim(v01.EXPECTED_REOPEN_REASONS[1], "INTEGRITY_FINDING", "valuation")
    durability = _claim(v01.EXPECTED_REOPEN_REASONS[2], "ASSUMPTION", "durability")

    monkeypatch.setattr(v01, "verify_reopen_request", lambda payload: v01.EXPECTED_REOPEN_HASH)
    monkeypatch.setattr(v01, "verify_postprocess", lambda payload: v01.EXPECTED_POSTPROCESS_HASH)
    monkeypatch.setattr(v01, "verify_judge_result", lambda payload: v01.EXPECTED_JUDGE_RESULT_HASH)
    monkeypatch.setattr(
        v02,
        "verify_rebuttal_freeze",
        lambda payload: (v01.EXPECTED_REBUTTAL_FREEZE_HASH, valuation, durability),
    )

    original = v01.verify_rebuttal_freeze
    judge = {
        "judge_proposal": {
            "what_would_change_decision": [
                {
                    "condition_id": condition_id,
                    "condition_text": f"Condition {index}",
                    "source_or_claim_refs": [f"REF_{index}"],
                }
                for index, condition_id in enumerate(v01.EXPECTED_META_CONDITION_IDS, start=1)
            ]
        }
    }

    artifact = v02.build_scope_artifact(
        reopen_request={},
        postprocess={},
        judge_result=judge,
        rebuttal_freeze={},
        code_commit_sha="a" * 40,
    )

    assert v01.verify_rebuttal_freeze is original
    assert artifact["artifact_version"] == v02.ARTIFACT_VERSION
    assert artifact["status"] == v02.PASS_STATUS
    assert artifact["rebuttal_reason_derivation"] == v02.REASON_DERIVATION
    assert artifact["supersedes_failed_v01_code_commit_sha"] == v02.FAILED_V01_CODE_SHA
    assert artifact["provider_reads"] == 0
    assert artifact["model_calls"] == 0
    assert artifact["broad_b3_rerun_authorized"] is False
    v02.verify_scope_artifact(artifact, expected_code_commit_sha="a" * 40)


def test_v02_scope_self_hash_rejects_tamper(monkeypatch) -> None:
    # Exercise V02-specific verification without relying on mutable runtime evidence.
    payload = {
        "artifact_version": v02.ARTIFACT_VERSION,
        "status": v02.PASS_STATUS,
        "code_commit_sha": "b" * 40,
        "rebuttal_reason_derivation": v02.REASON_DERIVATION,
        "supersedes_failed_v01_code_commit_sha": v02.FAILED_V01_CODE_SHA,
        "v01_runtime_failure_class": "FALSE_ASSUMPTION_TOP_LEVEL_REBUTTAL_REASON_MAP",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    monkeypatch.setattr(v01, "verify_scope_artifact", lambda *args, **kwargs: "ok")
    monkeypatch.setattr(v02, "_as_v01_semantic_view", lambda value: {})
    assert v02.verify_scope_artifact(payload, expected_code_commit_sha="b" * 40) == payload["artifact_hash"]
    tampered = deepcopy(payload)
    tampered["status"] = "TAMPERED"
    with pytest.raises(v02.JudgeReopenS00ScopeV02Error, match="self-hash mismatch"):
        v02.verify_scope_artifact(tampered, expected_code_commit_sha="b" * 40)
