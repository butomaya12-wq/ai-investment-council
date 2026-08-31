from __future__ import annotations

import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_s00_scope_v01 as v01


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_JUDGE_S00_SCOPE_v0_2"
PASS_STATUS = "B3_RESEARCH_REOPEN_S00_SCOPE_V02_ZERO_CALL_PASS"
REASON_DERIVATION = "REBUTTAL_V03_PROCESSED_RECORDS_RESEARCH_REOPEN_REASON_CODES"
FAILED_V01_CODE_SHA = "05603cca0daa3133f22adfa76401d6d98e72f680"
NEXT_GATE = v01.NEXT_GATE


class JudgeReopenS00ScopeV02Error(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise JudgeReopenS00ScopeV02Error(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(
        isinstance(observed, str)
        and re.fullmatch(r"[0-9a-f]{64}", observed) is not None,
        f"{field} missing",
    )
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _need(observed == expected, f"{field} self-hash mismatch")
    return observed


def _processed_record_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("processed_records")
    _need(isinstance(rows, list), "Rebuttal V03 processed records missing")
    _need(len(rows) == 3, "Rebuttal V03 processed record count drift")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        _need(
            isinstance(row, Mapping) and isinstance(row.get("candidate_id"), str),
            "Rebuttal V03 processed record malformed",
        )
        candidate = str(row["candidate_id"])
        _need(candidate not in result, "duplicate Rebuttal V03 processed candidate")
        result[candidate] = row
    _need(
        tuple(result) == ("NVDA", "MSFT", "META"),
        "Rebuttal V03 processed candidate order drift",
    )
    return result


def _reason_codes(record: Mapping[str, Any], *, candidate: str) -> list[str]:
    raw = record.get("research_reopen_reason_codes")
    _need(isinstance(raw, list), f"{candidate} Rebuttal reopen reasons missing")
    _need(
        all(isinstance(value, str) and value for value in raw),
        f"{candidate} Rebuttal reopen reasons malformed",
    )
    return list(raw)


def verify_rebuttal_freeze(
    payload: Mapping[str, Any],
    *,
    expected_hash: str = v01.EXPECTED_REBUTTAL_FREEZE_HASH,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    """Verify the real production V03 freeze shape.

    V03 stores per-candidate reopen flags and reason codes inside
    ``processed_records``. The later Judge-entry artifact derives a top-level
    ``research_reopen_reason_codes_by_candidate`` view, but that view is not a
    field of the Rebuttal freeze itself.
    """

    observed = _self_hash(payload)
    _need(observed == expected_hash, "Rebuttal freeze hash drift")
    _need(
        payload.get("artifact_version") == "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3",
        "Rebuttal freeze version drift",
    )
    _need(
        payload.get("status") == "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN",
        "Rebuttal not frozen",
    )
    _need(
        payload.get("candidate_order") == ["NVDA", "MSFT", "META"],
        "Rebuttal candidate order drift",
    )

    records = _processed_record_map(payload)
    expected_reason_map = {
        "NVDA": [v01.EXPECTED_REOPEN_REASONS[0]],
        "MSFT": [v01.EXPECTED_REOPEN_REASONS[1], v01.EXPECTED_REOPEN_REASONS[2]],
        "META": [],
    }
    expected_reopen_flags = {"NVDA": True, "MSFT": True, "META": False}

    derived_reopen_candidates: list[str] = []
    for candidate in ("NVDA", "MSFT", "META"):
        record = records[candidate]
        expected_flag = expected_reopen_flags[candidate]
        _need(
            record.get("research_reopen_required") is expected_flag,
            f"{candidate} Rebuttal reopen flag drift",
        )
        _need(
            _reason_codes(record, candidate=candidate) == expected_reason_map[candidate],
            f"{candidate} Rebuttal reopen reason drift",
        )
        if expected_flag:
            derived_reopen_candidates.append(candidate)

    _need(
        payload.get("research_reopen_required_candidates") == derived_reopen_candidates,
        "Rebuttal top-level reopen candidates do not match processed records",
    )

    claims = v01._claim_map(payload, "MSFT")
    valuation = claims.get(v01.EXPECTED_REOPEN_REASONS[1])
    durability = claims.get(v01.EXPECTED_REOPEN_REASONS[2])
    _need(isinstance(valuation, Mapping), "MSFT valuation reopen claim missing")
    _need(isinstance(durability, Mapping), "MSFT durability reopen claim missing")
    _need(
        valuation.get("candidate_id") == "MSFT"
        and valuation.get("category") == "INTEGRITY_FINDING",
        "MSFT valuation claim shape drift",
    )
    _need(
        valuation.get("support_status") == "SUPPORTED"
        and valuation.get("materiality") == "MATERIAL",
        "MSFT valuation claim status drift",
    )
    _need(
        durability.get("candidate_id") == "MSFT"
        and durability.get("category") == "ASSUMPTION",
        "MSFT durability claim shape drift",
    )
    _need(
        durability.get("support_status") == "SUPPORTED"
        and durability.get("materiality") == "MATERIAL",
        "MSFT durability claim status drift",
    )
    _need(
        payload.get("rebuttal_rerun_authorized") is False,
        "Rebuttal rerun must remain forbidden",
    )
    _need(
        payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0,
        "Rebuttal broker/order drift",
    )
    _need(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return observed, valuation, durability


def _as_v01_semantic_view(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct the already-tested V01 semantic surface for verification."""

    view = dict(payload)
    view.pop("artifact_hash", None)
    view.pop("rebuttal_reason_derivation", None)
    view.pop("supersedes_failed_v01_code_commit_sha", None)
    view.pop("v01_runtime_failure_class", None)
    view["artifact_version"] = v01.ARTIFACT_VERSION
    view["status"] = v01.PASS_STATUS
    view["artifact_hash"] = canonical_sha256(view)
    return view


def build_scope_artifact(
    *,
    reopen_request: Mapping[str, Any],
    postprocess: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    rebuttal_freeze: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    # Reuse the already-tested V01 scope semantics while replacing only the
    # incorrect freeze-shape verifier. This is a single-process zero-call CLI
    # path; restoration in finally prevents state leakage.
    original = v01.verify_rebuttal_freeze
    try:
        v01.verify_rebuttal_freeze = verify_rebuttal_freeze
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
    artifact["supersedes_failed_v01_code_commit_sha"] = FAILED_V01_CODE_SHA
    artifact["v01_runtime_failure_class"] = "FALSE_ASSUMPTION_TOP_LEVEL_REBUTTAL_REASON_MAP"
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_scope_artifact(
    payload: Mapping[str, Any],
    *,
    expected_code_commit_sha: str,
) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == ARTIFACT_VERSION, "V02 scope version drift")
    _need(payload.get("status") == PASS_STATUS, "V02 scope status drift")
    _need(payload.get("code_commit_sha") == expected_code_commit_sha, "V02 scope code SHA drift")
    _need(
        payload.get("rebuttal_reason_derivation") == REASON_DERIVATION,
        "V02 Rebuttal reason derivation drift",
    )
    _need(
        payload.get("supersedes_failed_v01_code_commit_sha") == FAILED_V01_CODE_SHA,
        "V02 failed-V01 lineage drift",
    )
    _need(
        payload.get("v01_runtime_failure_class")
        == "FALSE_ASSUMPTION_TOP_LEVEL_REBUTTAL_REASON_MAP",
        "V02 failure classification drift",
    )
    v01.verify_scope_artifact(
        _as_v01_semantic_view(payload),
        expected_code_commit_sha=expected_code_commit_sha,
    )
    return observed
