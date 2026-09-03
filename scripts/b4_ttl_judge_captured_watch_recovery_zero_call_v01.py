#!/usr/bin/env python3
"""Recover the consumed TTL Judge WATCH from immutable captured response evidence."""

from __future__ import annotations

import argparse
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping

from aic.council.proposal import (
    FrozenJudgeDecisionProposal,
    JudgeDecisionProposalDraft,
)
from aic.domain.canonical import canonical_sha256


CANONICAL_BRANCH = "hackathon/alpaca-2026"

SOURCE_EXECUTOR_HEAD = (
    "b16f3ae0beb6125217b951b381d07bdbd9b105b4"
)

SOURCE_REQUEST_HASH = (
    "1850c20fcf2173381b60d5a16589dcdd"
    "c9400cb85de03bb74cfd3899ffe1cacd"
)

SOURCE_GATE_HASH = (
    "7339b79a5ba723b1a75ec04af2915b1d"
    "3b0ca4594c1aa88dc5b05a64452fc7f3"
)

SOURCE_ACTIVATION_HASH = (
    "a2f36e3686545ea0961eca575914f970"
    "9d6b0140cf59c5fb405237f83dc5bf73"
)

SOURCE_PAID_HASH = (
    "20c7c798c5b6016b55d3f7d7ecebc6f"
    "20136b2893d93893b6537a7b708b76eb7"
)

SOURCE_RAW_HASH = (
    "7c18d1e76e89524a94609fda10a61421"
    "bb867b2c7ea708b25dbe4f4f05c22fb9"
)

SOURCE_RESPONSE_ID = (
    "resp_0834ff1ad19231de016a990a8ec75887d289d44e41e077cd36"
)

EXPECTED_ACTUAL_COST_USD = Decimal("0.153417")
MAX_APPROVED_COST_USD = Decimal("0.486939")

SOURCE_STATUS = "RESPONSE_CAPTURED_NOT_ACCEPTED"

SOURCE_STOP_REASON = (
    "RESPONSE_VALIDATION_FAILURE:CurrentJudgeV04Error"
)

SOURCE_VALIDATION_ERROR = (
    "v0.4 current closed-B3 lifecycle forbids embedded research reopen"
)

TTL_MODEL_RUN_REF = "B4_TTL_REEVALUATION_JUDGE_J1_V01"

EXPECTED_REOPEN_REASONS = (
    "ALPACA_NEWS_PAGINATION_INCOMPLETE",
    "VALUATION_EVIDENCE_NOT_SUPPLIED",
)

EXPECTED_WATCH_CANDIDATES = (
    "NVDA",
    "MSFT",
    "META",
)

RECOVERY_POLICY_VERSION = (
    "B4_TTL_CLOSED_B3_WATCH_REOPEN_SURFACE_RECOVERY_v0_1"
)

RECOVERY_RESULT_VERSION = (
    "B4_TTL_REEVALUATION_JUDGE_CAPTURED_WATCH_RECOVERY_v0_1"
)

RECOVERY_RECEIPT_VERSION = (
    "B4_TTL_REEVALUATION_JUDGE_CAPTURED_WATCH_RECOVERY_RECEIPT_v0_1"
)

SELF_RELATIVE_PATH = (
    "scripts/"
    "b4_ttl_judge_captured_watch_recovery_zero_call_v01.py"
)

CRITICAL_SOURCE_PATHS = (
    "scripts/b4_ttl_judge_paid_executor_v01.py",
    "scripts/b4_ttl_judge_activation_readiness_zero_call_v01.py",
    "scripts/b4_ttl_judge_production_gate_zero_call_v01.py",
    "src/aic/council/post_research_reopen_judge_current_v04.py",
    "src/aic/council/request.py",
    "src/aic/council/proposal.py",
)


class TtlCapturedWatchRecoveryError(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise TtlCapturedWatchRecoveryError(message)


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
        "git state unavailable",
    )

    return completed.stdout.strip()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise TtlCapturedWatchRecoveryError(
            f"invalid JSON artifact: {path}"
        ) from exc

    _need(
        isinstance(value, dict),
        f"object required: {path}",
    )

    return value


