from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_REMAINING_GAPS_SCOPE_v0_1"
PASS_STATUS = "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL_PASS"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
EXPECTED_REASONS = (
    "VALUATION_SPECIFIC_EVIDENCE_MISSING",
    "PORTFOLIO_INTERACTION_EVIDENCE_MISSING",
)
EXPECTED_NEWS_CLOSURE = "ALPACA_NEWS_BOUNDED_TOP_N_SATISFIED"
EXPECTED_NEWS_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"

_VALUATION_SIGNAL_RE = re.compile(
    r"(?i)(valuation|multiple|market[_ -]?cap|enterprise[_ -]?value|price[_ -]?(?:to|/)|"
    r"p/?e\b|earnings[_ -]?yield|free[_ -]?cash[_ -]?flow[_ -]?yield|fcf[_ -]?yield|"
    r"price[_ -]?sales|price[_ -]?book|ev[_ /-]?(?:sales|ebitda)|peg\b)"
)
_PORTFOLIO_SIGNAL_RE = re.compile(
    r"(?i)(portfolio|position|exposure|correlation|concentration|diversification|beta\b|"
    r"overlap|sector[_ -]?weight|factor[_ -]?exposure)"
)


class RemainingGapScopeError(ValueError):
    pass


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemainingGapScopeError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise RemainingGapScopeError(f"{label} root must be an object")
    return value


