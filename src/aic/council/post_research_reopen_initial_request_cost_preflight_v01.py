from __future__ import annotations

from decimal import Decimal
import json
import re
from pathlib import Path
from typing import Any, Mapping

from aic.council.initial_runtime_cost_v02 import (
    _decimal_text,
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from aic.council.initial_schema_repair_v05 import build_bounded_initial_request_v05
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_policy import CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from aic.council.model_selection import InitialSelectedModelAuthority
from aic.council.models import CouncilInputFreezeArtifact, CouncilLane
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_final_competition_closure_v01 as closure_v01
from aic.research.handoff import load_real_event_handoff

from . import post_research_reopen_verdict_preflight_v01 as verdict_v01


ARTIFACT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_REQUEST_COST_PREFLIGHT_v0_1"
PASS_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_REQUEST_COST_PREFLIGHT_ZERO_CALL_PASS"
NEXT_GATE = "B4_POST_RESEARCH_REOPEN_INITIAL_OWNER_APPROVAL_GATED_PRODUCTION_DISPATCH"
MODEL_INPUT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_MODEL_INPUT_v0_1"
EXPECTED_SOURCE_VERDICT_HASH = "fb8fb489ee31e1e3fb7763aee6499d1c95e7e7f02f4ac22a2d8ead2f479fde4d"
EXPECTED_SOURCE_VERDICT_CODE_SHA = "c5956302d0c2cce0d8855b46240795e85aeb3251"
EXPECTED_B3_CLOSURE_HASH = "ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149"
EXPECTED_INITIAL_SELECTION_HASH = "0554900c0e7c1b696a681301d249d011f6d500331fe53751998024477269d1e0"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
CALL_COUNT_CEILING = 9

_STAGE_LANE = (
    (CouncilRequestStage.BULL_INITIAL, CouncilLane.BULL),
    (CouncilRequestStage.BEAR_INITIAL, CouncilLane.BEAR),
    (CouncilRequestStage.RED_TEAM_INITIAL, CouncilLane.RED_TEAM),
)


class PostResearchReopenInitialRequestCostPreflightError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise PostResearchReopenInitialRequestCostPreflightError(message)


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostResearchReopenInitialRequestCostPreflightError(
            f"unable to read {label}"
        ) from exc
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


def _request_body_utf8_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _article_payload(article: Any) -> dict[str, Any]:
    payload = article.model_dump(mode="json", exclude_none=False)
    _need(isinstance(payload.get("content"), str) and payload["content"], "article content missing")
    _need(isinstance(payload.get("article_id"), int), "typed article id missing")
    return payload


def _closure_rows(closure: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = closure.get("requirement_closures")
    _need(isinstance(rows, list), "B3 requirement closures missing")
    result = {
        str(row.get("requirement_id")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("requirement_id"), str)
    }
    _need(tuple(result) == closure_v01.REQUIREMENTS, "B3 requirement closure identity drift")
    return result


def _materialize_saved_evidence(
    *,
    final_closure: Mapping[str, Any],
    s00: Mapping[str, Any],
    local_replay: Mapping[str, Any],
    original_result: Mapping[str, Any],
    wire_v02_result: Mapping[str, Any],
    repair_result: Mapping[str, Any],
    repair_authorization: Mapping[str, Any],
    repair_raw_dir: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Rebuild the additive post-reopen evidence view without any provider I/O."""

    source = verdict_v01._verify_source_lineage(
        s00=s00,
        local_replay=local_replay,
        original_result=original_result,
        wire_v02_result=wire_v02_result,
        repair_result=repair_result,
        repair_authorization=repair_authorization,
        repair_raw_dir=repair_raw_dir,
    )
    closure_v01.verify_final_closure(
        final_closure,
        expected_code_commit_sha=verdict_v01.EXPECTED_FINAL_CLOSURE_CODE_SHA,
    )
    _need(_self_hash(final_closure) == EXPECTED_B3_CLOSURE_HASH, "B3 closure hash drift")
    closures = _closure_rows(final_closure)
    nvda_closure = closures["NVDA_CURRENT_DEVELOPMENTS_Q4"]
    valuation_closure = closures["MSFT_VALUATION_CONTEXT_DEPTH"]
    durability_closure = closures["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]

    retained = closure_v01._verify_original_nvda(original_result)
    salvaged_sha, salvaged = closure_v01._salvaged_nvda(repair_raw_dir)
    msft = closure_v01._verify_wire_msft(wire_v02_result)
    retained_articles = [_article_payload(article) for article in retained.articles]
    raw_articles = salvaged.get("news")
    _need(isinstance(raw_articles, list), "salvaged NVDA news missing")
    salvaged_articles = [dict(row) for row in raw_articles if isinstance(row, Mapping)]
    _need(len(salvaged_articles) == len(raw_articles), "salvaged NVDA row malformed")
    for row in salvaged_articles:
        _need(isinstance(row.get("id"), int), "salvaged NVDA article id missing")
        _need(isinstance(row.get("content"), str) and row["content"], "salvaged NVDA content missing")
    msft_articles = [_article_payload(article) for article in msft.articles]

    nvda_ids = [item["article_id"] for item in retained_articles] + [
        item["id"] for item in salvaged_articles
    ]
    msft_ids = [item["article_id"] for item in msft_articles]
    _need(len(retained_articles) == 10, "NVDA retained article count drift")
    _need(len(salvaged_articles) == 5, "NVDA salvaged article count drift")
    _need(len(nvda_ids) == 15 and len(set(nvda_ids)) == 15, "NVDA content surface drift")
    _need(len(msft_articles) == 8 and len(set(msft_ids)) == 8, "MSFT content surface drift")
    _need(retained.pagination_complete is False, "NVDA must remain nonexhaustive")
    _need(msft.pagination_complete is True, "MSFT pagination completeness drift")
    _need(
        nvda_closure.get("closure_status") == "CLOSED_DECISION_USABLE_NONEXHAUSTIVE",
        "NVDA closure disposition drift",
    )
    _need(
        valuation_closure.get("closure_status")
        == "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED",
        "MSFT valuation disposition drift",
    )
    _need(
        durability_closure.get("closure_status") == "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK",
        "MSFT durability disposition drift",
    )
    conditions = s00.get("judge_change_conditions_for_executable_invest")
    _need(isinstance(conditions, list), "META condition context missing")
    condition_rows = [dict(row) for row in conditions if isinstance(row, Mapping)]
    _need(len(condition_rows) == len(conditions), "META condition row malformed")
    _need(
        tuple(row.get("condition_id") for row in condition_rows)
        == verdict_v01.EXPECTED_META_CONDITION_IDS,
        "META condition identity drift",
    )

    materialization: dict[str, Any] = {
        "materialization_version": "B4_POST_RESEARCH_REOPEN_SAVED_EVIDENCE_CONTENT_v0_1",
        "source_b3_final_closure_hash": EXPECTED_B3_CLOSURE_HASH,
        "source_s00_scope_hash": source["s00_hash"],
        "source_local_replay_hash": source["local_replay_hash"],
        "source_original_nvda_result_hash": source["original_result_hash"],
        "source_wire_v02_msft_result_hash": source["wire_v02_result_hash"],
        "source_repair_result_hash": source["repair_result_hash"],
        "source_repair_auth_artifact_hash": source["repair_authorization_hash"],
        "source_salvaged_nvda_response_sha256": salvaged_sha,
        "NVDA": {
            "closure_status": nvda_closure["closure_status"],
            "evidence_disposition": "CURRENT_DEVELOPMENTS_COVERAGE_PRESENT; TERMINAL_PAGINATION_NOT_CLAIMED",
            "pagination_boundary": "NONEXHAUSTIVE_CURRENT_COVERAGE; TERMINAL_PAGINATION_NOT_CLAIMED",
            "directional_inference_from_closure_forbidden": True,
            "retained_typed_current_articles": retained_articles,
            "salvaged_raw_current_articles": salvaged_articles,
            "combined_unique_article_ids": nvda_ids,
            "combined_unique_article_count": 15,
        },
        "MSFT": {
            "valuation_context": {
                "msft_point_in_time_pe": "28.821727019499",
                "meta_point_in_time_pe": "24.550021285653",
                "msft_pe_premium_vs_meta_ratio": "0.174000082694118851",
                "interpretive_boundary": "RELATIVE_POINT_IN_TIME_CONTEXT_ONLY; DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS, FORWARD_EARNINGS_POWER, OR FAIR_VALUE",
            },
            "durability_disposition": durability_closure["closure_status"],
            "durability_decision_rule": durability_closure["decision_rule"],
            "positive_extrapolation_from_current_growth_or_margin_forbidden": True,
            "typed_current_articles": msft_articles,
            "current_article_ids": msft_ids,
            "current_article_count": 8,
        },
        "META": {
            "legacy_candidate_and_evidence_material_unchanged": True,
            "conditions_preserved_as_post_research_reopen_decision_context": condition_rows,
            "conditions_are_not_canonical_b3_reopen_requirements": True,
        },
        "legacy_candidate_packets_and_material_claims_immutable": True,
        "new_evidence_is_additive": True,
        "historical_b4_outputs_are_historical_context_only": True,
        "historical_b4_outputs_reused_as_fresh_model_outputs": False,
        "provider_read_for_materialization_allowed": False,
    }
    materialization["evidence_content_hash"] = canonical_sha256(
        materialization, exclude_fields=("evidence_content_hash",)
    )
    lineage = {
        key: str(materialization[key])
        for key in (
            "source_b3_final_closure_hash",
            "source_s00_scope_hash",
            "source_local_replay_hash",
            "source_original_nvda_result_hash",
            "source_wire_v02_msft_result_hash",
            "source_repair_result_hash",
            "source_repair_auth_artifact_hash",
            "source_salvaged_nvda_response_sha256",
        )
    }
    return materialization, lineage


def _build_post_research_inputs(
    *,
    freeze: CouncilInputFreezeArtifact,
    reconciliation: Mapping[str, Any],
    handoff: Any,
    materialization: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    legacy_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
    _need(
        tuple(item.candidate_id for item in legacy_inputs) == EXPECTED_CANDIDATES,
        "legacy Initial candidate order drift",
    )
    bundles = {bundle.candidate_id: bundle for bundle in freeze.bundles}
    _need(tuple(bundles) == EXPECTED_CANDIDATES, "frozen bundle candidate order drift")
    inputs: dict[str, Any] = {}
    for legacy in legacy_inputs:
        body = legacy.model_dump(mode="json", exclude_none=False, warnings=False)
        legacy_hash = body.pop("model_input_hash")
        body["model_input_version"] = MODEL_INPUT_VERSION
        body["post_research_reopen_overlay"] = {
            "legacy_model_input_hash": legacy_hash,
            "legacy_candidate_packet_and_material_claims_unchanged": True,
            "new_evidence_is_additive": True,
            "saved_evidence_content": dict(materialization),
        }
        body["model_input_hash"] = canonical_sha256(body)
        inputs[legacy.candidate_id] = body
    return inputs, bundles


def build_initial_request_cost_preflight(
    *,
    code_commit_sha: str,
    source_verdict_preflight: Mapping[str, Any],
    final_closure: Mapping[str, Any],
    s00: Mapping[str, Any],
    local_replay: Mapping[str, Any],
    original_result: Mapping[str, Any],
    wire_v02_result: Mapping[str, Any],
    repair_result: Mapping[str, Any],
    repair_authorization: Mapping[str, Any],
    repair_raw_dir: Path,
    freeze: CouncilInputFreezeArtifact,
    reconciliation: Mapping[str, Any],
    handoff: Any,
    initial_authority: InitialSelectedModelAuthority,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    source_hash = _self_hash(source_verdict_preflight)
    _need(source_hash == EXPECTED_SOURCE_VERDICT_HASH, "upstream verdict-preflight hash drift")
    verdict_v01.verify_verdict_preflight(
        source_verdict_preflight, expected_code_commit_sha=EXPECTED_SOURCE_VERDICT_CODE_SHA
    )
    _need(
        source_verdict_preflight.get("source_b3_final_closure_hash") == EXPECTED_B3_CLOSURE_HASH,
        "upstream B3 closure lineage drift",
    )
    _need(initial_authority.selection_hash == EXPECTED_INITIAL_SELECTION_HASH, "Initial selection hash drift")
    selected = initial_authority.selected_candidate
    _need(
        selected.candidate_key == "L2"
        and selected.stage.value == "INITIAL"
        and selected.model == "gpt-5.6-terra"
        and selected.reasoning_effort == "low"
        and selected.ladder_position == 2,
        "Initial selected model authority drift",
    )
    _need(source_verdict_preflight.get("model_eval_reruns_required") is False, "model eval rerun drift")

    materialization, lineage = _materialize_saved_evidence(
        final_closure=final_closure,
        s00=s00,
        local_replay=local_replay,
        original_result=original_result,
        wire_v02_result=wire_v02_result,
        repair_result=repair_result,
        repair_authorization=repair_authorization,
        repair_raw_dir=repair_raw_dir,
    )
    inputs, bundles = _build_post_research_inputs(
        freeze=freeze,
        reconciliation=reconciliation,
        handoff=handoff,
        materialization=materialization,
    )
    output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    request_rows: list[dict[str, Any]] = []
    total_cost = Decimal("0")
    for candidate in EXPECTED_CANDIDATES:
        model_input = inputs[candidate]
        bundle = bundles[candidate]
        for stage, lane in _STAGE_LANE:
            model_run_ref = (
                f"B4_POST_RESEARCH_REOPEN_INITIAL_{candidate}_{lane.value}_L2_"
                f"{model_input['model_input_hash'][:12]}"
            )
            request = build_bounded_initial_request_v05(
                stage=stage,
                model_candidate=selected,
                bundle=bundle,
                model_run_ref=model_run_ref,
                model_input=model_input,
                allowed_data_gap_refs=tuple(model_input["data_gap_refs"]),
            )
            payload = dict(request.request_payload)
            _need(payload.get("max_output_tokens") == output_cap, "Initial output token cap drift")
            bytes_upper_bound = _request_body_utf8_bytes(payload)
            per_call_cost = runtime_cost_upper_bound_usd(
                model=selected.model,
                input_tokens_upper_bound=bytes_upper_bound,
                output_tokens_upper_bound=output_cap,
                call_count=1,
                pricing=pricing,
            )
            total_cost += per_call_cost
            request_rows.append(
                {
                    "candidate": candidate,
                    "council_role": lane.value,
                    "stage": stage.value,
                    "model": selected.model,
                    "reasoning_effort": selected.reasoning_effort,
                    "model_run_ref": model_run_ref,
                    "prompt_contract_version": request.prompt_contract_version,
                    "prompt_version": request.prompt_version,
                    "prompt_hash": request.prompt_hash,
                    "model_facing_input_hash": model_input["model_input_hash"],
                    "relevant_evidence_source_lineage_hashes": lineage,
                    "request_payload_canonical_hash": canonical_sha256(payload),
                    "request_hash": request.request_hash,
                    "estimated_input_tokens_upper_bound": bytes_upper_bound,
                    "maximum_output_tokens": output_cap,
                    "estimated_max_cost_usd": _decimal_text(per_call_cost),
                    "request_payload": payload,
                }
            )
    _need(len(request_rows) <= CALL_COUNT_CEILING, "INITIAL call-count ceiling exceeded")
    _need(len(request_rows) == 9, "INITIAL request set must contain exactly nine calls")
    pricing_hash = pricing.get("pricing_hash")
    _need(
        isinstance(pricing_hash, str)
        and pricing_hash == canonical_sha256(pricing, exclude_fields=("pricing_hash",)),
        "pricing hash mismatch",
    )
    input_artifact_hash = canonical_sha256(
        {"candidate_order": list(EXPECTED_CANDIDATES), "model_inputs": inputs}
    )
    request_set_hash = canonical_sha256(
        [
            {
                "candidate": row["candidate"],
                "council_role": row["council_role"],
                "request_payload_canonical_hash": row["request_payload_canonical_hash"],
                "request_hash": row["request_hash"],
            }
            for row in request_rows
        ]
    )
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_verdict_preflight_hash": source_hash,
        "source_b3_closure_hash": EXPECTED_B3_CLOSURE_HASH,
        "model": selected.model,
        "purpose": "Freeze fresh post-research-reopen INITIAL Council requests and compute a bounded zero-call cost preflight.",
        "call_count_planned": len(request_rows),
        "call_count_ceiling": CALL_COUNT_CEILING,
        "input_artifact_hash": input_artifact_hash,
        "model_facing_inputs_by_candidate": inputs,
        "evidence_content_hash": materialization["evidence_content_hash"],
        "prompt_contract_version": request_rows[0]["prompt_contract_version"],
        "prompt_hashes": {row["stage"]: row["prompt_hash"] for row in request_rows[:3]},
        "initial_requests": request_rows,
        "request_set_hash": request_set_hash,
        "estimated_input_tokens_upper_bound_total": sum(
            int(row["estimated_input_tokens_upper_bound"]) for row in request_rows
        ),
        "estimated_input_tokens_upper_bound_max_per_request": max(
            int(row["estimated_input_tokens_upper_bound"]) for row in request_rows
        ),
        "maximum_output_tokens_per_request": output_cap,
        "maximum_output_tokens_total": output_cap * len(request_rows),
        "reasoning_effort": selected.reasoning_effort,
        "current_pricing_source": "config/event/openai_text_pricing_2026_08_30.json",
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing_hash,
        "pricing_capture_time": pricing["as_of_date"],
        "estimated_max_cost_usd": _decimal_text(total_cost),
        "input_token_upper_bound_method": "CONSERVATIVE: one input token per UTF-8 serialized request-body byte; all input is charged at the frozen cache-write upper-bound rate.",
        "why_a_paid_call_is_necessary": "A fresh post-research-reopen INITIAL Council assessment is required before any later rebuttal or judge stage; this artifact does not authorize that assessment.",
        "zero_call_gates_status": "PASS",
        "owner_approval_status": "NOT_GRANTED",
        "owner_approval_granted": False,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "model_calls_this_step": 0,
        "provider_reads_this_step": 0,
        "automatic_retries": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd_this_step": "0",
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_initial_request_cost_preflight(
    payload: Mapping[str, Any], *, expected_code_commit_sha: str
) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "artifact version drift")
    _need(payload.get("status") == PASS_STATUS, "artifact status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "artifact code SHA drift")
    _need(payload.get("source_verdict_preflight_hash") == EXPECTED_SOURCE_VERDICT_HASH, "source verdict hash drift")
    _need(payload.get("source_b3_closure_hash") == EXPECTED_B3_CLOSURE_HASH, "source B3 closure hash drift")
    _need(payload.get("model") == "gpt-5.6-terra", "selected model drift")
    _need(payload.get("reasoning_effort") == "low", "reasoning effort drift")
    _need(payload.get("call_count_planned") == 9, "planned call count drift")
    _need(payload.get("call_count_ceiling") == CALL_COUNT_CEILING, "call-count ceiling drift")
    rows = payload.get("initial_requests")
    _need(isinstance(rows, list) and len(rows) == 9, "frozen request set drift")
    for row in rows:
        _need(isinstance(row, Mapping), "frozen request row malformed")
        request_payload = row.get("request_payload")
        _need(isinstance(request_payload, Mapping), "frozen request payload missing")
        _need(
            row.get("request_payload_canonical_hash") == canonical_sha256(request_payload),
            "frozen request payload hash mismatch",
        )
        _need(row.get("model") == "gpt-5.6-terra", "request model drift")
        _need(row.get("reasoning_effort") == "low", "request effort drift")
        _need(type(row.get("estimated_input_tokens_upper_bound")) is int, "request token estimate invalid")
        _need(row.get("maximum_output_tokens") == STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL], "request output cap drift")
    inputs = payload.get("model_facing_inputs_by_candidate")
    _need(
        isinstance(inputs, Mapping) and set(inputs) == set(EXPECTED_CANDIDATES),
        "model-facing input surface drift",
    )
    _need(
        payload.get("input_artifact_hash")
        == canonical_sha256({"candidate_order": list(EXPECTED_CANDIDATES), "model_inputs": inputs}),
        "input artifact hash mismatch",
    )
    _need(payload.get("owner_approval_status") == "NOT_GRANTED", "owner approval status drift")
    _need(payload.get("owner_approval_granted") is False, "owner approval unexpectedly granted")
    for field, expected in (
        ("model_calls_authorized", False),
        ("provider_reads_authorized", False),
        ("model_calls_this_step", 0),
        ("provider_reads_this_step", 0),
        ("automatic_retries", 0),
        ("broker_writes", 0),
        ("alpaca_orders", 0),
        ("cost_usd_this_step", "0"),
        ("live_money", "PROHIBITED"),
        ("final_decision_created", False),
        ("b5_handoff_created", False),
    ):
        _need(payload.get(field) == expected, f"{field} drift")
    _need(payload.get("next_gate") == NEXT_GATE, "next gate drift")
    return observed


def load_and_build_initial_request_cost_preflight(
    *,
    code_commit_sha: str,
    source_verdict_preflight_path: str | Path,
    final_closure_path: str | Path,
    s00_path: str | Path,
    local_replay_path: str | Path,
    original_result_path: str | Path,
    wire_v02_result_path: str | Path,
    repair_result_path: str | Path,
    repair_authorization_path: str | Path,
    repair_raw_dir: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
) -> dict[str, Any]:
    try:
        freeze = CouncilInputFreezeArtifact.model_validate(
            _read_object(freeze_path, label="historical B4 input freeze")
        )
        authority = InitialSelectedModelAuthority.model_validate(
            _read_object(initial_authority_path, label="Initial selected-model authority")
        )
        handoff = load_real_event_handoff(Path(handoff_path))
        pricing = load_initial_runtime_pricing(Path(pricing_path))
    except Exception as exc:
        raise PostResearchReopenInitialRequestCostPreflightError(
            "typed local authority validation failed"
        ) from exc
    return build_initial_request_cost_preflight(
        code_commit_sha=code_commit_sha,
        source_verdict_preflight=_read_object(source_verdict_preflight_path, label="source verdict preflight"),
        final_closure=_read_object(final_closure_path, label="final B3 closure"),
        s00=_read_object(s00_path, label="S00 scope"),
        local_replay=_read_object(local_replay_path, label="B3 local replay"),
        original_result=_read_object(original_result_path, label="original NVDA result"),
        wire_v02_result=_read_object(wire_v02_result_path, label="wire V02 MSFT result"),
        repair_result=_read_object(repair_result_path, label="repair result"),
        repair_authorization=_read_object(repair_authorization_path, label="repair authorization"),
        repair_raw_dir=Path(repair_raw_dir),
        freeze=freeze,
        reconciliation=_read_object(reconciliation_path, label="B3 reconciliation"),
        handoff=handoff,
        initial_authority=authority,
        pricing=pricing,
    )
