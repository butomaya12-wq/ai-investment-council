from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from aic.domain.canonical import canonical_sha256

from .initial_approval import (
    InitialStageApproval,
    init_initial_approval_state,
)
from .initial_preflight import (
    active_session_no_touch,
    load_initial_preflight_payload,
)
from .product_state import (
    Base,
    SessionLocal,
)


REHEARSAL_VERSION = (
    "MARKET_JURY_V62B_INITIAL_EXECUTION_REHEARSAL_v0_1"
)

REHEARSAL_MODE = (
    "FAKE_TRANSPORT_REHEARSAL"
)

READY_STATUS = (
    "READY_FOR_FAKE_DISPATCH"
)

RUNNING_STATUS = (
    "RUNNING_FAKE_DISPATCH"
)

COMPLETE_STATUS = (
    "COMPLETED_FAKE_DISPATCH"
)

EXPECTED_CALLS = 9


class InitialExecutionRun(Base):
    __tablename__ = "initial_execution_runs"

    run_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    approval_id: Mapped[str] = mapped_column(
        ForeignKey(
            "initial_stage_approvals.approval_id"
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    preflight_id: Mapped[str] = mapped_column(
        ForeignKey(
            "initial_stage_preflights.preflight_id"
        ),
        nullable=False,
        index=True,
    )

    session_id: Mapped[str] = mapped_column(
        ForeignKey(
            "investment_sessions.session_id"
        ),
        nullable=False,
        index=True,
    )

    focus_symbol: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )

    code_commit_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    request_set_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    binding_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    mode: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        index=True,
    )

    call_count_planned: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    call_count_completed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    model_calls_actual: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    cost_usd_actual: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )


class InitialExecutionCall(Base):
    __tablename__ = "initial_execution_calls"

    call_id: Mapped[str] = mapped_column(
        String(80),
        primary_key=True,
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey(
            "initial_execution_runs.run_id"
        ),
        nullable=False,
        index=True,
    )

    dispatch_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    candidate: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    lane: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    request_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    receipt_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def init_initial_execution_state() -> None:
    init_initial_approval_state()


def _load_approval_payload(
    approval_id: str,
) -> dict[str, Any]:
    init_initial_execution_state()

    with SessionLocal() as db:
        row = db.get(
            InitialStageApproval,
            approval_id,
        )

        if row is None:
            raise ValueError(
                "Initial owner approval not found."
            )

        payload = json.loads(
            row.payload_json
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Stored Initial approval malformed."
        )

    approval_hash = payload.get(
        "approval_hash"
    )

    if (
        not isinstance(
            approval_hash,
            str,
        )
        or approval_hash
        != canonical_sha256(
            payload,
            exclude_fields=(
                "approval_hash",
            ),
        )
    ):
        raise ValueError(
            "Stored Initial approval hash mismatch."
        )

    return payload


