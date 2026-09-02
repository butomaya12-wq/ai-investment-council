#!/usr/bin/env python3
"""Fail-closed zero-call production gate for TTL fresh-Judge reevaluation."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from typing import Any, Mapping, Sequence, TextIO

from aic.domain.canonical import canonical_sha256

ARTIFACT_VERSION = "B4_TTL_JUDGE_PRODUCTION_GATE_ZERO_CALL_v0_1"
STATUS = "PASS_ZERO_CALL_TTL_JUDGE_PRODUCTION_GATE"

FEATURE_BRANCH = "hackathon/b4-ttl-judge-production-gate-v1"
REQUIRED_EXECUTION_BRANCH = "hackathon/alpaca-2026"

READINESS_MERGED_BASE_HEAD = "e47944d37141d431b475ed5b363b683510902334"
FROZEN_JUDGE_SOURCE_COMMIT_SHA = "814895777015cfbf47a1be03c028b65030cab2df"

PROPOSAL_POLICY_HASH = (
    "0b9128d8b19505daa19ef556b50ae7c1435ad02d04db34cff7730e8235eb3c7a"
)

PROSPECTIVE_MODEL_RUN_REF = "B4_TTL_REEVALUATION_JUDGE_J1_V01"

PROSPECTIVE_JUDGE_INPUT_HASH = (
    "777c996cb92301fb1fd64a6e89eada81e56404e5b434e1bfe7b4808799b9d2f4"
)

PROSPECTIVE_REQUEST_HASH = (
    "1850c20fcf2173381b60d5a16589dcddc9400cb85de03bb74cfd3899ffe1cacd"
)

SUPERSEDED_PROSPECTIVE_REQUEST_HASH = (
    "77d61ad15f9217e25df95eaf1c359519be6139149197847d5112b9ff681b73fa"
)

HISTORICAL_JUDGE_REQUEST_HASH = (
    "2312558ae6e3979d6f8816b6b1c64309750e4e420890c4f6447f755ce4423c53"
)

REQUEST_BODY_UTF8_BYTES = 155454
INPUT_TOKENS_UPPER_BOUND = 155454
MAX_OUTPUT_TOKENS = 8192
MAX_CALL_COUNT = 1
AUTOMATIC_RETRIES = 0
JUDGE_MAX_COST_USD = "0.486939"

EXPECTED_PRICING_HASH = (
    "13b67bf92f56b2962694f463850e0a0e289fc08f0c4a3d3cafe8eb928d0ee336"
)

EXPECTED_PRICING_VERSION = "OPENAI_TEXT_PRICING_2026_08_30_CACHE_WRITE_AWARE"

DEFAULT_EVALUATION_TIME_UTC = "2026-09-01T19:45:35Z"

READINESS_SCRIPT = "b4_ttl_judge_activation_readiness_zero_call_v01.py"

SELF_RELATIVE_PATH = (
    "scripts/b4_ttl_judge_production_gate_zero_call_v01.py"
)

ACTIVATION_APPROVAL_VERSION = "B4_TTL_JUDGE_ACTIVATION_APPROVAL_v0_1"
PAID_APPROVAL_VERSION = "B4_TTL_JUDGE_PAID_CALL_APPROVAL_v0_1"

ACTIVATION_SCOPE = "TTL_FRESH_JUDGE_ACTIVATION_ONLY"
PAID_SCOPE = "ONE_TTL_FRESH_JUDGE_CALL_ONLY"

ACTIVATION_KEYS = {
    "artifact_version",
    "owner_activation_granted",
    "activation_scope",
    "gate_hash",
    "request_hash",
    "approved_executor_code_commit_sha",
    "activation_approval_id",
    "activation_approval_at_utc",
    "standalone_model_call_authority",
    "paid_call_authority",
    "provider_read_authority",
    "broker_write_authority",
    "live_money",
    "artifact_hash",
}

PAID_KEYS = {
    "artifact_version",
    "owner_paid_approval_granted",
    "paid_scope",
    "gate_hash",
    "activation_approval_hash",
    "request_hash",
    "approved_executor_code_commit_sha",
    "paid_approval_id",
    "paid_approval_at_utc",
    "approved_judge_max_cost_usd",
    "new_paid_call_count",
    "new_paid_call_count_ceiling",
    "max_output_tokens",
    "automatic_retries",
    "requires_activation_approval",
    "standalone_model_call_authority",
    "provider_read_authority",
    "broker_write_authority",
    "live_money",
    "artifact_hash",
}


class ProductionGateBlocked(ValueError):
    pass


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise ProductionGateBlocked(reason)


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionGateBlocked("BLOCK_TIMESTAMP") from exc

    _need(
        parsed.tzinfo is not None and parsed.utcoffset() is not None,
        "BLOCK_TIMESTAMP",
    )
    return parsed.astimezone(UTC)


def _hash(
    payload: Mapping[str, Any],
    field: str = "artifact_hash",
) -> str:
    value = payload.get(field)

    _need(
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"BLOCK_{field.upper()}",
    )

    _need(
        value
        == canonical_sha256(
            payload,
            exclude_fields=(field,),
        ),
        f"BLOCK_{field.upper()}_MISMATCH",
    )

    return value


def _load_script(
    repository: Path,
    name: str,
    filename: str,
) -> ModuleType:
    path = repository / "scripts" / filename

    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    _need(
        spec is not None and spec.loader is not None,
        f"BLOCK_{name.upper()}",
    )

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


def _git(
    repository: Path,
    *args: str,
) -> str:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    _need(
        completed.returncode == 0,
        "BLOCK_GIT_STATE",
    )

    return completed.stdout.strip()


def current_head(repository: Path) -> str:
    head = _git(
        repository,
        "rev-parse",
        "HEAD",
    )

    _need(
        re.fullmatch(r"[0-9a-f]{40}", head) is not None,
        "BLOCK_GATE_REPOSITORY_HEAD",
    )

    return head


def current_branch(repository: Path) -> str:
    branch = _git(
        repository,
        "branch",
        "--show-current",
    )

    _need(
        bool(branch),
        "BLOCK_GATE_BRANCH",
    )

    return branch


def tracked_worktree_clean(repository: Path) -> bool:
    return not _git(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )


def source_matches_committed_head(
    repository: Path,
    head: str,
) -> bool:
    local = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "hash-object",
            SELF_RELATIVE_PATH,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    committed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "rev-parse",
            f"{head}:{SELF_RELATIVE_PATH}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    local_hash = local.stdout.strip()
    committed_hash = committed.stdout.strip()

    return (
        local.returncode == 0
        and committed.returncode == 0
        and bool(local_hash)
        and local_hash == committed_hash
    )


def build_source_readiness(
    *,
    repository: Path,
    evaluation_time_utc: datetime,
    readiness_repository_head: str,
) -> dict[str, Any]:
    module = _load_script(
        repository,
        "b4_ttl_judge_activation_readiness_for_production_gate",
        READINESS_SCRIPT,
    )

    try:
        value = module.build_readiness(
            repository=repository,
            evaluation_time_utc=evaluation_time_utc,
            readiness_repository_head=readiness_repository_head,
        )
    except Exception as exc:
        raise ProductionGateBlocked(
            "BLOCK_SOURCE_READINESS"
        ) from exc

    _need(
        isinstance(value, dict),
        "BLOCK_SOURCE_READINESS",
    )

    return value


def verify_source_readiness(
    payload: Mapping[str, Any],
    *,
    expected_repository_head: str,
) -> str:
    observed = _hash(payload)

    exact = {
        "status":
            "PASS_ZERO_CALL_TTL_FRESH_JUDGE_ACTIVATION_READINESS",
        "readiness_repository_head":
            expected_repository_head,
        "source_judge_code_commit_sha":
            FROZEN_JUDGE_SOURCE_COMMIT_SHA,
        "ttl_status":
            "TTL_EXPIRED",
        "trigger":
            "TTL_EXPIRY",
        "proposal_policy_hash":
            PROPOSAL_POLICY_HASH,
        "proposal_status":
            "DRAFT_NOT_AUTHORITY",
        "prospective_model_run_ref":
            PROSPECTIVE_MODEL_RUN_REF,
        "prospective_judge_input_hash":
            PROSPECTIVE_JUDGE_INPUT_HASH,
        "prospective_request_hash":
            PROSPECTIVE_REQUEST_HASH,
        "request_body_utf8_bytes":
            REQUEST_BODY_UTF8_BYTES,
        "input_tokens_upper_bound":
            INPUT_TOKENS_UPPER_BOUND,
        "max_output_tokens":
            MAX_OUTPUT_TOKENS,
        "max_call_count":
            MAX_CALL_COUNT,
        "automatic_retries":
            AUTOMATIC_RETRIES,
        "judge_max_cost_usd":
            JUDGE_MAX_COST_USD,
        "pricing_hash":
            EXPECTED_PRICING_HASH,
        "pricing_version":
            EXPECTED_PRICING_VERSION,
        "activation_status":
            "NOT_GRANTED",
        "cost_approval_status":
            "NOT_GRANTED",
        "live_money":
            "PROHIBITED",
    }

    for field, expected in exact.items():
        _need(
            payload.get(field) == expected,
            f"BLOCK_READINESS_{field.upper()}",
        )

    required_true = (
        "request_identity_independent_of_readiness_repository_head",
        "owner_activation_required",
        "owner_paid_approval_required",
        "fresh_invest_requires_fresh_b5",
        "historical_b5_selection_is_lineage_only",
    )

    required_false = (
        "proposal_active",
        "historical_request_hash_reused",
        "historical_judge_response_in_model_input",
        "historical_judge_raw_hash_in_model_input",
        "provider_refresh_required_before_model",
        "provider_reads_authorized",
        "model_calls_authorized",
        "broker_write_authority",
        "live_execution",
        "watch_b5_started",
        "abstain_b5_started",
        "b6_started",
        "paper_order_sent",
    )

    for field in required_true:
        _need(
            payload.get(field) is True,
            f"BLOCK_READINESS_{field.upper()}",
        )

    for field in required_false:
        _need(
            payload.get(field) is False,
            f"BLOCK_READINESS_{field.upper()}",
        )

    for field in (
        "model_calls",
        "openai_calls",
        "provider_reads",
        "alpaca_reads",
        "network_calls",
        "broker_writes",
        "alpaca_orders",
    ):
        _need(
            payload.get(field) == 0,
            f"BLOCK_NONZERO_{field.upper()}",
        )

    _need(
        str(payload.get("paid_llm_cost_usd")) == "0",
        "BLOCK_NONZERO_PAID_COST",
    )

    return observed


def build_gate(
    *,
    repository: Path,
    evaluation_time_utc: datetime,
    gate_repository_head: str,
    gate_branch: str,
) -> dict[str, Any]:
    _need(
        re.fullmatch(
            r"[0-9a-f]{40}",
            gate_repository_head,
        ) is not None,
        "BLOCK_GATE_REPOSITORY_HEAD",
    )

    _need(
        bool(gate_branch),
        "BLOCK_GATE_BRANCH",
    )

    readiness = build_source_readiness(
        repository=repository,
        evaluation_time_utc=evaluation_time_utc,
        readiness_repository_head=gate_repository_head,
    )

    readiness_hash = verify_source_readiness(
        readiness,
        expected_repository_head=gate_repository_head,
    )

    clean = tracked_worktree_clean(repository)

    source_matches = source_matches_committed_head(
        repository,
        gate_repository_head,
    )

    blocked = [
        SUPERSEDED_PROSPECTIVE_REQUEST_HASH,
        HISTORICAL_JUDGE_REQUEST_HASH,
    ]

    _need(
        PROSPECTIVE_REQUEST_HASH not in blocked,
        "BLOCK_ACTIVE_REQUEST_SUPERSEDED",
    )

    value: dict[str, Any] = {
        "artifact_version":
            ARTIFACT_VERSION,
        "status":
            STATUS,

        "gate_repository_head":
            gate_repository_head,
        "gate_branch":
            gate_branch,
        "tracked_worktree_clean":
            clean,

        "gate_source_matches_committed_head":
            source_matches,

        "readiness_merged_base_head":
            READINESS_MERGED_BASE_HEAD,
        "source_readiness_hash":
            readiness_hash,
        "source_readiness_repository_head":
            readiness["readiness_repository_head"],
        "source_judge_code_commit_sha":
            FROZEN_JUDGE_SOURCE_COMMIT_SHA,

        "ttl_status":
            "TTL_EXPIRED",
        "trigger":
            "TTL_EXPIRY",

        "proposal_policy_hash":
            PROPOSAL_POLICY_HASH,
        "proposal_active":
            False,

        "prospective_model_run_ref":
            PROSPECTIVE_MODEL_RUN_REF,
        "prospective_judge_input_hash":
            PROSPECTIVE_JUDGE_INPUT_HASH,
        "prospective_request_hash":
            PROSPECTIVE_REQUEST_HASH,

        "blocked_request_hashes":
            blocked,
        "blocked_requests_approvable":
            False,

        "request_body_utf8_bytes":
            REQUEST_BODY_UTF8_BYTES,
        "input_tokens_upper_bound":
            INPUT_TOKENS_UPPER_BOUND,
        "max_output_tokens":
            MAX_OUTPUT_TOKENS,
        "max_call_count":
            MAX_CALL_COUNT,
        "automatic_retries":
            AUTOMATIC_RETRIES,
        "judge_max_cost_usd":
            JUDGE_MAX_COST_USD,

        "pricing_hash":
            EXPECTED_PRICING_HASH,
        "pricing_version":
            EXPECTED_PRICING_VERSION,

        "provider_refresh_required_before_model":
            False,

        "required_execution_branch":
            REQUIRED_EXECUTION_BRANCH,

        "approval_eligible":
            gate_branch == REQUIRED_EXECUTION_BRANCH
            and clean
            and source_matches,

        "approval_eligible_only_on_required_execution_branch":
            True,

        "canonical_head_binding_required_at_execution":
            True,

        "activation_approval_version":
            ACTIVATION_APPROVAL_VERSION,
        "activation_scope":
            ACTIVATION_SCOPE,
        "activation_approval_required":
            True,

        "paid_approval_version":
            PAID_APPROVAL_VERSION,
        "paid_scope":
            PAID_SCOPE,
        "paid_approval_required":
            True,

        "activation_and_paid_approval_must_be_distinct":
            True,

        "activation_approval_must_bind_gate_hash":
            True,
        "activation_approval_must_bind_request_hash":
            True,

        "paid_approval_must_bind_gate_hash":
            True,
        "paid_approval_must_bind_activation_approval_hash":
            True,
        "paid_approval_must_bind_request_hash":
            True,
        "paid_approval_must_bind_cost_ceiling":
            True,

        "activation_status":
            "NOT_GRANTED",
        "paid_approval_status":
            "NOT_GRANTED",

        "standalone_gate_model_call_authority":
            False,
        "model_calls_authorized":
            False,
        "provider_reads_authorized":
            False,
        "broker_write_authority":
            False,

        "fresh_judge_allowed_outcomes":
            [
                "INVEST",
                "WATCH",
                "ABSTAIN",
            ],

        "watch_is_terminal_without_b5":
            True,
        "abstain_is_terminal_without_b5":
            True,

        "invest_requires_fresh_b5":
            True,
        "historical_b5_selection_reuse_allowed":
            False,

        "b6_auto_start_allowed":
            False,

        "model_calls":
            0,
        "provider_reads":
            0,
        "alpaca_reads":
            0,
        "network_calls":
            0,
        "broker_writes":
            0,
        "alpaca_orders":
            0,

        "paid_llm_cost_usd":
            "0",

        "b6_started":
            False,
        "paper_order_sent":
            False,

        "live_money":
            "PROHIBITED",
    }

    value["artifact_hash"] = canonical_sha256(
        value,
        exclude_fields=("artifact_hash",),
    )

    return value


def verify_gate(
    payload: Mapping[str, Any],
    **inputs: Any,
) -> str:
    observed = _hash(payload)

    _need(
        dict(payload) == build_gate(**inputs),
        "BLOCK_GATE_DRIFT",
    )

    return observed


def verify_activation_approval(
    payload: Mapping[str, Any],
    *,
    gate: Mapping[str, Any],
    execution_head: str,
) -> str:
    _need(
        set(payload) == ACTIVATION_KEYS,
        "BLOCK_ACTIVATION_APPROVAL_SHAPE",
    )

    observed = _hash(payload)

    _need(
        gate.get("approval_eligible") is True,
        "BLOCK_GATE_NOT_APPROVAL_ELIGIBLE",
    )

    _need(
        gate.get("gate_branch")
        == REQUIRED_EXECUTION_BRANCH,
        "BLOCK_GATE_NOT_CANONICAL",
    )

    _need(
        execution_head
        == gate.get("gate_repository_head"),
        "BLOCK_EXECUTION_HEAD",
    )

    exact = {
        "artifact_version":
            ACTIVATION_APPROVAL_VERSION,
        "owner_activation_granted":
            True,
        "activation_scope":
            ACTIVATION_SCOPE,
        "gate_hash":
            gate.get("artifact_hash"),
        "request_hash":
            PROSPECTIVE_REQUEST_HASH,
        "approved_executor_code_commit_sha":
            execution_head,
        "standalone_model_call_authority":
            False,
        "paid_call_authority":
            False,
        "provider_read_authority":
            False,
        "broker_write_authority":
            False,
        "live_money":
            "PROHIBITED",
    }

    for field, expected in exact.items():
        _need(
            payload.get(field) == expected,
            f"BLOCK_ACTIVATION_{field.upper()}",
        )

    _need(
        isinstance(
            payload.get("activation_approval_id"),
            str,
        )
        and bool(payload["activation_approval_id"]),
        "BLOCK_ACTIVATION_APPROVAL_ID",
    )

    _utc(
        str(
            payload.get(
                "activation_approval_at_utc",
                "",
            )
        )
    )

    return observed


def verify_paid_approval(
    payload: Mapping[str, Any],
    *,
    gate: Mapping[str, Any],
    activation_approval_hash: str,
    execution_head: str,
) -> str:
    _need(
        set(payload) == PAID_KEYS,
        "BLOCK_PAID_APPROVAL_SHAPE",
    )

    observed = _hash(payload)

    _need(
        gate.get("approval_eligible") is True,
        "BLOCK_GATE_NOT_APPROVAL_ELIGIBLE",
    )

    _need(
        gate.get("gate_branch")
        == REQUIRED_EXECUTION_BRANCH,
        "BLOCK_GATE_NOT_CANONICAL",
    )

    _need(
        execution_head
        == gate.get("gate_repository_head"),
        "BLOCK_EXECUTION_HEAD",
    )

    exact = {
        "artifact_version":
            PAID_APPROVAL_VERSION,
        "owner_paid_approval_granted":
            True,
        "paid_scope":
            PAID_SCOPE,
        "gate_hash":
            gate.get("artifact_hash"),
        "activation_approval_hash":
            activation_approval_hash,
        "request_hash":
            PROSPECTIVE_REQUEST_HASH,
        "approved_executor_code_commit_sha":
            execution_head,
        "approved_judge_max_cost_usd":
            JUDGE_MAX_COST_USD,
        "new_paid_call_count":
            1,
        "new_paid_call_count_ceiling":
            1,
        "max_output_tokens":
            MAX_OUTPUT_TOKENS,
        "automatic_retries":
            AUTOMATIC_RETRIES,
        "requires_activation_approval":
            True,
        "standalone_model_call_authority":
            False,
        "provider_read_authority":
            False,
        "broker_write_authority":
            False,
        "live_money":
            "PROHIBITED",
    }

    for field, expected in exact.items():
        _need(
            payload.get(field) == expected,
            f"BLOCK_PAID_{field.upper()}",
        )

    _need(
        isinstance(
            payload.get("paid_approval_id"),
            str,
        )
        and bool(payload["paid_approval_id"]),
        "BLOCK_PAID_APPROVAL_ID",
    )

    _utc(
        str(
            payload.get(
                "paid_approval_at_utc",
                "",
            )
        )
    )

    return observed


def verify_authority_pair(
    *,
    repository: Path,
    evaluation_time_utc: datetime,
    gate: Mapping[str, Any],
    activation_approval: Mapping[str, Any],
    paid_approval: Mapping[str, Any],
    execution_branch: str,
    execution_head: str,
) -> dict[str, Any]:
    actual_branch = current_branch(repository)
    actual_head = current_head(repository)

    _need(
        actual_branch == execution_branch,
        "BLOCK_ACTUAL_EXECUTION_BRANCH",
    )

    _need(
        actual_branch == REQUIRED_EXECUTION_BRANCH,
        "BLOCK_EXECUTION_BRANCH",
    )

    _need(
        actual_head == execution_head,
        "BLOCK_ACTUAL_EXECUTION_HEAD",
    )

    _need(
        tracked_worktree_clean(repository),
        "BLOCK_EXECUTION_WORKTREE_DIRTY",
    )

    _need(
        source_matches_committed_head(
            repository,
            actual_head,
        ),
        "BLOCK_GATE_SOURCE_NOT_COMMITTED",
    )

    verify_gate(
        gate,
        repository=repository,
        evaluation_time_utc=evaluation_time_utc,
        gate_repository_head=execution_head,
        gate_branch=execution_branch,
    )

    _need(
        execution_branch
        == REQUIRED_EXECUTION_BRANCH,
        "BLOCK_EXECUTION_BRANCH",
    )

    activation_hash = verify_activation_approval(
        activation_approval,
        gate=gate,
        execution_head=execution_head,
    )

    paid_hash = verify_paid_approval(
        paid_approval,
        gate=gate,
        activation_approval_hash=activation_hash,
        execution_head=execution_head,
    )

    _need(
        activation_hash != paid_hash,
        "BLOCK_APPROVALS_NOT_DISTINCT",
    )

    _need(
        activation_approval.get(
            "activation_approval_id"
        )
        != paid_approval.get(
            "paid_approval_id"
        ),
        "BLOCK_APPROVAL_IDS_NOT_DISTINCT",
    )

    _need(
        _utc(
            str(
                paid_approval.get(
                    "paid_approval_at_utc"
                )
            )
        )
        >= _utc(
            str(
                activation_approval.get(
                    "activation_approval_at_utc"
                )
            )
        ),
        "BLOCK_PAID_APPROVAL_PRECEDES_ACTIVATION",
    )

    _need(
        PROSPECTIVE_REQUEST_HASH
        not in gate.get(
            "blocked_request_hashes",
            [],
        ),
        "BLOCK_ACTIVE_REQUEST_BLOCKED",
    )

    return {
        "gate_hash":
            gate["artifact_hash"],
        "activation_approval_hash":
            activation_hash,
        "paid_approval_hash":
            paid_hash,
        "request_hash":
            PROSPECTIVE_REQUEST_HASH,
        "judge_max_cost_usd":
            JUDGE_MAX_COST_USD,
        "max_call_count":
            1,
        "automatic_retries":
            0,
        "model_call_authorized":
            True,
        "provider_reads_authorized":
            False,
        "broker_write_authority":
            False,
        "live_money":
            "PROHIBITED",
    }


def write_artifact_exclusive(
    path: Path,
    artifact: Mapping[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise ProductionGateBlocked(
            "BLOCK_ARTIFACT_EXISTS"
        ) from exc

    with os.fdopen(
        descriptor,
        "w",
        encoding="utf-8",
    ) as stream:
        json.dump(
            artifact,
            stream,
            sort_keys=True,
            separators=(",", ":"),
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def default_artifact_path(
    repository: Path,
    gate_repository_head: str,
) -> Path:
    return (
        repository
        / ".aic-runtime"
        / (
            "b4_ttl_judge_production_gate_"
            "zero_call_v0_1__"
            f"{gate_repository_head[:7]}.json"
        )
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    output: TextIO | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )

    parser.add_argument(
        "--evaluation-time-utc",
        default=DEFAULT_EVALUATION_TIME_UTC,
    )

    parser.add_argument(
        "--artifact-path",
        type=Path,
    )

    args = parser.parse_args(argv)

    destination = (
        output
        if output is not None
        else sys.stdout
    )

    try:
        repository = args.repository.resolve()

        head = current_head(repository)
        branch = current_branch(repository)

        artifact = build_gate(
            repository=repository,
            evaluation_time_utc=_utc(
                args.evaluation_time_utc
            ),
            gate_repository_head=head,
            gate_branch=branch,
        )

        path = (
            args.artifact_path
            or default_artifact_path(
                repository,
                head,
            )
        )

        write_artifact_exclusive(
            path,
            artifact,
        )

    except ProductionGateBlocked as exc:
        print(
            f"TTL_JUDGE_PRODUCTION_GATE_STATUS={exc}",
            file=destination,
        )
        return 1

    for key in (
        "gate_repository_head",
        "gate_branch",
        "tracked_worktree_clean",
        "gate_source_matches_committed_head",
        "approval_eligible",
        "prospective_request_hash",
        "prospective_judge_input_hash",
        "judge_max_cost_usd",
        "max_call_count",
        "automatic_retries",
        "activation_status",
        "paid_approval_status",
        "model_calls_authorized",
        "provider_reads_authorized",
        "broker_write_authority",
    ):
        print(
            f"{key.upper()}={artifact[key]}",
            file=destination,
        )

    print(
        f"GATE_ARTIFACT_PATH={path}",
        file=destination,
    )

    print(
        f"GATE_ARTIFACT_HASH="
        f"{artifact['artifact_hash']}",
        file=destination,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
