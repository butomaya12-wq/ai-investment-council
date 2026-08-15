from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .schema_runtime import BUNDLE, CANONICAL_NAMES, RESOURCES

_FILENAME_MAP = {
    "INVESTMENT_MANDATE_V1":"investment_mandate_v1.json",
    "EVIDENCE_POLICY_V1":"evidence_policy_v1.json",
    "EVIDENCE_ITEM_V1":"evidence_item_v1.json",
    "RAW_NUMERIC_VALUE_V1":"raw_numeric_value_v1.json",
    "COMPUTED_VALUE_V1":"computed_value_v1.json",
    "PROVIDER_READ_RECEIPT_V1":"provider_read_receipt_v1.json",
    "CONFLICT_RECORD_V1":"conflict_record_v1.json",
    "SECURITY_TYPE_PROOF_V1":"security_type_proof_v1.json",
    "B2_DATA_SNAPSHOT_V1":"snapshot_b2_data_v1.json",
    "DEEP_COMPARISON_RESULT_V1":"deep_comparison_result_v1.json",
    "B3_RESEARCH_SNAPSHOT_V1":"snapshot_research_v1.json",
    "MATERIAL_CLAIM_V1":"material_claim_v1.json",
    "CANDIDATE_PACKET_V1":"candidate_packet_v1.json",
    "MODEL_RUN_RECEIPT_V1":"model_run_receipt_v1.json",
    "COUNCIL_OPINION_V1":"council_opinion_v1.json",
    "FINAL_DECISION_V1":"final_decision_v1.json",
    "INVALIDATION_CONDITION_V1":"invalidation_condition_v1.json",
    "DECISION_CHANGE_CONDITION_V1":"decision_change_condition_v1.json",
    "RESEARCH_REOPEN_REQUEST_V1":"research_reopen_request_v1.json",
    "SIZING_POLICY_V1":"sizing_policy_v1.json",
    "RISK_POLICY_V1":"risk_policy_v1.json",
    "SIZING_COMPUTATION_V1":"sizing_computation_v1.json",
    "PROPOSAL_CANDIDATE_V1":"proposal_candidate_v1.json",
    "RISK_CHECK_RESULT_V1":"risk_check_result_v1.json",
    "B5_RISK_INPUT_SNAPSHOT_V1":"snapshot_b5_risk_v1.json",
    "B5_RISK_RESULT_V1":"risk_result_b5_v1.json",
    "PORTFOLIO_IMPACT_V1":"portfolio_impact_v1.json",
    "ACCEPTED_PROPOSAL_CANDIDATE_V1":"accepted_proposal_candidate_v1.json",
    "TRADE_PROPOSAL_V1":"trade_proposal_v1.json",
    "APPROVAL_ENVELOPE_V1":"approval_envelope_v1.json",
    "OWNER_ACCOUNT_V1":"owner_account_v1.json",
    "OWNER_SESSION_V1":"owner_session_v1.json",
    "APPROVAL_ACTION_NONCE_V1":"approval_action_nonce_v1.json",
    "B6_PRE_SUBMIT_SNAPSHOT_V1":"snapshot_b6_pre_submit_v1.json",
    "B6_PREPARE_RISK_RESULT_V1":"risk_result_b6_prepare_v1.json",
    "EXECUTION_GATE_EVALUATION_V1":"execution_gate_evaluation_v1.json",
    "EXECUTION_INTENT_V1":"execution_intent_v1.json",
    "B6_COMMIT_STATE_SNAPSHOT_V1":"snapshot_b6_commit_v1.json",
    "B6_COMMIT_RISK_RESULT_V1":"risk_result_b6_commit_v1.json",
    "BROKER_SUBMIT_RECEIPT_V1":"broker_submit_receipt_v1.json",
    "EXECUTION_EVENT_V1":"execution_event_v1.json",
    "RECONCILIATION_RESULT_V1":"reconciliation_result_v1.json",
    "MONITOR_SUBSCRIPTION_V1":"monitor_subscription_v1.json",
    "MONITOR_SOURCE_EVENT_V1":"monitor_source_event_v1.json",
    "MONITOR_TRIGGER_MATCH_V1":"monitor_trigger_match_v1.json",
    "THESIS_EVENT_V1":"thesis_event_v1.json",
    "REVIEW_WORK_ITEM_V1":"review_work_item_v1.json",
    "JOURNAL_EVENT_ENVELOPE_V1":"journal_event_envelope_v1.json",
    "AGGREGATE_HEAD_V1":"aggregate_head_v1.json",
    "SCHEMA_REGISTRY_ENTRY_V1":"schema_registry_entry_v1.json",
    "RUN_ENVELOPE_V1":"run_envelope_v1.json",
    "POLICY_REFERENCE_V1":"policy_reference_v1.json",
    "EVAL_RUN_V1":"eval_run_v1.json",
    "EVAL_CASE_RESULT_V1":"eval_case_result_v1.json",
    "MUTATION_RESULT_V1":"mutation_result_v1.json",
    "POLICY_VALUE_V1":"policy_value_v1.json",
    "DECISION_LIFECYCLE_POLICY_V1":"decision_lifecycle_policy_v1.json",
    "DECISION_TTL_V1":"decision_ttl_v1.json",
    "NEXT_REVIEW_TRIGGER_V1":"next_review_trigger_v1.json",
}


def canonical_resource_bytes(name: str) -> bytes:
    return json.dumps(
        RESOURCES[name], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def resource_sha256(name: str) -> str:
    return hashlib.sha256(canonical_resource_bytes(name)).hexdigest()


def export_canonical_schemas(output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if set(_FILENAME_MAP) != set(CANONICAL_NAMES):
        raise RuntimeError("canonical filename map does not match 59-schema inventory")
    manifest: dict[str, str] = {}
    for name in CANONICAL_NAMES:
        path = output_dir / _FILENAME_MAP[name]
        payload = json.dumps(RESOURCES[name], ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path.write_text(payload, encoding="utf-8")
        manifest[_FILENAME_MAP[name]] = resource_sha256(name)
    return manifest
