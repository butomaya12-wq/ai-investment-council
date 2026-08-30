from __future__ import annotations

from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .rebuttal_runtime_execution import (
    REBUTTAL_COUNCIL_FROZEN_STATUS,
    REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
    verify_rebuttal_council_freeze_artifact,
)


JUDGE_ENTRY_PREFLIGHT_VERSION = "B4_JUDGE_ENTRY_PREFLIGHT_v0_1"
JUDGE_ENTRY_PREFLIGHT_STATUS = "PASS_ZERO_CALL_JUDGE_ENTRY_RESEARCH_REOPEN_BOUND"
EXPECTED_REBUTTAL_FREEZE_HASH = (
    "dd83aa4f873c0d6ac6582cd6dc89c1612088239aa8e979d18f7f95c3386552a5"
)
EXPECTED_REBUTTAL_RUN_ID = (
    "AIC-B4-REBUTTAL-RUNTIME-20260830T122106121542Z-b5dba042bc75"
)
EXPECTED_PAID_AUTHORIZATION_HASH = (
    "1ddaa743678ebc3aae7c7050e84566f627f720f7ffa350d365e48f063b535443"
)
EXPECTED_RECEIPT_MANIFEST_HASH = (
    "c36cb817bf0e61020a0781cd7a6dc30c5432acaaa2184f93abb8e4f1565270d3"
)
EXPECTED_CANDIDATE_ORDER = ("NVDA", "MSFT", "META")
EXPECTED_REBUTTAL_BUNDLE_HASHES = (
    "8824f4eeb792407a427657b9116c70a5a2557fd0958241b26f26854bd0361763",
    "e9ff46cb1e38db6ed525d34677a8af20048206fb1cc8f1a652b08815908fffb8",
    "dd400c55953a4c494611e5ab5f27c28a71bef1ab10a0774901083cb9914282a8",
)
EXPECTED_RESEARCH_REOPEN_CANDIDATES = EXPECTED_CANDIDATE_ORDER


class JudgeEntryPreflightError(ValueError):
    pass


def _require_zero_safety_surface(payload: Mapping[str, Any]) -> None:
    if payload.get("broker_writes") != 0:
        raise JudgeEntryPreflightError("Rebuttal freeze broker write invariant drift")
    if payload.get("alpaca_orders") != 0:
        raise JudgeEntryPreflightError("Rebuttal freeze Alpaca order invariant drift")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeEntryPreflightError("Rebuttal freeze live-money invariant drift")
    if payload.get("judge_authorized") is not False:
        raise JudgeEntryPreflightError("Rebuttal freeze unexpectedly authorizes Judge")
    if payload.get("rerun_authorized") is not False:
        raise JudgeEntryPreflightError("Rebuttal freeze unexpectedly authorizes rerun")
    if payload.get("automatic_repair_calls") != 0:
        raise JudgeEntryPreflightError("Rebuttal freeze automatic repair drift")


