#!/usr/bin/env python3
"""Determine post-TTL review scope from frozen local authority without calls."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any, Mapping, Sequence, TextIO

from aic.domain.canonical import canonical_sha256


CANONICAL_HEAD = "7d8e0d4dae4c4118895f6371fdcc53cae70bc835"
LIFECYCLE_POLICY_PATH = Path("config/event/decision_lifecycle_policy_competition_v1.json")
OPTIONS_POLICY_PATH = Path("config/event/competition_v1_options_policy.json")
TTL_LINEAGE_RECEIPT_PATH = Path(
    ".aic-runtime/b4_recovered_decision_ttl_lineage_v0_1__fc4d73a__5500332.json"
)
DEFAULT_ARTIFACT_PATH = Path(
    ".aic-runtime/b4_ttl_expiry_review_preflight_zero_call_v0_1__fc4d73a__7d8e0d4.json"
)
DEFAULT_EVALUATION_TIME_UTC = "2026-09-01T19:45:35Z"
ARTIFACT_VERSION = "B4_TTL_EXPIRY_REVIEW_PREFLIGHT_ZERO_CALL_v0_1"
LIFECYCLE_POLICY_HASH = "cceb58997b139b59039313d892ff330915ff49f2a5a236bcdb57141501bd98ce"
OPTIONS_POLICY_HASH = "a4e5f95746cf1e928069454e23bd0bf76e92afe38208c4d8cc0c9cb7a16f00a6"


class PreflightBlocked(ValueError):
    """Fail-closed zero-call preflight rejection."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise PreflightBlocked(reason)


def _mapping(value: object, reason: str) -> Mapping[str, Any]:
    _need(isinstance(value, Mapping), reason)
    return value


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreflightBlocked("BLOCK_EVALUATION_TIME_UTC") from exc
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None, "BLOCK_EVALUATION_TIME_UTC")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path, reason: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), reason)
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightBlocked(reason) from exc


