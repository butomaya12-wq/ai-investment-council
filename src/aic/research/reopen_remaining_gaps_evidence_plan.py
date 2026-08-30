from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_REMAINING_GAPS_EVIDENCE_PLAN_v0_1"
PASS_STATUS = "B3_REOPEN_REMAINING_GAPS_EVIDENCE_PLAN_ZERO_CALL_PASS"
EXPECTED_SCOPE_STATUS = "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL_PASS"
EXPECTED_CLAIM_RECON_STATUS = "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL_PASS"
EXPECTED_JUDGE_STATUS = "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED"
EXPECTED_JUDGE_HASH = "3354123bc0244ec258fad0cdab57d5551d5ed8e5d58088d11482bdcd489d259e"
EXPECTED_NEWS_CLOSURE = "ALPACA_NEWS_BOUNDED_TOP_N_SATISFIED"
EXPECTED_REASONS = (
    "VALUATION_SPECIFIC_EVIDENCE_MISSING",
    "PORTFOLIO_INTERACTION_EVIDENCE_MISSING",
)
TARGETS = (
    ("MSFT", ("VALUATION_SPECIFIC_EVIDENCE_MISSING",), "CONDITION_002"),
    (
        "META",
        ("VALUATION_SPECIFIC_EVIDENCE_MISSING", "PORTFOLIO_INTERACTION_EVIDENCE_MISSING"),
        "CONDITION_003",
    ),
)
EXPECTED_B2_METRICS = (
    "return_20s",
    "max_drawdown_20s",
    "adv_20s",
    "annual_revenue_growth",
    "annual_operating_margin",
)

_EPS_RE = re.compile(r"(?i)(diluted\s+(?:earnings\s+per\s+share|eps)|earnings\s+per\s+diluted\s+share)")
_REVENUE_RE = re.compile(r"(?i)\brevenue\b")
_NET_INCOME_RE = re.compile(r"(?i)\bnet\s+income\b")
_SHARE_RE = re.compile(r"(?i)(weighted[- ]average\s+(?:diluted\s+)?shares|shares\s+outstanding)")
_PRICE_RE = re.compile(r"(?i)(point[- ]in[- ]time\s+price|last\s+trade|closing\s+price|market\s+price|\bclose\b)")


class RemainingGapEvidencePlanError(ValueError):
    pass


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemainingGapEvidencePlanError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise RemainingGapEvidencePlanError(f"{label} root must be an object")
    return value


