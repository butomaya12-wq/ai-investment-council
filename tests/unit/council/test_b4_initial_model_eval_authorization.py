from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256


SCRIPT = Path("scripts/b4_initial_model_eval.py")


def _module():
    spec = spec_from_file_location("b4_initial_model_eval_test_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cost_artifact():
    value = {
        "artifact_version": "B4_INITIAL_EVAL_COST_PREFLIGHT_ARTIFACT_v0_1",
        "status": "REQUIRES_OWNER_COST_APPROVAL_BEFORE_INITIAL_MODEL_EVAL",
        "planned_paid_calls_max": 36,
        "max_output_tokens_per_call": 4096,
        "eval_case_ids": ["E1", "E2", "E5", "E6", "E7", "E8", "E9", "E13", "E16"],
        "owner_cost_approval_required": True,
        "eval_request_body_utf8_bytes_upper_bound": 40118,
        "total_initial_model_eval_cost_upper_bound_usd": "4.6269612",
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    value["artifact_hash"] = canonical_sha256(value)
    return value


def test_paid_eval_requires_exact_cost_hash_and_exact_ceiling() -> None:
    module = _module()
    cost = _cost_artifact()
    with pytest.raises(module.B4InitialEvalAuthorizationError):
        module.validate_paid_execution_authorization(
            cost,
            approve_cost_artifact_hash=None,
            approve_max_usd=None,
        )
    with pytest.raises(module.B4InitialEvalAuthorizationError):
        module.validate_paid_execution_authorization(
            cost,
            approve_cost_artifact_hash=cost["artifact_hash"],
            approve_max_usd="5.00",
        )
    approved = module.validate_paid_execution_authorization(
        cost,
        approve_cost_artifact_hash=cost["artifact_hash"],
        approve_max_usd="4.6269612",
    )
    assert str(approved) == "4.6269612"


def test_dry_run_precedes_secret_loading_and_paid_execution_flag_is_required() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'parser.add_argument("--execute-paid-eval", action="store_true")' in source
    dry_gate = source.index("if not args.execute_paid_eval:")
    secret_import = source.index("from aic.research.runtime import load_openai_api_key")
    secret_load = source.index("api_key = load_openai_api_key()")
    approval = source.index("approved_ceiling = validate_paid_execution_authorization")
    assert dry_gate < approval < secret_import < secret_load
    assert "execute_case_once" in source
    assert "while True" not in source
    assert "repair" not in source.lower()
