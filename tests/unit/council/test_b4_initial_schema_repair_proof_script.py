from pathlib import Path


def test_retained_output_schema_repair_proof_is_zero_call():
    text = Path("scripts/b4_verify_initial_schema_repair_v03.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in text
    assert "StdlibResponsesTransport" not in text
    assert "--execute-paid-initial" not in text
    assert "b4_initial_runtime_paid_receipts_v0_2.jsonl" in text
    assert "PASS_EXACT_RETAINED_OUTPUT_REJECTED_BY_REPAIRED_SCHEMA" in text
    assert '"model_calls": 0' in text
    assert '"provider_reads": 0' in text
    assert '"rerun_authorized": False' in text
