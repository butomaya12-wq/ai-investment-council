from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from aic.council.model_selection import InitialSelectedModelAuthority
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_final_competition_closure_v01 as closure_v01


ARTIFACT_VERSION = "B4_POST_RESEARCH_REOPEN_VERDICT_PREFLIGHT_v0_1"
PASS_STATUS = "B4_POST_RESEARCH_REOPEN_VERDICT_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "B4_POST_RESEARCH_REOPEN_INITIAL_REQUEST_COST_PREFLIGHT_ZERO_CALL"
COST_AUTHORITY_MODE = "STAGED_EXACT"

EXPECTED_FINAL_CLOSURE_HASH = "ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149"
EXPECTED_FINAL_CLOSURE_CODE_SHA = "18226ad79bdfda85c589fb15877cfe92f6b6b13f"
EXPECTED_FINAL_CLOSURE_STATUS = "B3_RESEARCH_REOPEN_CLOSED_FOR_NEW_B4_VERDICT"
EXPECTED_S00_HASH = closure_v01.EXPECTED_S00_HASH
EXPECTED_LOCAL_REPLAY_HASH = closure_v01.EXPECTED_LOCAL_REPLAY_HASH
EXPECTED_ORIGINAL_RESULT_HASH = closure_v01.EXPECTED_ORIGINAL_RESULT_HASH
EXPECTED_WIRE_V02_RESULT_HASH = closure_v01.EXPECTED_WIRE_V02_RESULT_HASH
EXPECTED_REPAIR_RESULT_HASH = closure_v01.EXPECTED_REPAIR_RESULT_HASH
EXPECTED_REPAIR_AUTH_HASH = closure_v01.EXPECTED_REPAIR_AUTH_HASH
EXPECTED_SALVAGED_NVDA_SHA256 = closure_v01.EXPECTED_RR3_SALVAGED_SHA
EXPECTED_MSFT_RESPONSE_HASH = closure_v01.EXPECTED_MSFT_RESPONSE_HASH
EXPECTED_INITIAL_SELECTION_HASH = "0554900c0e7c1b696a681301d249d011f6d500331fe53751998024477269d1e0"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_REQUIREMENTS = closure_v01.REQUIREMENTS
EXPECTED_META_CONDITION_IDS = (
    "META_CONDITION_001",
    "META_CONDITION_002",
    "META_CONDITION_003",
    "META_CONDITION_004",
)

EXPECTED_INITIAL_MODEL = {
    "candidate_key": "L2",
    "stage": "INITIAL",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "low",
    "ladder_position": 2,
}
EXPECTED_REBUTTAL_MODEL = {
    "candidate_key": "R3",
    "stage": "REBUTTAL",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "medium",
    "ladder_position": 3,
}
EXPECTED_JUDGE_MODEL = {
    "candidate_key": "J1",
    "stage": "JUDGE",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "medium",
    "ladder_position": 1,
}


class PostResearchReopenVerdictPreflightError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise PostResearchReopenVerdictPreflightError(message)


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostResearchReopenVerdictPreflightError(f"unable to read {label}") from exc
    _need(isinstance(payload, dict), f"{label} root must be object")
    return payload


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    _need(
        observed == canonical_sha256(payload, exclude_fields=(field,)),
        f"{field} self-hash mismatch",
    )
    return observed


def _verify_final_closure(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_FINAL_CLOSURE_HASH, "final B3 closure hash drift")
    try:
        closure_v01.verify_final_closure(
            payload,
            expected_code_commit_sha=EXPECTED_FINAL_CLOSURE_CODE_SHA,
        )
    except Exception as exc:
        raise PostResearchReopenVerdictPreflightError(
            "final B3 closure typed/deterministic verification failed"
        ) from exc
    _need(payload.get("status") == EXPECTED_FINAL_CLOSURE_STATUS, "final B3 closure status drift")
    _need(payload.get("canonical_research_reopen_closed") is True, "B3 reopen is not closed")
    _need(payload.get("remaining_canonical_reopen_requirement_ids") == [], "canonical B3 gaps remain")
    _need(payload.get("additional_provider_read_required_before_b4") is False, "B3 requires more provider evidence")
    _need(payload.get("new_b4_verdict_required") is True, "new B4 verdict requirement missing")
    _need(payload.get("provider_reads_authorized") is False, "B3 closure unexpectedly grants provider authority")
    _need(payload.get("model_calls_authorized") is False, "B3 closure unexpectedly grants model authority")
    return observed


