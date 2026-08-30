from pathlib import Path


def test_v05_preflight_and_proof_scripts_are_zero_call_surfaces():
    source = Path("scripts/b4_initial_request_preflight_v05.py").read_text(encoding="utf-8")
    runtime = Path("scripts/b4_initial_runtime_request_preflight_v05.py").read_text(
        encoding="utf-8"
    )
    proof = Path("scripts/b4_verify_initial_schema_repair_v05.py").read_text(
        encoding="utf-8"
    )
    combined = source + runtime + proof
    assert "OPENAI_API_KEY" not in combined
    assert "--execute-paid-initial" not in combined
    assert "StdlibResponsesTransport" not in combined
    assert "build_bounded_initial_request_v05" in source
    assert "initial_schema_repair_version" in source
    assert "initial_promotion_semantics_contract_version" in source
    assert "initial_stage_claim_type_contract_version" in source
    assert "initial_allowed_claim_types" in source
    assert "judge_only_claim_types" in source
    assert "initial_schema_repair_version" in runtime
    assert "initial_stage_claim_type_contract_version" in runtime
    assert "B4_LOCAL_ZERO_CALL_RETAINED_V04_STAGE_CONTRACT_SCHEMA_REPAIR_PROOF" in proof
    assert "non_stage_promotion_rule_failure_count" in proof
    assert '"model_calls": 0' in proof
    assert '"provider_reads": 0' in proof
    assert '"rerun_authorized": False' in proof


def test_v05_paid_runner_uses_fresh_evidence_paths_and_stage_schema_builder():
    text = Path("scripts/b4_run_initial_runtime_v05.py").read_text(encoding="utf-8")
    assert "b4_initial_council_freeze_v0_5.json" in text
    assert "b4_initial_runtime_paid_authorization_v0_5.json" in text
    assert "b4_initial_runtime_paid_receipts_v0_5.jsonl" in text
    assert "build_bounded_initial_request_v05" in text
    assert "initial_promotion_semantics_contract_version" in text
    assert "initial_stage_claim_type_contract_version" in text
    assert "initial_allowed_claim_types" in text
    assert "judge_only_claim_types" in text
    assert "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_5" in text
    assert "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_5" in text
