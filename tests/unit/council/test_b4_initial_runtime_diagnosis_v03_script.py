from pathlib import Path


def test_v03_diagnosis_script_has_no_paid_dispatch_surface() -> None:
    text = Path("scripts/b4_diagnose_initial_runtime_block_v03.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "OPENAI_API_KEY",
        "--execute-paid-initial",
        "HttpResponsesTransport",
        "requests.post",
        "urllib.request",
    ):
        assert forbidden not in text

    assert ".aic-runtime/b4_initial_runtime_paid_receipts_v0_3.jsonl" in text
    assert ".aic-runtime/b4_initial_council_freeze_v0_3.json" in text
    assert "rerun_authorized" in text
