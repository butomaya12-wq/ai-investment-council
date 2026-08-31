from __future__ import annotations

import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_s00_scope_v01 as v01
from aic.research import reopen_judge_s00_scope_v02 as v02


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_JUDGE_S00_SCOPE_v0_3"
PASS_STATUS = "B3_RESEARCH_REOPEN_S00_SCOPE_V03_ZERO_CALL_PASS"
REASON_DERIVATION = "REBUTTAL_V03_PROCESSED_RECORDS_RESEARCH_REOPEN_REASON_CODES"
CLAIM_DERIVATION = "RECOVERED_INITIAL_V02_MSFT_RED_TEAM_MATERIAL_CLAIMS"
EXPECTED_INITIAL_FREEZE_HASH = "b98a3fbb2ce43cd9cab0d97b28ec62c1819ea5c777d8ff0a0dc36eb7628e8440"
SUPERSEDES_FAILED_V02_CODE_SHA = "f6478b746d94d9a01b92384a18c7eab5506565ff"
NEXT_GATE = v01.NEXT_GATE


class JudgeReopenS00ScopeV03Error(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise JudgeReopenS00ScopeV03Error(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def _initial_record_map(payload: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    rows = payload.get("processed_records")
    _need(isinstance(rows, list), "Recovered Initial processed records missing")
    _need(len(rows) == 9, "Recovered Initial processed record count drift")
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        _need(isinstance(row, Mapping), "Recovered Initial processed record malformed")
        candidate = row.get("candidate_id")
        lane = row.get("lane")
        _need(isinstance(candidate, str) and isinstance(lane, str), "Recovered Initial candidate/lane missing")
        key = (candidate, lane)
        _need(key not in result, "duplicate Recovered Initial candidate/lane")
        result[key] = row
    expected = (
        ("NVDA", "BULL"), ("NVDA", "BEAR"), ("NVDA", "RED_TEAM"),
        ("MSFT", "BULL"), ("MSFT", "BEAR"), ("MSFT", "RED_TEAM"),
        ("META", "BULL"), ("META", "BEAR"), ("META", "RED_TEAM"),
    )
    _need(tuple(result) == expected, "Recovered Initial candidate/lane order drift")
    return result


def _claim_map(record: Mapping[str, Any], *, label: str) -> dict[str, Mapping[str, Any]]:
    claims = record.get("material_claims")
    _need(isinstance(claims, list), f"{label} material claims missing")
    result: dict[str, Mapping[str, Any]] = {}
    for claim in claims:
        _need(isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str), f"{label} claim malformed")
        claim_id = str(claim["claim_id"])
        _need(claim_id not in result, f"duplicate {label} claim")
        result[claim_id] = claim
    return result


def _metadata_map(record: Mapping[str, Any], *, label: str) -> dict[str, Mapping[str, Any]]:
    rows = record.get("claim_metadata")
    _need(isinstance(rows, list), f"{label} claim metadata missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        _need(isinstance(row, Mapping) and isinstance(row.get("material_claim_id"), str), f"{label} metadata malformed")
        claim_id = str(row["material_claim_id"])
        _need(claim_id not in result, f"duplicate {label} metadata")
        result[claim_id] = row
    return result


def verify_recovered_initial_freeze(
    payload: Mapping[str, Any],
    *,
    expected_hash: str = EXPECTED_INITIAL_FREEZE_HASH,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    observed = _self_hash(payload)
    _need(observed == expected_hash, "Recovered Initial freeze hash drift")
    _need(payload.get("artifact_version") == "B4_REOPEN_INITIAL_COUNCIL_FREEZE_RECOVERED_v0_2", "Recovered Initial version drift")
    _need(payload.get("status") == "B4_REOPEN_INITIAL_COUNCIL_FROZEN_AFTER_UNKNOWN_DISPATCH_RECOVERY", "Recovered Initial status drift")
    _need(payload.get("candidate_order") == ["NVDA", "MSFT", "META"], "Recovered Initial candidate order drift")
    _need(payload.get("initial_opinion_count") == 9, "Recovered Initial opinion count drift")

    records = _initial_record_map(payload)
    msft_red = records[("MSFT", "RED_TEAM")]
    _need(msft_red.get("stage") == "RED_TEAM_INITIAL", "Recovered Initial MSFT Red-Team stage drift")
    _need(msft_red.get("candidate_id") == "MSFT" and msft_red.get("lane") == "RED_TEAM", "Recovered Initial MSFT Red-Team identity drift")
    claims = _claim_map(msft_red, label="Recovered Initial MSFT Red-Team")
    metadata = _metadata_map(msft_red, label="Recovered Initial MSFT Red-Team")

    valuation_id = v01.EXPECTED_REOPEN_REASONS[1]
    durability_id = v01.EXPECTED_REOPEN_REASONS[2]
    valuation = claims.get(valuation_id)
    durability = claims.get(durability_id)
    _need(isinstance(valuation, Mapping), "Recovered Initial MSFT valuation reopen claim missing")
    _need(isinstance(durability, Mapping), "Recovered Initial MSFT durability reopen claim missing")

    _need(valuation.get("candidate_id") == "MSFT" and valuation.get("category") == "INTEGRITY_FINDING", "Recovered Initial MSFT valuation claim shape drift")
    _need(valuation.get("support_status") == "SUPPORTED" and valuation.get("materiality") == "MATERIAL", "Recovered Initial MSFT valuation claim status drift")
    _need(valuation.get("evidence_ids") == ["B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z"], "Recovered Initial MSFT valuation evidence drift")
    _need(valuation.get("computed_value_ids") == [], "Recovered Initial MSFT valuation computed-value drift")

    _need(durability.get("candidate_id") == "MSFT" and durability.get("category") == "ASSUMPTION", "Recovered Initial MSFT durability claim shape drift")
    _need(durability.get("support_status") == "SUPPORTED" and durability.get("materiality") == "MATERIAL", "Recovered Initial MSFT durability claim status drift")
    _need(durability.get("computed_value_ids") == ["B2_MSFT_ANNUAL_REVENUE_GROWTH_20260827", "B2_MSFT_ANNUAL_OPERATING_MARGIN_20260827"], "Recovered Initial MSFT durability computed-value drift")
    _need(durability.get("evidence_ids") == ["B3_SEC_MSFT_N3_SEC_MDA_1", "B3_SEC_MSFT_N2_SEC_RISK_FACTORS_1", "B3_SEC_MSFT_N1_SEC_BUSINESS_1"], "Recovered Initial MSFT durability evidence drift")

    _need(metadata.get(valuation_id, {}).get("council_claim_type") == "INTEGRITY_FINDING", "Recovered Initial MSFT valuation metadata drift")
    _need(metadata.get(valuation_id, {}).get("lane") == "RED_TEAM", "Recovered Initial MSFT valuation metadata lane drift")
    _need(metadata.get(durability_id, {}).get("council_claim_type") == "ASSUMPTION", "Recovered Initial MSFT durability metadata drift")
    _need(metadata.get(durability_id, {}).get("lane") == "RED_TEAM", "Recovered Initial MSFT durability metadata lane drift")
    return observed, valuation, durability


def verify_rebuttal_reason_lineage(
    payload: Mapping[str, Any],
    *,
    expected_hash: str = v01.EXPECTED_REBUTTAL_FREEZE_HASH,
) -> str:
    observed = _self_hash(payload)
    _need(observed == expected_hash, "Rebuttal freeze hash drift")
    _need(payload.get("artifact_version") == "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3", "Rebuttal freeze version drift")
    _need(payload.get("status") == "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN", "Rebuttal not frozen")
    _need(payload.get("candidate_order") == ["NVDA", "MSFT", "META"], "Rebuttal candidate order drift")

    records = v02._processed_record_map(payload)
    expected_reason_map = {
        "NVDA": [v01.EXPECTED_REOPEN_REASONS[0]],
        "MSFT": [v01.EXPECTED_REOPEN_REASONS[1], v01.EXPECTED_REOPEN_REASONS[2]],
        "META": [],
    }
    expected_flags = {"NVDA": True, "MSFT": True, "META": False}
    derived: list[str] = []
    for candidate in ("NVDA", "MSFT", "META"):
        record = records[candidate]
        _need(record.get("research_reopen_required") is expected_flags[candidate], f"{candidate} Rebuttal reopen flag drift")
        _need(v02._reason_codes(record, candidate=candidate) == expected_reason_map[candidate], f"{candidate} Rebuttal reopen reason drift")
        if expected_flags[candidate]:
            derived.append(candidate)
    _need(payload.get("research_reopen_required_candidates") == derived, "Rebuttal top-level reopen candidates do not match processed records")
    _need(payload.get("rebuttal_rerun_authorized") is False, "Rebuttal rerun must remain forbidden")
    _need(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "Rebuttal broker/order drift")
    _need(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return observed


def _as_v01_semantic_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    view = dict(payload)
    for field in (
        "artifact_hash",
        "rebuttal_reason_derivation",
        "initial_claim_derivation",
        "source_recovered_initial_freeze_artifact_hash",
        "supersedes_failed_v02_code_commit_sha",
        "v02_runtime_failure_class",
    ):
        view.pop(field, None)
    view["artifact_version"] = v01.ARTIFACT_VERSION
    view["status"] = v01.PASS_STATUS
    view.pop("supersedes_failed_v01_code_commit_sha", None)
    view.pop("v01_runtime_failure_class", None)
    view["artifact_hash"] = canonical_sha256(view)
    return view


def build_scope_artifact(
    *,
    reopen_request: Mapping[str, Any],
    postprocess: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    rebuttal_freeze: Mapping[str, Any],
    recovered_initial_freeze: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    initial_hash, valuation, durability = verify_recovered_initial_freeze(recovered_initial_freeze)
    rebuttal_hash = verify_rebuttal_reason_lineage(rebuttal_freeze)

    original = v01.verify_rebuttal_freeze
    try:
        v01.verify_rebuttal_freeze = lambda payload: (rebuttal_hash, valuation, durability)
        base_artifact = v01.build_scope_artifact(
            reopen_request=reopen_request,
            postprocess=postprocess,
            judge_result=judge_result,
            rebuttal_freeze=rebuttal_freeze,
            code_commit_sha=code_commit_sha,
        )
    finally:
        v01.verify_rebuttal_freeze = original

    v01.verify_scope_artifact(base_artifact, expected_code_commit_sha=code_commit_sha)
    artifact = dict(base_artifact)
    artifact.pop("artifact_hash", None)
    artifact["artifact_version"] = ARTIFACT_VERSION
    artifact["status"] = PASS_STATUS
    artifact["rebuttal_reason_derivation"] = REASON_DERIVATION
    artifact["initial_claim_derivation"] = CLAIM_DERIVATION
    artifact["source_recovered_initial_freeze_artifact_hash"] = initial_hash
    artifact["supersedes_failed_v02_code_commit_sha"] = SUPERSEDES_FAILED_V02_CODE_SHA
    artifact["v02_runtime_failure_class"] = "FALSE_ASSUMPTION_REOPEN_REASON_IDS_ARE_REBUTTAL_PROMOTED_CLAIMS"
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_scope_artifact(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "V03 scope version drift")
    _need(payload.get("status") == PASS_STATUS, "V03 scope status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "V03 scope code SHA drift")
    _need(payload.get("rebuttal_reason_derivation") == REASON_DERIVATION, "V03 Rebuttal reason derivation drift")
    _need(payload.get("initial_claim_derivation") == CLAIM_DERIVATION, "V03 Initial claim derivation drift")
    _need(payload.get("source_recovered_initial_freeze_artifact_hash") == EXPECTED_INITIAL_FREEZE_HASH, "V03 Initial freeze lineage drift")
    _need(payload.get("supersedes_failed_v02_code_commit_sha") == SUPERSEDES_FAILED_V02_CODE_SHA, "V03 failed-V02 lineage drift")
    _need(payload.get("v02_runtime_failure_class") == "FALSE_ASSUMPTION_REOPEN_REASON_IDS_ARE_REBUTTAL_PROMOTED_CLAIMS", "V03 failure classification drift")
    v01.verify_scope_artifact(_as_v01_semantic_view(payload), expected_code_commit_sha=expected_code_commit_sha)
    return observed
