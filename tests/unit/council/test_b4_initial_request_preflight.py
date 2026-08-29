from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path("scripts/b4_initial_request_preflight.py")


def _load_preflight_module():
    spec = importlib.util.spec_from_file_location("b4_initial_request_preflight_test_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_b4_initial_request_preflight_is_zero_call_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "StdlibResponsesTransport" not in source
    assert "load_openai_api_key" not in source
    assert "OPENAI_API_KEY" not in source
    assert ".post(" not in source
    assert "urllib" not in source
    assert "requests." not in source
    assert "build_initial_request" in source
    assert "assert_request_invariants" in source
    assert '"model_calls": 0' in source
    assert '"provider_reads": 0' in source
    assert '"broker_writes": 0' in source
    assert '"alpaca_orders": 0' in source
    assert '"live_money": "PROHIBITED"' in source


def test_semantic_schema_hash_ignores_only_application_model_run_ref() -> None:
    module = _load_preflight_module()
    schema_l1 = {
        "type": "object",
        "properties": {
            "candidate_id": {"type": "string", "const": "NVDA"},
            "model_run_ref": {"type": "string", "const": "B4_INITIAL_NVDA_BULL_L1_abc"},
        },
        "required": ["candidate_id", "model_run_ref"],
        "additionalProperties": False,
    }
    schema_l2 = {
        "type": "object",
        "properties": {
            "candidate_id": {"type": "string", "const": "NVDA"},
            "model_run_ref": {"type": "string", "const": "B4_INITIAL_NVDA_BULL_L2_abc"},
        },
        "required": ["candidate_id", "model_run_ref"],
        "additionalProperties": False,
    }
    assert module._semantic_schema_hash(schema_l1) == module._semantic_schema_hash(schema_l2)

    schema_drift = {
        **schema_l2,
        "properties": {
            **schema_l2["properties"],
            "candidate_id": {"type": "string", "const": "MSFT"},
        },
    }
    assert module._semantic_schema_hash(schema_l1) != module._semantic_schema_hash(schema_drift)