def _verify_hash(
    payload: Mapping[str, Any],
    expected: str | None = None,
) -> str:
    observed = payload.get(
        "artifact_hash"
    )

    _need(
        isinstance(observed, str)
        and re.fullmatch(
            r"[0-9a-f]{64}",
            observed,
        )
        is not None,
        "artifact_hash missing",
    )

    recomputed = canonical_sha256(
        payload,
        exclude_fields=("artifact_hash",),
    )

    _need(
        observed == recomputed,
        "artifact_hash mismatch",
    )

    if expected is not None:
        _need(
            observed == expected,
            "artifact identity mismatch",
        )

    return observed


def _load_executor(
    repository: Path,
):
    path = (
        repository
        / "scripts"
        / "b4_ttl_judge_paid_executor_v01.py"
    )

    spec = importlib.util.spec_from_file_location(
        "b4_ttl_recovery_source_executor",
        path,
    )

    _need(
        spec is not None
        and spec.loader is not None,
        "source executor module load failed",
    )

    module = importlib.util.module_from_spec(
        spec
    )

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


def _paths(
    repository: Path,
) -> dict[str, Path]:
    runtime = (
        repository
        / ".aic-runtime"
    )

    short = SOURCE_EXECUTOR_HEAD[:7]
    suffix = SOURCE_REQUEST_HASH

    return {
        "gate":
            runtime
            / (
                "b4_ttl_judge_production_gate_zero_call_"
                f"v0_1__{short}.json"
            ),

        "activation":
            runtime
            / (
                "b4_ttl_judge_activation_approval_"
                f"v0_1__{short}.json"
            ),

        "paid":
            runtime
            / (
                "b4_ttl_judge_paid_call_approval_"
                f"v0_1__{short}.json"
            ),

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

        "original_result":
            runtime
            / (
                "b4_ttl_judge_result_"
                f"v0_1__{suffix}.json"
            ),

        "recovered_result":
            runtime
            / (
                "b4_ttl_judge_captured_watch_recovery_"
                f"v0_1__{suffix}.json"
            ),

        "recovery_receipt":
            runtime
            / (
                "b4_ttl_judge_captured_watch_recovery_receipt_"
                f"v0_1__{suffix}.json"
            ),
    }


def _verify_source_semantics_unchanged(
    repository: Path,
) -> None:
    for relative in CRITICAL_SOURCE_PATHS:
        working = _git(
            repository,
            "hash-object",
            relative,
        )

        source = _git(
            repository,
            "rev-parse",
            f"{SOURCE_EXECUTOR_HEAD}:{relative}",
        )

        _need(
            working == source,
            (
                "source semantics drift: "
                + relative
            ),
        )


def _persist_or_verify(
    path: Path,
    payload: Mapping[str, Any],
) -> str:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        with path.open(
            "x",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                sort_keys=True,
                indent=2,
            )

            handle.write("\n")

    except FileExistsError:
        _need(
            _read(path) == dict(payload),
            (
                "existing recovery artifact differs: "
                + str(path)
            ),
        )

        return "EXISTING_VERIFIED"

    return "CREATED"


def closed_b3_watch_projection(
    proposal: JudgeDecisionProposalDraft,
) -> JudgeDecisionProposalDraft:
    _need(
        proposal.outcome.value == "WATCH",
        "recovery requires model-authored WATCH",
    )

    _need(
        proposal.next_directive.value
        == "MONITOR",
        "recovery requires model-authored MONITOR",
    )

    _need(
        proposal.primary_candidate_id
        == "NVDA",
        "unexpected WATCH primary candidate",
    )

    _need(
        tuple(
            proposal.watch_candidate_ids
        )
        == EXPECTED_WATCH_CANDIDATES,
        "unexpected WATCH candidate set",
    )

    _need(
        proposal.evidence_status.value
        == "PARTIAL",
        "unexpected WATCH evidence status",
    )

    _need(
        proposal.research_reopen_required
        is True,
        (
            "source proposal does not contain "
            "the captured reopen conflict"
        ),
    )

    _need(
        tuple(
            proposal.research_reopen_reason_codes
        )
        == EXPECTED_REOPEN_REASONS,
        "source reopen reason codes drift",
    )

    _need(
        tuple(
            proposal.blocking_reason_codes
        )
        == EXPECTED_REOPEN_REASONS,
        "blocking reason codes drift",
    )

    recovered = proposal.model_copy(
        update={
            "research_reopen_required":
                False,

            "research_reopen_reason_codes":
                (),
        }
    )

    before = proposal.model_dump(
        mode="json",
        exclude_none=False,
    )

    after = recovered.model_dump(
        mode="json",
        exclude_none=False,
    )

    changed = sorted(
        key
        for key in before
        if before.get(key)
        != after.get(key)
    )

    _need(
        changed
        == [
            "research_reopen_reason_codes",
            "research_reopen_required",
        ],
        (
            "recovery changed fields outside "
            "the closed-B3 lifecycle projection"
        ),
    )

    return recovered