def _verify_initial_selected_model_authority(payload: Mapping[str, Any]) -> str:
    _need(payload.get("selection_hash") == EXPECTED_INITIAL_SELECTION_HASH, "Initial selected-model hash drift")
    try:
        typed = InitialSelectedModelAuthority.model_validate(dict(payload))
    except Exception as exc:
        raise PostResearchReopenVerdictPreflightError(
            "Initial selected-model authority typed validation failed"
        ) from exc
    normalized = typed.model_dump(mode="python", exclude_none=False)
    _need(
        canonical_sha256(normalized, exclude_fields=("selection_hash",))
        == EXPECTED_INITIAL_SELECTION_HASH,
        "Initial selected-model authority canonical replay mismatch",
    )
    _need(normalized.get("selection_status") == "SELECTED", "Initial selected-model authority is not SELECTED")
    _need(normalized.get("selected_candidate") == EXPECTED_INITIAL_MODEL, "Initial selected-model identity drift")
    return EXPECTED_INITIAL_SELECTION_HASH


def _verify_source_lineage(
    *,
    s00: Mapping[str, Any],
    local_replay: Mapping[str, Any],
    original_result: Mapping[str, Any],
    wire_v02_result: Mapping[str, Any],
    repair_result: Mapping[str, Any],
    repair_authorization: Mapping[str, Any],
    repair_raw_dir: Path,
) -> dict[str, Any]:
    try:
        s00_hash = closure_v01._verify_s00(s00)
        local_hash, valuation = closure_v01._verify_local_replay(local_replay)
        original_nvda = closure_v01._verify_original_nvda(original_result)
        msft_news = closure_v01._verify_wire_msft(wire_v02_result)
        repair_hash, _rr1 = closure_v01._verify_repair_result(
            repair_result,
            repair_authorization,
        )
        salvaged_sha, salvaged_payload = closure_v01._salvaged_nvda(repair_raw_dir)
    except Exception as exc:
        raise PostResearchReopenVerdictPreflightError(
            "saved B3 evidence lineage verification failed"
        ) from exc

    _need(s00_hash == EXPECTED_S00_HASH, "S00 hash drift")
    _need(local_hash == EXPECTED_LOCAL_REPLAY_HASH, "local replay hash drift")
    _need(repair_hash == EXPECTED_REPAIR_RESULT_HASH, "repair result hash drift")
    _need(_self_hash(repair_authorization) == EXPECTED_REPAIR_AUTH_HASH, "repair authorization hash drift")
    _need(salvaged_sha == EXPECTED_SALVAGED_NVDA_SHA256, "salvaged NVDA SHA drift")

    retained_nvda_ids = [int(article.article_id) for article in original_nvda.articles]
    salvaged_rows = salvaged_payload.get("news")
    _need(isinstance(salvaged_rows, list), "salvaged NVDA news missing")
    salvaged_nvda_ids = [
        int(row["id"])
        for row in salvaged_rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), int)
    ]
    combined_nvda_ids = [*retained_nvda_ids, *salvaged_nvda_ids]
    _need(len(retained_nvda_ids) == 10, "retained NVDA article count drift")
    _need(len(salvaged_nvda_ids) == 5, "salvaged NVDA article count drift")
    _need(len(combined_nvda_ids) == 15 and len(set(combined_nvda_ids)) == 15, "NVDA article surface drift")

    msft_ids = [int(article.article_id) for article in msft_news.articles]
    _need(len(msft_ids) == 8 and len(set(msft_ids)) == 8, "MSFT article surface drift")
    _need(msft_news.pagination_complete is True, "MSFT current news pagination is not complete")

    conditions = s00.get("judge_change_conditions_for_executable_invest")
    _need(isinstance(conditions, list), "Judge META condition surface missing")
    condition_ids = tuple(
        row.get("condition_id")
        for row in conditions
        if isinstance(row, Mapping)
    )
    _need(condition_ids == EXPECTED_META_CONDITION_IDS, "Judge META condition identity drift")

    return {
        "s00_hash": s00_hash,
        "local_replay_hash": local_hash,
        "original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "wire_v02_result_hash": EXPECTED_WIRE_V02_RESULT_HASH,
        "repair_result_hash": repair_hash,
        "repair_authorization_hash": EXPECTED_REPAIR_AUTH_HASH,
        "salvaged_nvda_sha256": salvaged_sha,
        "nvda_article_ids": combined_nvda_ids,
        "msft_article_ids": msft_ids,
        "valuation": dict(valuation),
        "meta_condition_ids": list(condition_ids),
    }


