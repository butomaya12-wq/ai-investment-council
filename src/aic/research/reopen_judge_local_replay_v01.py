from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_existing_evidence_inventory_v01 as inventory_v01
from aic.research import reopen_judge_s00_scope_v01 as scope_v01
from aic.research import reopen_local_primitives as local
from aic.research import reopen_remaining_gaps_closure_v02 as closure_v02


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_LOCAL_REPLAY_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_LOCAL_REPLAY_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PLAN_ZERO_CALL"
EXPECTED_INVENTORY_HASH = "a4971f24a537cbcf946281c1e9ca48a1b0c7193d8f8277396f56908c8e57b5a2"
EXPECTED_INVENTORY_CODE_SHA = "5cfdeaaca0baeb00c16a52eb3eb825e885501d32"
EXPECTED_HISTORICAL_CLOSURE_HASH = "af8f48ae8e6984c73c7ff447eeb523fbda72855ee49460bdc60f0634be4216e6"
EXPECTED_JUDGE_HASH = scope_v01.EXPECTED_JUDGE_RESULT_HASH
EXPECTED_HANDOFF_HASH = local.EXPECTED_HANDOFF_HASH
LOCAL_TARGET_IDS = inventory_v01.LOCAL_REPLAY_TARGETS
ALL_TARGET_IDS = inventory_v01.TARGET_IDS
PREEXISTING_EXTERNAL_TARGET_IDS = inventory_v01.EXTERNAL_READ_TARGETS


