from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B4_REOPEN_INPUT_OVERLAY_v0_1"
PASS_STATUS = "B4_REOPEN_INPUT_OVERLAY_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_LIFECYCLE_PLAN_ZERO_CALL"

EXPECTED_CLOSURE_HASH = "af8f48ae8e6984c73c7ff447eeb523fbda72855ee49460bdc60f0634be4216e6"
EXPECTED_CLOSURE_STATUS = "B3_REOPEN_REMAINING_GAPS_CLOSURE_ZERO_CALL_PASS"
EXPECTED_SELECTED_B3_HASH = "938b7eecfee58d1074be662d30a1bf183f1133f92815028637de4cd662307f27"
EXPECTED_PRODUCTION_JUDGE_HASH = "3354123bc0244ec258fad0cdab57d5551d5ed8e5d58088d11482bdcd489d259e"
EXPECTED_PRODUCTION_JUDGE_STATUS = "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED"
EXPECTED_REOPEN_REQUEST_HASH = "eb4c06f47f372413d25b25632ba84a35057fdbb9d244c4f1960f6b7fb40dfeb1"
EXPECTED_REOPEN_REQUEST_ID = "B4_RESEARCH_REOPEN_4dceff8d109cff9642cad677"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_LEGACY_COUNTS = {"NVDA": 12, "MSFT": 12, "META": 10}
EXPECTED_CLOSED_REASONS = (
    "ALPACA_NEWS_PAGINATION_INCOMPLETE",
    "VALUATION_SPECIFIC_EVIDENCE_MISSING",
    "PORTFOLIO_INTERACTION_EVIDENCE_MISSING",
)
EXPECTED_SUPPLEMENTAL = (
    (
        "MSFT",
        "B3_REOPEN_SUPPLEMENTAL_MSFT_VALUATION_001",
        "valuation_context",
        "B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z",
    ),
    (
        "META",
        "B3_REOPEN_SUPPLEMENTAL_META_VALUATION_001",
        "valuation_context",
        "B3_REOPEN_EVID_META_VALUATION_20260828T173300Z",
    ),
    (
        "META",
        "B3_REOPEN_SUPPLEMENTAL_META_PORTFOLIO_001",
        "portfolio_interaction",
        "B3_REOPEN_EVID_META_PORTFOLIO_20260827T200000Z",
    ),
)


class B4ReopenInputOverlayError(ValueError):
    pass


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenInputOverlayError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenInputOverlayError(f"{label} root must be an object")
    return value


