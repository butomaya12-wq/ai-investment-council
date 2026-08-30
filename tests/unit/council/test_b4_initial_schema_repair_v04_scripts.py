from pathlib import Path


def test_v04_preflight_and_proof_scripts_are_zero_call_surfaces():
    source = Path("scripts/b4_initial_request_preflight_v04.py").read_text(encoding="utf-8")
    runtime = Path("scripts/b4_initial_runtime_request_preflight_v04.py").read_text(
        encoding="utf-8"
    )
    proof = Path("scripts/b4_verify_initial_schema_repair_v04.py").read_text(
        encoding="utf-8"
    )
    combined = source + runtime + proof
    assert "OPENAI_API_KEY" not in combined
    assert "--execute-paid-initial" not in combined
    assert "StdlibResponsesTransport" not in combined
    assert "build_bounded_initial_request_v04" in source
    assert "initial_schema_repair_version" in source
    assert "initial_promotion_semantics_contract_version" in source
    assert "initial_schema_repair_version" in runtime
    assert "initial_promotion_semantics_contract_version" in runtime
    assert "B4_LOCAL_ZERO_CALL_RETAINED_V03_OUTPUT_SCHEMA_REPAIR_PROOF" in proof
    assert '"model_calls": 0' in proof
    assert '"provider_reads": 0' in proof
    assert '"rerun_authorized": False' in proof


def test_v04_paid_runner_uses_fresh_evidence_paths_and_promotion_schema_builder():
    text = Path("scripts/b4_run_initial_runtime_v04.py").read_text(encoding="utf-8")
    assert "b4_initial_council_freeze_v0_4.json" in text
    assert "b4_initial_runtime_paid_authorization_v0_4.json" in text
    assert "b4_initial_runtime_paid_receipts_v0_4.jsonl" in text
    assert "build_bounded_initial_request_v04" in text
    assert "initial_promotion_semantics_contract_version" in text
    assert "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_4" in text
    assert "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_4" in text
