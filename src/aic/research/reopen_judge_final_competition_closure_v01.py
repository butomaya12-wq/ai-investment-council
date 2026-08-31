from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from aic.data.providers.alpaca_news_reopen import AlpacaNewsReopenRead
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v03 as preflight_v03


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_FINAL_COMPETITION_CLOSURE_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_CLOSED_FOR_NEW_B4_VERDICT"
NEXT_GATE = "B4_POST_RESEARCH_REOPEN_VERDICT_PREFLIGHT_ZERO_CALL"

EXPECTED_S00_HASH = "43c1dac11dfe8e038b74a25f5e6a0f5643ec97349247021c16f1e8abf386413b"
EXPECTED_LOCAL_REPLAY_HASH = "74a539f68fda0102918039f96e4a3ec28bfc5468f17fcb24400e9eecaf875c29"
EXPECTED_ORIGINAL_RESULT_HASH = "45980cba660dff7df1e013808c760a7eae95456e830e734ecd1641021d0cdfc1"
EXPECTED_WIRE_V02_RESULT_HASH = "ee6f58136022e49278750e4d2c82a109adabdc7e4bd6183964bf99e2c545e565"
EXPECTED_REPAIR_RESULT_HASH = "30d056cc55dd780bd4fcb7fbcda810e11f2a2df8fc8381f4969a76161e7afd1d"
EXPECTED_REPAIR_AUTH_HASH = "6c6314ad772b6cca6f0b64ffbad2d36954850c526c23c1660dc1b4e2c2a3d8bf"
EXPECTED_RR1_SHA = "bece0a471ec98fddfe7e2469adf520340b42ce172e943791787d4f171123d13d"
EXPECTED_RR3_SALVAGED_SHA = "86275e3421ab89da4116fca811049bdaa5e46f63b41d39602016a79f2ce553c5"
EXPECTED_MSFT_RESPONSE_HASH = "94aeef972aed14129af0805c1d5118c4432980a51ed50046048793fe30f22a3b"
EXPECTED_REOPEN_CUTOFF_UTC = "2026-08-31T08:58:17Z"

REQUIREMENTS = (
    "NVDA_CURRENT_DEVELOPMENTS_Q4",
    "MSFT_VALUATION_CONTEXT_DEPTH",
    "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
)


class FinalCompetitionClosureError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise FinalCompetitionClosureError(message)


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


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalCompetitionClosureError(f"unable to read {label}") from exc
    _need(isinstance(payload, dict), f"{label} root must be object")
    return payload


def _bundle(payload: Mapping[str, Any], bundle_id: str) -> Mapping[str, Any]:
    rows = payload.get("bundle_results")
    _need(isinstance(rows, list), "bundle_results missing")
    matches = [
        row for row in rows
        if isinstance(row, Mapping) and row.get("bundle_id") == bundle_id
    ]
    _need(len(matches) == 1, f"bundle missing or duplicated: {bundle_id}")
    return matches[0]


def _verify_s00(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_S00_HASH, "S00 hash drift")
    _need(payload.get("status") == "B3_RESEARCH_REOPEN_S00_SCOPE_V03_ZERO_CALL_PASS", "S00 status drift")
    rows = payload.get("canonical_reopen_requirements")
    _need(isinstance(rows, list), "S00 canonical requirements missing")
    ids = tuple(
        row.get("requirement_id")
        for row in rows
        if isinstance(row, Mapping)
    )
    _need(ids == REQUIREMENTS, "S00 canonical requirement surface drift")
    _need(payload.get("canonical_reopen_requirement_count") == 3, "S00 requirement count drift")
    _need(payload.get("broad_b3_rerun_authorized") is False, "broad B3 rerun unexpectedly authorized")
    return observed