def _verify_hash(payload: Mapping[str, Any], *, label: str, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise RemainingGapScopeError(f"{label} {field} missing")
    expected = canonical_sha256(payload, exclude_fields=(field,))
    if observed != expected:
        raise RemainingGapScopeError(f"{label} {field} mismatch")
    return observed


def _candidate_map(payload: Mapping[str, Any], *, label: str, key: str) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        raise RemainingGapScopeError(f"{label} candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RemainingGapScopeError(f"{label} candidate row malformed")
        candidate = row.get(key)
        if not isinstance(candidate, str) or candidate in result:
            raise RemainingGapScopeError(f"{label} candidate identity invalid")
        result[candidate] = row
    if tuple(result) != EXPECTED_CANDIDATES:
        raise RemainingGapScopeError(f"{label} candidate order drift")
    return result


def _handoff_candidate_map(handoff: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = handoff.get("candidates")
    if not isinstance(rows, list):
        raise RemainingGapScopeError("B2 handoff candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RemainingGapScopeError("B2 handoff candidate row malformed")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol in result:
            raise RemainingGapScopeError("B2 handoff candidate identity invalid")
        result[symbol] = row
    if tuple(result) != EXPECTED_CANDIDATES:
        raise RemainingGapScopeError("B2 handoff candidate order drift")
    return result


def _claim_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    required_strings = ("claim_id", "candidate_id", "category", "claim_text", "claim_kind", "materiality", "support_status")
    for field in required_strings:
        value = raw.get(field)
        if not isinstance(value, str) or not value or value != value.strip():
            raise RemainingGapScopeError(f"MaterialClaim {field} invalid")
    refs: dict[str, list[str]] = {}
    for field in ("evidence_ids", "computed_value_ids", "conflict_ids", "assumptions"):
        value = raw.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise RemainingGapScopeError(f"MaterialClaim {field} invalid")
        refs[field] = list(value)
    uncertainty = raw.get("uncertainty_note")
    if uncertainty is not None and not isinstance(uncertainty, str):
        raise RemainingGapScopeError("MaterialClaim uncertainty_note invalid")
    return {
        "claim_id": raw["claim_id"],
        "claim_text": raw["claim_text"],
        "claim_kind": raw["claim_kind"],
        "materiality": raw["materiality"],
        "support_status": raw["support_status"],
        **refs,
        "uncertainty_note": uncertainty,
    }


def _scope_group(
    *,
    candidate: str,
    category: str,
    group_ids: list[str],
    claims_by_id: Mapping[str, Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    computed_by_id: Mapping[str, Mapping[str, Any]],
    shared_portfolio_context_refs: list[str],
) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    computed_ids: list[str] = []
    for claim_id in group_ids:
        raw = claims_by_id.get(claim_id)
        if raw is None:
            raise RemainingGapScopeError(f"{candidate} {category} group references missing claim")
        if raw.get("candidate_id") != candidate or raw.get("category") != category:
            raise RemainingGapScopeError(f"{candidate} {category} claim category/identity drift")
        view = _claim_view(raw)
        claims.append(view)
        evidence_ids.extend(view["evidence_ids"])
        computed_ids.extend(view["computed_value_ids"])
    evidence_ids = list(dict.fromkeys(evidence_ids))
    computed_ids = list(dict.fromkeys(computed_ids))
    unresolved_evidence = [ref for ref in evidence_ids if ref not in evidence_by_id]
    unresolved_computed = [ref for ref in computed_ids if ref not in computed_by_id]
    if unresolved_evidence or unresolved_computed:
        raise RemainingGapScopeError(f"{candidate} {category} support lineage cannot be resolved")

    evidence_details = [
        {
            "evidence_id": ref,
            "provider": evidence_by_id[ref].get("provider"),
            "source_type": evidence_by_id[ref].get("source_type"),
            "field_or_claim": evidence_by_id[ref].get("field_or_claim"),
            "authoritative_for": list(evidence_by_id[ref].get("authoritative_for", [])),
        }
        for ref in evidence_ids
    ]
    computed_details = [
        {
            "computed_value_id": ref,
            "metric_id": computed_by_id[ref].get("metric_id"),
            "value": computed_by_id[ref].get("value"),
            "unit": computed_by_id[ref].get("unit"),
        }
        for ref in computed_ids
    ]

    signal_texts: list[str] = []
    signal_texts.extend(str(item.get("field_or_claim") or "") for item in evidence_details)
    signal_texts.extend(
        " ".join(str(value) for value in item.get("authoritative_for", []))
        for item in evidence_details
    )
    signal_texts.extend(str(item.get("metric_id") or "") for item in computed_details)
    signal_texts.extend(shared_portfolio_context_refs)
    joined = " | ".join(signal_texts)
    signal_re = _VALUATION_SIGNAL_RE if category == "valuation_context" else _PORTFOLIO_SIGNAL_RE

    return {
        "category": category,
        "claim_count": len(claims),
        "claim_ids": group_ids,
        "claims": claims,
        "evidence_ref_count": len(evidence_ids),
        "computed_value_ref_count": len(computed_ids),
        "evidence_details": evidence_details,
        "computed_value_details": computed_details,
        "shared_portfolio_context_refs": shared_portfolio_context_refs if category == "portfolio_interaction" else [],
        "support_lineage_resolved": True,
        "category_specific_reference_signal_detected": bool(signal_re.search(joined)),
        "gap_closed_by_this_inventory": False,
    }


def build_remaining_gaps_scope(
    *,
    code_commit_sha: str,
    claim_reconciliation_path: str | Path,
    selected_reconciliation_path: str | Path,
    retrieval_path: str | Path,
    b4_input_freeze_path: str | Path,
    handoff_path: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise RemainingGapScopeError("code_commit_sha must be lowercase 40-char SHA")

    prior = _read_json(claim_reconciliation_path, label="claim reconciliation")
    prior_hash = _verify_hash(prior, label="claim reconciliation")
    if prior.get("status") != "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL_PASS":
        raise RemainingGapScopeError("claim reconciliation is not PASS")
    if prior.get("news_gap_closed") is not True or prior.get("closure_evidence_ref") != EXPECTED_NEWS_CLOSURE:
        raise RemainingGapScopeError("news closure lineage missing")
    if tuple(prior.get("remaining_reopen_reason_codes", [])) != EXPECTED_REASONS:
        raise RemainingGapScopeError("remaining reopen reason scope drift")

    selected = _read_json(selected_reconciliation_path, label="selected-model reconciliation")
    selected_hash = _verify_hash(selected, label="selected-model reconciliation")
    if selected_hash != prior.get("source_b3_selected_model_reconciliation_hash"):
        raise RemainingGapScopeError("selected-model reconciliation lineage mismatch")

    retrieval = _read_json(retrieval_path, label="historical B3 retrieval")
    retrieval_hash = _verify_hash(retrieval, label="historical B3 retrieval")
    if selected.get("retrieval_artifact_hash") != retrieval_hash:
        raise RemainingGapScopeError("selected-model reconciliation/retrieval lineage mismatch")

    freeze = _read_json(b4_input_freeze_path, label="B4 input freeze")
    freeze_hash = _verify_hash(freeze, label="B4 input freeze")
    if freeze.get("b3_reconciliation_artifact_hash") != selected_hash:
        raise RemainingGapScopeError("B4 input freeze/B3 reconciliation lineage mismatch")

    handoff = _read_json(handoff_path, label="B2 handoff")
    handoff_hash = _verify_hash(handoff, label="B2 handoff", field="handoff_hash")
    if selected.get("handoff_hash") != handoff_hash or freeze.get("b2_handoff_hash") != handoff_hash:
        raise RemainingGapScopeError("B2 handoff lineage mismatch")

    selected_candidates = _candidate_map(selected, label="selected-model reconciliation", key="candidate")
    retrieval_candidates = _candidate_map(retrieval, label="historical B3 retrieval", key="candidate")
    handoff_candidates = _handoff_candidate_map(handoff)

    bundles = freeze.get("bundles")
    if not isinstance(bundles, list) or len(bundles) != 3:
        raise RemainingGapScopeError("B4 input freeze bundles missing")
    freeze_by_candidate: dict[str, Mapping[str, Any]] = {}
    for bundle in bundles:
        if not isinstance(bundle, Mapping) or not isinstance(bundle.get("candidate_id"), str):
            raise RemainingGapScopeError("B4 input freeze bundle malformed")
        freeze_by_candidate[str(bundle["candidate_id"])] = bundle
    if tuple(freeze_by_candidate) != EXPECTED_CANDIDATES:
        raise RemainingGapScopeError("B4 input freeze candidate order drift")

    candidate_scopes: list[dict[str, Any]] = []
    total_valuation_claims = 0
    total_portfolio_claims = 0
    total_shared_portfolio_refs = 0

    for candidate in EXPECTED_CANDIDATES:
        record = selected_candidates[candidate]
        packet = record.get("candidate_packet")
        claims_raw = record.get("material_claims")
        if not isinstance(packet, Mapping) or not isinstance(claims_raw, list):
            raise RemainingGapScopeError(f"{candidate} packet/claims missing")
        claims_by_id: dict[str, Mapping[str, Any]] = {}
        for raw in claims_raw:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("claim_id"), str):
                raise RemainingGapScopeError(f"{candidate} MaterialClaim malformed")
            claims_by_id[str(raw["claim_id"])] = raw
        if len(claims_by_id) != len(claims_raw):
            raise RemainingGapScopeError(f"{candidate} duplicate MaterialClaim ID")

        retrieval_row = retrieval_candidates[candidate]
        research_evidence = retrieval_row.get("research_evidence")
        evidence_rows = research_evidence.get("evidence_items") if isinstance(research_evidence, Mapping) else None
        if not isinstance(evidence_rows, list):
            raise RemainingGapScopeError(f"{candidate} evidence items missing")
        evidence_by_id: dict[str, Mapping[str, Any]] = {}
        for item in evidence_rows:
            if not isinstance(item, Mapping) or not isinstance(item.get("evidence_id"), str):
                raise RemainingGapScopeError(f"{candidate} evidence item malformed")
            evidence_by_id[str(item["evidence_id"])] = item

        metric_rows = handoff_candidates[candidate].get("metrics")
        if not isinstance(metric_rows, list):
            raise RemainingGapScopeError(f"{candidate} B2 metrics missing")
        computed_by_id: dict[str, Mapping[str, Any]] = {}
        for item in metric_rows:
            if not isinstance(item, Mapping) or not isinstance(item.get("computed_value_id"), str):
                raise RemainingGapScopeError(f"{candidate} B2 metric malformed")
            computed_by_id[str(item["computed_value_id"])] = item

        valuation_ids = packet.get("valuation_context_claim_ids")
        portfolio_ids = packet.get("portfolio_interaction_claim_ids")
        if not isinstance(valuation_ids, list) or any(not isinstance(x, str) for x in valuation_ids):
            raise RemainingGapScopeError(f"{candidate} valuation claim group malformed")
        if not isinstance(portfolio_ids, list) or any(not isinstance(x, str) for x in portfolio_ids):
            raise RemainingGapScopeError(f"{candidate} portfolio claim group malformed")

        shared_refs_raw = freeze_by_candidate[candidate].get("shared_portfolio_context_refs")
        if not isinstance(shared_refs_raw, list) or any(not isinstance(x, str) for x in shared_refs_raw):
            raise RemainingGapScopeError(f"{candidate} shared portfolio context refs malformed")
        shared_refs = list(shared_refs_raw)

        valuation = _scope_group(
            candidate=candidate,
            category="valuation_context",
            group_ids=list(valuation_ids),
            claims_by_id=claims_by_id,
            evidence_by_id=evidence_by_id,
            computed_by_id=computed_by_id,
            shared_portfolio_context_refs=shared_refs,
        )
        portfolio = _scope_group(
            candidate=candidate,
            category="portfolio_interaction",
            group_ids=list(portfolio_ids),
            claims_by_id=claims_by_id,
            evidence_by_id=evidence_by_id,
            computed_by_id=computed_by_id,
            shared_portfolio_context_refs=shared_refs,
        )
        total_valuation_claims += valuation["claim_count"]
        total_portfolio_claims += portfolio["claim_count"]
        total_shared_portfolio_refs += len(shared_refs)
        candidate_scopes.append(
            {
                "candidate_id": candidate,
                "valuation_context": valuation,
                "portfolio_interaction": portfolio,
                "b2_metric_ids_available": [str(row.get("metric_id")) for row in metric_rows],
            }
        )

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_claim_reconciliation_hash": prior_hash,
        "source_b3_selected_model_reconciliation_hash": selected_hash,
        "source_historical_b3_retrieval_hash": retrieval_hash,
        "source_b4_input_freeze_hash": freeze_hash,
        "source_b2_handoff_hash": handoff_hash,
        "news_gap_state": "CLOSED",
        "news_gap_superseded_ref": EXPECTED_NEWS_GAP,
        "news_gap_closure_evidence_ref": EXPECTED_NEWS_CLOSURE,
        "remaining_reopen_reason_codes": list(EXPECTED_REASONS),
        "candidate_scopes": candidate_scopes,
        "inventory_summary": {
            "valuation_context_claim_count": total_valuation_claims,
            "portfolio_interaction_claim_count": total_portfolio_claims,
            "shared_portfolio_context_ref_count": total_shared_portfolio_refs,
            "b2_handoff_metric_ids": [
                "return_20s",
                "max_drawdown_20s",
                "adv_20s",
                "annual_revenue_growth",
                "annual_operating_margin",
            ],
        },
        "valuation_gap_closed_by_this_inventory": False,
        "portfolio_interaction_gap_closed_by_this_inventory": False,
        "overall_research_reopen_complete": False,
        "material_claim_payloads_mutated": False,
        "legacy_frozen_artifacts_mutated": False,
        "new_provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": "B3_REOPEN_REMAINING_GAPS_EVIDENCE_PLAN_ZERO_CALL",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
