from pathlib import Path


def test_b4_initial_request_preflight_is_zero_call_surface() -> None:
    source = Path("scripts/b4_initial_request_preflight.py").read_text(encoding="utf-8")
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
