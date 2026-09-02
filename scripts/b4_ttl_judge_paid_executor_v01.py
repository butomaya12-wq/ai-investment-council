#!/usr/bin/env python3
"""TTL-aware one-shot paid executor for the fresh B4 Judge reevaluation.

The executor consumes only a valid production gate plus distinct activation and
paid approvals.  It reconstructs the immutable prospective request, reserves
that request durably before transport, performs at most one OpenAI Responses
call, never retries an ambiguous dispatch, and creates no B5/B6/broker action.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence

from aic.council import post_research_reopen_judge_current_v04 as v04
from aic.domain.canonical import canonical_sha256


CANONICAL_BRANCH = "hackathon/alpaca-2026"
SELF_RELATIVE_PATH = "scripts/b4_ttl_judge_paid_executor_v01.py"
GATE_SCRIPT = "b4_ttl_judge_production_gate_zero_call_v01.py"
READINESS_SCRIPT = "b4_ttl_judge_activation_readiness_zero_call_v01.py"

EXPECTED_REQUEST_HASH = (
    "1850c20fcf2173381b60d5a16589dcddc9400cb85de03bb74cfd3899ffe1cacd"
)
EXPECTED_JUDGE_INPUT_HASH = (
    "777c996cb92301fb1fd64a6e89eada81e56404e5b434e1bfe7b4808799b9d2f4"
)
EXPECTED_MAX_COST_USD = "0.486939"
EXPECTED_MAX_CALL_COUNT = 1
EXPECTED_AUTOMATIC_RETRIES = 0
EXPECTED_MAX_OUTPUT_TOKENS = 8192
EXPECTED_MODEL = "gpt-5.6-terra"
EXPECTED_REASONING = {"effort": "medium"}

CONSUMPTION_VERSION = "B4_TTL_JUDGE_PAID_CALL_CONSUMPTION_v0_1"
RAW_CAPTURE_VERSION = "B4_TTL_REEVALUATION_JUDGE_RAW_PROVIDER_RESPONSE_v0_1"
RESULT_VERSION = "B4_TTL_REEVALUATION_JUDGE_COUNCIL_FREEZE_v0_1"


class PaidJudgeExecutionBlocked(RuntimeError):
    """A fail-closed execution condition was not satisfied."""


def canonical_output_paths(
    repository: Path,
) -> dict[str, Path]:
    runtime = repository / ".aic-runtime"
    suffix = EXPECTED_REQUEST_HASH

    return {
        "consumption":
            runtime
            / (
                "b4_ttl_judge_paid_call_consumption_"
                f"v0_1__{suffix}.json"
            ),

        "raw":
            runtime
            / (
                "b4_ttl_judge_raw_provider_response_"
                f"v0_1__{suffix}.json"
            ),

        "result":
            runtime
            / (
                "b4_ttl_judge_result_"
                f"v0_1__{suffix}.json"
            ),
    }


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise PaidJudgeExecutionBlocked(reason)


def _read(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaidJudgeExecutionBlocked(reason) from exc
    _need(isinstance(value, dict), reason)
    return value


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    _need(completed.returncode == 0, "BLOCK_GIT_STATE")
    return completed.stdout.strip()


def _load_script(repository: Path, name: str, filename: str) -> ModuleType:
    path = repository / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    _need(
        spec is not None and spec.loader is not None,
        f"BLOCK_LOAD_{name.upper()}",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tracked_worktree_clean(repository: Path) -> bool:
    return not _git(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )


def _source_matches_committed_head(repository: Path, head: str) -> bool:
    local = subprocess.run(
        ["git", "-C", str(repository), "hash-object", SELF_RELATIVE_PATH],
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
    return (
        local.returncode == 0
        and committed.returncode == 0
        and bool(local.stdout.strip())
        and local.stdout.strip() == committed.stdout.strip()
    )


def remote_canonical_head(
    repository: Path,
) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "http.version=HTTP/1.1",
            "-C",
            str(repository),
            "ls-remote",
            "origin",
            f"refs/heads/{CANONICAL_BRANCH}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    _need(
        completed.returncode == 0,
        "BLOCK_REMOTE_CANONICAL_READ",
    )

    rows = [
        line.split()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]

    _need(
        len(rows) == 1
        and len(rows[0]) == 2,
        "BLOCK_REMOTE_CANONICAL_FORMAT",
    )

    observed, ref = rows[0]

    _need(
        ref
        == f"refs/heads/{CANONICAL_BRANCH}",
        "BLOCK_REMOTE_CANONICAL_REF",
    )

    _need(
        re.fullmatch(
            r"[0-9a-f]{40}",
            observed,
        )
        is not None,
        "BLOCK_REMOTE_CANONICAL_SHA",
    )

    return observed


def _utc_now() -> str:
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    value["artifact_hash"] = canonical_sha256(
        value,
        exclude_fields=("artifact_hash",),
    )
    return value


def _update_consumption(
    path: Path,
    consumption: dict[str, Any],
    **changes: Any,
) -> None:
    consumption.update(changes)
    _seal(consumption)
    v04._replace_durable(path, consumption)


def _reconstruct_request_bundle(
    repository: Path,
    readiness: ModuleType,
    evaluation_time_utc: datetime,
) -> dict[str, Any]:
    policy_hash = readiness.verify_inactive_proposal(repository)

    _, receipt, _ = readiness.verify_expired_ttl_lineage(
        repository,
        evaluation_time_utc=evaluation_time_utc,
    )

    readiness.verify_canonical_ttl_preflight(repository)
    readiness.verify_frozen_judge_source_files(repository)

    source_entry, source_context, entry, context, source_gate = (
        readiness._source_inputs(repository)
    )

    prospective_context = readiness.build_prospective_judge_context(
        source_entry=source_entry,
        source_context=source_context,
        entry=entry,
        context=context,
        policy_hash=policy_hash,
        ttl_receipt_hash=str(receipt["artifact_hash"]),
    )

    request = readiness.build_prospective_request(
        entry=entry,
        context=prospective_context,
    )

    pricing = readiness.load_initial_runtime_pricing(
        repository / readiness.PRICING_PATH
    )

    return {
        "request": request,
        "context": prospective_context,
        "source_gate": source_gate,
        "pricing": pricing,
        "ttl_model_run_ref": readiness.MODEL_RUN_REF,
    }


def _prepare(
    args: argparse.Namespace,
    *,
    repository: Path,
) -> dict[str, Any]:
    _need(
        args.execute_paid_judge is True,
        "BLOCK_EXPLICIT_PAID_FLAG_REQUIRED",
    )

    branch = _git(repository, "branch", "--show-current")
    head = _git(repository, "rev-parse", "HEAD")

    _need(
        branch == CANONICAL_BRANCH,
        "BLOCK_CANONICAL_BRANCH_REQUIRED",
    )

    _need(
        re.fullmatch(r"[0-9a-f]{40}", head) is not None,
        "BLOCK_EXECUTOR_HEAD",
    )

    _need(
        _tracked_worktree_clean(repository),
        "BLOCK_TRACKED_WORKTREE_DIRTY",
    )

    _need(
        _source_matches_committed_head(repository, head),
        "BLOCK_EXECUTOR_SOURCE_NOT_COMMITTED",
    )

    remote_head = remote_canonical_head(
        repository
    )

    _need(
        remote_head == head,
        "BLOCK_REMOTE_CANONICAL_HEAD",
    )

    expected_outputs = canonical_output_paths(
        repository
    )

    observed_outputs = {
        "consumption":
            args.consumption,

        "raw":
            args.raw,

        "result":
            args.result,
    }

    for label, observed in observed_outputs.items():
        expected = expected_outputs[label]

        _need(
            observed.resolve()
            == expected.resolve(),
            (
                "BLOCK_NONCANONICAL_OUTPUT_PATH:"
                f"{label}"
            ),
        )

        _need(
            not expected.exists(),
            (
                "BLOCK_EXCLUSIVE_OUTPUT_EXISTS:"
                f"{expected}"
            ),
        )

    gate_module = _load_script(
        repository,
        "b4_ttl_paid_executor_gate",
        GATE_SCRIPT,
    )

    readiness = _load_script(
        repository,
        "b4_ttl_paid_executor_readiness",
        READINESS_SCRIPT,
    )

    gate = _read(
        args.gate,
        "BLOCK_GATE_ARTIFACT",
    )

    activation = _read(
        args.activation_approval,
        "BLOCK_ACTIVATION_APPROVAL",
    )

    paid = _read(
        args.paid_approval,
        "BLOCK_PAID_APPROVAL",
    )

    evaluation_time = gate_module._utc(
        gate_module.DEFAULT_EVALUATION_TIME_UTC
    )

    authority = gate_module.verify_authority_pair(
        repository=repository,
        evaluation_time_utc=evaluation_time,
        gate=gate,
        activation_approval=activation,
        paid_approval=paid,
        execution_branch=branch,
        execution_head=head,
    )

    _need(
        authority.get("model_call_authorized") is True,
        "BLOCK_MODEL_AUTHORITY",
    )

    _need(
        authority.get("max_call_count")
        == EXPECTED_MAX_CALL_COUNT,
        "BLOCK_CALL_CEILING",
    )

    _need(
        authority.get("automatic_retries")
        == EXPECTED_AUTOMATIC_RETRIES,
        "BLOCK_RETRY_POLICY",
    )

    _need(
        authority.get("judge_max_cost_usd")
        == EXPECTED_MAX_COST_USD,
        "BLOCK_COST_CEILING",
    )

    _need(
        authority.get("provider_reads_authorized") is False,
        "BLOCK_PROVIDER_READ_AUTHORITY",
    )

    _need(
        authority.get("broker_write_authority") is False,
        "BLOCK_BROKER_AUTHORITY",
    )

    _need(
        authority.get("live_money") == "PROHIBITED",
        "BLOCK_LIVE_MONEY",
    )

    bundle = _reconstruct_request_bundle(
        repository,
        readiness,
        evaluation_time,
    )

    request = bundle["request"]

    request_bytes = len(
        json.dumps(
            request.request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    _need(
        request.request_hash == EXPECTED_REQUEST_HASH,
        "BLOCK_REQUEST_HASH",
    )

    _need(
        request.request_hash == authority.get("request_hash"),
        "BLOCK_AUTHORITY_REQUEST_BINDING",
    )

    _need(
        bundle["context"].judge_input_hash
        == EXPECTED_JUDGE_INPUT_HASH,
        "BLOCK_JUDGE_INPUT_HASH",
    )

    _need(
        request.request_payload.get("model")
        == EXPECTED_MODEL,
        "BLOCK_MODEL_POLICY",
    )

    _need(
        request.request_payload.get("reasoning")
        == EXPECTED_REASONING,
        "BLOCK_REASONING_POLICY",
    )

    _need(
        request.request_payload.get("max_output_tokens")
        == EXPECTED_MAX_OUTPUT_TOKENS,
        "BLOCK_OUTPUT_CAP",
    )

    _need(
        request_bytes
        == gate_module.REQUEST_BODY_UTF8_BYTES,
        "BLOCK_REQUEST_BODY_SIZE",
    )

    _need(
        bundle["pricing"].get("pricing_hash")
        == gate_module.EXPECTED_PRICING_HASH,
        "BLOCK_PRICING_HASH",
    )

    _need(
        bundle["pricing"].get("pricing_version")
        == gate_module.EXPECTED_PRICING_VERSION,
        "BLOCK_PRICING_VERSION",
    )

    return {
        **bundle,
        "authority": authority,
        "activation_approval": activation,
        "paid_approval": paid,
        "execution_branch": branch,
        "execution_head": head,
    }


def _real_transport_factory(
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    from aic.research.runtime import (
        StdlibResponsesTransport,
        load_openai_api_key,
    )

    api_key = load_openai_api_key()
    transport = StdlibResponsesTransport()

    return lambda payload: transport.post(
        payload=payload,
        api_key=api_key,
    )


def _build_consumption(
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    authority = prepared["authority"]
    paid = prepared["paid_approval"]

    return _seal(
        {
            "artifact_version":
                CONSUMPTION_VERSION,

            "status":
                "RESERVED_NOT_DISPATCHED",

            "request_hash":
                authority["request_hash"],

            "gate_hash":
                authority["gate_hash"],

            "activation_approval_hash":
                authority["activation_approval_hash"],

            "paid_approval_hash":
                authority["paid_approval_hash"],

            "paid_approval_id":
                paid["paid_approval_id"],

            "approved_executor_code_commit_sha":
                prepared["execution_head"],

            "reserved_at_utc":
                _utc_now(),

            "dispatch_started_at_utc":
                None,

            "raw_response_hash":
                None,

            "processed_result_hash":
                None,

            "actual_cost_usd":
                None,

            "model_calls_attempted":
                0,

            "max_call_count":
                1,

            "automatic_retries":
                0,

            "automatic_retry_permitted":
                False,

            "provider_reads":
                0,

            "broker_writes":
                0,

            "alpaca_orders":
                0,

            "b6_started":
                False,

            "live_money":
                "PROHIBITED",
        }
    )


def _build_raw_capture(
    *,
    request_hash: str,
    raw: Mapping[str, Any],
    started_at: str,
) -> dict[str, Any]:
    external = v04._external_json_value(raw)

    _need(
        isinstance(external, Mapping),
        "BLOCK_PROVIDER_RESPONSE_SHAPE",
    )

    capture: dict[str, Any] = {
        "capture_version":
            RAW_CAPTURE_VERSION,

        "request_hash":
            request_hash,

        "provider_response_id":
            external.get("id"),

        "dispatch_started_at_utc":
            started_at,

        "captured_at_utc":
            _utc_now(),

        "raw_response":
            dict(external),
    }

    capture["raw_response_hash"] = (
        v04.external_provider_json_sha256(
            capture
        )
    )

    return capture


def _verify_raw_capture(
    capture: Mapping[str, Any],
    *,
    request_hash: str,
) -> str:
    observed = capture.get("raw_response_hash")

    _need(
        isinstance(observed, str)
        and re.fullmatch(
            r"[0-9a-f]{64}",
            observed,
        ) is not None,
        "BLOCK_RAW_RESPONSE_HASH",
    )

    stripped = dict(capture)
    stripped.pop(
        "raw_response_hash",
        None,
    )

    _need(
        observed
        == v04.external_provider_json_sha256(
            stripped
        ),
        "BLOCK_RAW_RESPONSE_HASH_MISMATCH",
    )

    _need(
        capture.get("capture_version")
        == RAW_CAPTURE_VERSION,
        "BLOCK_RAW_VERSION",
    )

    _need(
        capture.get("request_hash")
        == request_hash,
        "BLOCK_RAW_REQUEST_BINDING",
    )

    return observed


def _process_response(
    *,
    prepared: Mapping[str, Any],
    raw: Mapping[str, Any],
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    request = prepared["request"]
    context = prepared["context"]
    source_gate = prepared["source_gate"]

    call, proposal = (
        v04.parse_council_responses_payload(
            raw,
            request=request,
            latency_ms=0,
        )
    )

    _need(
        proposal.model_run_ref
        == prepared["ttl_model_run_ref"],
        "BLOCK_TTL_MODEL_RUN_REF",
    )

    adapted = proposal.model_copy(
        update={
            "model_run_ref":
                v04.MODEL_RUN_REF
        }
    )

    v04.validate_proposal(
        adapted,
        context=context,
        gate=source_gate,
    )

    frozen = (
        v04.FrozenJudgeDecisionProposal
        .from_draft(proposal)
    )

    actual = v04.actual_cost_usd(
        raw,
        model=EXPECTED_MODEL,
        pricing=prepared["pricing"],
    )

    ceiling = Decimal(
        str(
            prepared["authority"][
                "judge_max_cost_usd"
            ]
        )
    )

    _need(
        actual <= ceiling,
        "BLOCK_ACTUAL_COST_EXCEEDS_APPROVAL",
    )

    record: dict[str, Any] = {
        "request_hash":
            request.request_hash,

        "model_run_ref":
            prepared["ttl_model_run_ref"],

        "response_id":
            call.response_id,

        "outcome":
            proposal.outcome.value,

        "next_directive":
            proposal.next_directive.value,

        "frozen_judge_proposal":
            frozen.model_dump(
                mode="json",
                exclude_none=False,
            ),
    }

    record["record_hash"] = canonical_sha256(
        record,
        exclude_fields=("record_hash",),
    )

    invest = (
        proposal.outcome
        == v04.JudgeOutcome.INVEST
    )

    watch = (
        proposal.outcome
        == v04.JudgeOutcome.WATCH
    )

    abstain = (
        proposal.outcome
        == v04.JudgeOutcome.ABSTAIN
    )

    result: dict[str, Any] = {
        "artifact_version":
            RESULT_VERSION,

        "status":
            "B4_TTL_REEVALUATION_JUDGE_FROZEN",

        "code_commit_sha":
            prepared["execution_head"],

        "request_hash":
            request.request_hash,

        "gate_hash":
            prepared["authority"]["gate_hash"],

        "activation_approval_hash":
            prepared["authority"][
                "activation_approval_hash"
            ],

        "paid_approval_hash":
            prepared["authority"][
                "paid_approval_hash"
            ],

        "raw_response_hash":
            capture["raw_response_hash"],

        "processed_record":
            record,

        "decision_created_at_utc":
            capture["captured_at_utc"],

        "actual_cost_usd":
            format(actual, "f"),

        "fresh_ttl_reevaluation":
            True,

        "historical_judge_reactivated":
            False,

        "final_b4_decision_created":
            True,

        "b5_handoff_eligible":
            invest,

        "b5_handoff_created":
            False,

        "watch_terminal_without_b5":
            watch,

        "abstain_terminal_without_b5":
            abstain,

        "fresh_invest_requires_fresh_b5":
            True,

        "model_calls":
            1,

        "provider_reads":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "automatic_retries":
            0,

        "b6_started":
            False,

        "live_money":
            "PROHIBITED",
    }

    return _seal(result)


def _execute_once(
    prepared: Mapping[str, Any],
    *,
    consumption_path: Path,
    raw_path: Path,
    result_path: Path,
    sender: Callable[
        [Mapping[str, Any]],
        Mapping[str, Any],
    ],
) -> dict[str, Any]:
    for path in (
        consumption_path,
        raw_path,
        result_path,
    ):
        _need(
            not path.exists(),
            f"BLOCK_EXCLUSIVE_OUTPUT_EXISTS:{path}",
        )

    consumption = _build_consumption(
        prepared
    )

    v04._write_exclusive(
        consumption_path,
        consumption,
    )

    started_at = _utc_now()

    _update_consumption(
        consumption_path,
        consumption,
        status="DISPATCH_STARTED_UNKNOWN",
        dispatch_started_at_utc=started_at,
        model_calls_attempted=1,
    )

    try:
        raw = sender(
            prepared["request"].request_payload
        )
    except Exception as exc:
        _update_consumption(
            consumption_path,
            consumption,
            stop_reason=(
                "AMBIGUOUS_PROVIDER_OUTCOME:"
                f"{type(exc).__name__}"
            ),
        )

        raise PaidJudgeExecutionBlocked(
            "BLOCK_AMBIGUOUS_PROVIDER_OUTCOME"
        ) from exc

    try:
        capture = _build_raw_capture(
            request_hash=prepared[
                "request"
            ].request_hash,
            raw=raw,
            started_at=started_at,
        )

        v04._write_exclusive(
            raw_path,
            capture,
        )

        raw_hash = _verify_raw_capture(
            capture,
            request_hash=prepared[
                "request"
            ].request_hash,
        )

        _update_consumption(
            consumption_path,
            consumption,
            status=(
                "RESPONSE_CAPTURED_UNVALIDATED"
            ),
            raw_response_hash=raw_hash,
            raw_response_path=str(raw_path),
        )

    except Exception as exc:
        _update_consumption(
            consumption_path,
            consumption,
            status=(
                "RESPONSE_CAPTURE_PERSISTENCE_FAILED"
            ),
            stop_reason=(
                "RESPONSE_CAPTURE_FAILURE:"
                f"{type(exc).__name__}"
            ),
        )

        raise PaidJudgeExecutionBlocked(
            "BLOCK_RESPONSE_CAPTURE"
        ) from exc

    try:
        result = _process_response(
            prepared=prepared,
            raw=raw,
            capture=capture,
        )

    except Exception as exc:
        _update_consumption(
            consumption_path,
            consumption,
            status=(
                "RESPONSE_CAPTURED_NOT_ACCEPTED"
            ),
            stop_reason=(
                "RESPONSE_VALIDATION_FAILURE:"
                f"{type(exc).__name__}"
            ),
        )

        raise PaidJudgeExecutionBlocked(
            "BLOCK_RESPONSE_NOT_ACCEPTED"
        ) from exc

    try:
        v04._write_exclusive(
            result_path,
            result,
        )

    except Exception as exc:
        _update_consumption(
            consumption_path,
            consumption,
            status=(
                "RESULT_PERSISTENCE_FAILED"
            ),
            stop_reason=(
                "RESULT_PERSISTENCE_FAILURE:"
                f"{type(exc).__name__}"
            ),
        )

        raise PaidJudgeExecutionBlocked(
            "BLOCK_RESULT_PERSISTENCE"
        ) from exc

    _update_consumption(
        consumption_path,
        consumption,
        status="COMPLETED",
        processed_result_hash=(
            result["artifact_hash"]
        ),
        result_path=str(result_path),
        actual_cost_usd=(
            result["actual_cost_usd"]
        ),
    )

    return result


def parse_args(
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )

    parser.add_argument(
        "--execute-paid-judge",
        action="store_true",
    )

    parser.add_argument(
        "--gate",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--activation-approval",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--paid-approval",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--consumption",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--raw",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--result",
        type=Path,
        required=True,
    )

    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    *,
    repository: Path | None = None,
    transport_factory: Callable[
        [],
        Callable[
            [Mapping[str, Any]],
            Mapping[str, Any],
        ],
    ]
    | None = None,
) -> dict[str, Any]:
    repo = (
        repository
        or Path.cwd()
    ).resolve()

    prepared = _prepare(
        args,
        repository=repo,
    )

    factory = (
        transport_factory
        or _real_transport_factory
    )

    sender = factory()

    _need(
        callable(sender),
        "BLOCK_TRANSPORT_FACTORY",
    )

    return _execute_once(
        prepared,
        consumption_path=args.consumption,
        raw_path=args.raw,
        result_path=args.result,
        sender=sender,
    )


def main(
    argv: Sequence[str] | None = None,
) -> int:
    result = run(
        parse_args(argv)
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