def _verify_hash(payload: Mapping[str, Any], *, label: str, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise RemainingGapEvidencePlanError(f"{label} {field} missing")
    expected = canonical_sha256(payload, exclude_fields=(field,))
    if observed != expected:
        raise RemainingGapEvidencePlanError(f"{label} {field} mismatch")
    return observed


def _candidate_scope_map(scope: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = scope.get("candidate_scopes")
    if not isinstance(rows, list):
        raise RemainingGapEvidencePlanError("remaining-gap scope candidate_scopes missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("candidate_id"), str):
            raise RemainingGapEvidencePlanError("remaining-gap candidate scope malformed")
        candidate = str(row["candidate_id"])
        if candidate in result:
            raise RemainingGapEvidencePlanError("duplicate remaining-gap candidate scope")
        result[candidate] = row
    if tuple(result) != ("NVDA", "MSFT", "META"):
        raise RemainingGapEvidencePlanError("remaining-gap candidate order drift")
    return result


def _retrieval_map(retrieval: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = retrieval.get("candidates")
    if not isinstance(rows, list):
        raise RemainingGapEvidencePlanError("historical B3 retrieval candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("candidate"), str):
            raise RemainingGapEvidencePlanError("historical B3 retrieval candidate malformed")
        candidate = str(row["candidate"])
        if candidate in result:
            raise RemainingGapEvidencePlanError("duplicate historical B3 retrieval candidate")
        result[candidate] = row
    if tuple(result) != ("NVDA", "MSFT", "META"):
        raise RemainingGapEvidencePlanError("historical B3 retrieval candidate order drift")
    return result


def _safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _local_valuation_signal_inventory(candidate: str, retrieval_row: Mapping[str, Any]) -> dict[str, Any]:
    research_evidence = retrieval_row.get("research_evidence")
    rows = research_evidence.get("evidence_items") if isinstance(research_evidence, Mapping) else None
    if not isinstance(rows, list):
        raise RemainingGapEvidencePlanError(f"{candidate} historical evidence items missing")

    sec_rows: list[Mapping[str, Any]] = []
    local_price_refs: list[str] = []
    primitive_refs: dict[str, list[str]] = {
        "diluted_eps": [],
        "revenue": [],
        "net_income": [],
        "share_count": [],
    }
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise RemainingGapEvidencePlanError(f"{candidate} evidence item malformed")
        evidence_id = raw.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise RemainingGapEvidencePlanError(f"{candidate} evidence id missing")
        joined = " | ".join(
            (
                _safe_text(raw.get("field_or_claim")),
                _safe_text(raw.get("normalized_value")),
                _safe_text(raw.get("raw_value_or_record_ref")),
                _safe_text(raw.get("authoritative_for")),
            )
        )
        if raw.get("provider") == "SEC" or str(raw.get("source_type", "")).startswith("SEC"):
            sec_rows.append(raw)
            if _EPS_RE.search(joined):
                primitive_refs["diluted_eps"].append(evidence_id)
            if _REVENUE_RE.search(joined):
                primitive_refs["revenue"].append(evidence_id)
            if _NET_INCOME_RE.search(joined):
                primitive_refs["net_income"].append(evidence_id)
            if _SHARE_RE.search(joined):
                primitive_refs["share_count"].append(evidence_id)
        if _PRICE_RE.search(joined):
            local_price_refs.append(evidence_id)

    for key in tuple(primitive_refs):
        primitive_refs[key] = list(dict.fromkeys(primitive_refs[key]))
    local_price_refs = list(dict.fromkeys(local_price_refs))

    return {
        "sec_evidence_ids": [str(row["evidence_id"]) for row in sec_rows],
        "local_fundamental_primitive_signal_refs": primitive_refs,
        "local_point_in_time_price_signal_refs": local_price_refs,
        "local_fundamental_text_available": bool(sec_rows),
        "local_point_in_time_price_signal_detected": bool(local_price_refs),
        "local_exact_valuation_multiple_already_present": False,
    }


def _walk_for_key(value: Any, key: str, *, path: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for k, child in value.items():
            child_path = f"{path}.{k}"
            if k == key:
                found.append((child_path, child))
            found.extend(_walk_for_key(child, key, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_for_key(child, key, path=f"{path}[{index}]"))
    return found


def _discover_local_portfolio_refs(runtime_root: str | Path) -> list[dict[str, Any]]:
    root = Path(runtime_root)
    if not root.exists() or not root.is_dir():
        raise RemainingGapEvidencePlanError("runtime_root is missing")
    discoveries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        matches = _walk_for_key(payload, "portfolio_snapshot_ref")
        for json_path, value in matches:
            if isinstance(value, str) and value.strip():
                discoveries.append(
                    {
                        "path": str(path),
                        "json_path": json_path,
                        "portfolio_snapshot_ref": value,
                    }
                )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in discoveries:
        key = (row["path"], row["json_path"], row["portfolio_snapshot_ref"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _judge_conditions(judge: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    structured = judge.get("structured_output")
    rows = structured.get("what_would_change_decision") if isinstance(structured, Mapping) else None
    if not isinstance(rows, list):
        raise RemainingGapEvidencePlanError("Judge what_would_change_decision missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("condition_id"), str):
            raise RemainingGapEvidencePlanError("Judge condition malformed")
        result[str(row["condition_id"])] = row
    if tuple(result) != ("CONDITION_001", "CONDITION_002", "CONDITION_003"):
        raise RemainingGapEvidencePlanError("Judge condition order/surface drift")
    if "valuation evidence for MSFT" not in str(result["CONDITION_002"].get("condition_text", "")):
        raise RemainingGapEvidencePlanError("Judge MSFT valuation condition drift")
    meta_text = str(result["CONDITION_003"].get("condition_text", ""))
    if "valuation-specific" not in meta_text or "portfolio-interaction" not in meta_text:
        raise RemainingGapEvidencePlanError("Judge META valuation/portfolio condition drift")
    return result


def build_remaining_gaps_evidence_plan(
    *,
    code_commit_sha: str,
    scope_path: str | Path,
    claim_reconciliation_path: str | Path,
    judge_result_path: str | Path,
    retrieval_path: str | Path,
    handoff_path: str | Path,
    runtime_root: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise RemainingGapEvidencePlanError("code_commit_sha must be lowercase 40-char SHA")

    scope = _read_json(scope_path, label="remaining-gap scope")
    scope_hash = _verify_hash(scope, label="remaining-gap scope")
    if scope.get("status") != EXPECTED_SCOPE_STATUS:
        raise RemainingGapEvidencePlanError("remaining-gap scope is not PASS")
    if tuple(scope.get("remaining_reopen_reason_codes", [])) != EXPECTED_REASONS:
        raise RemainingGapEvidencePlanError("remaining-gap reason scope drift")
    if scope.get("valuation_gap_closed_by_this_inventory") is not False:
        raise RemainingGapEvidencePlanError("valuation gap unexpectedly closed")
    if scope.get("portfolio_interaction_gap_closed_by_this_inventory") is not False:
        raise RemainingGapEvidencePlanError("portfolio gap unexpectedly closed")

    claim_recon = _read_json(claim_reconciliation_path, label="claim reconciliation")
    claim_recon_hash = _verify_hash(claim_recon, label="claim reconciliation")
    if claim_recon_hash != scope.get("source_claim_reconciliation_hash"):
        raise RemainingGapEvidencePlanError("claim-reconciliation lineage mismatch")
    if claim_recon.get("status") != EXPECTED_CLAIM_RECON_STATUS:
        raise RemainingGapEvidencePlanError("claim reconciliation is not PASS")
    if claim_recon.get("closure_evidence_ref") != EXPECTED_NEWS_CLOSURE:
        raise RemainingGapEvidencePlanError("news closure lineage missing")

    judge = _read_json(judge_result_path, label="production Judge result")
    judge_hash = _verify_hash(judge, label="production Judge result")
    if judge_hash != EXPECTED_JUDGE_HASH or judge_hash != claim_recon.get("source_production_judge_result_hash"):
        raise RemainingGapEvidencePlanError("production Judge lineage mismatch")
    if judge.get("status") != EXPECTED_JUDGE_STATUS:
        raise RemainingGapEvidencePlanError("production Judge status drift")
    reopen = judge.get("research_reopen_request")
    if not isinstance(reopen, Mapping):
        raise RemainingGapEvidencePlanError("production Judge reopen request missing")
    if tuple(reopen.get("reason_codes", [])) != (
        "ALPACA_NEWS_PAGINATION_INCOMPLETE",
        *EXPECTED_REASONS,
    ):
        raise RemainingGapEvidencePlanError("production Judge reopen reason surface drift")
    conditions = _judge_conditions(judge)

    retrieval = _read_json(retrieval_path, label="historical B3 retrieval")
    retrieval_hash = _verify_hash(retrieval, label="historical B3 retrieval")
    if retrieval_hash != scope.get("source_historical_b3_retrieval_hash"):
        raise RemainingGapEvidencePlanError("historical B3 retrieval lineage mismatch")
    retrieval_by_candidate = _retrieval_map(retrieval)

    handoff = _read_json(handoff_path, label="B2 handoff")
    handoff_hash = _verify_hash(handoff, label="B2 handoff", field="handoff_hash")
    if handoff_hash != scope.get("source_b2_handoff_hash"):
        raise RemainingGapEvidencePlanError("B2 handoff lineage mismatch")
    handoff_rows = handoff.get("candidates")
    if not isinstance(handoff_rows, list):
        raise RemainingGapEvidencePlanError("B2 handoff candidates missing")
    for row in handoff_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("metrics"), list):
            raise RemainingGapEvidencePlanError("B2 handoff candidate/metrics malformed")
        metric_ids = tuple(
            metric.get("metric_id")
            for metric in row["metrics"]
            if isinstance(metric, Mapping)
        )
        if metric_ids != EXPECTED_B2_METRICS:
            raise RemainingGapEvidencePlanError("B2 metric surface drift")

    scope_by_candidate = _candidate_scope_map(scope)
    if scope.get("inventory_summary", {}).get("valuation_context_claim_count") != 0:
        raise RemainingGapEvidencePlanError("valuation inventory is no longer empty")
    if scope.get("inventory_summary", {}).get("shared_portfolio_context_ref_count") != 0:
        raise RemainingGapEvidencePlanError("shared portfolio context unexpectedly exists")

    portfolio_discoveries = _discover_local_portfolio_refs(runtime_root)
    targets: list[dict[str, Any]] = []
    for candidate, gaps, condition_id in TARGETS:
        candidate_scope = scope_by_candidate[candidate]
        valuation_inventory = _local_valuation_signal_inventory(candidate, retrieval_by_candidate[candidate])
        condition = conditions[condition_id]
        refs = condition.get("source_or_claim_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise RemainingGapEvidencePlanError(f"{condition_id} source refs malformed")
        targets.append(
            {
                "candidate_id": candidate,
                "required_gap_codes": list(gaps),
                "judge_condition_id": condition_id,
                "judge_condition_text": condition["condition_text"],
                "judge_source_or_claim_refs": list(refs),
                "valuation_inventory": valuation_inventory,
                "historical_valuation_claim_count": candidate_scope["valuation_context"]["claim_count"],
                "historical_portfolio_interaction_claim_count": candidate_scope["portfolio_interaction"]["claim_count"],
            }
        )

    if scope_by_candidate["NVDA"]["valuation_context"]["claim_count"] != 0:
        raise RemainingGapEvidencePlanError("NVDA valuation inventory drift")

    valuation_actions = [
        {
            "candidate_id": candidate,
            "phase_1_local_only": [
                "Extract a valuation denominator from already-frozen SEC evidence if the text supports it, preferring diluted EPS; otherwise use revenue plus share-count only if both are provenance-resolved.",
                "Search retained B2/B3 point-in-time market evidence for a price at or before the 2026-08-28T17:34:00Z research cutoff.",
            ],
            "required_output_contract": {
                "minimum_supported_valuation_metric_count": 1,
                "accepted_primary_metric_priority": ["TRAILING_PE", "PRICE_TO_SALES"],
                "requires_point_in_time_price_at_or_before_cutoff": True,
                "requires_primary_filing_denominator_provenance": True,
                "binary_float_forbidden": True,
            },
            "external_read_fallback_not_authorized": {
                "alpaca_market_data": "At most one point-in-time price read for this candidate if no retained local price evidence exists.",
                "sec": "At most one bounded primary-filing read only if the retained SEC evidence cannot supply an auditable denominator.",
            },
        }
        for candidate in ("MSFT", "META")
    ]

    portfolio_action = {
        "candidate_id": "META",
        "phase_1_local_only": [
            "Recover the frozen B2 SnapshotManifest and its portfolio_snapshot_ref from retained local artifacts.",
            "Resolve the referenced historical portfolio snapshot/receipt and compute point-in-time position overlap and concentration context for META without changing the cutoff.",
        ],
        "required_output_contract": {
            "must_be_point_in_time_at_or_before_b2_decision_cutoff": True,
            "current_2026_08_30_positions_are_not_valid_substitute": True,
            "minimum_fields": [
                "portfolio_snapshot_ref",
                "snapshot_as_of",
                "account_equity_or_portfolio_value_if_available",
                "META_existing_position_or_zero",
                "META_existing_weight_if_derivable",
                "top_position_or_concentration_context_if_derivable",
            ],
        },
        "local_portfolio_snapshot_ref_discoveries": portfolio_discoveries,
        "external_read_fallback_not_authorized": {
            "current_positions": "PROHIBITED_AS_CUTOFF_SUBSTITUTE",
            "historical_reconstruction": "Requires a separate zero-call preflight and owner approval only if no retained historical snapshot can be resolved.",
        },
    }

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_remaining_gaps_scope_hash": scope_hash,
        "source_claim_reconciliation_hash": claim_recon_hash,
        "source_production_judge_result_hash": judge_hash,
        "source_historical_b3_retrieval_hash": retrieval_hash,
        "source_b2_handoff_hash": handoff_hash,
        "closed_news_gap_ref": "ALPACA_NEWS_PAGINATION_INCOMPLETE",
        "closed_news_gap_evidence_ref": EXPECTED_NEWS_CLOSURE,
        "active_reopen_reason_codes": list(EXPECTED_REASONS),
        "target_candidates": ["MSFT", "META"],
        "non_target_candidate_ids": ["NVDA"],
        "target_scopes": targets,
        "valuation_evidence_plan": valuation_actions,
        "portfolio_interaction_evidence_plan": portfolio_action,
        "provider_reads_authorized": False,
        "planned_provider_reads_at_this_gate": 0,
        "model_calls_authorized": False,
        "planned_model_calls_at_this_gate": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_ZERO_CALL",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