def load_ttl_lineage_module(repository: Path) -> ModuleType:
    path = repository / "scripts/b4_recovered_decision_ttl_lineage_zero_call_v01.py"
    spec = importlib.util.spec_from_file_location("b4_ttl_lineage_for_preflight", path)
    _need(spec is not None and spec.loader is not None, "BLOCK_TTL_LINEAGE_MODULE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def current_head(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    _need(completed.returncode == 0, "BLOCK_CANONICAL_HEAD")
    return completed.stdout.strip()


def verify_frozen_authorities(repository: Path) -> dict[str, str]:
    lifecycle = load_json(repository / LIFECYCLE_POLICY_PATH, "BLOCK_LIFECYCLE_POLICY")
    options = load_json(repository / OPTIONS_POLICY_PATH, "BLOCK_OPTIONS_POLICY")
    _need(lifecycle.get("policy_hash") == LIFECYCLE_POLICY_HASH, "BLOCK_LIFECYCLE_POLICY_HASH")
    _need(
        canonical_sha256(lifecycle, exclude_fields=("policy_hash",)) == LIFECYCLE_POLICY_HASH,
        "BLOCK_LIFECYCLE_POLICY_SELF_HASH",
    )
    _need(lifecycle.get("active") is True, "BLOCK_LIFECYCLE_POLICY_INACTIVE")
    _need(lifecycle.get("decision_ttl_seconds") == 7200, "BLOCK_LIFECYCLE_POLICY_TTL")
    _need(lifecycle.get("ttl_anchor") == "APPLICATION_ASSIGNED_CANONICAL_DECISION_CREATED_AT", "BLOCK_TTL_ANCHOR")
    _need(lifecycle.get("next_review_trigger_mode") == "TTL_EXPIRY", "BLOCK_TTL_TRIGGER_MODE")
    _need(options.get("policy_hash") == OPTIONS_POLICY_HASH, "BLOCK_OPTIONS_POLICY_HASH")
    _need(
        canonical_sha256(options, exclude_fields=("policy_hash",)) == OPTIONS_POLICY_HASH,
        "BLOCK_OPTIONS_POLICY_SELF_HASH",
    )
    commit = _mapping(options.get("commit_revalidation"), "BLOCK_OPTIONS_COMMIT_REVALIDATION")
    _need(commit.get("decision_ttl_valid_required") is True, "BLOCK_DECISION_TTL_REVALIDATION")
    _need(options.get("broker_write_authority") is False, "BLOCK_OPTIONS_BROKER_AUTHORITY")
    _need(options.get("live_execution") is False, "BLOCK_OPTIONS_LIVE_EXECUTION")
    return {
        "lifecycle_policy": "config/event/decision_lifecycle_policy_competition_v1.json: active=true, ttl=7200, anchor=APPLICATION_ASSIGNED_CANONICAL_DECISION_CREATED_AT, trigger=TTL_EXPIRY",
        "options_policy": "config/event/competition_v1_options_policy.json: commit_revalidation.decision_ttl_valid_required=true; broker_write_authority=false; live_execution=false",
    }


def verify_canonical_review_contracts(repository: Path) -> dict[str, str]:
    next_trigger = load_json(repository / "schemas/json/canonical/next_review_trigger_v1.json", "BLOCK_NEXT_REVIEW_SCHEMA")
    work_item = load_json(repository / "schemas/json/canonical/review_work_item_v1.json", "BLOCK_REVIEW_WORK_ITEM_SCHEMA")
    decision_ttl = load_json(repository / "schemas/json/canonical/decision_ttl_v1.json", "BLOCK_DECISION_TTL_SCHEMA")
    final_decision = load_json(repository / "schemas/json/canonical/final_decision_v1.json", "BLOCK_FINAL_DECISION_SCHEMA")
    next_rule = next_trigger["x-aic-semantic-validators"][0]["rule"]
    _need(next_trigger["properties"]["trigger_type"].get("const") == "TTL_EXPIRY", "BLOCK_TTL_TRIGGER_SCHEMA")
    _need(
        next_rule == "trigger_at_utc == lifecycle_anchor_at + bound DECISION_TTL_V1.duration_seconds; lifecycle_anchor_at equals draft/final created_at; no promotion recomputation",
        "BLOCK_NEXT_REVIEW_TIMESTAMP_SEMANTICS",
    )
    _need("REEVALUATION_STARTED" in work_item["properties"]["status"]["enum"], "BLOCK_REEVALUATION_WORKFLOW")
    _need("resolved_by_decision_id" in work_item["properties"], "BLOCK_REVIEW_RESOLUTION_BINDING")
    _need(decision_ttl["properties"]["unit"].get("const") == "SECONDS", "BLOCK_DECISION_TTL_UNIT")
    final_rules = {item["id"]: item["rule"] for item in final_decision["x-aic-semantic-validators"]}
    _need(
        final_rules.get("FD-V01") == "created_at equals source DECISION_DRAFT_B4_v0_4.created_at canonical-identically",
        "BLOCK_FINAL_DECISION_TIMESTAMP_SEMANTICS",
    )
    _need("model_run_ref" in final_decision["required"], "BLOCK_FINAL_DECISION_MODEL_LINEAGE")
    return {
        "ttl_expiry_review_trigger": "schemas/json/canonical/next_review_trigger_v1.json:NRT-V01",
        "no_old_timestamp_promotion_refresh": "schemas/json/canonical/next_review_trigger_v1.json:NRT-V01; schemas/json/canonical/final_decision_v1.json:FD-V01",
        "review_workflow": "schemas/json/canonical/review_work_item_v1.json:status includes REEVALUATION_STARTED; resolved_by_decision_id",
        "final_decision_lineage": "schemas/json/canonical/final_decision_v1.json:required model_run_ref and FD-V01",
    }


def verify_production_authority_inventory(repository: Path) -> dict[str, str]:
    judge = (repository / "src/aic/council/post_research_reopen_judge_current_v04.py").read_text(encoding="utf-8")
    b5 = (repository / "src/aic/b5/production_readonly_v1.py").read_text(encoding="utf-8")
    b6 = (repository / "src/aic/b6/competition_v1.py").read_text(encoding="utf-8")
    _need("judge_retains_terminal_outcome_authority" in judge, "BLOCK_JUDGE_TERMINAL_AUTHORITY")
    _need("historical Judge request reuse" in judge, "BLOCK_JUDGE_REQUEST_REUSE_GUARD")
    _need("current closed-B3 lifecycle forbids embedded research reopen" in judge, "BLOCK_B3_CLOSED_CONTEXT")
    _need("snapshot timestamp and as_of_date disagree" in b5, "BLOCK_B5_SNAPSHOT_AUTHORITY")
    _need("selection quote is stale" in b5, "BLOCK_B5_QUOTE_AUTHORITY")
    _need("fresh risk does not support approved quantity" in b6, "BLOCK_B6_FRESH_RISK_AUTHORITY")
    return {
        "b3_closed_context": "src/aic/council/post_research_reopen_judge_current_v04.py:validate_proposal closed-B3/no embedded reopen invariant",
        "judge_terminal_authority": "src/aic/council/post_research_reopen_judge_current_v04.py:judge_retains_terminal_outcome_authority; historical Judge request reuse guard",
        "b5_market_freshness": "src/aic/b5/production_readonly_v1.py:normalized snapshot and selection quote freshness checks",
        "b6_commit_freshness": "src/aic/b6/competition_v1.py:commit quote checks and fresh risk support",
    }


def reuse_matrix() -> dict[str, dict[str, str]]:
    return {
        "b3_evidence_research_closure": {
            "classification": "REUSABLE_IF_REVALIDATED_ZERO_CALL",
            "authority_invariant": "post_research_reopen_judge_current_v04.py: closed-B3 context only; embedded research reopen forbidden",
        },
        "b4_initial_opinions": {
            "classification": "REUSABLE_IF_REVALIDATED_ZERO_CALL",
            "authority_invariant": "post_research_reopen_judge_current_v04.py: builds and verifies current Judge context from frozen source context",
        },
        "b4_rebuttal_opinions": {
            "classification": "REUSABLE_IF_REVALIDATED_ZERO_CALL",
            "authority_invariant": "post_research_reopen_judge_current_v04.py: builds and verifies current Judge context from frozen source context",
        },
        "b4_judge_output": {
            "classification": "FRESH_MODEL_DECISION_REQUIRED",
            "authority_invariant": "post_research_reopen_judge_current_v04.py: Judge retains terminal outcome authority; historical Judge request reuse rejected",
        },
        "b4_invest_eligibility_gate": {
            "classification": "REUSABLE_IF_REVALIDATED_ZERO_CALL",
            "authority_invariant": "post_research_reopen_judge_current_v04.py: build_gate/verify_gate are deterministic and model_calls=0",
        },
        "b4_recovered_decision_artifact": {
            "classification": "REUSABLE_AS_IMMUTABLE_LINEAGE",
            "authority_invariant": "scripts/b4_recovered_decision_ttl_lineage_zero_call_v01.py: recovered artifact hash and raw binding are exact",
        },
        "b5_account_snapshot": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b5/production_readonly_v1.py: normalizes one complete account/market snapshot",
        },
        "b5_positions_snapshot": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b5/production_readonly_v1.py: account risk inputs bind the normalized snapshot",
        },
        "b5_option_contracts": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b5/production_readonly_v1.py: option contracts must be active, tradable, and current",
        },
        "b5_option_quote_snapshots": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b5/production_readonly_v1.py: selection quote age must not exceed 60 seconds",
        },
        "b5_selected_option": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b5/production_readonly_v1.py: selection is derived from normalized current contracts and quotes",
        },
        "b5_risk_result": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b5/production_readonly_v1.py: risk inputs are derived from normalized account and quote snapshot",
        },
        "human_approval": {
            "classification": "NOT_APPLICABLE",
            "authority_invariant": "src/aic/b6/competition_v1.py: approval binds the exact option-intent payload and economics hash",
        },
        "b6_commit_time_quote": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b6/competition_v1.py: commit quote freshness, spread, active, and tradable checks",
        },
        "b6_commit_time_account_risk": {
            "classification": "FRESH_PROVIDER_READ_REQUIRED",
            "authority_invariant": "src/aic/b6/competition_v1.py: fresh risk must support approved quantity",
        },
    }


