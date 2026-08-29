from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "b3_independent_review_preflight.py"


def _load_script():
    module_name = f"b3_independent_review_preflight_test_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_preflight_is_zero_call_and_local_only() -> None:
    module = _load_script()
    assert module.ARTIFACT_VERSION == "B3_INDEPENDENT_REVIEW_PREFLIGHT_ARTIFACT_v0_1"
    assert module.RUN_CLASS == "B3_INDEPENDENT_REVIEW_LOCAL_ZERO_CALL_PREFLIGHT"
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "load_openai_api_key" not in source
    assert ".post(" not in source
    assert "urlopen(" not in source
    assert "execute_synthesis_runtime" not in source
    assert "execute_planner_runtime" not in source
    assert "execute_retrieval_plan" not in source
    assert "submit_order(" not in source
    assert '"model_calls": 0' in source
    assert '"provider_reads": 0' in source
    assert '"broker_writes": 0' in source
    assert '"alpaca_orders": 0' in source


def test_candidate_metrics_count_bounded_evidence() -> None:
    module = _load_script()
    record = {
        "candidate": "NVDA",
        "claims": [{"claim_id": "C1"}, {"claim_id": "C2"}],
        "referenced_computed_values": [{"computed_value_id": "V1"}],
        "referenced_evidence": [
            {
                "review_value": "abc",
                "review_value_truncated": False,
                "original_char_count": 3,
            },
            {
                "review_value": "abcdef",
                "review_value_truncated": True,
                "original_char_count": 30,
            },
        ],
    }
    metrics = module._candidate_metrics(record)
    assert metrics == {
        "candidate": "NVDA",
        "claim_count": 2,
        "referenced_evidence_count": 2,
        "referenced_computed_value_count": 1,
        "truncated_evidence_count": 1,
        "evidence_original_char_count": 33,
        "evidence_review_char_count": 9,
    }
