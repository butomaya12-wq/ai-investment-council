from pathlib import Path


def test_schema_repair_preflight_scripts_are_zero_call_surfaces():
    source = Path("scripts/b4_initial_request_preflight_v03.py").read_text(encoding="utf-8")
    runtime = Path("scripts/b4_initial_runtime_request_preflight_v03.py").read_text(
        encoding="utf-8"
    )
    combined = source + runtime
    assert "OPENAI_API_KEY" not in combined
    assert "--execute-paid-initial" not in combined
    assert "StdlibResponsesTransport" not in combined
    assert "build_bounded_initial_request_v03" in source
    assert "initial_schema_repair_version" in source
    assert "initial_schema_repair_version" in runtime


def test_v03_paid_runner_uses_fresh_evidence_paths_and_repaired_request_builder():
    text = Path("scripts/b4_run_initial_runtime_v03.py").read_text(encoding="utf-8")
    assert "b4_initial_council_freeze_v0_3.json" in text
    assert "b4_initial_runtime_paid_authorization_v0_3.json" in text
    assert "b4_initial_runtime_paid_receipts_v0_3.jsonl" in text
    assert "build_bounded_initial_request_v03" in text
    assert "runtime preflight does not bind Initial schema repair version" in text
    assert "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_3" in text
    assert "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_3" in text
