from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "b3_independent_review.py"


def _load_script():
    module_name = f"b3_independent_review_test_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_independent_review_runner_is_local_one_shot_and_bounded() -> None:
    module = _load_script()
    assert module.ARTIFACT_VERSION == "B3_INDEPENDENT_REVIEW_ARTIFACT_v0_1"
    assert module.RUN_CLASS == "B3_INDEPENDENT_READ_ONLY_ACCEPTANCE_REVIEW"
    assert str(module.DEFAULT_RETRIEVAL) == ".aic-runtime/b3_retrieval_batch.json"
    assert str(module.DEFAULT_MODEL_EVAL) == ".aic-runtime/b3_model_eval.json"
    assert str(module.DEFAULT_RECONCILIATION) == ".aic-runtime/b3_selected_model_reconciliation.json"
    assert str(module.DEFAULT_OUTPUT) == ".aic-runtime/b3_independent_review.json"

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "execute_synthesis_runtime" not in source
    assert "execute_planner_runtime" not in source
    assert "execute_retrieval_plan" not in source
    assert "provider_adapters" not in source
    assert "submit_order(" not in source
    assert "repair_attempts\": 0" in source
    assert "tools\": []" not in source  # tools are frozen in the review contract module, not rebuilt here.


def test_independent_review_secret_scan_and_public_summary_do_not_expose_review_input() -> None:
    module = _load_script()
    assert module._secret_scan({"safe": "ordinary evidence"}) == ()
    hits = module._secret_scan({"unsafe": "OPENAI_API_KEY"})
    assert "OPENAI_API_KEY_NAME" in hits

    artifact = {
        "artifact_version": module.ARTIFACT_VERSION,
        "run_class": module.RUN_CLASS,
        "review_status": "PASS",
        "reviewer": {"candidate_key": "M3", "model": "gpt-5.6-sol"},
        "reconciliation_artifact_hash": "a" * 64,
        "model_eval_artifact_hash": "b" * 64,
        "review_input_hash": "c" * 64,
        "review_input": {"secret_evidence_text": "must remain local"},
        "model_call": {"response_id": "resp_review"},
        "repair_attempts": 0,
        "review": {
            "attack_results": [
                {
                    "attack_class": "PROMPT_INJECTION",
                    "status": "PASS",
                    "finding": "No material gap identified.",
                    "evidence_refs": ["STATIC:prompt_injection"],
                }
            ],
            "material_gap_summary": [],
            "inconclusive_summary": [],
        },
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "artifact_hash": "d" * 64,
    }
    summary = module._public_summary(artifact, output_path=Path("x.json"))
    assert "review_input" not in summary
    assert "secret_evidence_text" not in str(summary)
    assert summary["response_id"] == "resp_review"
