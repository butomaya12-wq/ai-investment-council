from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Mapping

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_v0_1"
PASS_STATUS = "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL_PASS"
EXPECTED_BOUNDED_REVIEW_VERSION = "B3_REOPEN_BOUNDED_NEWS_REVIEW_v0_1"
EXPECTED_BOUNDED_REVIEW_STATUS = "B3_REOPEN_BOUNDED_NEWS_ZERO_CALL_PASS"
EXPECTED_B3_RECONCILIATION_VERSION = "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1"
EXPECTED_B3_RECONCILIATION_STATUS = "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED"
EXPECTED_B4_INPUT_FREEZE_VERSION = "B4_COUNCIL_INPUT_FREEZE_ARTIFACT_v0_1"
EXPECTED_JUDGE_STATUS = "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED"
EXPECTED_CANDIDATES = ("NVDA", "MSFT", "META")
SUPERSEDED_GAP_REF = "ALPACA_NEWS_PAGINATION_INCOMPLETE"
CLOSURE_EVIDENCE_REF = "ALPACA_NEWS_BOUNDED_TOP_N_SATISFIED"
VALUATION_GAP = "VALUATION_SPECIFIC_EVIDENCE_MISSING"
PORTFOLIO_GAP = "PORTFOLIO_INTERACTION_EVIDENCE_MISSING"
EXPECTED_REOPEN_REASON_CODES = (SUPERSEDED_GAP_REF, VALUATION_GAP, PORTFOLIO_GAP)
NEXT_GATE = "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL"

# Immutable event anchors. Tests may override them when exercising synthetic fixtures.
EXPECTED_INITIAL_FREEZE_HASH = "ca7391e5e0c3a754eabc54fbf959b0f36e0986b552d405a06cf649116135361f"
EXPECTED_REBUTTAL_FREEZE_HASH = "dd83aa4f873c0d6ac6582cd6dc89c1612088239aa8e979d18f7f95c3386552a5"
EXPECTED_JUDGE_RESULT_HASH = "3354123bc0244ec258fad0cdab57d5551d5ed8e5d58088d11482bdcd489d259e"


class ReopenClaimReconciliationError(ValueError):
    pass


