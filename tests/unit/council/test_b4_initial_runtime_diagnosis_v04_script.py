from pathlib import Path


def test_v04_stage_contract_diagnosis_is_zero_call_surface() -> None:
    script = Path("scripts/b4_diagnose_initial_runtime_block_v04.py").read_text(
        encoding="utf-8"
    )
    module = Path("src/aic/council/initial_runtime_diagnosis_v04.py").read_text(
        encoding="utf-8"
    )
    combined = script + module

    assert "OPENAI_API_KEY" not in combined
    assert "--execute-paid-initial" not in combined
    assert "StdlibResponsesTransport" not in combined
    assert "b4_initial_runtime_paid_receipts_v0_4.jsonl" in script
    assert "b4_initial_council_freeze_v0_4.json" in script
    assert "DECISION_BASIS is Judge-only and forbidden in initial opinions" in module
    assert "model_calls_performed_by_diagnosis\": 0" in script
    assert "provider_reads_performed_by_diagnosis\": 0" in script
    assert "rerun_authorized\": False" in script