def build_judge_entry_preflight(
    rebuttal_freeze: Mapping[str, Any],
    *,
    code_commit_sha: str,
) -> dict[str, Any]:
    if (
        not isinstance(code_commit_sha, str)
        or len(code_commit_sha) != 40
        or any(ch not in "0123456789abcdef" for ch in code_commit_sha)
    ):
        raise JudgeEntryPreflightError("Judge entry requires exact lowercase git SHA")

    observed_hash = verify_rebuttal_council_freeze_artifact(rebuttal_freeze)
    if observed_hash != EXPECTED_REBUTTAL_FREEZE_HASH:
        raise JudgeEntryPreflightError("production Rebuttal freeze hash drift")
    if rebuttal_freeze.get("artifact_version") != REBUTTAL_COUNCIL_FREEZE_ARTIFACT_VERSION:
        raise JudgeEntryPreflightError("production Rebuttal freeze version drift")
    if rebuttal_freeze.get("status") != REBUTTAL_COUNCIL_FROZEN_STATUS:
        raise JudgeEntryPreflightError("production Rebuttal is not frozen")
    if rebuttal_freeze.get("run_id") != EXPECTED_REBUTTAL_RUN_ID:
        raise JudgeEntryPreflightError("production Rebuttal run ID drift")
    if rebuttal_freeze.get("paid_authorization_artifact_hash") != EXPECTED_PAID_AUTHORIZATION_HASH:
        raise JudgeEntryPreflightError("production Rebuttal authorization hash drift")
    if rebuttal_freeze.get("receipt_manifest_hash") != EXPECTED_RECEIPT_MANIFEST_HASH:
        raise JudgeEntryPreflightError("production Rebuttal receipt manifest drift")
    if tuple(rebuttal_freeze.get("candidate_order", ())) != EXPECTED_CANDIDATE_ORDER:
        raise JudgeEntryPreflightError("production Rebuttal candidate order drift")
    if tuple(rebuttal_freeze.get("rebuttal_bundle_hashes", ())) != EXPECTED_REBUTTAL_BUNDLE_HASHES:
        raise JudgeEntryPreflightError("production Rebuttal bundle hashes drift")
    if rebuttal_freeze.get("rebuttal_bundle_count") != 3:
        raise JudgeEntryPreflightError("Judge requires exactly three frozen Rebuttal bundles")
    if rebuttal_freeze.get("dispatch_attempts") != 3 or rebuttal_freeze.get("model_calls") != 3:
        raise JudgeEntryPreflightError("production Rebuttal 3-call completion drift")
    if rebuttal_freeze.get("cost_receipt_status") != "COMPLETE":
        raise JudgeEntryPreflightError("production Rebuttal cost receipts are incomplete")
    if rebuttal_freeze.get("rebuttal_freeze_barrier") is not True:
        raise JudgeEntryPreflightError("production Rebuttal freeze barrier missing")
    if rebuttal_freeze.get("production_rebuttal_authorization_consumed") is not True:
        raise JudgeEntryPreflightError("production Rebuttal authorization is not consumed")
    _require_zero_safety_surface(rebuttal_freeze)

    research_reopen = tuple(rebuttal_freeze.get("research_reopen_required_candidates", ()))
    if research_reopen != EXPECTED_RESEARCH_REOPEN_CANDIDATES:
        raise JudgeEntryPreflightError(
            "event-specific research-reopen candidate set differs from frozen production evidence"
        )

    artifact: dict[str, Any] = {
        "artifact_version": JUDGE_ENTRY_PREFLIGHT_VERSION,
        "status": JUDGE_ENTRY_PREFLIGHT_STATUS,
        "code_commit_sha": code_commit_sha,
        "rebuttal_council_freeze_artifact_hash": observed_hash,
        "rebuttal_run_id": EXPECTED_REBUTTAL_RUN_ID,
        "paid_rebuttal_authorization_artifact_hash": EXPECTED_PAID_AUTHORIZATION_HASH,
        "rebuttal_receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "candidate_order": list(EXPECTED_CANDIDATE_ORDER),
        "rebuttal_bundle_hashes": list(EXPECTED_REBUTTAL_BUNDLE_HASHES),
        "rebuttal_bundle_count": 3,
        "research_reopen_required_candidates": list(research_reopen),
        "judge_entry_barrier_satisfied": True,
        "judge_model_selection_required": True,
        "judge_execution_authorized": False,
        "invest_eligible_candidates": [],
        "invest_persistence_allowed": False,
        "invest_block_reason": "RESEARCH_REOPEN_REQUIRED_FOR_ALL_THREE_FROZEN_CANDIDATES",
        "allowed_judge_outcomes_for_current_frozen_run": ["WATCH", "ABSTAIN"],
        "research_reopen_must_remain_visible_to_judge": True,
        "b3_reopen_is_separate_lifecycle": True,
        "new_research_inside_b4_allowed": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "judge_authorized": False,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_judge_entry_preflight(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or len(observed) != 64:
        raise JudgeEntryPreflightError("Judge entry artifact_hash missing")
    if observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeEntryPreflightError("Judge entry artifact_hash mismatch")
    if payload.get("artifact_version") != JUDGE_ENTRY_PREFLIGHT_VERSION:
        raise JudgeEntryPreflightError("Judge entry artifact version drift")
    if payload.get("status") != JUDGE_ENTRY_PREFLIGHT_STATUS:
        raise JudgeEntryPreflightError("Judge entry preflight is not PASS")
    if payload.get("rebuttal_council_freeze_artifact_hash") != EXPECTED_REBUTTAL_FREEZE_HASH:
        raise JudgeEntryPreflightError("Judge entry Rebuttal freeze binding drift")
    if payload.get("candidate_order") != list(EXPECTED_CANDIDATE_ORDER):
        raise JudgeEntryPreflightError("Judge entry candidate order drift")
    if payload.get("research_reopen_required_candidates") != list(EXPECTED_RESEARCH_REOPEN_CANDIDATES):
        raise JudgeEntryPreflightError("Judge entry research-reopen binding drift")
    if payload.get("judge_entry_barrier_satisfied") is not True:
        raise JudgeEntryPreflightError("Judge entry barrier is not satisfied")
    if payload.get("judge_model_selection_required") is not True:
        raise JudgeEntryPreflightError("Judge model selection requirement missing")
    if payload.get("judge_execution_authorized") is not False:
        raise JudgeEntryPreflightError("Judge entry unexpectedly authorizes execution")
    if payload.get("invest_eligible_candidates") != []:
        raise JudgeEntryPreflightError("Judge entry unexpectedly permits INVEST candidate")
    if payload.get("invest_persistence_allowed") is not False:
        raise JudgeEntryPreflightError("Judge entry unexpectedly permits INVEST persistence")
    if payload.get("allowed_judge_outcomes_for_current_frozen_run") != ["WATCH", "ABSTAIN"]:
        raise JudgeEntryPreflightError("Judge entry outcome surface drift")
    if payload.get("research_reopen_must_remain_visible_to_judge") is not True:
        raise JudgeEntryPreflightError("Judge entry may not suppress research reopen")
    if payload.get("b3_reopen_is_separate_lifecycle") is not True:
        raise JudgeEntryPreflightError("Judge entry B3 lifecycle boundary drift")
    if payload.get("new_research_inside_b4_allowed") is not False:
        raise JudgeEntryPreflightError("Judge entry unexpectedly permits research inside B4")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeEntryPreflightError(f"Judge entry {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeEntryPreflightError("Judge entry live-money invariant drift")
    if payload.get("judge_authorized") is not False or payload.get("rerun_authorized") is not False:
        raise JudgeEntryPreflightError("Judge entry unexpectedly authorizes Judge/rerun")
    return observed