def _load_json(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReopenClaimReconciliationError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise ReopenClaimReconciliationError(f"{label} root must be an object")
    return payload


def _validate_hash_shape(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ReopenClaimReconciliationError(f"{field} must be a lowercase SHA-256")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise ReopenClaimReconciliationError(f"{field} must be a lowercase SHA-256")
    return value


def _validate_self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    observed = _validate_hash_shape(payload.get("artifact_hash"), field=f"{label}.artifact_hash")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if observed != expected:
        raise ReopenClaimReconciliationError(f"{label} self-hash mismatch")
    return observed


def _as_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise ReopenClaimReconciliationError(f"{field} must be an array of non-empty trimmed strings")
    if len(set(value)) != len(value):
        raise ReopenClaimReconciliationError(f"{field} must not contain duplicates")
    return list(value)


def _candidate_map(payload: Mapping[str, Any], *, label: str) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        raise ReopenClaimReconciliationError(f"{label}.candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReopenClaimReconciliationError(f"{label} candidate row must be an object")
        candidate = row.get("candidate")
        if not isinstance(candidate, str) or candidate in result:
            raise ReopenClaimReconciliationError(f"{label} candidate identity invalid or duplicated")
        result[candidate] = row
    if tuple(result) != EXPECTED_CANDIDATES:
        raise ReopenClaimReconciliationError(f"{label} candidate order drift")
    return result


def _bundle_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("bundles")
    if not isinstance(rows, list):
        raise ReopenClaimReconciliationError("B4 input freeze bundles missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReopenClaimReconciliationError("B4 input freeze bundle must be an object")
        candidate = row.get("candidate_id")
        if not isinstance(candidate, str) or candidate in result:
            raise ReopenClaimReconciliationError("B4 input freeze candidate identity invalid or duplicated")
        result[candidate] = row
    if tuple(result) != EXPECTED_CANDIDATES:
        raise ReopenClaimReconciliationError("B4 input freeze candidate order drift")
    return result


def _walk_scalars(value: Any, *, path: str = "$") -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_scalars(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_scalars(child, path=f"{path}[{index}]")
        return
    yield path, value


def _scalar_paths(payload: Mapping[str, Any], target: str) -> list[str]:
    return sorted(path for path, value in _walk_scalars(payload) if value == target)


def _iter_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mappings(child)


def _find_reopen_reason_codes(judge_result: Mapping[str, Any]) -> tuple[str, ...]:
    matches: set[tuple[str, ...]] = set()
    for row in _iter_mappings(judge_result):
        raw = row.get("reason_codes")
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            continue
        values = tuple(raw)
        if SUPERSEDED_GAP_REF in values and VALUATION_GAP in values and PORTFOLIO_GAP in values:
            matches.add(values)
    if EXPECTED_REOPEN_REASON_CODES not in matches:
        raise ReopenClaimReconciliationError("Judge research-reopen reason-code lineage drift")
    return EXPECTED_REOPEN_REASON_CODES


def _validate_bounded_review(payload: Mapping[str, Any]) -> str:
    artifact_hash = _validate_self_hash(payload, label="bounded-news review")
    if payload.get("artifact_version") != EXPECTED_BOUNDED_REVIEW_VERSION:
        raise ReopenClaimReconciliationError("bounded-news review version drift")
    if payload.get("status") != EXPECTED_BOUNDED_REVIEW_STATUS:
        raise ReopenClaimReconciliationError("bounded-news review is not PASS")
    if payload.get("superseded_source_ref_id") != SUPERSEDED_GAP_REF:
        raise ReopenClaimReconciliationError("bounded-news superseded ref drift")
    if payload.get("replacement_source_ref_id") != CLOSURE_EVIDENCE_REF:
        raise ReopenClaimReconciliationError("bounded-news replacement ref drift")
    if payload.get("gap_closed") is not True:
        raise ReopenClaimReconciliationError("bounded-news gap is not closed")
    if payload.get("provider_dataset_exhaustion_required") is not False:
        raise ReopenClaimReconciliationError("bounded-news exhaustion semantics drift")
    for field in ("new_provider_reads", "model_calls", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise ReopenClaimReconciliationError(f"bounded-news side-effect invariant violated: {field}")
    if payload.get("live_money") != "PROHIBITED":
        raise ReopenClaimReconciliationError("bounded-news live-money invariant drift")
    reviews = payload.get("candidate_reviews")
    if not isinstance(reviews, list) or [row.get("candidate_id") for row in reviews if isinstance(row, Mapping)] != list(EXPECTED_CANDIDATES):
        raise ReopenClaimReconciliationError("bounded-news candidate reviews drift")
    if any(not isinstance(row, Mapping) or row.get("bounded_request_satisfied") is not True for row in reviews):
        raise ReopenClaimReconciliationError("bounded-news candidate request is not satisfied")
    return artifact_hash


def _reconcile_candidates(
    reconciliation: Mapping[str, Any],
    input_freeze: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    records = _candidate_map(reconciliation, label="B3 selected-model reconciliation")
    bundles = _bundle_map(input_freeze)
    output: list[dict[str, Any]] = []
    total_claim_count = 0
    for candidate in EXPECTED_CANDIDATES:
        record = records[candidate]
        if record.get("status") != "CANONICAL_RECONCILED":
            raise ReopenClaimReconciliationError(f"{candidate} is not CANONICAL_RECONCILED")
        if record.get("reconstructibility_status") != "PASS":
            raise ReopenClaimReconciliationError(f"{candidate} reconciliation is not reconstructible")
        record_gaps = _as_string_list(record.get("source_gaps"), field=f"{candidate}.source_gaps")
        if record_gaps != [SUPERSEDED_GAP_REF]:
            raise ReopenClaimReconciliationError(f"{candidate} historical source-gap surface drift")
        packet = record.get("candidate_packet")
        if not isinstance(packet, Mapping):
            raise ReopenClaimReconciliationError(f"{candidate} candidate_packet missing")
        packet_gaps = _as_string_list(packet.get("source_gaps"), field=f"{candidate}.candidate_packet.source_gaps")
        if packet_gaps != record_gaps:
            raise ReopenClaimReconciliationError(f"{candidate} candidate packet/source-gap lineage mismatch")

        claims = record.get("material_claims")
        if not isinstance(claims, list) or not claims:
            raise ReopenClaimReconciliationError(f"{candidate} material_claims missing")
        claim_ids: list[str] = []
        for claim in claims:
            if not isinstance(claim, Mapping):
                raise ReopenClaimReconciliationError(f"{candidate} material claim malformed")
            claim_id = claim.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id or claim_id != claim_id.strip():
                raise ReopenClaimReconciliationError(f"{candidate} material claim id invalid")
            claim_ids.append(claim_id)
        if len(set(claim_ids)) != len(claim_ids):
            raise ReopenClaimReconciliationError(f"{candidate} duplicate material claim id")

        bundle = bundles[candidate]
        allowed_claim_ids = _as_string_list(
            bundle.get("allowed_material_claim_ids"),
            field=f"{candidate}.allowed_material_claim_ids",
        )
        if claim_ids != allowed_claim_ids:
            raise ReopenClaimReconciliationError(f"{candidate} material claim allowlist/order drift")
        total_claim_count += len(claim_ids)
        output.append(
            {
                "candidate_id": candidate,
                "historical_source_gaps": record_gaps,
                "superseded_source_gap_ref": SUPERSEDED_GAP_REF,
                "closure_evidence_ref": CLOSURE_EVIDENCE_REF,
                "effective_open_source_gaps": [],
                "source_gap_state": "CLOSED_BY_BOUNDED_TOP_N_EVIDENCE",
                "material_claim_count": len(claim_ids),
                "material_claim_ids_hash": canonical_sha256(claim_ids),
                "material_claim_rewrite_required": False,
                "material_claim_ids_unchanged": True,
            }
        )
    return output, total_claim_count


def build_claim_reconciliation(
    *,
    code_commit_sha: str,
    bounded_review_path: str | Path,
    b3_reconciliation_path: str | Path,
    b4_input_freeze_path: str | Path,
    initial_freeze_path: str | Path,
    rebuttal_freeze_path: str | Path,
    judge_result_path: str | Path,
    expected_initial_freeze_hash: str = EXPECTED_INITIAL_FREEZE_HASH,
    expected_rebuttal_freeze_hash: str = EXPECTED_REBUTTAL_FREEZE_HASH,
    expected_judge_result_hash: str = EXPECTED_JUDGE_RESULT_HASH,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise ReopenClaimReconciliationError("code_commit_sha must be lowercase 40-char git SHA")

    bounded = _load_json(bounded_review_path, label="bounded-news review")
    bounded_hash = _validate_bounded_review(bounded)

    reconciliation = _load_json(b3_reconciliation_path, label="B3 selected-model reconciliation")
    reconciliation_hash = _validate_self_hash(reconciliation, label="B3 selected-model reconciliation")
    if reconciliation.get("artifact_version") != EXPECTED_B3_RECONCILIATION_VERSION:
        raise ReopenClaimReconciliationError("B3 reconciliation version drift")
    if reconciliation.get("canonical_reconciliation") != EXPECTED_B3_RECONCILIATION_STATUS:
        raise ReopenClaimReconciliationError("B3 reconciliation is not canonical/final")
    if reconciliation.get("reconstructibility_status") != "PASS":
        raise ReopenClaimReconciliationError("B3 reconciliation is not reconstructible")

    input_freeze = _load_json(b4_input_freeze_path, label="B4 input freeze")
    input_freeze_hash = _validate_self_hash(input_freeze, label="B4 input freeze")
    if input_freeze.get("artifact_version") != EXPECTED_B4_INPUT_FREEZE_VERSION:
        raise ReopenClaimReconciliationError("B4 input freeze version drift")
    if input_freeze.get("b3_reconciliation_artifact_hash") != reconciliation_hash:
        raise ReopenClaimReconciliationError("B4 input freeze/B3 reconciliation binding drift")
    if input_freeze.get("candidate_order") != list(EXPECTED_CANDIDATES):
        raise ReopenClaimReconciliationError("B4 input freeze candidate order drift")

    initial = _load_json(initial_freeze_path, label="Initial Council freeze")
    initial_hash = _validate_self_hash(initial, label="Initial Council freeze")
    if initial_hash != expected_initial_freeze_hash:
        raise ReopenClaimReconciliationError("Initial Council freeze event anchor drift")

    rebuttal = _load_json(rebuttal_freeze_path, label="Rebuttal Council freeze")
    rebuttal_hash = _validate_self_hash(rebuttal, label="Rebuttal Council freeze")
    if rebuttal_hash != expected_rebuttal_freeze_hash:
        raise ReopenClaimReconciliationError("Rebuttal Council freeze event anchor drift")

    judge = _load_json(judge_result_path, label="production Judge result")
    judge_hash = _validate_self_hash(judge, label="production Judge result")
    if judge_hash != expected_judge_result_hash:
        raise ReopenClaimReconciliationError("production Judge result event anchor drift")
    if judge.get("status") != EXPECTED_JUDGE_STATUS:
        raise ReopenClaimReconciliationError("production Judge did not complete with research reopen")
    reason_codes = _find_reopen_reason_codes(judge)

    candidate_rows, total_claim_count = _reconcile_candidates(reconciliation, input_freeze)

    legacy_sources = {
        "b3_selected_model_reconciliation": reconciliation,
        "initial_council_freeze": initial,
        "rebuttal_council_freeze": rebuttal,
        "production_judge_result": judge,
    }
    old_paths: dict[str, list[str]] = {}
    replacement_paths: dict[str, list[str]] = {}
    for label, payload in legacy_sources.items():
        old_paths[label] = _scalar_paths(payload, SUPERSEDED_GAP_REF)
        replacement_paths[label] = _scalar_paths(payload, CLOSURE_EVIDENCE_REF)
    for label in ("b3_selected_model_reconciliation", "initial_council_freeze", "rebuttal_council_freeze", "production_judge_result"):
        if not old_paths[label]:
            raise ReopenClaimReconciliationError(f"historical unknown-ref lineage missing from {label}")
        if replacement_paths[label]:
            raise ReopenClaimReconciliationError(f"closure evidence ref unexpectedly present in immutable legacy artifact: {label}")

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_bounded_news_review_hash": bounded_hash,
        "source_b3_selected_model_reconciliation_hash": reconciliation_hash,
        "source_b4_input_freeze_hash": input_freeze_hash,
        "source_initial_council_freeze_hash": initial_hash,
        "source_rebuttal_council_freeze_hash": rebuttal_hash,
        "source_production_judge_result_hash": judge_hash,
        "superseded_source_gap_ref": SUPERSEDED_GAP_REF,
        "closure_evidence_ref": CLOSURE_EVIDENCE_REF,
        "candidate_reconciliations": candidate_rows,
        "legacy_unknown_ref_occurrence_counts": {
            label: len(paths) for label, paths in old_paths.items()
        },
        "legacy_unknown_ref_paths": old_paths,
        "legacy_frozen_artifacts_mutated": False,
        "claim_reconciliation": {
            "material_claim_count": total_claim_count,
            "material_claim_rewrite_required": False,
            "material_claim_payloads_mutated": False,
            "material_claim_ids_unchanged": True,
            "reconciliation_mode": "SOURCE_GAP_OVERLAY_ONLY",
        },
        "judge_reopen_reason_codes": list(reason_codes),
        "closed_reopen_reason_codes": [SUPERSEDED_GAP_REF],
        "remaining_reopen_reason_codes": [VALUATION_GAP, PORTFOLIO_GAP],
        "news_gap_closed": True,
        "overall_research_reopen_complete": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "new_provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
