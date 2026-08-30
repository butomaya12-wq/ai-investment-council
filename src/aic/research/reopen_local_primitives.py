from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_v0_1"
PASS_STATUS = "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_ZERO_CALL_PASS"
EXPECTED_PLAN_STATUS = "B3_REOPEN_REMAINING_GAPS_EVIDENCE_PLAN_ZERO_CALL_PASS"
EXPECTED_SCOPE_STATUS = "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL_PASS"
EXPECTED_PLAN_NEXT_GATE = "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_ZERO_CALL"
EXPECTED_HANDOFF_HASH = "75df1e47b1f469bdce6d118f7a529b3f7a95061bcd760d756918a0e13e1a04e7"
TARGET_CANDIDATES = ("MSFT", "META")

_EPS_SIGNAL_RE = re.compile(
    r"(?i)(?:diluted\s+(?:earnings\s+per\s+share|eps)|earnings\s+per\s+diluted\s+share|per\s+diluted\s+share)"
)
_PRICE_SIGNAL_RE = re.compile(
    r"(?i)(?:closing\s+price|market\s+price|last\s+(?:trade|sale|price)|stock\s+price|share\s+price|\bclose\b)"
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\$?\(?-?\d+(?:,\d{3})*(?:\.\d+)?\)?%?")
_MARKET_SOURCE_RE = re.compile(r"(?i)(BAR|TRADE|QUOTE|MARKET[_ -]?SNAPSHOT|PRICE[_ -]?SNAPSHOT)")
_MARKET_FIELD_RE = re.compile(r"(?i)^(?:close|closing_price|market_price|last_trade|last_price|price)$")
_TIMESTAMP_KEYS = ("snapshot_as_of", "as_of", "decision_cutoff", "created_at", "captured_at")


class LocalPrimitiveReviewError(ValueError):
    pass


def _read_json(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalPrimitiveReviewError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise LocalPrimitiveReviewError(f"{label} root must be an object")
    return value


def _verify_hash(payload: Mapping[str, Any], *, label: str, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise LocalPrimitiveReviewError(f"{label} {field} missing")
    expected = canonical_sha256(payload, exclude_fields=(field,))
    if observed != expected:
        raise LocalPrimitiveReviewError(f"{label} {field} mismatch")
    return observed


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _context_fragments(text: str, signal_re: re.Pattern[str], *, radius: int = 240, limit: int = 12) -> list[dict[str, Any]]:
    collapsed = _collapse(text)
    fragments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in signal_re.finditer(collapsed):
        start = max(0, match.start() - radius)
        end = min(len(collapsed), match.end() + radius)
        fragment = collapsed[start:end].strip()
        if fragment in seen:
            continue
        seen.add(fragment)
        fragments.append(
            {
                "matched_signal": match.group(0),
                "fragment": fragment,
                "numeric_tokens": _NUMBER_RE.findall(fragment),
            }
        )
        if len(fragments) >= limit:
            break
    return fragments


def _candidate_map(retrieval: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = retrieval.get("candidates")
    if not isinstance(rows, list):
        raise LocalPrimitiveReviewError("historical B3 retrieval candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("candidate"), str):
            raise LocalPrimitiveReviewError("historical B3 retrieval candidate malformed")
        candidate = str(row["candidate"])
        if candidate in result:
            raise LocalPrimitiveReviewError("duplicate historical B3 retrieval candidate")
        result[candidate] = row
    if tuple(result) != ("NVDA", "MSFT", "META"):
        raise LocalPrimitiveReviewError("historical B3 retrieval candidate order drift")
    return result


def _evidence_rows(candidate: str, retrieval_row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    research_evidence = retrieval_row.get("research_evidence")
    rows = research_evidence.get("evidence_items") if isinstance(research_evidence, Mapping) else None
    if not isinstance(rows, list):
        raise LocalPrimitiveReviewError(f"{candidate} evidence items missing")
    output: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("evidence_id"), str):
            raise LocalPrimitiveReviewError(f"{candidate} evidence item malformed")
        output.append(row)
    return output


def _valuation_primitives(candidate: str, retrieval_row: Mapping[str, Any], *, research_cutoff: datetime) -> dict[str, Any]:
    rows = _evidence_rows(candidate, retrieval_row)
    eps_fragments: list[dict[str, Any]] = []
    disqualified_sec_price_fragments: list[dict[str, Any]] = []
    market_price_candidates: list[dict[str, Any]] = []

    for row in rows:
        evidence_id = str(row["evidence_id"])
        provider = str(row.get("provider") or "")
        source_type = str(row.get("source_type") or "")
        field_or_claim = str(row.get("field_or_claim") or "")
        normalized = _safe_text(row.get("normalized_value"))
        raw_ref = _safe_text(row.get("raw_value_or_record_ref"))
        joined = " | ".join((field_or_claim, normalized, raw_ref, _safe_text(row.get("authoritative_for"))))
        is_sec = provider == "SEC" or source_type.startswith("SEC")
        is_news = source_type == "ALPACA_NEWS"

        if is_sec and _EPS_SIGNAL_RE.search(joined):
            for fragment in _context_fragments(joined, _EPS_SIGNAL_RE):
                eps_fragments.append(
                    {
                        "evidence_id": evidence_id,
                        "source_type": source_type,
                        "source_uri": row.get("source_uri"),
                        "evidence_as_of": row.get("as_of"),
                        **fragment,
                    }
                )

        if is_sec and _PRICE_SIGNAL_RE.search(joined):
            for fragment in _context_fragments(joined, _PRICE_SIGNAL_RE, limit=8):
                disqualified_sec_price_fragments.append(
                    {
                        "evidence_id": evidence_id,
                        "source_type": source_type,
                        "classification": "SEC_TEXT_NOT_CUTOFF_MARKET_FEED",
                        "reason": "A primary filing can mention prices, but its section text is not a point-in-time market-price observation at the research cutoff.",
                        **fragment,
                    }
                )

        if is_sec or is_news:
            continue
        as_of = _parse_utc(row.get("as_of")) or _parse_utc(row.get("observed_at"))
        if as_of is None or as_of > research_cutoff:
            continue
        market_source = bool(_MARKET_SOURCE_RE.search(source_type))
        market_field = bool(_MARKET_FIELD_RE.fullmatch(field_or_claim.strip()))
        if not (market_source or market_field):
            continue
        if not _PRICE_SIGNAL_RE.search(joined) and not market_field:
            continue
        numeric_tokens = _NUMBER_RE.findall(_collapse(joined))
        if not numeric_tokens:
            continue
        market_price_candidates.append(
            {
                "evidence_id": evidence_id,
                "provider": provider,
                "source_type": source_type,
                "field_or_claim": field_or_claim,
                "as_of": as_of.isoformat().replace("+00:00", "Z"),
                "numeric_tokens": numeric_tokens[:12],
                "normalized_value": row.get("normalized_value"),
                "classification": "ELIGIBLE_LOCAL_MARKET_PRICE_EVIDENCE",
            }
        )

    # Stable de-duplication by evidence/fragment identity.
    unique_eps: list[dict[str, Any]] = []
    seen_eps: set[tuple[str, str]] = set()
    for row in eps_fragments:
        key = (str(row["evidence_id"]), str(row["fragment"]))
        if key not in seen_eps:
            seen_eps.add(key)
            unique_eps.append(row)

    unique_sec_price: list[dict[str, Any]] = []
    seen_sec: set[tuple[str, str]] = set()
    for row in disqualified_sec_price_fragments:
        key = (str(row["evidence_id"]), str(row["fragment"]))
        if key not in seen_sec:
            seen_sec.add(key)
            unique_sec_price.append(row)

    return {
        "candidate_id": candidate,
        "diluted_eps_candidate_fragments": unique_eps,
        "diluted_eps_candidate_fragment_count": len(unique_eps),
        "diluted_eps_denominator_status": (
            "LOCAL_PRIMARY_FILING_CANDIDATES_PRESENT_NEEDS_DETERMINISTIC_SELECTION"
            if unique_eps
            else "LOCAL_PRIMARY_FILING_DENOMINATOR_NOT_FOUND"
        ),
        "disqualified_sec_price_fragments": unique_sec_price,
        "disqualified_sec_price_fragment_count": len(unique_sec_price),
        "eligible_local_market_price_candidates": market_price_candidates,
        "eligible_local_market_price_candidate_count": len(market_price_candidates),
        "local_market_price_status": (
            "LOCAL_MARKET_PRICE_RESOLVED"
            if len(market_price_candidates) == 1
            else "LOCAL_MARKET_PRICE_AMBIGUOUS"
            if len(market_price_candidates) > 1
            else "LOCAL_MARKET_PRICE_NOT_FOUND"
        ),
        "valuation_metric_computed_at_this_gate": False,
    }


def _walk_mappings(value: Any, *, path: str = "$") -> list[tuple[str, Mapping[str, Any]]]:
    found: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        found.append((path, value))
        for key, child in value.items():
            found.extend(_walk_mappings(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_mappings(child, path=f"{path}[{index}]"))
    return found


def _first_timestamp(mapping: Mapping[str, Any]) -> tuple[str | None, datetime | None]:
    for key in _TIMESTAMP_KEYS:
        parsed = _parse_utc(mapping.get(key))
        if parsed is not None:
            return key, parsed
    return None, None


def _position_symbol(item: Mapping[str, Any]) -> str | None:
    for key in ("symbol", "asset", "ticker"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _portfolio_discoveries(*, roots: tuple[str | Path, ...], b2_cutoff: datetime) -> list[dict[str, Any]]:
    discoveries: list[dict[str, Any]] = []
    for root_value in roots:
        root = Path(root_value)
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            try:
                if path.stat().st_size > 8_000_000:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for json_path, mapping in _walk_mappings(payload):
                positions = mapping.get("positions")
                portfolio_ref = mapping.get("portfolio_snapshot_ref")
                has_ref = isinstance(portfolio_ref, str) and bool(portfolio_ref.strip())
                has_positions = isinstance(positions, list) and all(isinstance(item, Mapping) for item in positions)
                if not has_ref and not has_positions:
                    continue
                timestamp_key, as_of = _first_timestamp(mapping)
                position_rows = [item for item in positions if isinstance(item, Mapping)] if has_positions else []
                meta_rows = [item for item in position_rows if _position_symbol(item) == "META"]
                historical = as_of is not None and as_of <= b2_cutoff
                discoveries.append(
                    {
                        "path": str(path),
                        "json_path": json_path,
                        "portfolio_snapshot_ref": portfolio_ref if has_ref else None,
                        "timestamp_field": timestamp_key,
                        "snapshot_as_of": None if as_of is None else as_of.isoformat().replace("+00:00", "Z"),
                        "at_or_before_b2_cutoff": historical,
                        "position_count": len(position_rows),
                        "meta_position_present": bool(meta_rows),
                        "meta_position_safe_view": [
                            {
                                key: row.get(key)
                                for key in ("symbol", "asset", "ticker", "qty", "quantity", "market_value", "weight")
                                if key in row
                            }
                            for row in meta_rows[:2]
                        ],
                        "account_equity_or_portfolio_value": mapping.get("equity", mapping.get("portfolio_value")),
                    }
                )
    # Stable unique rows.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in discoveries:
        key = (row["path"], row["json_path"], str(row.get("portfolio_snapshot_ref")))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def build_local_primitives_review(
    *,
    code_commit_sha: str,
    evidence_plan_path: str | Path,
    scope_path: str | Path,
    retrieval_path: str | Path,
    handoff_path: str | Path,
    runtime_root: str | Path,
    config_root: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise LocalPrimitiveReviewError("code_commit_sha must be lowercase 40-char SHA")

    plan = _read_json(evidence_plan_path, label="remaining-gaps evidence plan")
    plan_hash = _verify_hash(plan, label="remaining-gaps evidence plan")
    if plan.get("status") != EXPECTED_PLAN_STATUS or plan.get("next_gate") != EXPECTED_PLAN_NEXT_GATE:
        raise LocalPrimitiveReviewError("remaining-gaps evidence plan status/gate drift")
    if plan.get("provider_reads_authorized") is not False or plan.get("model_calls_authorized") is not False:
        raise LocalPrimitiveReviewError("remaining-gaps evidence plan unexpectedly authorizes external calls")
    if plan.get("target_candidates") != ["MSFT", "META"] or plan.get("non_target_candidate_ids") != ["NVDA"]:
        raise LocalPrimitiveReviewError("remaining-gaps target scope drift")

    scope = _read_json(scope_path, label="remaining-gaps scope")
    scope_hash = _verify_hash(scope, label="remaining-gaps scope")
    if scope.get("status") != EXPECTED_SCOPE_STATUS or plan.get("source_remaining_gaps_scope_hash") != scope_hash:
        raise LocalPrimitiveReviewError("remaining-gaps scope lineage mismatch")

    retrieval = _read_json(retrieval_path, label="historical B3 retrieval")
    retrieval_hash = _verify_hash(retrieval, label="historical B3 retrieval")
    if scope.get("source_historical_b3_retrieval_hash") != retrieval_hash:
        raise LocalPrimitiveReviewError("historical B3 retrieval lineage mismatch")

    handoff = _read_json(handoff_path, label="B2 handoff")
    handoff_hash = _verify_hash(handoff, label="B2 handoff", field="handoff_hash")
    if handoff_hash != EXPECTED_HANDOFF_HASH or scope.get("source_b2_handoff_hash") != handoff_hash:
        raise LocalPrimitiveReviewError("B2 handoff lineage mismatch")
    research_cutoff = _parse_utc(handoff.get("research_cutoff"))
    b2_cutoff = _parse_utc(handoff.get("b2_decision_cutoff"))
    if research_cutoff is None or b2_cutoff is None:
        raise LocalPrimitiveReviewError("B2 handoff cutoff timestamps invalid")

    retrieval_by_candidate = _candidate_map(retrieval)
    valuation_reviews = [
        _valuation_primitives(candidate, retrieval_by_candidate[candidate], research_cutoff=research_cutoff)
        for candidate in TARGET_CANDIDATES
    ]
    portfolio_discoveries = _portfolio_discoveries(
        roots=(runtime_root, config_root),
        b2_cutoff=b2_cutoff,
    )
    historical_portfolio_candidates = [
        row for row in portfolio_discoveries
        if row["at_or_before_b2_cutoff"] and row["position_count"] > 0
    ]

    missing_price_candidates = [
        row["candidate_id"] for row in valuation_reviews
        if row["eligible_local_market_price_candidate_count"] == 0
    ]
    ambiguous_price_candidates = [
        row["candidate_id"] for row in valuation_reviews
        if row["eligible_local_market_price_candidate_count"] > 1
    ]
    missing_eps_candidates = [
        row["candidate_id"] for row in valuation_reviews
        if row["diluted_eps_candidate_fragment_count"] == 0
    ]

    external_need_summary = {
        "point_in_time_market_price_read_candidates": missing_price_candidates,
        "point_in_time_market_price_local_disambiguation_candidates": ambiguous_price_candidates,
        "primary_filing_denominator_read_candidates": missing_eps_candidates,
        "historical_portfolio_reconstruction_needed": len(historical_portfolio_candidates) == 0,
        "external_reads_authorized": False,
    }
    external_read_needed = bool(
        missing_price_candidates
        or missing_eps_candidates
        or len(historical_portfolio_candidates) == 0
    )

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_evidence_plan_hash": plan_hash,
        "source_remaining_gaps_scope_hash": scope_hash,
        "source_historical_b3_retrieval_hash": retrieval_hash,
        "source_b2_handoff_hash": handoff_hash,
        "research_cutoff": handoff["research_cutoff"],
        "b2_decision_cutoff": handoff["b2_decision_cutoff"],
        "target_candidates": list(TARGET_CANDIDATES),
        "valuation_primitive_reviews": valuation_reviews,
        "portfolio_local_discoveries": portfolio_discoveries,
        "historical_portfolio_candidate_count": len(historical_portfolio_candidates),
        "historical_portfolio_candidates": historical_portfolio_candidates,
        "external_need_summary": external_need_summary,
        "external_read_needed_after_local_review": external_read_needed,
        "valuation_gap_closed_by_this_gate": False,
        "portfolio_interaction_gap_closed_by_this_gate": False,
        "provider_reads_authorized": False,
        "planned_provider_reads_at_this_gate": 0,
        "model_calls_authorized": False,
        "planned_model_calls_at_this_gate": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "legacy_frozen_artifacts_mutated": False,
        "next_gate": (
            "B3_REOPEN_MINIMAL_EXTERNAL_READ_PREFLIGHT_ZERO_CALL"
            if external_read_needed
            else "B3_REOPEN_LOCAL_PRIMITIVE_COMPUTATION_ZERO_CALL"
        ),
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
