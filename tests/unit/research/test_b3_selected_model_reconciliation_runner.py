from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "b3_reconcile_selected_model.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("b3_reconcile_selected_model_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_model_reconciliation_runner_imports_and_is_bounded() -> None:
    module = _load_script()
    assert module.ARTIFACT_VERSION == "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1"
    assert module.RUN_CLASS == "B3_SELECTED_MODEL_REAL_CANDIDATE_RECONCILIATION"
    assert str(module.DEFAULT_MODEL_EVAL) == ".aic-runtime/b3_model_eval.json"
    assert str(module.DEFAULT_OUTPUT) == ".aic-runtime/b3_selected_model_reconciliation.json"


def test_public_summary_excludes_full_drafts_and_claim_payloads() -> None:
    module = _load_script()
    artifact = {
        "artifact_version": module.ARTIFACT_VERSION,
        "run_class": module.RUN_CLASS,
        "model_eval_artifact_hash": "a" * 64,
        "selected_model_authority_hash": "b" * 64,
        "selected_candidate": {
            "candidate_key": "M2",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "ladder_position": 2,
        },
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "retrieval_artifact_hash": "c" * 64,
        "candidates": [
            {
                "candidate": "NVDA",
                "status": "CANONICAL_RECONCILED",
                "research_status": "INCOMPLETE",
                "claim_count": 3,
                "repair_attempts": 0,
                "response_id": "resp_safe",
                "draft_hash": "d" * 64,
                "candidate_packet": {"packet_hash": "e" * 64},
                "model_run_receipt": {"receipt_hash": "f" * 64},
                "source_gaps": ["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
                "reconstructibility_status": "PASS",
                "initial_draft": {"secret_prose": "must stay local"},
                "validated_draft": {"secret_prose": "must stay local"},
                "material_claims": [{"claim_text": "must stay local"}],
            }
        ],
        "reconstructibility_status": "PASS",
        "canonical_reconciliation": "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED",
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "artifact_hash": "1" * 64,
    }
    summary = module._public_summary(artifact, output_path=Path("x.json"))
    candidate = summary["candidates"][0]
    assert "initial_draft" not in candidate
    assert "validated_draft" not in candidate
    assert "material_claims" not in candidate
    assert candidate["packet_hash"] == "e" * 64
