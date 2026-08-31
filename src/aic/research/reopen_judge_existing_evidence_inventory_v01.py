from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_s00_scope_v01 as scope_v01
from aic.research import reopen_judge_s00_scope_v03 as scope_v03
from aic.research import reopen_local_primitives as local
from aic.research import reopen_remaining_gaps_closure_v02 as closure_v02


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_EXISTING_EVIDENCE_INVENTORY_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_EXISTING_EVIDENCE_INVENTORY_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_LOCAL_REPLAY_ZERO_CALL"
EXPECTED_SCOPE_HASH = "43c1dac11dfe8e038b74a25f5e6a0f5643ec97349247021c16f1e8abf386413b"
EXPECTED_SCOPE_CODE_SHA = "1e9036a7057ce7466a6cf768f504724fc3f833cd"
EXPECTED_JUDGE_HASH = scope_v01.EXPECTED_JUDGE_RESULT_HASH
EXPECTED_INITIAL_HASH = scope_v03.EXPECTED_INITIAL_FREEZE_HASH
EXPECTED_SELECTED_HASH = closure_v02.EXPECTED_SELECTED_HASH
EXPECTED_HANDOFF_HASH = local.EXPECTED_HANDOFF_HASH
TARGET_IDS = (
    "NVDA_CURRENT_DEVELOPMENTS_Q4",
    "MSFT_VALUATION_CONTEXT_DEPTH",
    "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
    "META_CONDITION_001",
    "META_CONDITION_002",
    "META_CONDITION_003",
    "META_CONDITION_004",
)
LOCAL_REPLAY_TARGETS = (
    "MSFT_VALUATION_CONTEXT_DEPTH",
    "META_CONDITION_004",
)
EXTERNAL_READ_TARGETS = (
    "NVDA_CURRENT_DEVELOPMENTS_Q4",
    "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
    "META_CONDITION_001",
    "META_CONDITION_002",
    "META_CONDITION_003",
)


