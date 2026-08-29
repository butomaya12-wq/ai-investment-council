from __future__ import annotations

from aic.council.initial_eval_runtime import (
    EXPECTED_INITIAL_CASE_IDS,
    build_initial_eval_cases,
    dry_run_manifest,
)
from aic.council.model_policy import INITIAL_MODEL_LADDER


def test_initial_eval_cases_match_frozen_plan_and_cover_roles() -> None:
    cases = build_initial_eval_cases()
    assert tuple(case.case_id for case in cases) == EXPECTED_INITIAL_CASE_IDS
    assert len(cases) == 9
    lanes = {case.lane.value for case in cases}
    assert lanes == {"BULL", "BEAR", "RED_TEAM"}
    assert sum(1 for case in cases if case.critical_safety) >= 5


def test_initial_eval_dry_run_builds_exact_36_bounded_requests() -> None:
    manifest = dry_run_manifest()
    assert manifest["case_ids"] == list(EXPECTED_INITIAL_CASE_IDS)
    assert manifest["candidate_keys"] == [item.candidate_key for item in INITIAL_MODEL_LADDER]
    assert manifest["request_count"] == 36
    assert len(manifest["requests"]) == 36
    assert all(item["max_output_tokens"] == 4096 for item in manifest["requests"])
    assert max(item["request_body_utf8_bytes"] for item in manifest["requests"]) <= 40118
    pairs = {(item["candidate_key"], item["case_id"]) for item in manifest["requests"]}
    assert len(pairs) == 36