def _verify_binding(
    *,
    preflight: Mapping[str, Any],
    approval: Mapping[str, Any],
    active_session: Mapping[str, str],
    current_code_sha: str,
    git_worktree_clean: bool,
) -> None:
    if not re.fullmatch(
        r"[0-9a-f]{40}",
        current_code_sha,
    ):
        raise ValueError(
            "Exact current code SHA required."
        )

    if git_worktree_clean is not True:
        raise ValueError(
            "Execution rehearsal requires clean tracked worktree."
        )

    artifact_hash = preflight.get(
        "artifact_hash"
    )

    if (
        not isinstance(
            artifact_hash,
            str,
        )
        or artifact_hash
        != canonical_sha256(
            preflight,
            exclude_fields=(
                "artifact_hash",
            ),
        )
    ):
        raise ValueError(
            "Initial preflight artifact hash mismatch."
        )

    if (
        preflight.get("code_commit_sha")
        != current_code_sha
    ):
        raise ValueError(
            "Initial preflight belongs to a different code commit."
        )

    if (
        approval.get("code_commit_sha")
        != current_code_sha
    ):
        raise ValueError(
            "Initial approval belongs to a different code commit."
        )

    if (
        approval.get("preflight_id")
        != preflight.get("preflight_id")
    ):
        raise ValueError(
            "Approval/preflight ID binding mismatch."
        )

    if (
        approval.get(
            "preflight_artifact_hash"
        )
        != preflight.get(
            "artifact_hash"
        )
    ):
        raise ValueError(
            "Approval/preflight artifact binding mismatch."
        )

    if (
        approval.get(
            "session_evidence_hash"
        )
        != preflight.get(
            "session_evidence_hash"
        )
    ):
        raise ValueError(
            "Approval/evidence binding mismatch."
        )

    if (
        approval.get(
            "request_set_hash"
        )
        != preflight.get(
            "request_set_hash"
        )
    ):
        raise ValueError(
            "Approval/request-set binding mismatch."
        )

    if (
        approval.get(
            "approved_max_cost_usd"
        )
        != preflight.get(
            "estimated_max_cost_usd"
        )
    ):
        raise ValueError(
            "Approval/cost binding mismatch."
        )

    if (
        approval.get(
            "owner_approval_granted"
        )
        is not True
    ):
        raise ValueError(
            "Owner approval is not granted."
        )

    # Rehearsal is deliberately allowed while paid execution
    # remains impossible.
    if (
        approval.get(
            "approval_grants_model_execution"
        )
        is not False
    ):
        raise ValueError(
            "Unexpected model-execution authority."
        )

    if (
        approval.get(
            "executor_present"
        )
        is not False
    ):
        raise ValueError(
            "Approval unexpectedly declares executor present."
        )

    if (
        approval.get(
            "model_calls_authorized"
        )
        is not False
    ):
        raise ValueError(
            "Model calls unexpectedly authorized."
        )

    if (
        approval.get(
            "paid_execution_ready"
        )
        is not False
    ):
        raise ValueError(
            "Paid execution unexpectedly ready."
        )

    if (
        approval.get(
            "automatic_retries"
        )
        != 0
    ):
        raise ValueError(
            "Automatic retries must remain zero."
        )

    if (
        active_session.get(
            "session_id"
        )
        != preflight.get(
            "session_id"
        )
        or active_session.get(
            "symbol"
        )
        != preflight.get(
            "focus_symbol"
        )
    ):
        raise ValueError(
            "Active Investment Session changed."
        )

    requests = preflight.get(
        "initial_requests"
    )

    if (
        not isinstance(
            requests,
            list,
        )
        or len(requests) != EXPECTED_CALLS
    ):
        raise ValueError(
            "Exact nine frozen Initial requests required."
        )

    hashes = []

    for row in requests:
        if not isinstance(
            row,
            Mapping,
        ):
            raise ValueError(
                "Frozen Initial request row malformed."
            )

        envelope = row.get(
            "request_envelope"
        )

        if not isinstance(
            envelope,
            Mapping,
        ):
            raise ValueError(
                "Frozen Initial request envelope missing."
            )

        request_hash = envelope.get(
            "request_hash"
        )

        if not isinstance(
            request_hash,
            str,
        ):
            raise ValueError(
                "Frozen Initial request hash missing."
            )

        hashes.append(
            request_hash
        )

    if len(set(hashes)) != EXPECTED_CALLS:
        raise ValueError(
            "Frozen Initial request hashes are not unique."
        )