def build_recovery(
    *,
    repository: Path,
    recovery_code_head: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    _need(
        re.fullmatch(
            r"[0-9a-f]{40}",
            recovery_code_head,
        )
        is not None,
        "recovery code HEAD invalid",
    )

    _verify_source_semantics_unchanged(
        repository
    )

    paths = _paths(
        repository
    )

    for key in (
        "gate",
        "activation",
        "paid",
        "consumption",
        "raw",
    ):
        _need(
            paths[key].is_file(),
            (
                "source artifact missing: "
                + str(paths[key])
            ),
        )

    _need(
        not paths[
            "original_result"
        ].exists(),
        (
            "original executor result exists; "
            "recovery is not applicable"
        ),
    )

    gate = _read(
        paths["gate"]
    )

    activation = _read(
        paths["activation"]
    )

    paid = _read(
        paths["paid"]
    )

    consumption = _read(
        paths["consumption"]
    )

    capture = _read(
        paths["raw"]
    )

    _verify_hash(
        gate,
        SOURCE_GATE_HASH,
    )

    _verify_hash(
        activation,
        SOURCE_ACTIVATION_HASH,
    )

    _verify_hash(
        paid,
        SOURCE_PAID_HASH,
    )

    consumption_hash = _verify_hash(
        consumption
    )

    _need(
        gate.get(
            "prospective_request_hash"
        )
        == SOURCE_REQUEST_HASH,
        "gate request drift",
    )

    _need(
        activation.get(
            "request_hash"
        )
        == SOURCE_REQUEST_HASH,
        "activation request drift",
    )

    _need(
        paid.get(
            "request_hash"
        )
        == SOURCE_REQUEST_HASH,
        "paid request drift",
    )

    _need(
        activation.get(
            "gate_hash"
        )
        == SOURCE_GATE_HASH,
        "activation gate drift",
    )

    _need(
        paid.get(
            "gate_hash"
        )
        == SOURCE_GATE_HASH,
        "paid gate drift",
    )

    _need(
        paid.get(
            "activation_approval_hash"
        )
        == SOURCE_ACTIVATION_HASH,
        "paid activation drift",
    )

    expected_consumption = {
        "status":
            SOURCE_STATUS,

        "request_hash":
            SOURCE_REQUEST_HASH,

        "gate_hash":
            SOURCE_GATE_HASH,

        "activation_approval_hash":
            SOURCE_ACTIVATION_HASH,

        "paid_approval_hash":
            SOURCE_PAID_HASH,

        "approved_executor_code_commit_sha":
            SOURCE_EXECUTOR_HEAD,

        "model_calls_attempted":
            1,

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

        "stop_reason":
            SOURCE_STOP_REASON,
    }

    for field, expected in (
        expected_consumption.items()
    ):
        _need(
            consumption.get(field)
            == expected,
            (
                "source consumption drift: "
                + field
            ),
        )

    executor = _load_executor(
        repository
    )

    raw_hash = (
        executor._verify_raw_capture(
            capture,
            request_hash=(
                SOURCE_REQUEST_HASH
            ),
        )
    )

    _need(
        raw_hash
        == SOURCE_RAW_HASH,
        "source raw hash drift",
    )

    _need(
        capture.get(
            "provider_response_id"
        )
        == SOURCE_RESPONSE_ID,
        "source response ID drift",
    )

    _need(
        consumption.get(
            "raw_response_hash"
        )
        == SOURCE_RAW_HASH,
        "consumption/raw binding drift",
    )

    gate_module = (
        executor._load_script(
            repository,
            "b4_ttl_recovery_gate",
            executor.GATE_SCRIPT,
        )
    )

    readiness = (
        executor._load_script(
            repository,
            "b4_ttl_recovery_readiness",
            executor.READINESS_SCRIPT,
        )
    )

    evaluation_time = (
        gate_module._utc(
            gate_module
            .DEFAULT_EVALUATION_TIME_UTC
        )
    )

    bundle = (
        executor
        ._reconstruct_request_bundle(
            repository,
            readiness,
            evaluation_time,
        )
    )

    request = bundle[
        "request"
    ]

    _need(
        request.request_hash
        == SOURCE_REQUEST_HASH,
        "request reconstruction drift",
    )

    _need(
        bundle[
            "context"
        ].judge_input_hash
        == executor.EXPECTED_JUDGE_INPUT_HASH,
        "Judge input reconstruction drift",
    )

    constraints = (
        bundle[
            "context"
        ]
        .model_input
        .get(
            "event_outcome_constraints",
            {},
        )
    )

    _need(
        constraints.get(
            "canonical_b3_reopen_closed"
        )
        is True,
        "closed-B3 source constraint missing",
    )

    _need(
        constraints.get(
            "new_research_inside_current_b4_allowed"
        )
        is False,
        (
            "current-B4 no-research "
            "source constraint missing"
        ),
    )

    call, proposal = (
        executor.v04
        .parse_council_responses_payload(
            capture[
                "raw_response"
            ],
            request=request,
            latency_ms=0,
        )
    )

    _need(
        call.response_id
        == SOURCE_RESPONSE_ID,
        "parsed response ID drift",
    )

    _need(
        proposal.model_run_ref
        == TTL_MODEL_RUN_REF,
        "TTL model run ref drift",
    )

    adapted = (
        proposal.model_copy(
            update={
                "model_run_ref":
                    executor.v04
                    .MODEL_RUN_REF
            }
        )
    )

    try:
        executor.v04.validate_proposal(
            adapted,
            context=bundle[
                "context"
            ],
            gate=bundle[
                "source_gate"
            ],
        )

    except (
        executor.v04
        .CurrentJudgeV04Error
    ) as exc:
        _need(
            str(exc)
            == SOURCE_VALIDATION_ERROR,
            (
                "unexpected source "
                "validation failure"
            ),
        )

    else:
        raise (
            TtlCapturedWatchRecoveryError(
                (
                    "source proposal unexpectedly "
                    "passes authoritative "
                    "v0.4 validation"
                )
            )
        )

    recovered = (
        closed_b3_watch_projection(
            proposal
        )
    )

    recovered_for_validation = (
        recovered.model_copy(
            update={
                "model_run_ref":
                    executor.v04
                    .MODEL_RUN_REF
            }
        )
    )

    executor.v04.validate_proposal(
        recovered_for_validation,
        context=bundle[
            "context"
        ],
        gate=bundle[
            "source_gate"
        ],
    )

    actual = (
        executor.v04.actual_cost_usd(
            capture[
                "raw_response"
            ],
            model=(
                executor.EXPECTED_MODEL
            ),
            pricing=bundle[
                "pricing"
            ],
        )
    )

    _need(
        actual
        == EXPECTED_ACTUAL_COST_USD,
        "captured actual cost drift",
    )

    _need(
        actual
        <= MAX_APPROVED_COST_USD,
        (
            "captured cost exceeds "
            "paid approval"
        ),
    )

    source_draft = (
        proposal.model_dump(
            mode="json",
            exclude_none=False,
        )
    )

    source_proposal_hash = (
        canonical_sha256(
            source_draft
        )
    )

    frozen = (
        FrozenJudgeDecisionProposal
        .from_draft(
            recovered
        )
    )

    record: dict[str, Any] = {
        "outcome":
            recovered.outcome.value,

        "next_directive":
            recovered.next_directive.value,

        "primary_candidate_id":
            recovered.primary_candidate_id,

        "watch_candidate_ids":
            list(
                recovered
                .watch_candidate_ids
            ),

        "response_id":
            call.response_id,

        "source_model_proposal_hash":
            source_proposal_hash,

        "frozen_judge_proposal":
            frozen.model_dump(
                mode="json",
                exclude_none=False,
            ),
    }

    record[
        "record_hash"
    ] = canonical_sha256(
        record,
        exclude_fields=(
            "record_hash",
        ),
    )

    result: dict[str, Any] = {
        "artifact_version":
            RECOVERY_RESULT_VERSION,

        "status":
            (
                "B4_TTL_CAPTURED_WATCH_"
                "RECOVERED_ZERO_CALL"
            ),

        "source_executor_head":
            SOURCE_EXECUTOR_HEAD,

        "recovery_code_head":
            recovery_code_head,

        "source_request_hash":
            SOURCE_REQUEST_HASH,

        "source_gate_hash":
            SOURCE_GATE_HASH,

        "source_activation_approval_hash":
            SOURCE_ACTIVATION_HASH,

        "source_paid_approval_hash":
            SOURCE_PAID_HASH,

        "source_consumption_hash":
            consumption_hash,

        "source_raw_response_hash":
            SOURCE_RAW_HASH,

        "source_response_id":
            SOURCE_RESPONSE_ID,

        "source_consumption_status":
            SOURCE_STATUS,

        "source_stop_reason":
            SOURCE_STOP_REASON,

        "source_validation_failure":
            SOURCE_VALIDATION_ERROR,

        "source_model_authored_outcome":
            proposal.outcome.value,

        "source_model_authored_next_directive":
            proposal.next_directive.value,

        "source_model_authored_primary_candidate_id":
            proposal.primary_candidate_id,

        "source_model_authored_research_reopen_required":
            proposal.research_reopen_required,

        "source_model_authored_research_reopen_reason_codes":
            list(
                proposal
                .research_reopen_reason_codes
            ),

        "preserved_blocking_reason_codes":
            list(
                proposal
                .blocking_reason_codes
            ),

        "recovery_policy_version":
            RECOVERY_POLICY_VERSION,

        "normalized_fields":
            [
                "research_reopen_required",
                "research_reopen_reason_codes",
            ],

        "normalized_research_reopen_required":
            False,

        "normalized_research_reopen_reason_codes":
            [],

        "repaired_validation":
            "PASS",

        "processed_record":
            record,

        "actual_cost_usd":
            format(
                actual,
                "f",
            ),

        "source_paid_model_calls":
            1,

        "source_automatic_retries":
            0,

        "recovery_model_calls":
            0,

        "recovery_provider_calls":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "raw_provider_response_modified":
            False,

        "original_executor_result_exists":
            False,

        "final_b4_decision_created":
            True,

        "b5_handoff_eligible":
            False,

        "b5_handoff_created":
            False,

        "watch_terminal_without_b5":
            True,

        "abstain_terminal_without_b5":
            False,

        "research_reopen_created":
            False,

        "b6_started":
            False,

        "live_money":
            "PROHIBITED",
    }

    result[
        "artifact_hash"
    ] = canonical_sha256(
        result,
        exclude_fields=(
            "artifact_hash",
        ),
    )

    receipt: dict[str, Any] = {
        "artifact_version":
            RECOVERY_RECEIPT_VERSION,

        "source_executor_head":
            SOURCE_EXECUTOR_HEAD,

        "recovery_code_head":
            recovery_code_head,

        "source_request_hash":
            SOURCE_REQUEST_HASH,

        "source_consumption_hash":
            consumption_hash,

        "source_raw_response_hash":
            SOURCE_RAW_HASH,

        "source_response_id":
            SOURCE_RESPONSE_ID,

        "source_validation_failure":
            SOURCE_VALIDATION_ERROR,

        "recovery_policy_version":
            RECOVERY_POLICY_VERSION,

        "normalized_fields":
            list(
                result[
                    "normalized_fields"
                ]
            ),

        "recovered_result_hash":
            result[
                "artifact_hash"
            ],

        "recovered_outcome":
            "WATCH",

        "recovered_next_directive":
            "MONITOR",

        "actual_cost_usd":
            format(
                actual,
                "f",
            ),

        "source_paid_model_calls":
            1,

        "recovery_model_calls":
            0,

        "recovery_provider_calls":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "b5_handoff_created":
            False,

        "b6_started":
            False,

        "live_money":
            "PROHIBITED",
    }

    receipt[
        "artifact_hash"
    ] = canonical_sha256(
        receipt,
        exclude_fields=(
            "artifact_hash",
        ),
    )

    return result, receipt


def _require_write_authority(
    repository: Path,
    recovery_code_head: str,
) -> None:
    _need(
        _git(
            repository,
            "branch",
            "--show-current",
        )
        == CANONICAL_BRANCH,
        (
            "recovery write requires "
            "canonical branch"
        ),
    )

    _need(
        _git(
            repository,
            "rev-parse",
            "HEAD",
        )
        == recovery_code_head,
        "recovery HEAD drift",
    )

    _need(
        not _git(
            repository,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ),
        "tracked worktree dirty",
    )

    working = _git(
        repository,
        "hash-object",
        SELF_RELATIVE_PATH,
    )

    committed = _git(
        repository,
        "rev-parse",
        (
            recovery_code_head
            + ":"
            + SELF_RELATIVE_PATH
        ),
    )

    _need(
        working
        == committed,
        (
            "recovery source "
            "is not committed"
        ),
    )

    completed = subprocess.run(
        [
            "git",
            "-c",
            "http.version=HTTP/1.1",
            "-C",
            str(repository),
            "ls-remote",
            "origin",
            (
                "refs/heads/"
                + CANONICAL_BRANCH
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    _need(
        completed.returncode == 0,
        "remote canonical read failed",
    )

    rows = [
        line.split()
        for line
        in completed.stdout.splitlines()
        if line.strip()
    ]

    _need(
        len(rows) == 1
        and rows[0][0]
        == recovery_code_head,
        "remote canonical HEAD drift",
    )


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--audit-only",
        action="store_true",
    )

    mode.add_argument(
        "--materialize-recovery",
        action="store_true",
    )

    return parser.parse_args(
        argv
    )


def main(
    argv: list[str] | None = None,
) -> int:
    args = parse_args(
        argv
    )

    repository = (
        Path.cwd()
        .resolve()
    )

    recovery_code_head = _git(
        repository,
        "rev-parse",
        "HEAD",
    )

    result, receipt = (
        build_recovery(
            repository=repository,
            recovery_code_head=(
                recovery_code_head
            ),
        )
    )

    paths = _paths(
        repository
    )

    print(
        "SOURCE_RAW_RESPONSE_HASH="
        + SOURCE_RAW_HASH
    )

    print(
        "SOURCE_RESPONSE_ID="
        + SOURCE_RESPONSE_ID
    )

    print(
        "SOURCE_PAID_MODEL_CALLS=1"
    )

    print(
        "SOURCE_ACTUAL_COST_USD="
        + result[
            "actual_cost_usd"
        ]
    )

    print(
        "SOURCE_MODEL_AUTHORED_OUTCOME="
        + result[
            "source_model_authored_outcome"
        ]
    )

    print(
        "SOURCE_MODEL_AUTHORED_NEXT_DIRECTIVE="
        + result[
            "source_model_authored_next_directive"
        ]
    )

    print(
        "SOURCE_RESEARCH_REOPEN_REQUIRED="
        + str(
            result[
                "source_model_authored_research_reopen_required"
            ]
        )
    )

    print(
        "SOURCE_RESEARCH_REOPEN_REASON_CODES="
        + json.dumps(
            result[
                "source_model_authored_research_reopen_reason_codes"
            ],
            separators=(",", ":"),
        )
    )

    print(
        "RECOVERY_NORMALIZED_FIELDS="
        + ",".join(
            result[
                "normalized_fields"
            ]
        )
    )

    print(
        "RECOVERED_OUTCOME="
        + result[
            "processed_record"
        ][
            "outcome"
        ]
    )

    print(
        "RECOVERED_NEXT_DIRECTIVE="
        + result[
            "processed_record"
        ][
            "next_directive"
        ]
    )

    print(
        "RECOVERED_VALIDATION=PASS"
    )

    print(
        "B5_HANDOFF_ELIGIBLE=False"
    )

    print(
        "B6_STARTED=False"
    )

    print(
        "NEW_MODEL_CALLS=0"
    )

    print(
        "NEW_PROVIDER_CALLS=0"
    )

    print(
        "NEW_COST_USD=0"
    )

    if (
        args.audit_only
        or not args.materialize_recovery
    ):
        print(
            "RECOVERY_WRITE_PERFORMED=NO"
        )

        print(
            "B4_TTL_CAPTURED_WATCH_RECOVERY_AUDIT=PASS"
        )

        return 0

    _require_write_authority(
        repository,
        recovery_code_head,
    )

    result_state = (
        _persist_or_verify(
            paths[
                "recovered_result"
            ],
            result,
        )
    )

    receipt_state = (
        _persist_or_verify(
            paths[
                "recovery_receipt"
            ],
            receipt,
        )
    )

    print(
        "RECOVERED_RESULT_STATE="
        + result_state
    )

    print(
        "RECOVERY_RECEIPT_STATE="
        + receipt_state
    )

    print(
        "RECOVERED_RESULT_HASH="
        + result[
            "artifact_hash"
        ]
    )

    print(
        "RECOVERY_RECEIPT_HASH="
        + receipt[
            "artifact_hash"
        ]
    )

    print(
        "B4_TTL_CAPTURED_WATCH_RECOVERY=PASS"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