def _verify_hash(
    payload: Mapping[str, Any],
    *,
    label: str,
    expected_hash: str,
    field: str = "artifact_hash",
) -> str:
    observed = payload.get(field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise B4ReopenInputOverlayError(f"{label} {field} missing")
    if observed != canonical_sha256(payload, exclude_fields=(field,)):
        raise B4ReopenInputOverlayError(f"{label} {field} self-hash mismatch")
    if observed != expected_hash:
        raise B4ReopenInputOverlayError(f"{label} {field} lineage drift")
    return observed


def _legacy_claims(selected: Mapping[str, Any]) -> tuple[dict[str, list[str]], dict[str, int]]:
    rows = selected.get("candidates")
    if not isinstance(rows, list):
        raise B4ReopenInputOverlayError("selected B3 candidates missing")
    by_candidate: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    all_ids: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise B4ReopenInputOverlayError("selected B3 candidate row malformed")
        candidate = row.get("candidate")
        claims = row.get("material_claims")
        if not isinstance(candidate, str) or candidate in by_candidate or not isinstance(claims, list):
            raise B4ReopenInputOverlayError("selected B3 candidate/claim surface malformed")
        ids: list[str] = []
        for raw in claims:
            if not isinstance(raw, Mapping):
                raise B4ReopenInputOverlayError("selected B3 MaterialClaim malformed")
            claim_id = raw.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id or claim_id in all_ids:
                raise B4ReopenInputOverlayError("selected B3 MaterialClaim ID invalid")
            ids.append(claim_id)
            all_ids.append(claim_id)
        by_candidate[candidate] = ids
        counts[candidate] = len(ids)
    if tuple(by_candidate) != EXPECTED_CANDIDATES:
        raise B4ReopenInputOverlayError("selected B3 candidate order drift")
    if counts != EXPECTED_LEGACY_COUNTS or len(all_ids) != 34:
        raise B4ReopenInputOverlayError("legacy B3 claim-count surface drift")
    return by_candidate, counts


def _validate_closure(closure: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    if closure.get("status") != EXPECTED_CLOSURE_STATUS:
        raise B4ReopenInputOverlayError("B3 reopen closure is not PASS")
    if closure.get("overall_research_reopen_complete") is not True:
        raise B4ReopenInputOverlayError("B3 reopen is not complete")
    if closure.get("research_reopen_request_satisfied") is not True:
        raise B4ReopenInputOverlayError("research reopen request is not satisfied")
    if closure.get("all_judge_conditions_satisfied") is not True:
        raise B4ReopenInputOverlayError("Judge conditions are not all satisfied")
    if closure.get("remaining_reopen_reason_codes") != []:
        raise B4ReopenInputOverlayError("B3 reopen still has remaining reason codes")
    if tuple(closure.get("closed_reopen_reason_codes", [])) != EXPECTED_CLOSED_REASONS:
        raise B4ReopenInputOverlayError("closed reopen reason surface drift")
    if closure.get("legacy_frozen_artifacts_mutated") is not False:
        raise B4ReopenInputOverlayError("legacy frozen artifact mutation detected")
    if closure.get("legacy_material_claim_payloads_mutated") is not False:
        raise B4ReopenInputOverlayError("legacy MaterialClaim mutation detected")
    if closure.get("reopen_overlay_is_additive") is not True:
        raise B4ReopenInputOverlayError("B3 closure is not additive")
    if closure.get("historical_production_judge_rerun_authorized") is not False:
        raise B4ReopenInputOverlayError("historical production Judge rerun boundary drift")
    if closure.get("final_decision_created") is not False or closure.get("b5_handoff_created") is not False:
        raise B4ReopenInputOverlayError("B3 closure advanced beyond B3")
    if closure.get("new_provider_dispatch_attempts") != 0 or closure.get("new_provider_reads") != 0:
        raise B4ReopenInputOverlayError("B3 closure is not zero-call")
    if closure.get("model_calls") != 0 or closure.get("broker_writes") != 0 or closure.get("alpaca_orders") != 0:
        raise B4ReopenInputOverlayError("B3 closure side-effect boundary drift")
    if closure.get("live_money") != "PROHIBITED":
        raise B4ReopenInputOverlayError("B3 closure live-money boundary drift")
    if closure.get("source_selected_b3_reconciliation_hash") != EXPECTED_SELECTED_B3_HASH:
        raise B4ReopenInputOverlayError("B3 closure selected-reconciliation lineage drift")
    if closure.get("source_production_judge_result_hash") != EXPECTED_PRODUCTION_JUDGE_HASH:
        raise B4ReopenInputOverlayError("B3 closure Judge lineage drift")
    if closure.get("source_reopen_request_hash") != EXPECTED_REOPEN_REQUEST_HASH:
        raise B4ReopenInputOverlayError("B3 closure reopen-request hash drift")
    if closure.get("source_reopen_request_id") != EXPECTED_REOPEN_REQUEST_ID:
        raise B4ReopenInputOverlayError("B3 closure reopen-request ID drift")

    claims = closure.get("supplemental_claims")
    evidence = closure.get("supplemental_evidence_units")
    if not isinstance(claims, list) or len(claims) != 3:
        raise B4ReopenInputOverlayError("supplemental claim surface must contain exactly three claims")
    if not isinstance(evidence, list) or len(evidence) != 3:
        raise B4ReopenInputOverlayError("supplemental evidence surface must contain exactly three units")
    evidence_ids = {
        item.get("evidence_id")
        for item in evidence
        if isinstance(item, Mapping) and isinstance(item.get("evidence_id"), str)
    }
    if len(evidence_ids) != 3:
        raise B4ReopenInputOverlayError("supplemental evidence IDs invalid")
    observed_claims: list[tuple[str, str, str, str]] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise B4ReopenInputOverlayError("supplemental claim malformed")
        refs = claim.get("evidence_ids")
        if not isinstance(refs, list) or len(refs) != 1 or refs[0] not in evidence_ids:
            raise B4ReopenInputOverlayError("supplemental claim evidence closure mismatch")
        observed_claims.append(
            (
                str(claim.get("candidate_id")),
                str(claim.get("claim_id")),
                str(claim.get("category")),
                str(refs[0]),
            )
        )
        if claim.get("claim_kind") != "FACT" or claim.get("support_status") != "SUPPORTED":
            raise B4ReopenInputOverlayError("supplemental claim support contract drift")
    if tuple(observed_claims) != EXPECTED_SUPPLEMENTAL:
        raise B4ReopenInputOverlayError("supplemental claim identity/category surface drift")
    return claims, evidence


def _validate_judge(judge: Mapping[str, Any]) -> None:
    if judge.get("status") != EXPECTED_PRODUCTION_JUDGE_STATUS:
        raise B4ReopenInputOverlayError("historical production Judge status drift")
    if judge.get("research_reopen_required") is not True:
        raise B4ReopenInputOverlayError("historical production Judge lost reopen requirement")
    if judge.get("research_reopen_request_hash") != EXPECTED_REOPEN_REQUEST_HASH:
        raise B4ReopenInputOverlayError("historical production Judge reopen hash drift")
    reopen = judge.get("research_reopen_request")
    if not isinstance(reopen, Mapping) or reopen.get("reopen_request_id") != EXPECTED_REOPEN_REQUEST_ID:
        raise B4ReopenInputOverlayError("historical production Judge reopen request missing")
    if judge.get("final_decision_created") is not False or judge.get("b5_handoff_created") is not False:
        raise B4ReopenInputOverlayError("historical production Judge unexpectedly advanced to B5")
    if judge.get("rerun_authorized") is not False:
        raise B4ReopenInputOverlayError("historical production Judge rerun boundary drift")


def build_b4_reopen_input_overlay(
    *,
    code_commit_sha: str,
    closure_path: str | Path,
    selected_b3_path: str | Path,
    production_judge_path: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenInputOverlayError("code_commit_sha must be lowercase 40-char SHA")

    closure = _read_json(closure_path, label="B3 reopen closure")
    closure_hash = _verify_hash(
        closure,
        label="B3 reopen closure",
        expected_hash=EXPECTED_CLOSURE_HASH,
    )
    supplemental_claims, supplemental_evidence = _validate_closure(closure)

    selected = _read_json(selected_b3_path, label="selected B3 reconciliation")
    selected_hash = _verify_hash(
        selected,
        label="selected B3 reconciliation",
        expected_hash=EXPECTED_SELECTED_B3_HASH,
    )
    legacy_by_candidate, legacy_counts = _legacy_claims(selected)

    judge = _read_json(production_judge_path, label="historical production Judge")
    judge_hash = _verify_hash(
        judge,
        label="historical production Judge",
        expected_hash=EXPECTED_PRODUCTION_JUDGE_HASH,
    )
    _validate_judge(judge)

    supplemental_by_candidate: dict[str, list[str]] = {candidate: [] for candidate in EXPECTED_CANDIDATES}
    supplemental_evidence_by_candidate: dict[str, list[str]] = {candidate: [] for candidate in EXPECTED_CANDIDATES}
    portfolio_context_by_candidate: dict[str, list[str]] = {candidate: [] for candidate in EXPECTED_CANDIDATES}
    for claim in supplemental_claims:
        candidate = str(claim["candidate_id"])
        supplemental_by_candidate[candidate].append(str(claim["claim_id"]))
    for evidence in supplemental_evidence:
        candidate = str(evidence["candidate_id"])
        evidence_id = str(evidence["evidence_id"])
        supplemental_evidence_by_candidate[candidate].append(evidence_id)
        if evidence.get("category") == "portfolio_interaction":
            portfolio_context_by_candidate[candidate].append(evidence_id)

    effective_surfaces: list[dict[str, Any]] = []
    effective_ids_all: list[str] = []
    for candidate in EXPECTED_CANDIDATES:
        legacy_ids = list(legacy_by_candidate[candidate])
        supplemental_ids = list(supplemental_by_candidate[candidate])
        if set(legacy_ids).intersection(supplemental_ids):
            raise B4ReopenInputOverlayError("supplemental claim collides with legacy claim ID")
        effective_ids = [*legacy_ids, *supplemental_ids]
        effective_ids_all.extend(effective_ids)
        effective_surfaces.append(
            {
                "candidate_id": candidate,
                "legacy_material_claim_count": legacy_counts[candidate],
                "legacy_material_claim_ids": legacy_ids,
                "supplemental_claim_count": len(supplemental_ids),
                "supplemental_claim_ids": supplemental_ids,
                "supplemental_evidence_ids": list(supplemental_evidence_by_candidate[candidate]),
                "supplemental_portfolio_context_refs": list(portfolio_context_by_candidate[candidate]),
                "effective_material_claim_count": len(effective_ids),
                "effective_material_claim_ids": effective_ids,
            }
        )
    if len(effective_ids_all) != 37 or len(set(effective_ids_all)) != 37:
        raise B4ReopenInputOverlayError("effective B4 claim surface must be exactly 37 unique claims")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_b3_reopen_closure_hash": closure_hash,
        "source_selected_b3_reconciliation_hash": selected_hash,
        "source_historical_production_judge_hash": judge_hash,
        "source_reopen_request_id": EXPECTED_REOPEN_REQUEST_ID,
        "source_reopen_request_hash": EXPECTED_REOPEN_REQUEST_HASH,
        "candidate_order": list(EXPECTED_CANDIDATES),
        "legacy_material_claim_count": 34,
        "supplemental_claim_count": 3,
        "effective_material_claim_count": 37,
        "effective_candidate_surfaces": effective_surfaces,
        "supplemental_claims": [dict(item) for item in supplemental_claims],
        "supplemental_evidence_units": [dict(item) for item in supplemental_evidence],
        "effective_gap_overlay": {
            "closed_reopen_reason_codes": list(EXPECTED_CLOSED_REASONS),
            "effective_unresolved_reopen_reason_codes": [],
            "effective_unresolved_data_gap_refs": [],
            "legacy_candidate_packet_source_gaps_preserved_immutable": True,
            "gap_closure_is_overlay_only": True,
        },
        "legacy_b3_artifacts_mutated": False,
        "legacy_material_claims_mutated": False,
        "reopen_overlay_is_additive": True,
        "historical_b4_frozen_outputs_are_historical_context_only": True,
        "historical_b4_frozen_outputs_reusable_as_new_model_outputs": False,
        "new_b4_decision_lifecycle_required": True,
        "historical_production_judge_rerun_authorized": False,
        "provider_reads_authorized": False,
        "planned_provider_reads": 0,
        "model_calls_authorized": False,
        "planned_model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