def _verify_local_replay(payload: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_LOCAL_REPLAY_HASH, "local replay hash drift")
    _need(payload.get("status") == "B3_RESEARCH_REOPEN_LOCAL_REPLAY_ZERO_CALL_PASS", "local replay status drift")
    context = payload.get("deterministic_context")
    _need(isinstance(context, Mapping), "local replay deterministic context missing")
    valuation = context.get("valuation_comparison")
    _need(isinstance(valuation, Mapping), "valuation comparison missing")
    msft = valuation.get("msft")
    meta = valuation.get("meta")
    relative = valuation.get("derived_relative_view")
    _need(isinstance(msft, Mapping) and isinstance(meta, Mapping) and isinstance(relative, Mapping), "valuation comparison malformed")
    _need(msft.get("price_to_reported_annual_gaap_diluted_eps") == "28.821727019499", "MSFT P/E drift")
    _need(meta.get("price_to_reported_annual_gaap_diluted_eps") == "24.550021285653", "META P/E drift")
    _need(relative.get("msft_pe_premium_vs_meta_ratio") == "0.174000082694118851", "MSFT premium drift")
    _need(
        valuation.get("interpretive_boundary")
        == "RELATIVE_POINT_IN_TIME_CONTEXT_ONLY; DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS, FORWARD_EARNINGS_POWER, OR FAIR_VALUE",
        "valuation interpretive boundary drift",
    )
    return observed, valuation


def _verify_original_nvda(payload: Mapping[str, Any]) -> AlpacaNewsReopenRead:
    try:
        summary = preflight_v03.verify_original_result_v03(payload)
    except Exception as exc:
        raise FinalCompetitionClosureError("original retained NVDA result validation failed") from exc
    _need(summary.get("result_artifact_hash") == EXPECTED_ORIGINAL_RESULT_HASH, "original NVDA result hash drift")
    row = _bundle(payload, "ER1_NVDA_NEWS_REFRESH")
    response = row.get("response_artifact")
    _need(isinstance(response, Mapping), "original NVDA response missing")
    try:
        typed = AlpacaNewsReopenRead.model_validate(dict(response))
    except Exception as exc:
        raise FinalCompetitionClosureError("original NVDA typed response invalid") from exc
    _need(len(typed.articles) == 10, "original NVDA article count drift")
    _need(typed.pagination_complete is False, "original NVDA unexpectedly terminal")
    return typed


def _verify_wire_msft(payload: Mapping[str, Any]) -> AlpacaNewsReopenRead:
    observed = _self_hash(payload)
    _need(observed == EXPECTED_WIRE_V02_RESULT_HASH, "wire V02 result hash drift")
    row = _bundle(payload, "CR1_MSFT_NEWS_REFRESH")
    _need(row.get("status") == "PASS", "MSFT frozen news is not PASS")
    _need(row.get("pagination_complete") is True, "MSFT frozen news pagination incomplete")
    _need(row.get("article_count") == 8, "MSFT frozen article count drift")
    _need(row.get("response_artifact_hash") == EXPECTED_MSFT_RESPONSE_HASH, "MSFT response hash drift")
    response = row.get("response_artifact")
    _need(isinstance(response, Mapping), "MSFT frozen response missing")
    try:
        typed = AlpacaNewsReopenRead.model_validate(dict(response))
    except Exception as exc:
        raise FinalCompetitionClosureError("MSFT frozen typed response invalid") from exc
    _need(len(typed.articles) == 8 and typed.pagination_complete is True, "MSFT typed news contract drift")
    return typed