class JudgeReopenLocalReplayError(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise JudgeReopenLocalReplayError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def verify_inventory(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_INVENTORY_HASH, "existing-evidence inventory hash drift")
    inventory_v01.verify_inventory(payload, expected_code_commit_sha=EXPECTED_INVENTORY_CODE_SHA)
    _need(payload.get("next_gate") == "B3_RESEARCH_REOPEN_LOCAL_REPLAY_ZERO_CALL", "inventory next gate drift")
    _need(payload.get("local_replay_target_ids") == list(LOCAL_TARGET_IDS), "inventory local target drift")
    _need(payload.get("residual_external_read_target_ids") == list(PREEXISTING_EXTERNAL_TARGET_IDS), "inventory preexisting external target drift")
    return observed


def verify_historical_closure(payload: Mapping[str, Any]) -> str:
    observed = inventory_v01.verify_closure(payload)
    _need(observed == EXPECTED_HISTORICAL_CLOSURE_HASH, "historical closure hash drift")
    return observed


def verify_handoff(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload, field="handoff_hash")
    _need(observed == EXPECTED_HANDOFF_HASH, "B2 handoff hash drift")
    _need(payload.get("top3") == ["NVDA", "MSFT", "META"], "B2 handoff top3 drift")
    return observed


def verify_judge(payload: Mapping[str, Any]) -> str:
    observed = scope_v01.verify_judge_result(payload)
    _need(observed == EXPECTED_JUDGE_HASH, "Judge result hash drift")
    proposal = payload.get("judge_proposal")
    _need(isinstance(proposal, Mapping), "Judge proposal missing")
    rows = proposal.get("what_would_change_decision")
    _need(isinstance(rows, list), "Judge change conditions missing")
    condition_map = {
        str(row.get("condition_id")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("condition_id"), str)
    }
    _need(tuple(condition_map) == ("META_CONDITION_001", "META_CONDITION_002", "META_CONDITION_003", "META_CONDITION_004"), "Judge condition identity drift")
    expected_text = (
        "Additional valuation and portfolio-interaction context must address the stated limits "
        "of the point-in-time price-to-reported-earnings observation and cutoff-specific direct-exposure fact."
    )
    _need(condition_map["META_CONDITION_004"].get("condition_text") == expected_text, "META condition 004 semantic drift")
    return observed


def _decimal(value: Any, *, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise JudgeReopenLocalReplayError(f"{label} invalid decimal") from exc
    _need(result.is_finite(), f"{label} must be finite")
    return result


def _fixed(value: Decimal, *, places: int = 18) -> str:
    quantum = Decimal(1).scaleb(-places)
    return format(value.quantize(quantum), "f")


def _metric_map(handoff: Mapping[str, Any], candidate: str) -> dict[str, Mapping[str, Any]]:
    rows = handoff.get("candidates")
    _need(isinstance(rows, list), "B2 handoff candidates missing")
    candidate_row = next((row for row in rows if isinstance(row, Mapping) and row.get("symbol") == candidate), None)
    _need(isinstance(candidate_row, Mapping), f"{candidate} B2 handoff row missing")
    metrics = candidate_row.get("metrics")
    _need(isinstance(metrics, list), f"{candidate} B2 metrics missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in metrics:
        _need(isinstance(row, Mapping) and isinstance(row.get("metric_id"), str), f"{candidate} metric malformed")
        metric_id = str(row["metric_id"])
        _need(metric_id not in result, f"duplicate {candidate} metric")
        result[metric_id] = row
    return result


def _closure_evidence_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("supplemental_evidence_units")
    _need(isinstance(rows, list), "historical supplemental evidence missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        _need(isinstance(row, Mapping) and isinstance(row.get("evidence_id"), str), "historical evidence malformed")
        evidence_id = str(row["evidence_id"])
        _need(evidence_id not in result, "duplicate historical evidence")
        result[evidence_id] = row
    return result


def _target_map(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = inventory.get("inventory_targets")
    _need(isinstance(rows, list), "inventory target rows missing")
    result = {
        str(row.get("target_id")): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("target_id"), str)
    }
    _need(tuple(result) == ALL_TARGET_IDS, "inventory target coverage drift")
    return result


def _comparison_context(
    *,
    historical_closure: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _closure_evidence_map(historical_closure)
    msft_val = evidence.get(closure_v02.E_MSFT_VAL)
    meta_val = evidence.get(closure_v02.E_META_VAL)
    meta_port = evidence.get(closure_v02.E_META_PORT)
    _need(isinstance(msft_val, Mapping) and isinstance(meta_val, Mapping) and isinstance(meta_port, Mapping), "required historical supplemental evidence missing")

    msft_observed = msft_val.get("observed")
    meta_observed = meta_val.get("observed")
    port_observed = meta_port.get("observed")
    _need(isinstance(msft_observed, Mapping) and isinstance(meta_observed, Mapping) and isinstance(port_observed, Mapping), "historical observed payload missing")

    msft_pe = _decimal(msft_observed.get("price_to_eps"), label="MSFT price_to_eps")
    meta_pe = _decimal(meta_observed.get("price_to_eps"), label="META price_to_eps")
    _need(msft_pe > 0 and meta_pe > 0, "point-in-time P/E must be positive")
    _need(msft_pe == Decimal("28.821727019499"), "MSFT P/E drift")
    _need(meta_pe == Decimal("24.550021285653"), "META P/E drift")

    msft_metrics = _metric_map(handoff, "MSFT")
    meta_metrics = _metric_map(handoff, "META")
    msft_growth = _decimal(msft_metrics["annual_revenue_growth"].get("value"), label="MSFT revenue growth")
    msft_margin = _decimal(msft_metrics["annual_operating_margin"].get("value"), label="MSFT operating margin")
    meta_growth = _decimal(meta_metrics["annual_revenue_growth"].get("value"), label="META revenue growth")
    meta_margin = _decimal(meta_metrics["annual_operating_margin"].get("value"), label="META operating margin")

    with localcontext() as ctx:
        ctx.prec = 60
        msft_yield = Decimal(1) / msft_pe
        meta_yield = Decimal(1) / meta_pe
        msft_pe_premium_vs_meta = msft_pe / meta_pe - Decimal(1)
        meta_pe_discount_vs_msft = Decimal(1) - meta_pe / msft_pe

    _need(port_observed.get("direct_position_exposure") == "ZERO", "META direct exposure drift")
    _need(str(port_observed.get("meta_quantity")) == "0", "META cutoff quantity drift")
    _need(str(port_observed.get("meta_portfolio_weight")) == "0.000000000000", "META cutoff portfolio weight drift")

    return {
        "valuation_comparison": {
            "as_of_basis": "FROZEN_RESEARCH_CUTOFF_POINT_IN_TIME_REPORTED_EPS_MULTIPLES",
            "msft": {
                "price_to_reported_annual_gaap_diluted_eps": str(msft_observed["price_to_eps"]),
                "earnings_yield_from_same_multiple": _fixed(msft_yield),
                "annual_revenue_growth": str(msft_metrics["annual_revenue_growth"]["value"]),
                "annual_operating_margin": str(msft_metrics["annual_operating_margin"]["value"]),
                "source_evidence_ref": closure_v02.E_MSFT_VAL,
            },
            "meta": {
                "price_to_reported_annual_gaap_diluted_eps": str(meta_observed["price_to_eps"]),
                "earnings_yield_from_same_multiple": _fixed(meta_yield),
                "annual_revenue_growth": str(meta_metrics["annual_revenue_growth"]["value"]),
                "annual_operating_margin": str(meta_metrics["annual_operating_margin"]["value"]),
                "source_evidence_ref": closure_v02.E_META_VAL,
            },
            "derived_relative_view": {
                "msft_pe_premium_vs_meta_ratio": _fixed(msft_pe_premium_vs_meta),
                "meta_pe_discount_vs_msft_ratio": _fixed(meta_pe_discount_vs_msft),
            },
            "interpretive_boundary": "RELATIVE_POINT_IN_TIME_CONTEXT_ONLY; DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS, FORWARD_EARNINGS_POWER, OR FAIR_VALUE",
        },
        "meta_portfolio_context": {
            "b2_cutoff_utc": str(port_observed.get("b2_cutoff_utc")),
            "portfolio_equity": str(port_observed.get("portfolio_equity")),
            "meta_quantity": str(port_observed.get("meta_quantity")),
            "meta_market_value": str(port_observed.get("meta_market_value")),
            "meta_portfolio_weight": str(port_observed.get("meta_portfolio_weight")),
            "direct_position_exposure": str(port_observed.get("direct_position_exposure")),
            "source_evidence_ref": closure_v02.E_META_PORT,
            "interpretive_boundary": "CUTOFF_DIRECT_EXPOSURE_ONLY; DOES_NOT ESTABLISH CORRELATION, FACTOR, SECTOR, CONCENTRATION, OR DIVERSIFICATION INTERACTION",
        },
        "source_metric_refs": {
            "msft": [
                str(msft_metrics["annual_revenue_growth"]["computed_value_id"]),
                str(msft_metrics["annual_operating_margin"]["computed_value_id"]),
            ],
            "meta": [
                str(meta_metrics["annual_revenue_growth"]["computed_value_id"]),
                str(meta_metrics["annual_operating_margin"]["computed_value_id"]),
            ],
        },
    }


def build_local_replay(
    *,
    inventory: Mapping[str, Any],
    historical_closure: Mapping[str, Any],
    handoff: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "local replay code SHA invalid")
    inventory_hash = verify_inventory(inventory)
    closure_hash = verify_historical_closure(historical_closure)
    handoff_hash = verify_handoff(handoff)
    judge_hash = verify_judge(judge_result)
    targets = _target_map(inventory)
    comparison = _comparison_context(historical_closure=historical_closure, handoff=handoff)

    msft_source = targets["MSFT_VALUATION_CONTEXT_DEPTH"]
    meta_source = targets["META_CONDITION_004"]
    _need(msft_source.get("inventory_status") == "LOCAL_REPLAY_FIRST", "MSFT inventory state drift")
    _need(meta_source.get("inventory_status") == "LOCAL_REPLAY_FIRST", "META condition 004 inventory state drift")

    replay_rows = [
        {
            "target_id": "MSFT_VALUATION_CONTEXT_DEPTH",
            "candidate_id": "MSFT",
            "local_replay_status": "PARTIAL_LOCAL_CONTEXT_DERIVED_EXTERNAL_READ_STILL_REQUIRED",
            "derived_context_refs": [
                closure_v02.E_MSFT_VAL,
                closure_v02.E_META_VAL,
                "B2_MSFT_ANNUAL_REVENUE_GROWTH_20260827",
                "B2_MSFT_ANNUAL_OPERATING_MARGIN_20260827",
                "B2_META_ANNUAL_REVENUE_GROWTH_20260827",
                "B2_META_ANNUAL_OPERATING_MARGIN_20260827",
            ],
            "derived_context": comparison["valuation_comparison"],
            "resolved": False,
            "residual_need": "The frozen record now supports a deterministic MSFT-vs-META point-in-time multiple/earnings-yield and operating-profile comparison, but it still lacks broader valuation depth such as forward earnings/free-cash-flow or enterprise-value context and cannot establish valuation attractiveness.",
            "external_read_required_after_local_replay": True,
            "provider_read_authorized": False,
            "model_call_authorized": False,
        },
        {
            "target_id": "META_CONDITION_004",
            "candidate_id": "META",
            "local_replay_status": "PARTIAL_LOCAL_CONTEXT_DERIVED_EXTERNAL_READ_STILL_REQUIRED",
            "derived_context_refs": [
                closure_v02.E_META_VAL,
                closure_v02.E_MSFT_VAL,
                closure_v02.E_META_PORT,
                "B2_META_ANNUAL_REVENUE_GROWTH_20260827",
                "B2_META_ANNUAL_OPERATING_MARGIN_20260827",
            ],
            "derived_context": {
                "valuation_comparison": comparison["valuation_comparison"],
                "portfolio_context": comparison["meta_portfolio_context"],
            },
            "resolved": False,
            "residual_need": "The frozen record now adds a relative point-in-time valuation comparison to the cutoff-specific zero-direct-exposure fact, but broader portfolio interaction remains unavailable and valuation evidence remains backward-looking/narrow; correlation, factor/sector/concentration context and broader valuation evidence are still required.",
            "external_read_required_after_local_replay": True,
            "provider_read_authorized": False,
            "model_call_authorized": False,
        },
    ]
    _need(tuple(row["target_id"] for row in replay_rows) == LOCAL_TARGET_IDS, "local replay target order drift")
    _need(all(row["resolved"] is False for row in replay_rows), "local replay cannot over-promote narrow historical evidence")

    residual_targets = list(ALL_TARGET_IDS)
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_existing_evidence_inventory_hash": inventory_hash,
        "source_historical_closure_hash": closure_hash,
        "source_b2_handoff_hash": handoff_hash,
        "source_judge_result_hash": judge_hash,
        "local_replay_target_count": 2,
        "local_replay_target_ids": list(LOCAL_TARGET_IDS),
        "local_replay_results": replay_rows,
        "local_replay_partial_target_count": 2,
        "local_replay_partial_target_ids": list(LOCAL_TARGET_IDS),
        "local_replay_resolved_target_count": 0,
        "local_replay_resolved_target_ids": [],
        "preexisting_external_read_target_count": 5,
        "preexisting_external_read_target_ids": list(PREEXISTING_EXTERNAL_TARGET_IDS),
        "newly_escalated_external_read_target_count": 2,
        "newly_escalated_external_read_target_ids": list(LOCAL_TARGET_IDS),
        "residual_external_read_target_count": 7,
        "residual_external_read_target_ids": residual_targets,
        "deterministic_context": comparison,
        "resolution_rule": "NARROW_POINT_IN_TIME_VALUATION_OR_DIRECT_EXPOSURE_FACTS_MAY_BE_DETERMINISTICALLY_COMPARED_BUT_CANNOT_BY_THEMSELVES_RESOLVE_VALUATION_ATTRACTIVENESS_OR_BROADER_PORTFOLIO_INTERACTION",
        "external_read_authority_rule": "LOCAL_REPLAY_IDENTIFIES_RESIDUAL_NEED_ONLY; PROVIDER READ AUTHORITY REQUIRES A SEPARATE ZERO_CALL PLAN AND OWNER-GATED EXECUTION STEP",
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


def verify_local_replay(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "local replay version drift")
    _need(payload.get("status") == PASS_STATUS, "local replay status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "local replay code SHA drift")
    _need(payload.get("source_existing_evidence_inventory_hash") == EXPECTED_INVENTORY_HASH, "local replay inventory lineage drift")
    _need(payload.get("source_historical_closure_hash") == EXPECTED_HISTORICAL_CLOSURE_HASH, "local replay historical closure lineage drift")
    _need(payload.get("source_judge_result_hash") == EXPECTED_JUDGE_HASH, "local replay Judge lineage drift")
    _need(payload.get("source_b2_handoff_hash") == EXPECTED_HANDOFF_HASH, "local replay B2 lineage drift")
    _need(payload.get("local_replay_target_ids") == list(LOCAL_TARGET_IDS), "local replay target identity drift")
    _need(payload.get("local_replay_partial_target_ids") == list(LOCAL_TARGET_IDS), "local replay partial target drift")
    _need(payload.get("local_replay_resolved_target_count") == 0 and payload.get("local_replay_resolved_target_ids") == [], "local replay cannot claim resolution")
    _need(payload.get("preexisting_external_read_target_ids") == list(PREEXISTING_EXTERNAL_TARGET_IDS), "preexisting external target drift")
    _need(payload.get("newly_escalated_external_read_target_ids") == list(LOCAL_TARGET_IDS), "new escalation drift")
    _need(payload.get("residual_external_read_target_count") == 7, "residual external target count drift")
    _need(payload.get("residual_external_read_target_ids") == list(ALL_TARGET_IDS), "residual external target identity drift")
    rows = payload.get("local_replay_results")
    _need(isinstance(rows, list) and len(rows) == 2, "local replay results missing")
    for row in rows:
        _need(isinstance(row, Mapping), "local replay row malformed")
        _need(row.get("resolved") is False, "local replay row over-promoted")
        _need(row.get("external_read_required_after_local_replay") is True, "local replay row must retain residual read need")
        _need(row.get("provider_read_authorized") is False and row.get("model_call_authorized") is False, "local replay row unexpectedly authorizes calls")
    _need(payload.get("provider_reads_authorized") is False and payload.get("model_calls_authorized") is False, "local replay cannot authorize calls")
    _need(payload.get("broad_b3_rerun_authorized") is False and payload.get("research_run_started") is False, "local replay cannot start broad research")
    _need(payload.get("judge_rerun_authorized") is False and payload.get("rebuttal_rerun_authorized") is False, "local replay cannot authorize B4 reruns")
    _need(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "local replay cannot create FinalDecision/B5")
    _need(payload.get("execution_authority") is False, "local replay cannot grant execution authority")
    _need(payload.get("model_calls") == 0 and payload.get("provider_reads") == 0, "local replay zero-call counter drift")
    _need(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "local replay broker/order boundary drift")
    _need(payload.get("live_money") == "PROHIBITED", "local replay live-money boundary drift")
    _need(payload.get("next_gate") == NEXT_GATE, "local replay next gate drift")
    return observed
