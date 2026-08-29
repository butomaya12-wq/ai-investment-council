from pathlib import Path


SCRIPT = Path("scripts/b4_initial_eval_cost_preflight.py")


def test_b4_initial_eval_cost_preflight_is_zero_call_approval_gate() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "StdlibResponsesTransport" not in source
    assert "load_openai_api_key" not in source
    assert "OPENAI_API_KEY" not in source
    assert ".post(" not in source
    assert "urllib" not in source
    assert "requests." not in source
    assert "cost_upper_bound_usd" in source
    assert "planned_paid_calls_max" in source
    assert "owner_cost_approval_required" in source
    assert "REQUIRES_OWNER_COST_APPROVAL_BEFORE_INITIAL_MODEL_EVAL" in source
    assert '"model_calls": 0' in source
    assert '"provider_reads": 0' in source
    assert '"broker_writes": 0' in source
    assert '"alpaca_orders": 0' in source
    assert '"live_money": "PROHIBITED"' in source