def _verify_repair_result(payload: Mapping[str, Any], authorization: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    auth_hash = _self_hash(authorization)
    _need(auth_hash == EXPECTED_REPAIR_AUTH_HASH, "repair authorization hash drift")
    observed = _self_hash(payload)
    _need(observed == EXPECTED_REPAIR_RESULT_HASH, "repair result hash drift")
    _need(payload.get("authorization_artifact_hash") == auth_hash, "repair result authorization lineage drift")
    _need(payload.get("provider_dispatch_attempts") == 4, "repair dispatch count drift")
    _need(payload.get("failed_bundle_ids") == ["RR2_DYNAMIC_MARKET_CONTEXT", "RR3_NVDA_NEWS_CONTINUATION"], "repair failure surface drift")
    _need(payload.get("next_gate") == "B3_RESEARCH_REOPEN_CR4_TO_CR6_POST_READ_EVIDENCE_RECONCILIATION_ZERO_CALL", "repair next gate drift")
    rr1 = _bundle(payload, "RR1_CURRENT_PORTFOLIO_EQUITY_REPAIR")
    _need(rr1.get("status") == "PASS", "RR1 portfolio repair not PASS")
    _need(rr1.get("response_sha256") == EXPECTED_RR1_SHA, "RR1 response SHA drift")
    response = rr1.get("response_payload")
    _need(isinstance(response, Mapping), "RR1 response payload missing")
    _need(response.get("timeframe") == "1D", "RR1 timeframe drift")
    _need(response.get("base_value") == 100000, "RR1 base value drift")
    _need(response.get("equity") == [0, 0, 100000, 100000, 100000], "RR1 equity series drift")
    return observed, rr1


def _salvaged_nvda(raw_dir: Path) -> tuple[str, dict[str, Any]]:
    matches = list(raw_dir.glob(f"RR3_NVDA_NEWS_CONTINUATION__01__provider_response__{EXPECTED_RR3_SALVAGED_SHA}.bin"))
    _need(len(matches) == 1, "exact salvaged NVDA response snapshot missing")
    raw = matches[0].read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == EXPECTED_RR3_SALVAGED_SHA, "salvaged NVDA SHA mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalCompetitionClosureError("salvaged NVDA response invalid JSON") from exc
    _need(isinstance(payload, dict), "salvaged NVDA payload must be object")
    news = payload.get("news")
    _need(isinstance(news, list) and len(news) == 5, "salvaged NVDA article count drift")
    _need(isinstance(payload.get("next_page_token"), str) and bool(payload.get("next_page_token")), "salvaged NVDA continuation token missing")
    return EXPECTED_RR3_SALVAGED_SHA, payload


def _article_ids_from_raw_news(payload: Mapping[str, Any]) -> list[int]:
    rows = payload.get("news")
    _need(isinstance(rows, list), "raw news array missing")
    out: list[int] = []
    for row in rows:
        _need(isinstance(row, Mapping), "raw news row malformed")
        article_id = row.get("id")
        _need(isinstance(article_id, int), "raw news id malformed")
        out.append(article_id)
    return out


def build_final_closure(
    *,
    code_commit_sha: str,
    s00: Mapping[str, Any],
    local_replay: Mapping[str, Any],
    original_result: Mapping[str, Any],
    wire_v02_result: Mapping[str, Any],
    repair_result: Mapping[str, Any],
    repair_authorization: Mapping[str, Any],
    repair_raw_dir: Path,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha or "") is not None, "exact code SHA required")
    s00_hash = _verify_s00(s00)
    local_hash, valuation = _verify_local_replay(local_replay)
    original_nvda = _verify_original_nvda(original_result)
    msft_news = _verify_wire_msft(wire_v02_result)
    repair_hash, rr1 = _verify_repair_result(repair_result, repair_authorization)
    salvaged_sha, salvaged = _salvaged_nvda(repair_raw_dir)

    retained_ids = [int(article.article_id) for article in original_nvda.articles]
    salvaged_ids = _article_ids_from_raw_news(salvaged)
    combined_ids = [*retained_ids, *salvaged_ids]
    _need(len(combined_ids) == 15 and len(set(combined_ids)) == 15, "NVDA decision-usable article surface must be 15 unique articles")

    msft_ids = [int(article.article_id) for article in msft_news.articles]
    _need(len(msft_ids) == 8 and len(set(msft_ids)) == 8, "MSFT article surface drift")

    requirements = [
        {
            "requirement_id": "NVDA_CURRENT_DEVELOPMENTS_Q4",
            "candidate_id": "NVDA",
            "closure_status": "CLOSED_DECISION_USABLE_NONEXHAUSTIVE",
            "evidence_disposition": "CURRENT_DEVELOPMENTS_COVERAGE_PRESENT; TERMINAL_PAGINATION_NOT_CLAIMED",
            "retained_article_count": 10,
            "salvaged_article_count": 5,
            "combined_unique_article_count": 15,
            "combined_article_ids": combined_ids,
            "source_original_provider_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
            "source_salvaged_provider_response_sha256": salvaged_sha,
            "interpretive_boundary": "CLOSES_Q4_FOR_B4_REEVALUATION; DOES_NOT_CLAIM EXHAUSTIVE NEWS COVERAGE OR POSITIVE NVDA OUTLOOK",
        },
        {
            "requirement_id": "MSFT_VALUATION_CONTEXT_DEPTH",
            "candidate_id": "MSFT",
            "closure_status": "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED",
            "msft_point_in_time_pe": valuation["msft"]["price_to_reported_annual_gaap_diluted_eps"],
            "meta_point_in_time_pe": valuation["meta"]["price_to_reported_annual_gaap_diluted_eps"],
            "msft_pe_premium_vs_meta_ratio": valuation["derived_relative_view"]["msft_pe_premium_vs_meta_ratio"],
            "interpretive_boundary": valuation["interpretive_boundary"],
            "competition_grade_stop_rule": "VALUATION_DEPTH_IS_DECISION_USABLE_WHEN COMPARATIVE CONTEXT AND ITS LIMITS ARE EXPLICIT; FAVORABLE ATTRACTIVENESS NEED NOT BE PROVEN TO RETURN TO B4",
        },
        {
            "requirement_id": "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
            "candidate_id": "MSFT",
            "closure_status": "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK",
            "msft_current_news_article_count": 8,
            "msft_current_news_article_ids": msft_ids,
            "epistemic_disposition": "CURRENT_STRENGTH_AND_AI_DATA_CENTER_SPEND_DO_NOT ESTABLISH FUTURE MONETIZATION OR INVESTMENT RETURN DURABILITY",
            "decision_rule": "NO_POSITIVE_EXTRAPOLATION_FROM CURRENT GROWTH OR MARGIN; DURABILITY REMAINS A MATERIAL RISK INPUT FOR B4",
            "interpretive_boundary": "ABSENCE OF PROOF OF DURABILITY IS NOT PROOF OF FAILURE; THE UNCERTAINTY ITSELF IS NOW DECISION-USABLE",
        },
    ]

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "source_s00_scope_hash": s00_hash,
        "source_local_replay_hash": local_hash,
        "source_original_provider_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "source_wire_v02_result_hash": EXPECTED_WIRE_V02_RESULT_HASH,
        "source_cr4_to_cr6_repair_result_hash": repair_hash,
        "source_cr4_to_cr6_repair_authorization_hash": EXPECTED_REPAIR_AUTH_HASH,
        "canonical_reopen_requirement_count": 3,
        "canonical_reopen_requirement_ids": list(REQUIREMENTS),
        "requirement_closures": requirements,
        "remaining_canonical_reopen_requirement_ids": [],
        "canonical_research_reopen_closed": True,
        "research_reopen_request_satisfied_for_return_to_b4": True,
        "judge_meta_change_conditions_preserved_as_b4_decision_context": True,
        "judge_meta_change_conditions_reclassified_as_canonical_reopen_requirements": False,
        "rr2_dynamic_market_context_transport_failure_is_canonical_blocker": False,
        "rr2_failure_class": "ALPACA_TLS_HANDSHAKE_TIMEOUT",
        "rr3_second_dispatch_failure_class": "ALPACA_TLS_HANDSHAKE_TIMEOUT",
        "additional_provider_read_required_before_b4": False,
        "provider_reads_authorized": False,
        "provider_reads_this_step": 0,
        "model_calls_authorized": False,
        "model_calls_this_step": 0,
        "model_synthesis_calls_this_step": 0,
        "historical_b4_outputs_reusable_as_new_model_outputs": False,
        "new_b4_verdict_required": True,
        "b4_input_overlay": {
            "candidate_order": ["NVDA", "MSFT", "META"],
            "new_research_findings": requirements,
            "current_portfolio_context": {
                "paper_account_base_value": rr1["response_payload"]["base_value"],
                "paper_account_timeframe": rr1["response_payload"]["timeframe"],
                "paper_account_equity_series": list(rr1["response_payload"]["equity"]),
                "source_response_sha256": EXPECTED_RR1_SHA,
            },
            "unresolved_uncertainties_are_decision_inputs_not_reopen_triggers": [
                "MSFT_VALUATION_ATTRACTIVENESS_NOT_ESTABLISHED",
                "MSFT_FORWARD_AI_CLOUD_RETURN_DURABILITY_NOT_ESTABLISHED",
                "NVDA_NEWS_COVERAGE_NONEXHAUSTIVE",
            ],
            "historical_judge_v02_is_context_only": True,
            "historical_judge_v02_rerun_authorized": False,
        },
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_final_closure(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "closure version drift")
    _need(payload.get("status") == PASS_STATUS, "closure status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "closure code SHA drift")
    _need(payload.get("source_s00_scope_hash") == EXPECTED_S00_HASH, "closure S00 lineage drift")
    _need(payload.get("source_local_replay_hash") == EXPECTED_LOCAL_REPLAY_HASH, "closure local replay lineage drift")
    _need(payload.get("source_original_provider_result_hash") == EXPECTED_ORIGINAL_RESULT_HASH, "closure original result lineage drift")
    _need(payload.get("source_wire_v02_result_hash") == EXPECTED_WIRE_V02_RESULT_HASH, "closure wire V02 lineage drift")
    _need(payload.get("source_cr4_to_cr6_repair_result_hash") == EXPECTED_REPAIR_RESULT_HASH, "closure repair lineage drift")
    _need(payload.get("canonical_reopen_requirement_ids") == list(REQUIREMENTS), "closure requirement IDs drift")
    _need(payload.get("remaining_canonical_reopen_requirement_ids") == [], "closure still has canonical gaps")
    _need(payload.get("canonical_research_reopen_closed") is True, "canonical reopen not closed")
    _need(payload.get("additional_provider_read_required_before_b4") is False, "unexpected provider read requirement")
    _need(payload.get("provider_reads_this_step") == 0 and payload.get("model_calls_this_step") == 0, "closure is not zero-call")
    _need(payload.get("new_b4_verdict_required") is True, "new B4 verdict requirement lost")
    _need(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "closure advanced beyond B3")
    _need(payload.get("live_money") == "PROHIBITED", "live-money boundary drift")
    _need(payload.get("next_gate") == NEXT_GATE, "closure next gate drift")
    return observed


def load_and_build_final_closure(
    *,
    code_commit_sha: str,
    s00_path: Path,
    local_replay_path: Path,
    original_result_path: Path,
    wire_v02_result_path: Path,
    repair_result_path: Path,
    repair_authorization_path: Path,
    repair_raw_dir: Path,
) -> dict[str, Any]:
    return build_final_closure(
        code_commit_sha=code_commit_sha,
        s00=_read_json(s00_path, label="S00 V03"),
        local_replay=_read_json(local_replay_path, label="local replay"),
        original_result=_read_json(original_result_path, label="original provider result"),
        wire_v02_result=_read_json(wire_v02_result_path, label="wire V02 result"),
        repair_result=_read_json(repair_result_path, label="CR4-to-CR6 repair result"),
        repair_authorization=_read_json(repair_authorization_path, label="CR4-to-CR6 repair authorization"),
        repair_raw_dir=repair_raw_dir,
    )