def _binding_artifact(
    *,
    preflight: Mapping[str, Any],
    approval: Mapping[str, Any],
    current_code_sha: str,
    run_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    artifact = {
        "artifact_version":
            REHEARSAL_VERSION,

        "mode":
            REHEARSAL_MODE,

        "run_id":
            run_id,

        "created_at_utc":
            created_at.isoformat(
                timespec="seconds"
            ).replace(
                "+00:00",
                "Z",
            ),

        "code_commit_sha":
            current_code_sha,

        "session_id":
            preflight[
                "session_id"
            ],

        "focus_symbol":
            preflight[
                "focus_symbol"
            ],

        "preflight_id":
            preflight[
                "preflight_id"
            ],

        "preflight_artifact_hash":
            preflight[
                "artifact_hash"
            ],

        "session_evidence_hash":
            preflight[
                "session_evidence_hash"
            ],

        "request_set_hash":
            preflight[
                "request_set_hash"
            ],

        "approval_id":
            approval[
                "approval_id"
            ],

        "approval_hash":
            approval[
                "approval_hash"
            ],

        "approved_max_cost_usd":
            approval[
                "approved_max_cost_usd"
            ],

        "call_count_planned":
            EXPECTED_CALLS,

        "automatic_retries":
            0,

        "provider_transport":
            "FAKE_ONLY",

        "openai_transport_present":
            False,

        "provider_calls_authorized":
            False,

        "model_calls_authorized":
            False,

        "paid_execution_ready":
            False,

        "model_calls_actual":
            0,

        "cost_usd_actual":
            "0",

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",
    }

    artifact[
        "binding_hash"
    ] = canonical_sha256(
        artifact,
        exclude_fields=(
            "binding_hash",
        ),
    )

    return artifact


def start_fake_initial_execution(
    *,
    approval_id: str,
    current_code_sha: str,
    git_worktree_clean: bool,
) -> dict[str, Any]:
    init_initial_execution_state()

    approval = _load_approval_payload(
        approval_id
    )

    preflight_id = approval.get(
        "preflight_id"
    )

    if not isinstance(
        preflight_id,
        str,
    ):
        raise ValueError(
            "Approval preflight ID missing."
        )

    preflight = (
        load_initial_preflight_payload(
            preflight_id
        )
    )

    active = active_session_no_touch()

    # Every call, including an idempotent replay,
    # must first revalidate the complete authority binding.
    _verify_binding(
        preflight=preflight,
        approval=approval,
        active_session=active,
        current_code_sha=current_code_sha,
        git_worktree_clean=
            git_worktree_clean,
    )

    with SessionLocal() as db:
        existing = db.scalar(
            select(
                InitialExecutionRun
            )
            .where(
                InitialExecutionRun.approval_id
                == approval_id
            )
            .limit(1)
        )

        if existing is not None:
            existing_run_id = (
                existing.run_id
            )

            return initial_execution_state(
                existing_run_id
            )

    run_id = str(
        uuid4()
    )

    now = _now()

    artifact = _binding_artifact(
        preflight=preflight,
        approval=approval,
        current_code_sha=current_code_sha,
        run_id=run_id,
        created_at=now,
    )

    requests = preflight[
        "initial_requests"
    ]

    try:
        with SessionLocal.begin() as db:
            db.add(
                InitialExecutionRun(
                    run_id=
                        run_id,

                    approval_id=
                        approval_id,

                    preflight_id=
                        preflight_id,

                    session_id=
                        preflight[
                            "session_id"
                        ],

                    focus_symbol=
                        preflight[
                            "focus_symbol"
                        ],

                    code_commit_sha=
                        current_code_sha,

                    request_set_hash=
                        preflight[
                            "request_set_hash"
                        ],

                    binding_hash=
                        artifact[
                            "binding_hash"
                        ],

                    mode=
                        REHEARSAL_MODE,

                    status=
                        READY_STATUS,

                    call_count_planned=
                        EXPECTED_CALLS,

                    call_count_completed=
                        0,

                    model_calls_actual=
                        0,

                    cost_usd_actual=
                        "0",

                    payload_json=
                        _json(
                            artifact
                        ),

                    created_at=
                        now,

                    updated_at=
                        now,
                )
            )

            for index, raw in enumerate(
                requests,
                start=1,
            ):
                envelope = raw[
                    "request_envelope"
                ]

                db.add(
                    InitialExecutionCall(
                        call_id=
                            f"{run_id}:{index}",

                        run_id=
                            run_id,

                        dispatch_index=
                            index,

                        candidate=
                            str(
                                raw[
                                    "candidate"
                                ]
                            ),

                        lane=
                            str(
                                raw[
                                    "lane"
                                ]
                            ),

                        request_hash=
                            str(
                                envelope[
                                    "request_hash"
                                ]
                            ),

                        status=
                            "PENDING",

                        receipt_hash=
                            None,

                        completed_at=
                            None,
                    )
                )

    except IntegrityError as exc:
        # Another concurrent START may have won the
        # UNIQUE(approval_id) race. That is an
        # idempotent replay, not a second run.
        with SessionLocal() as db:
            winner = db.scalar(
                select(
                    InitialExecutionRun
                )
                .where(
                    InitialExecutionRun.approval_id
                    == approval_id
                )
                .limit(1)
            )

            if winner is None:
                raise ValueError(
                    "Concurrent execution start "
                    "conflict without persisted winner."
                ) from exc

            winner_run_id = (
                winner.run_id
            )

        return initial_execution_state(
            winner_run_id
        )

    return initial_execution_state(
        run_id
    )

def _public_call(
    row: InitialExecutionCall,
) -> dict[str, Any]:
    return {
        "dispatch_index":
            row.dispatch_index,

        "candidate":
            row.candidate,

        "lane":
            row.lane,

        "request_hash":
            row.request_hash,

        "status":
            row.status,

        "receipt_hash":
            row.receipt_hash,

        "completed_at_utc":
            (
                row.completed_at
                .astimezone(
                    timezone.utc
                )
                .isoformat(
                    timespec="seconds"
                )
                .replace(
                    "+00:00",
                    "Z",
                )
                if row.completed_at
                is not None
                else None
            ),
    }


def initial_execution_state(
    run_id: str,
) -> dict[str, Any]:
    init_initial_execution_state()

    with SessionLocal() as db:
        run = db.get(
            InitialExecutionRun,
            run_id,
        )

        if run is None:
            raise ValueError(
                "Initial execution rehearsal not found."
            )

        calls = list(
            db.scalars(
                select(
                    InitialExecutionCall
                )
                .where(
                    InitialExecutionCall.run_id
                    == run_id
                )
                .order_by(
                    InitialExecutionCall.dispatch_index
                )
            )
        )

    if len(calls) != EXPECTED_CALLS:
        raise ValueError(
            "Initial execution call ledger drift."
        )

    pending = [
        row.dispatch_index
        for row in calls
        if row.status == "PENDING"
    ]

    return {
        "available":
            True,

        "artifact_version":
            REHEARSAL_VERSION,

        "mode":
            run.mode,

        "run_id":
            run.run_id,

        "approval_id":
            run.approval_id,

        "preflight_id":
            run.preflight_id,

        "session_id":
            run.session_id,

        "focus_symbol":
            run.focus_symbol,

        "code_commit_sha":
            run.code_commit_sha,

        "request_set_hash":
            run.request_set_hash,

        "binding_hash":
            run.binding_hash,

        "status":
            run.status,

        "call_count_planned":
            run.call_count_planned,

        "call_count_completed":
            run.call_count_completed,

        "next_dispatch_index":
            (
                pending[0]
                if pending
                else None
            ),

        "calls":
            [
                _public_call(row)
                for row in calls
            ],

        "automatic_retries":
            0,

        "provider_transport":
            "FAKE_ONLY",

        "openai_transport_present":
            False,

        "provider_calls_authorized":
            False,

        "model_calls_authorized":
            False,

        "paid_execution_ready":
            False,

        "model_calls_actual":
            run.model_calls_actual,

        "cost_usd_actual":
            run.cost_usd_actual,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",
    }


def latest_initial_execution_rehearsal() -> dict[str, Any]:
    init_initial_execution_state()

    active = active_session_no_touch()

    with SessionLocal() as db:
        run = db.scalar(
            select(
                InitialExecutionRun
            )
            .where(
                InitialExecutionRun.session_id
                == active[
                    "session_id"
                ]
            )
            .where(
                InitialExecutionRun.focus_symbol
                == active[
                    "symbol"
                ]
            )
            .order_by(
                InitialExecutionRun.created_at.desc()
            )
            .limit(1)
        )

        if run is None:
            return {
                "available":
                    False,

                "focus_symbol":
                    active[
                        "symbol"
                    ],

                "provider_transport":
                    "FAKE_ONLY",

                "model_calls_authorized":
                    False,

                "paid_execution_ready":
                    False,

                "model_calls_actual":
                    0,

                "cost_usd_actual":
                    "0",

                "broker_writes":
                    0,

                "alpaca_orders":
                    0,

                "live_money":
                    "PROHIBITED",
            }

        run_id = run.run_id

    return initial_execution_state(
        run_id
    )


def advance_fake_initial_execution(
    *,
    run_id: str,
    dispatch_index: int,
) -> dict[str, Any]:
    init_initial_execution_state()

    if (
        dispatch_index < 1
        or dispatch_index > EXPECTED_CALLS
    ):
        raise ValueError(
            "Dispatch index outside exact nine-call plan."
        )

    # Read immutable call identity first.
    # No provider action occurs here.
    with SessionLocal() as db:
        run = db.get(
            InitialExecutionRun,
            run_id,
        )

        if run is None:
            raise ValueError(
                "Initial execution rehearsal not found."
            )

        if run.mode != REHEARSAL_MODE:
            raise ValueError(
                "Execution run is not fake rehearsal."
            )

        ledger_count = db.scalar(
            select(
                func.count()
            )
            .select_from(
                InitialExecutionCall
            )
            .where(
                InitialExecutionCall.run_id
                == run_id
            )
        )

        if ledger_count != EXPECTED_CALLS:
            raise ValueError(
                "Initial execution call ledger drift."
            )

        target = db.scalar(
            select(
                InitialExecutionCall
            )
            .where(
                InitialExecutionCall.run_id
                == run_id
            )
            .where(
                InitialExecutionCall.dispatch_index
                == dispatch_index
            )
            .limit(1)
        )

        if target is None:
            raise ValueError(
                "Requested dispatch does not exist."
            )

        # Fast idempotent replay path.
        if target.status == "COMPLETED":
            return initial_execution_state(
                run_id
            )

        candidate = target.candidate
        lane = target.lane
        request_hash = (
            target.request_hash
        )
        call_id = target.call_id

    receipt = {
        "receipt_version":
            (
                "MARKET_JURY_V62B_"
                "FAKE_DISPATCH_RECEIPT_v0_1"
            ),

        "mode":
            REHEARSAL_MODE,

        "run_id":
            run_id,

        "dispatch_index":
            dispatch_index,

        "candidate":
            candidate,

        "lane":
            lane,

        "request_hash":
            request_hash,

        "provider_transport_invoked":
            False,

        "openai_call":
            False,

        "model_call":
            False,

        "automatic_retry":
            False,

        "cost_usd":
            "0",

        "result":
            "FAKE_TRANSPORT_COMPLETED",
    }

    receipt_hash = (
        canonical_sha256(
            receipt
        )
    )

    now = _now()

    claimed = False

    # Atomic compare-and-set:
    #
    # exactly one concurrent caller can change this
    # call from PENDING to COMPLETED.
    #
    # An earlier non-completed dispatch also blocks
    # this transition, preserving strict call order.
    with SessionLocal.begin() as db:
        result = db.execute(
            text(
                """
                UPDATE initial_execution_calls
                   SET status = 'COMPLETED',
                       receipt_hash = :receipt_hash,
                       completed_at = :completed_at
                 WHERE call_id = :call_id
                   AND status = 'PENDING'
                   AND NOT EXISTS (
                       SELECT 1
                         FROM initial_execution_calls AS earlier
                        WHERE earlier.run_id = :run_id
                          AND earlier.dispatch_index < :dispatch_index
                          AND earlier.status <> 'COMPLETED'
                   )
                """
            ),
            {
                "receipt_hash":
                    receipt_hash,

                "completed_at":
                    now,

                "call_id":
                    call_id,

                "run_id":
                    run_id,

                "dispatch_index":
                    dispatch_index,
            },
        )

        claimed = (
            result.rowcount == 1
        )

        if claimed:
            completed = db.scalar(
                select(
                    func.count()
                )
                .select_from(
                    InitialExecutionCall
                )
                .where(
                    InitialExecutionCall.run_id
                    == run_id
                )
                .where(
                    InitialExecutionCall.status
                    == "COMPLETED"
                )
            )

            if (
                not isinstance(
                    completed,
                    int,
                )
                or completed < 1
                or completed > EXPECTED_CALLS
            ):
                raise ValueError(
                    "Initial execution completion count drift."
                )

            run = db.get(
                InitialExecutionRun,
                run_id,
            )

            if run is None:
                raise ValueError(
                    "Initial execution rehearsal disappeared."
                )

            run.call_count_completed = (
                completed
            )

            run.status = (
                COMPLETE_STATUS
                if completed
                == EXPECTED_CALLS
                else RUNNING_STATUS
            )

            run.updated_at = now

            # Critical rehearsal invariant.
            run.model_calls_actual = 0
            run.cost_usd_actual = "0"

    state = initial_execution_state(
        run_id
    )

    if claimed:
        return state

    # Atomic claim lost:
    # either another concurrent copy already completed
    # the exact same dispatch, or this request was
    # out of order.
    target_state = next(
        (
            item
            for item in state["calls"]
            if item[
                "dispatch_index"
            ]
            == dispatch_index
        ),
        None,
    )

    if (
        target_state is not None
        and target_state.get(
            "status"
        )
        == "COMPLETED"
    ):
        # Concurrent duplicate is an exact no-op.
        return state

    if (
        state.get(
            "next_dispatch_index"
        )
        != dispatch_index
    ):
        raise ValueError(
            "Out-of-order fake dispatch rejected."
        )

    raise ValueError(
        "Atomic fake dispatch claim failed "
        "without a persisted state transition."
    )
