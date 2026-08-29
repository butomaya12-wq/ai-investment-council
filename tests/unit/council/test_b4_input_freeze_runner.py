from pathlib import Path


def test_b4_input_freeze_runner_has_zero_model_provider_broker_surface() -> None:
    source = Path("scripts/b4_prepare_council_inputs.py").read_text(encoding="utf-8")
    forbidden = (
        "load_openai_api_key",
        "StdlibResponsesTransport",
        "ResponsesTransport",
        "execute_synthesis_runtime",
        "provider_adapters",
        "AlpacaNewsRetrievalAdapter",
        "SecFilingRetrievalAdapter",
        "submit_order",
        "place_order",
    )
    for token in forbidden:
        assert token not in source
    assert "model_calls" in source
    assert "provider_reads" in source
    assert "broker_writes" in source
    assert "alpaca_orders" in source