def build_preflight(
    *,
    repository: Path,
    evaluation_time_utc: datetime,
    canonical_head: str,
    ttl_module: ModuleType | None = None,
    lineage_receipt_path: Path | None = None,
) -> dict[str, object]:
    _need(canonical_head == CANONICAL_HEAD, "BLOCK_CANONICAL_HEAD")
    frozen = verify_frozen_authorities(repository)
    contracts = verify_canonical_review_contracts(repository)
    inventory = verify_production_authority_inventory(repository)
    lineage_module = ttl_module or load_ttl_lineage_module(repository)
    lineage = lineage_module.recover_lineage(
        raw=lineage_module.load_json(repository / lineage_module.RAW_CAPTURE_RELATIVE_PATH, "RAW_CAPTURE"),
        recovered=lineage_module.load_json(repository / lineage_module.RECOVERED_RELATIVE_PATH, "RECOVERED_B4"),
        policy=lineage_module.load_json(repository / lineage_module.POLICY_RELATIVE_PATH, "LIFECYCLE_POLICY"),
    )
    _, ttl_status, expires = lineage_module.evaluate_ttl(lineage, evaluation_time_utc)
    _need(ttl_status == "TTL_EXPIRED", "BLOCK_TTL_NOT_EXPIRED")
    receipt = load_json(lineage_receipt_path or repository / TTL_LINEAGE_RECEIPT_PATH, "BLOCK_TTL_LINEAGE_RECEIPT")
    _need(
        receipt.get("artifact_hash") == canonical_sha256(receipt, exclude_fields=("artifact_hash",)),
        "BLOCK_TTL_LINEAGE_RECEIPT_SELF_HASH",
    )
    _need(receipt.get("source_raw_response_sha256") == lineage.raw_response_hash, "BLOCK_TTL_LINEAGE_RAW_BINDING")
    _need(receipt.get("recovered_b4_artifact_hash") == lineage.recovered_artifact_hash, "BLOCK_TTL_LINEAGE_RECOVERED_BINDING")
    _need(receipt.get("decision_expires_at_utc") == _utc_text(expires), "BLOCK_TTL_LINEAGE_EXPIRY_BINDING")

    artifact: dict[str, object] = {
        "artifact_version": ARTIFACT_VERSION,
        "canonical_head": canonical_head,
        "source_expired_b4_artifact_hash": lineage.recovered_artifact_hash,
        "source_ttl_lineage_receipt_hash": receipt["artifact_hash"],
        "decision_created_at_utc": _utc_text(lineage.decision_created_at_utc),
        "decision_expires_at_utc": _utc_text(expires),
        "evaluation_time_utc": _utc_text(evaluation_time_utc),
        "ttl_status": ttl_status,
        "lifecycle_policy_hash": LIFECYCLE_POLICY_HASH,
        "options_policy_hash": OPTIONS_POLICY_HASH,
        "review_trigger_required": True,
        "new_decision_required": True,
        "old_decision_can_be_made_valid_zero_call": False,
        "new_final_decision_by_timestamp_refresh_allowed": False,
        "b6_ready_for_paper_send": False,
        "schema_semantics": contracts,
        "frozen_authorities": frozen,
        "production_authority_inventory": inventory,
        "reuse_matrix": reuse_matrix(),
        "provider_refresh_required_before_model": "UNDERSPECIFIED",
        "model_stage_scope_required": "UNDERSPECIFIED",
        "preflight_outcome": "TTL_REVIEW_SCOPE_UNDERSPECIFIED",
        "blocking_reason_codes": [
            "TTL_EXPIRED",
            "NO_FROZEN_TTL_REEVALUATION_STAGE_SCOPE_AUTHORITY",
            "NO_FROZEN_TTL_PROVIDER_REFRESH_REQUIREMENT_AUTHORITY",
        ],
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
        "broker_write_authority": False,
        "live_execution": False,
        "model_calls": 0,
        "openai_calls": 0,
        "alpaca_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "network_calls": 0,
        "paid_llm_cost_usd": "0",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def write_artifact_exclusive(path: Path, artifact: Mapping[str, object]) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PreflightBlocked("BLOCK_ARTIFACT_EXISTS") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(artifact, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: Sequence[str] | None = None, *, output: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--evaluation-time-utc", default=DEFAULT_EVALUATION_TIME_UTC)
    parser.add_argument("--artifact-path", type=Path)
    arguments = parser.parse_args(argv)
    destination = output if output is not None else sys.stdout
    try:
        repository = arguments.repository.resolve()
        artifact = build_preflight(
            repository=repository,
            evaluation_time_utc=_utc(arguments.evaluation_time_utc),
            canonical_head=current_head(repository),
        )
        path = arguments.artifact_path or repository / DEFAULT_ARTIFACT_PATH
        write_artifact_exclusive(path, artifact)
    except PreflightBlocked as exc:
        print(f"TTL_REVIEW_PREFLIGHT_STATUS={exc}", file=destination)
        return 1
    print(f"TTL_STATUS={artifact['ttl_status']}", file=destination)
    print(f"REVIEW_TRIGGER_REQUIRED={artifact['review_trigger_required']}", file=destination)
    print(f"NEW_DECISION_REQUIRED={artifact['new_decision_required']}", file=destination)
    print(f"PROVIDER_REFRESH_REQUIRED_BEFORE_MODEL={artifact['provider_refresh_required_before_model']}", file=destination)
    print(f"MODEL_STAGE_SCOPE_REQUIRED={artifact['model_stage_scope_required']}", file=destination)
    print(f"PREFLIGHT_OUTCOME={artifact['preflight_outcome']}", file=destination)
    print(f"PREFLIGHT_ARTIFACT_PATH={path}", file=destination)
    print(f"PREFLIGHT_ARTIFACT_HASH={artifact['artifact_hash']}", file=destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