def _closure_map(closure: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = closure.get("requirement_closures")
    _need(isinstance(rows, list), "final closure requirement_closures missing")
    result = {
        str(row.get("requirement_id")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("requirement_id"), str)
    }
    _need(tuple(result) == EXPECTED_REQUIREMENTS, "final closure requirement identity drift")
    return result


def build_verdict_preflight(
    *,
    code_commit_sha: str,
    final_closure: Mapping[str, Any],
    s00: Mapping[str, Any],
    local_replay: Mapping[str, Any],
    original_result: Mapping[str, Any],
    wire_v02_result: Mapping[str, Any],
    repair_result: Mapping[str, Any],
    repair_authorization: Mapping[str, Any],
    repair_raw_dir: Path,
    initial_selected_model_authority: Mapping[str, Any],
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    closure_hash = _verify_final_closure(final_closure)
    source = _verify_source_lineage(
        s00=s00,
        local_replay=local_replay,
        original_result=original_result,
        wire_v02_result=wire_v02_result,
        repair_result=repair_result,
        repair_authorization=repair_authorization,
        repair_raw_dir=repair_raw_dir,
    )
    initial_selection_hash = _verify_initial_selected_model_authority(
        initial_selected_model_authority
    )
    closures = _closure_map(final_closure)

    nvda = closures["NVDA_CURRENT_DEVELOPMENTS_Q4"]
    msft_val = closures["MSFT_VALUATION_CONTEXT_DEPTH"]
    msft_durability = closures["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]
    _need(nvda.get("closure_status") == "CLOSED_DECISION_USABLE_NONEXHAUSTIVE", "NVDA Q4 closure drift")
    _need(nvda.get("combined_unique_article_count") == 15, "NVDA Q4 article count drift")
    _need(msft_val.get("closure_status") == "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED", "MSFT valuation closure drift")
    _need(msft_durability.get("closure_status") == "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK", "MSFT durability closure drift")

    production_stages = [
        {
            "stage": "INITIAL",
            "selected_model": dict(EXPECTED_INITIAL_MODEL),
            "fresh_model_calls_max": 9,
            "dependency": "POST_RESEARCH_REOPEN_MODEL_FACING_INPUT_FREEZE",
        },
        {
            "stage": "REBUTTAL",
            "selected_model": dict(EXPECTED_REBUTTAL_MODEL),
            "fresh_model_calls_max": 3,
            "dependency": "FRESH_INITIAL_COUNCIL_FREEZE",
        },
        {
            "stage": "JUDGE",
            "selected_model": dict(EXPECTED_JUDGE_MODEL),
            "fresh_model_calls_max": 1,
            "dependency": "FRESH_REBUTTAL_COUNCIL_FREEZE",
        },
    ]

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_b3_final_closure_hash": closure_hash,
        "source_b3_final_closure_code_commit_sha": EXPECTED_FINAL_CLOSURE_CODE_SHA,
        "canonical_research_reopen_closed": True,
        "remaining_canonical_reopen_requirement_ids": [],
        "additional_provider_read_required_before_b4": False,
        "candidate_order": list(EXPECTED_CANDIDATES),
        "evidence_source_manifest": [
            {"source": "B3_FINAL_CLOSURE", "artifact_hash": closure_hash},
            {"source": "B3_S00_SCOPE_V03", "artifact_hash": source["s00_hash"]},
            {"source": "B3_LOCAL_REPLAY", "artifact_hash": source["local_replay_hash"]},
            {"source": "B3_ORIGINAL_NVDA_PROVIDER_RESULT", "artifact_hash": source["original_result_hash"]},
            {"source": "B3_WIRE_REPAIR_V02_RESULT", "artifact_hash": source["wire_v02_result_hash"]},
            {"source": "B3_CR4_TO_CR6_REPAIR_RESULT", "artifact_hash": source["repair_result_hash"]},
            {"source": "B3_CR4_TO_CR6_REPAIR_AUTHORIZATION", "artifact_hash": source["repair_authorization_hash"]},
            {"source": "NVDA_SALVAGED_PROVIDER_RESPONSE", "sha256": source["salvaged_nvda_sha256"]},
        ],
        "post_research_reopen_decision_context": {
            "NVDA": {
                "current_developments_disposition": nvda.get("closure_status"),
                "current_article_count": 15,
                "current_article_ids": list(source["nvda_article_ids"]),
                "pagination_boundary": "NONEXHAUSTIVE_CURRENT_COVERAGE; TERMINAL_PAGINATION_NOT_CLAIMED",
                "directional_inference_from_closure_forbidden": True,
            },
            "MSFT": {
                "valuation_disposition": msft_val.get("closure_status"),
                "valuation_context": {
                    "msft_point_in_time_pe": msft_val.get("msft_point_in_time_pe"),
                    "meta_point_in_time_pe": msft_val.get("meta_point_in_time_pe"),
                    "msft_pe_premium_vs_meta_ratio": msft_val.get("msft_pe_premium_vs_meta_ratio"),
                    "interpretive_boundary": msft_val.get("interpretive_boundary"),
                },
                "durability_disposition": msft_durability.get("closure_status"),
                "durability_decision_rule": msft_durability.get("decision_rule"),
                "current_article_count": 8,
                "current_article_ids": list(source["msft_article_ids"]),
                "positive_extrapolation_forbidden": True,
            },
            "META": {
                "judge_change_condition_ids": list(source["meta_condition_ids"]),
                "conditions_preserved_as_decision_context": True,
                "conditions_are_not_canonical_b3_reopen_requirements": True,
            },
        },
        "known_transport_limitations": [
            {
                "surface": "RR2_DYNAMIC_MARKET_CONTEXT",
                "failure_class": "ALPACA_TLS_HANDSHAKE_TIMEOUT",
                "canonical_b3_blocker": False,
            },
            {
                "surface": "RR3_NVDA_NEWS_CONTINUATION_SECOND_DISPATCH",
                "failure_class": "ALPACA_TLS_HANDSHAKE_TIMEOUT",
                "canonical_b3_blocker": False,
            },
        ],
        "model_selection_plan": {
            "INITIAL": {
                "selection_hash": initial_selection_hash,
                "selected_model": dict(EXPECTED_INITIAL_MODEL),
                "eval_rerun_required": False,
            },
            "REBUTTAL": {
                "selected_model": dict(EXPECTED_REBUTTAL_MODEL),
                "eval_rerun_required": False,
                "historical_authority_revalidation_required_before_paid_dispatch": True,
            },
            "JUDGE": {
                "selected_model": dict(EXPECTED_JUDGE_MODEL),
                "eval_rerun_required": False,
                "historical_authority_revalidation_required_before_paid_dispatch": True,
            },
        },
        "model_eval_reruns_required": False,
        "planned_model_eval_calls": 0,
        "fresh_production_stages": production_stages,
        "planned_fresh_production_model_calls_max": 13,
        "planned_paid_calls_max": 13,
        "stage_dependencies": [
            "INITIAL_FREEZE_BEFORE_REBUTTAL",
            "REBUTTAL_FREEZE_BEFORE_JUDGE",
            "JUDGE_PROPOSAL_POSTPROCESS_BEFORE_FINAL_DECISION_OR_B5",
        ],
        "historical_b4_frozen_outputs_reusable_as_new_model_outputs": False,
        "historical_reopen_restricted_judge_runtime_reusable": False,
        "new_post_research_reopen_judge_contract_required": True,
        "initial_model_facing_materialization_required": True,
        "initial_model_facing_materialization_contract": {
            "must_materialize_saved_evidence_content_not_only_ids": True,
            "nvda_retained_typed_articles": 10,
            "nvda_salvaged_raw_articles": 5,
            "msft_typed_current_articles": 8,
            "valuation_context_required": True,
            "final_closure_interpretive_boundaries_required": True,
            "meta_judge_decision_context_required": True,
            "legacy_candidate_packets_and_material_claims_mutable": False,
            "new_evidence_must_be_additive": True,
            "provider_read_for_materialization_allowed": False,
        },
        "cost_authority_mode": COST_AUTHORITY_MODE,
        "owner_cost_approval_required": True,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "automatic_retries": 0,
        "automatic_repair_calls_authorized": 0,
        "broker_writes_authorized": 0,
        "alpaca_orders_authorized": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )
    return artifact


def verify_verdict_preflight(
    payload: Mapping[str, Any],
    *,
    expected_code_commit_sha: str,
) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "artifact version drift")
    _need(payload.get("status") == PASS_STATUS, "preflight status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "preflight code SHA drift")
    _need(payload.get("source_b3_final_closure_hash") == EXPECTED_FINAL_CLOSURE_HASH, "preflight closure lineage drift")
    _need(payload.get("candidate_order") == list(EXPECTED_CANDIDATES), "candidate order drift")
    _need(payload.get("canonical_research_reopen_closed") is True, "preflight reopened B3")
    _need(payload.get("remaining_canonical_reopen_requirement_ids") == [], "preflight has B3 gaps")
    _need(payload.get("additional_provider_read_required_before_b4") is False, "preflight requests provider read")
    _need(payload.get("planned_model_eval_calls") == 0, "model eval rerun drift")
    _need(payload.get("planned_fresh_production_model_calls_max") == 13, "fresh model-call ceiling drift")
    _need(payload.get("planned_paid_calls_max") == 13, "paid-call ceiling drift")
    _need(payload.get("historical_b4_frozen_outputs_reusable_as_new_model_outputs") is False, "historical B4 output reuse drift")
    _need(payload.get("new_post_research_reopen_judge_contract_required") is True, "new Judge contract requirement missing")
    materialization = payload.get("initial_model_facing_materialization_contract")
    _need(isinstance(materialization, Mapping), "model-facing materialization contract missing")
    _need(materialization.get("must_materialize_saved_evidence_content_not_only_ids") is True, "saved evidence content materialization requirement missing")
    _need(materialization.get("provider_read_for_materialization_allowed") is False, "materialization provider read unexpectedly allowed")
    _need(payload.get("model_calls_authorized") is False, "preflight grants model authority")
    _need(payload.get("provider_reads_authorized") is False, "preflight grants provider authority")
    _need(payload.get("automatic_retries") == 0, "automatic retry drift")
    _need(payload.get("broker_writes_authorized") == 0, "broker authority drift")
    _need(payload.get("alpaca_orders_authorized") == 0, "order authority drift")
    _need(payload.get("live_money") == "PROHIBITED", "live-money boundary drift")
    _need(payload.get("final_decision_created") is False, "FinalDecision created too early")
    _need(payload.get("b5_handoff_created") is False, "B5 handoff created too early")
    _need(payload.get("next_gate") == NEXT_GATE, "next gate drift")
    return observed


def load_and_build_verdict_preflight(
    *,
    code_commit_sha: str,
    final_closure_path: str | Path,
    s00_path: str | Path,
    local_replay_path: str | Path,
    original_result_path: str | Path,
    wire_v02_result_path: str | Path,
    repair_result_path: str | Path,
    repair_authorization_path: str | Path,
    repair_raw_dir: str | Path,
    initial_selected_model_authority_path: str | Path,
) -> dict[str, Any]:
    return build_verdict_preflight(
        code_commit_sha=code_commit_sha,
        final_closure=_read_object(final_closure_path, label="final B3 closure"),
        s00=_read_object(s00_path, label="S00 scope"),
        local_replay=_read_object(local_replay_path, label="B3 local replay"),
        original_result=_read_object(original_result_path, label="original NVDA provider result"),
        wire_v02_result=_read_object(wire_v02_result_path, label="wire-repair V02 result"),
        repair_result=_read_object(repair_result_path, label="CR4-to-CR6 repair result"),
        repair_authorization=_read_object(repair_authorization_path, label="CR4-to-CR6 repair authorization"),
        repair_raw_dir=Path(repair_raw_dir),
        initial_selected_model_authority=_read_object(
            initial_selected_model_authority_path,
            label="Initial selected-model authority",
        ),
    )