class ExistingEvidenceInventoryError(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ExistingEvidenceInventoryError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def verify_scope(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_SCOPE_HASH, "S00 Scope V03 hash drift")
    scope_v03.verify_scope_artifact(
        payload,
        expected_code_commit_sha=EXPECTED_SCOPE_CODE_SHA,
    )
    _need(payload.get("canonical_reopen_requirement_count") == 3, "canonical reopen count drift")
    _need(payload.get("judge_change_condition_count") == 4, "META condition count drift")
    _need(payload.get("provider_reads_authorized") is False, "scope unexpectedly authorizes provider reads")
    _need(payload.get("model_calls_authorized") is False, "scope unexpectedly authorizes model calls")
    _need(payload.get("broad_b3_rerun_authorized") is False, "scope unexpectedly authorizes broad B3 rerun")
    return observed


def verify_closure(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == closure_v02.ARTIFACT_VERSION, "historical closure version drift")
    _need(payload.get("status") == closure_v02.PASS_STATUS, "historical closure is not PASS")
    _need(payload.get("supplemental_evidence_unit_count") == 3, "supplemental evidence count drift")
    _need(payload.get("supplemental_claim_count") == 3, "supplemental claim count drift")
    _need(payload.get("supplemental_claims_are_separate_from_legacy_material_claims") is True, "supplemental claim separation drift")
    _need(payload.get("new_provider_dispatch_attempts") == 0, "historical closure unexpectedly dispatched provider work")
    _need(payload.get("new_provider_reads") == 0, "historical closure unexpectedly added provider reads")
    _need(payload.get("model_calls") == 0, "historical closure unexpectedly called a model")
    _need(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "historical closure unexpectedly advanced decision authority")
    _need(payload.get("live_money") == "PROHIBITED", "historical closure live-money boundary drift")

    evidence = payload.get("supplemental_evidence_units")
    claims = payload.get("supplemental_claims")
    _need(isinstance(evidence, list) and isinstance(claims, list), "historical supplemental evidence/claims missing")
    evidence_map = {
        str(row.get("evidence_id")): row
        for row in evidence
        if isinstance(row, Mapping) and isinstance(row.get("evidence_id"), str)
    }
    claim_map = {
        str(row.get("claim_id")): row
        for row in claims
        if isinstance(row, Mapping) and isinstance(row.get("claim_id"), str)
    }
    _need(
        tuple(evidence_map) == (closure_v02.E_MSFT_VAL, closure_v02.E_META_VAL, closure_v02.E_META_PORT),
        "historical supplemental evidence identity drift",
    )
    _need(
        tuple(claim_map) == (closure_v02.C_MSFT_VAL, closure_v02.C_META_VAL, closure_v02.C_META_PORT),
        "historical supplemental claim identity drift",
    )

    msft_val = evidence_map[closure_v02.E_MSFT_VAL]
    meta_val = evidence_map[closure_v02.E_META_VAL]
    meta_port = evidence_map[closure_v02.E_META_PORT]
    _need(msft_val.get("observed", {}).get("price_to_eps") == "28.821727019499", "MSFT historical valuation drift")
    _need(meta_val.get("observed", {}).get("price_to_eps") == "24.550021285653", "META historical valuation drift")
    _need(meta_port.get("observed", {}).get("direct_position_exposure") == "ZERO", "META historical direct exposure drift")
    return observed


def verify_selected_and_retrieval(
    selected: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> tuple[str, str, str, datetime, datetime]:
    selected_hash = _self_hash(selected)
    _need(selected_hash == EXPECTED_SELECTED_HASH, "selected B3 reconciliation hash drift")
    retrieval_hash = _self_hash(retrieval)
    _need(selected.get("retrieval_artifact_hash") == retrieval_hash, "selected/retrieval lineage drift")
    local._candidate_map(retrieval)

    handoff_hash = _self_hash(handoff, field="handoff_hash")
    _need(handoff_hash == EXPECTED_HANDOFF_HASH, "B2 handoff hash drift")
    _need(selected.get("handoff_hash") == handoff_hash, "selected/handoff lineage drift")
    research_cutoff = local._parse_utc(handoff.get("research_cutoff"))
    b2_cutoff = local._parse_utc(handoff.get("b2_decision_cutoff"))
    _need(research_cutoff is not None and b2_cutoff is not None, "handoff cutoffs invalid")
    return selected_hash, retrieval_hash, handoff_hash, research_cutoff, b2_cutoff


def _news_inventory(candidate: str, retrieval_row: Mapping[str, Any], *, research_cutoff: datetime) -> dict[str, Any]:
    rows = local._evidence_rows(candidate, retrieval_row)
    news: list[dict[str, Any]] = []
    for row in rows:
        if row.get("source_type") != "ALPACA_NEWS":
            continue
        as_of = local._parse_utc(row.get("as_of")) or local._parse_utc(row.get("observed_at"))
        news.append(
            {
                "evidence_id": row.get("evidence_id"),
                "as_of": None if as_of is None else as_of.isoformat().replace("+00:00", "Z"),
                "at_or_before_research_cutoff": as_of is not None and as_of <= research_cutoff,
                "field_or_claim": row.get("field_or_claim"),
            }
        )
    valid_times = [local._parse_utc(row.get("as_of")) for row in news]
    valid_times = [value for value in valid_times if value is not None]
    latest = max(valid_times) if valid_times else None
    return {
        "candidate_id": candidate,
        "historical_news_evidence_count": len(news),
        "historical_news_evidence_ids": [str(row["evidence_id"]) for row in news if isinstance(row.get("evidence_id"), str)],
        "latest_historical_news_as_of": None if latest is None else latest.isoformat().replace("+00:00", "Z"),
        "all_news_rows_at_or_before_research_cutoff": all(row["at_or_before_research_cutoff"] for row in news) if news else True,
        "post_judge_refresh_present_in_historical_retrieval": False,
    }


def _scope_maps(scope: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    requirements = scope.get("canonical_reopen_requirements")
    conditions = scope.get("judge_change_conditions_for_executable_invest")
    _need(isinstance(requirements, list) and isinstance(conditions, list), "scope target rows missing")
    req_map = {
        str(row.get("requirement_id")): row
        for row in requirements
        if isinstance(row, Mapping) and isinstance(row.get("requirement_id"), str)
    }
    cond_map = {
        str(row.get("condition_id")): row
        for row in conditions
        if isinstance(row, Mapping) and isinstance(row.get("condition_id"), str)
    }
    _need(tuple(req_map) == TARGET_IDS[:3], "canonical requirement identity drift")
    _need(tuple(cond_map) == TARGET_IDS[3:], "META condition identity drift")
    return req_map, cond_map


def _closure_maps(closure: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    evidence = closure["supplemental_evidence_units"]
    claims = closure["supplemental_claims"]
    return (
        {str(row["evidence_id"]): row for row in evidence if isinstance(row, Mapping)},
        {str(row["claim_id"]): row for row in claims if isinstance(row, Mapping)},
    )


def _target(
    *,
    target_id: str,
    candidate_id: str,
    scope_class: str,
    source_refs: list[str],
    existing_evidence_refs: list[str],
    existing_claim_refs: list[str],
    existing_computed_value_refs: list[str],
    inventory_status: str,
    residual_need: str,
    local_replay_required: bool,
    external_read_required: bool,
) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "candidate_id": candidate_id,
        "scope_class": scope_class,
        "source_refs": source_refs,
        "existing_evidence_refs": list(dict.fromkeys(existing_evidence_refs)),
        "existing_claim_refs": list(dict.fromkeys(existing_claim_refs)),
        "existing_computed_value_refs": list(dict.fromkeys(existing_computed_value_refs)),
        "inventory_status": inventory_status,
        "residual_need": residual_need,
        "local_replay_required": local_replay_required,
        "external_read_required_after_inventory": external_read_required,
        "provider_read_authorized": False,
        "model_call_authorized": False,
    }


def build_inventory(
    *,
    scope: Mapping[str, Any],
    historical_closure: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    selected_reconciliation: Mapping[str, Any],
    handoff: Mapping[str, Any],
    recovered_initial: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    runtime_root: str,
    config_root: str,
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "inventory code SHA invalid")
    scope_hash = verify_scope(scope)
    closure_hash = verify_closure(historical_closure)
    selected_hash, retrieval_hash, handoff_hash, research_cutoff, b2_cutoff = verify_selected_and_retrieval(
        selected_reconciliation,
        retrieval,
        handoff,
    )
    initial_hash, msft_valuation_claim, msft_durability_claim = scope_v03.verify_recovered_initial_freeze(recovered_initial)
    _need(initial_hash == EXPECTED_INITIAL_HASH, "Recovered Initial lineage drift")
    judge_hash = scope_v01.verify_judge_result(judge_result)
    _need(judge_hash == EXPECTED_JUDGE_HASH, "Judge result lineage drift")

    req_map, cond_map = _scope_maps(scope)
    closure_evidence, closure_claims = _closure_maps(historical_closure)
    retrieval_map = local._candidate_map(retrieval)
    news = {
        candidate: _news_inventory(candidate, retrieval_map[candidate], research_cutoff=research_cutoff)
        for candidate in ("NVDA", "MSFT", "META")
    }
    valuation_reviews = {
        candidate: local._valuation_primitives(candidate, retrieval_map[candidate], research_cutoff=research_cutoff)
        for candidate in ("MSFT", "META")
    }
    portfolio_discoveries = local._portfolio_discoveries(
        roots=(runtime_root, config_root),
        b2_cutoff=b2_cutoff,
    )
    historical_portfolio_candidates = [
        row for row in portfolio_discoveries
        if row.get("at_or_before_b2_cutoff") is True and int(row.get("position_count") or 0) > 0
    ]

    msft_val_local_eps = [
        str(row["evidence_id"])
        for row in valuation_reviews["MSFT"]["diluted_eps_candidate_fragments"]
        if isinstance(row.get("evidence_id"), str)
    ]
    meta_val_local_eps = [
        str(row["evidence_id"])
        for row in valuation_reviews["META"]["diluted_eps_candidate_fragments"]
        if isinstance(row.get("evidence_id"), str)
    ]

    meta_conditions = judge_result["judge_proposal"]["what_would_change_decision"]
    judge_condition_map = {str(row["condition_id"]): row for row in meta_conditions if isinstance(row, Mapping)}

    targets = [
        _target(
            target_id="NVDA_CURRENT_DEVELOPMENTS_Q4",
            candidate_id="NVDA",
            scope_class="CANONICAL_REOPEN_REQUIREMENT",
            source_refs=[str(req_map["NVDA_CURRENT_DEVELOPMENTS_Q4"]["source_ref_id"])],
            existing_evidence_refs=list(news["NVDA"]["historical_news_evidence_ids"]),
            existing_claim_refs=[],
            existing_computed_value_refs=[],
            inventory_status="RESIDUAL_EXTERNAL_READ_REQUIRED",
            residual_need="A decision-usable post-freeze current-developments refresh is still required; historical/pagination-closed news alone cannot satisfy Q4_RECENT_DEVELOPMENTS.",
            local_replay_required=False,
            external_read_required=True,
        ),
        _target(
            target_id="MSFT_VALUATION_CONTEXT_DEPTH",
            candidate_id="MSFT",
            scope_class="CANONICAL_REOPEN_REQUIREMENT",
            source_refs=[str(req_map["MSFT_VALUATION_CONTEXT_DEPTH"]["source_ref_id"])],
            existing_evidence_refs=[closure_v02.E_MSFT_VAL, *msft_val_local_eps],
            existing_claim_refs=[closure_v02.C_MSFT_VAL, str(msft_valuation_claim["claim_id"])],
            existing_computed_value_refs=list(msft_valuation_claim.get("computed_value_ids") or []),
            inventory_status="LOCAL_REPLAY_FIRST",
            residual_need="Existing evidence proves one point-in-time price-to-reported-EPS observation, but not valuation attractiveness or depth; replay existing fundamentals for additional deterministic context before authorizing any external read.",
            local_replay_required=True,
            external_read_required=False,
        ),
        _target(
            target_id="MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
            candidate_id="MSFT",
            scope_class="CANONICAL_REOPEN_REQUIREMENT",
            source_refs=[str(req_map["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]["source_ref_id"])],
            existing_evidence_refs=list(msft_durability_claim.get("evidence_ids") or []),
            existing_claim_refs=[str(msft_durability_claim["claim_id"])],
            existing_computed_value_refs=list(msft_durability_claim.get("computed_value_ids") or []),
            inventory_status="RESIDUAL_EXTERNAL_READ_REQUIRED",
            residual_need="Frozen growth, margin, business and risk-factor evidence describes current strength and risks but cannot prove forward monetization or investment-return durability.",
            local_replay_required=False,
            external_read_required=True,
        ),
        _target(
            target_id="META_CONDITION_001",
            candidate_id="META",
            scope_class="JUDGE_EXIT_TO_B5_CONDITION",
            source_refs=list(judge_condition_map["META_CONDITION_001"]["source_or_claim_refs"]),
            existing_evidence_refs=list(news["META"]["historical_news_evidence_ids"]),
            existing_claim_refs=list(judge_condition_map["META_CONDITION_001"]["source_or_claim_refs"]),
            existing_computed_value_refs=[],
            inventory_status="RESIDUAL_EXTERNAL_READ_REQUIRED",
            residual_need="Advertising demand, engagement and targeting/measurement signal durability require fresh decision-usable evidence beyond the frozen record.",
            local_replay_required=False,
            external_read_required=True,
        ),
        _target(
            target_id="META_CONDITION_002",
            candidate_id="META",
            scope_class="JUDGE_EXIT_TO_B5_CONDITION",
            source_refs=list(judge_condition_map["META_CONDITION_002"]["source_or_claim_refs"]),
            existing_evidence_refs=[],
            existing_claim_refs=list(judge_condition_map["META_CONDITION_002"]["source_or_claim_refs"]),
            existing_computed_value_refs=[],
            inventory_status="RESIDUAL_EXTERNAL_READ_REQUIRED",
            residual_need="The frozen record identifies AI infrastructure spend and Reality Labs risk but does not resolve forward financial manageability or sufficient anticipated benefits.",
            local_replay_required=False,
            external_read_required=True,
        ),
        _target(
            target_id="META_CONDITION_003",
            candidate_id="META",
            scope_class="JUDGE_EXIT_TO_B5_CONDITION",
            source_refs=list(judge_condition_map["META_CONDITION_003"]["source_or_claim_refs"]),
            existing_evidence_refs=list(news["META"]["historical_news_evidence_ids"]),
            existing_claim_refs=list(judge_condition_map["META_CONDITION_003"]["source_or_claim_refs"]),
            existing_computed_value_refs=[],
            inventory_status="RESIDUAL_EXTERNAL_READ_REQUIRED",
            residual_need="Regulatory developments are time-sensitive and require a fresh bounded current-developments/primary-source read to assess material operating effects.",
            local_replay_required=False,
            external_read_required=True,
        ),
        _target(
            target_id="META_CONDITION_004",
            candidate_id="META",
            scope_class="JUDGE_EXIT_TO_B5_CONDITION",
            source_refs=list(judge_condition_map["META_CONDITION_004"]["source_or_claim_refs"]),
            existing_evidence_refs=[closure_v02.E_META_VAL, closure_v02.E_META_PORT, *meta_val_local_eps],
            existing_claim_refs=[closure_v02.C_META_VAL, closure_v02.C_META_PORT, *judge_condition_map["META_CONDITION_004"]["source_or_claim_refs"]],
            existing_computed_value_refs=[],
            inventory_status="LOCAL_REPLAY_FIRST",
            residual_need="Existing evidence proves one point-in-time P/E observation and zero direct META exposure at the B2 cutoff; additional valuation and broader portfolio-interaction context must be derived locally before any external read is considered.",
            local_replay_required=True,
            external_read_required=False,
        ),
    ]

    _need(tuple(row["target_id"] for row in targets) == TARGET_IDS, "inventory target coverage drift")
    _need(tuple(row["target_id"] for row in targets if row["local_replay_required"]) == LOCAL_REPLAY_TARGETS, "local replay target drift")
    _need(tuple(row["target_id"] for row in targets if row["external_read_required_after_inventory"]) == EXTERNAL_READ_TARGETS, "external-read target drift")
    _need(all(row["inventory_status"] != "RESOLVED" for row in targets), "inventory must not silently resolve Judge/reopen targets")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_s00_scope_v03_hash": scope_hash,
        "source_historical_closure_hash": closure_hash,
        "source_selected_b3_reconciliation_hash": selected_hash,
        "source_historical_b3_retrieval_hash": retrieval_hash,
        "source_b2_handoff_hash": handoff_hash,
        "source_recovered_initial_freeze_hash": initial_hash,
        "source_judge_result_hash": judge_hash,
        "research_cutoff": handoff.get("research_cutoff"),
        "b2_decision_cutoff": handoff.get("b2_decision_cutoff"),
        "inventory_target_count": 7,
        "inventory_targets": targets,
        "resolved_target_count": 0,
        "local_replay_target_count": 2,
        "local_replay_target_ids": list(LOCAL_REPLAY_TARGETS),
        "residual_external_read_target_count": 5,
        "residual_external_read_target_ids": list(EXTERNAL_READ_TARGETS),
        "historical_news_inventory": [news[candidate] for candidate in ("NVDA", "MSFT", "META")],
        "valuation_local_primitive_reviews": [valuation_reviews["MSFT"], valuation_reviews["META"]],
        "historical_portfolio_candidate_count": len(historical_portfolio_candidates),
        "historical_portfolio_candidates": historical_portfolio_candidates,
        "historical_supplemental_evidence_refs": [closure_v02.E_MSFT_VAL, closure_v02.E_META_VAL, closure_v02.E_META_PORT],
        "historical_supplemental_claim_refs": [closure_v02.C_MSFT_VAL, closure_v02.C_META_VAL, closure_v02.C_META_PORT],
        "forward_durability_resolution_rule": "CURRENT_STRENGTH_OR_DISCLOSED_RISK_ALONE_CANNOT_RESOLVE_FORWARD_DURABILITY",
        "external_read_authority_rule": "INVENTORY_IDENTIFIES_RESIDUAL_NEED_ONLY; IT DOES NOT AUTHORIZE PROVIDER READS",
        "broad_b3_rerun_authorized": False,
        "research_run_started": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_inventory(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "inventory version drift")
    _need(payload.get("status") == PASS_STATUS, "inventory status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "inventory code SHA drift")
    _need(payload.get("source_s00_scope_v03_hash") == EXPECTED_SCOPE_HASH, "inventory scope lineage drift")
    _need(payload.get("source_judge_result_hash") == EXPECTED_JUDGE_HASH, "inventory Judge lineage drift")
    _need(payload.get("source_recovered_initial_freeze_hash") == EXPECTED_INITIAL_HASH, "inventory Initial lineage drift")
    _need(payload.get("inventory_target_count") == 7, "inventory target count drift")
    targets = payload.get("inventory_targets")
    _need(isinstance(targets, list), "inventory targets missing")
    _need(tuple(row.get("target_id") for row in targets if isinstance(row, Mapping)) == TARGET_IDS, "inventory target identity drift")
    _need(payload.get("resolved_target_count") == 0, "inventory cannot silently resolve targets")
    _need(payload.get("local_replay_target_ids") == list(LOCAL_REPLAY_TARGETS), "inventory local replay target drift")
    _need(payload.get("residual_external_read_target_ids") == list(EXTERNAL_READ_TARGETS), "inventory external-read target drift")
    _need(payload.get("provider_reads_authorized") is False and payload.get("model_calls_authorized") is False, "inventory cannot authorize calls")
    _need(payload.get("broad_b3_rerun_authorized") is False, "inventory cannot authorize broad B3 rerun")
    _need(payload.get("research_run_started") is False, "inventory cannot start research")
    _need(payload.get("judge_rerun_authorized") is False and payload.get("rebuttal_rerun_authorized") is False, "inventory cannot authorize B4 reruns")
    _need(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "inventory cannot create FinalDecision/B5")
    _need(payload.get("execution_authority") is False, "inventory cannot grant execution authority")
    _need(payload.get("model_calls") == 0 and payload.get("provider_reads") == 0, "inventory zero-call counters drift")
    _need(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "inventory broker/order boundary drift")
    _need(payload.get("live_money") == "PROHIBITED", "inventory live-money boundary drift")
    _need(payload.get("next_gate") == NEXT_GATE, "inventory next gate drift")
    for row in targets:
        _need(isinstance(row, Mapping), "inventory target malformed")
        _need(row.get("provider_read_authorized") is False and row.get("model_call_authorized") is False, "inventory target unexpectedly authorizes calls")
        _need(row.get("inventory_status") != "RESOLVED", "inventory target silently resolved")
    return observed
