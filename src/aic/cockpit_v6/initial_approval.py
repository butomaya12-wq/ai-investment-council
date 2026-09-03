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
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from aic.domain.canonical import canonical_sha256

from .initial_preflight import (
    InitialStagePreflight,
    active_session_no_touch,
    init_initial_preflight_state,
    latest_initial_preflight,
    load_initial_preflight_payload,
)
from .product_state import (
    Base,
    SessionLocal,
)


APPROVAL_VERSION = (
    "MARKET_JURY_V62B_INITIAL_OWNER_APPROVAL_v0_1"
)

APPROVAL_STATUS = (
    "OWNER_APPROVED_EXECUTOR_NOT_PRESENT"
)

APPROVAL_SCOPE = (
    "INITIAL_9_CALL_REQUEST_SET_ONLY"
)

APPROVAL_CONFIRMATION = (
    "APPROVE_INITIAL_PREFLIGHT_NO_EXECUTION"
)


class InitialStageApproval(Base):
    __tablename__ = "initial_stage_approvals"

    approval_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
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

    preflight_artifact_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    evidence_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    request_set_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    approved_max_cost_usd: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    call_count_ceiling: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    approval_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
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


def init_initial_approval_state() -> None:
    init_initial_preflight_state()


def _verify_preflight_for_approval(
    preflight: Mapping[str, Any],
    *,
    current_code_sha: str,
) -> None:
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
        preflight.get("status")
        != "READY_FOR_OWNER_APPROVAL"
    ):
        raise ValueError(
            "Initial preflight is not approval-ready."
        )

    if (
        preflight.get("code_commit_sha")
        != current_code_sha
    ):
        raise ValueError(
            "Initial preflight belongs to a different code commit."
        )

    if (
        preflight.get("call_count_planned")
        != 9
        or preflight.get("call_count_ceiling")
        != 9
    ):
        raise ValueError(
            "Initial call-count authority drift."
        )

    if (
        preflight.get("model")
        != "gpt-5.6-terra"
        or preflight.get("reasoning_effort")
        != "low"
    ):
        raise ValueError(
            "Initial model authority drift."
        )

    if (
        preflight.get(
            "owner_approval_granted"
        )
        is not False
    ):
        raise ValueError(
            "Preflight owner-approval state drift."
        )

    if (
        preflight.get(
            "model_calls_authorized"
        )
        is not False
    ):
        raise ValueError(
            "Preflight unexpectedly authorizes model calls."
        )

    if (
        preflight.get(
            "paid_execution_ready"
        )
        is not False
    ):
        raise ValueError(
            "Preflight unexpectedly enables paid execution."
        )

    if (
        preflight.get(
            "automatic_retries"
        )
        != 0
    ):
        raise ValueError(
            "Automatic-retry authority drift."
        )

    if (
        preflight.get("broker_writes")
        != 0
        or preflight.get("alpaca_orders")
        != 0
        or preflight.get("live_money")
        != "PROHIBITED"
    ):
        raise ValueError(
            "Trading safety boundary drift."
        )

    requests = preflight.get(
        "initial_requests"
    )

    if (
        not isinstance(
            requests,
            list,
        )
        or len(requests) != 9
    ):
        raise ValueError(
            "Frozen Initial request set missing."
        )

    request_hashes = []

    for row in requests:
        if not isinstance(
            row,
            Mapping,
        ):
            raise ValueError(
                "Initial request row malformed."
            )

        envelope = row.get(
            "request_envelope"
        )

        if not isinstance(
            envelope,
            Mapping,
        ):
            raise ValueError(
                "Initial request envelope missing."
            )

        request_hash = envelope.get(
            "request_hash"
        )

        if not isinstance(
            request_hash,
            str,
        ):
            raise ValueError(
                "Initial request hash missing."
            )

        request_hashes.append(
            request_hash
        )

    if len(set(request_hashes)) != 9:
        raise ValueError(
            "Initial request hashes are not unique."
        )


def build_initial_owner_approval(
    *,
    preflight: Mapping[str, Any],
    active_session: Mapping[str, str],
    current_code_sha: str,
    supplied_preflight_id: str,
    supplied_artifact_hash: str,
    supplied_request_set_hash: str,
    supplied_max_cost_usd: str,
    confirmation: str,
    approved_at: datetime | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(
        r"[0-9a-f]{40}",
        current_code_sha,
    ):
        raise ValueError(
            "Exact current code SHA required."
        )

    _verify_preflight_for_approval(
        preflight,
        current_code_sha=
            current_code_sha,
    )

    if (
        confirmation
        != APPROVAL_CONFIRMATION
    ):
        raise ValueError(
            "Explicit Initial owner confirmation missing."
        )

    if (
        supplied_preflight_id
        != preflight.get(
            "preflight_id"
        )
    ):
        raise ValueError(
            "Preflight ID approval binding mismatch."
        )

    if (
        supplied_artifact_hash
        != preflight.get(
            "artifact_hash"
        )
    ):
        raise ValueError(
            "Preflight artifact-hash approval binding mismatch."
        )

    if (
        supplied_request_set_hash
        != preflight.get(
            "request_set_hash"
        )
    ):
        raise ValueError(
            "Request-set approval binding mismatch."
        )

    if (
        supplied_max_cost_usd
        != preflight.get(
            "estimated_max_cost_usd"
        )
    ):
        raise ValueError(
            "Maximum-cost approval binding mismatch."
        )

    if (
        active_session.get(
            "session_id"
        )
        != preflight.get(
            "session_id"
        )
    ):
        raise ValueError(
            "Active Investment Session changed."
        )

    if (
        active_session.get(
            "symbol"
        )
        != preflight.get(
            "focus_symbol"
        )
    ):
        raise ValueError(
            "Active focus symbol changed."
        )

    now = (
        approved_at
        if approved_at is not None
        else _now()
    )

    if (
        now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError(
            "Approval timestamp must be timezone-aware."
        )

    now = now.astimezone(
        timezone.utc
    )

    approval = {
        "artifact_version":
            APPROVAL_VERSION,

        "status":
            APPROVAL_STATUS,

        "approval_scope":
            APPROVAL_SCOPE,

        "approval_id":
            str(uuid4()),

        "approved_at_utc":
            now.isoformat(
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

        "approved_model":
            preflight[
                "model"
            ],

        "approved_reasoning_effort":
            preflight[
                "reasoning_effort"
            ],

        "approved_call_count":
            preflight[
                "call_count_planned"
            ],

        "approved_call_count_ceiling":
            preflight[
                "call_count_ceiling"
            ],

        "approved_max_cost_usd":
            preflight[
                "estimated_max_cost_usd"
            ],

        "approved_pricing_version":
            preflight[
                "pricing_version"
            ],

        "approved_pricing_hash":
            preflight[
                "pricing_hash"
            ],

        "automatic_retries":
            0,

        "owner_approval_granted":
            True,

        # Critical boundary:
        # approval exists, but no paid executor exists yet.
        "approval_grants_model_execution":
            False,

        "executor_present":
            False,

        "model_calls_authorized":
            False,

        "paid_execution_ready":
            False,

        "model_calls_this_step":
            0,

        "cost_usd_this_step":
            "0",

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",

        "next_gate":
            (
                "IMPLEMENT_AND_REVIEW_"
                "PAID_INITIAL_EXECUTOR"
            ),
    }

    approval[
        "approval_hash"
    ] = canonical_sha256(
        approval,
        exclude_fields=(
            "approval_hash",
        ),
    )

    return approval


def _public(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "available":
            True,

        "artifact_version":
            payload[
                "artifact_version"
            ],

        "status":
            payload[
                "status"
            ],

        "approval_scope":
            payload[
                "approval_scope"
            ],

        "approval_id":
            payload[
                "approval_id"
            ],

        "approval_hash":
            payload[
                "approval_hash"
            ],

        "approved_at_utc":
            payload[
                "approved_at_utc"
            ],

        "code_commit_sha":
            payload[
                "code_commit_sha"
            ],

        "session_id":
            payload[
                "session_id"
            ],

        "focus_symbol":
            payload[
                "focus_symbol"
            ],

        "preflight_id":
            payload[
                "preflight_id"
            ],

        "preflight_artifact_hash":
            payload[
                "preflight_artifact_hash"
            ],

        "session_evidence_hash":
            payload[
                "session_evidence_hash"
            ],

        "request_set_hash":
            payload[
                "request_set_hash"
            ],

        "approved_call_count":
            payload[
                "approved_call_count"
            ],

        "approved_call_count_ceiling":
            payload[
                "approved_call_count_ceiling"
            ],

        "approved_max_cost_usd":
            payload[
                "approved_max_cost_usd"
            ],

        "owner_approval_granted":
            True,

        "approval_grants_model_execution":
            False,

        "executor_present":
            False,

        "model_calls_authorized":
            False,

        "paid_execution_ready":
            False,

        "automatic_retries":
            0,

        "model_calls_this_step":
            0,

        "cost_usd_this_step":
            "0",

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",

        "next_gate":
            payload[
                "next_gate"
            ],
    }


def create_initial_owner_approval(
    *,
    current_code_sha: str,
    preflight_id: str,
    preflight_artifact_hash: str,
    request_set_hash: str,
    approved_max_cost_usd: str,
    confirmation: str,
) -> dict[str, Any]:
    init_initial_approval_state()

    preflight = (
        load_initial_preflight_payload(
            preflight_id
        )
    )

    active = (
        active_session_no_touch()
    )

    # Validate the complete approval request BEFORE
    # the idempotent existing-row path.
    #
    # A previously stored approval must never allow a
    # caller to bypass current code/session/hash/cost
    # binding checks merely because preflight_id matches.
    approval = (
        build_initial_owner_approval(
            preflight=
                preflight,

            active_session=
                active,

            current_code_sha=
                current_code_sha,

            supplied_preflight_id=
                preflight_id,

            supplied_artifact_hash=
                preflight_artifact_hash,

            supplied_request_set_hash=
                request_set_hash,

            supplied_max_cost_usd=
                approved_max_cost_usd,

            confirmation=
                confirmation,
        )
    )

    with SessionLocal() as db:
        existing = db.scalar(
            select(
                InitialStageApproval
            )
            .where(
                InitialStageApproval.preflight_id
                == preflight_id
            )
            .limit(1)
        )

        if existing is not None:
            payload = json.loads(
                existing.payload_json
            )

            if not isinstance(
                payload,
                dict,
            ):
                raise ValueError(
                    "Stored Initial approval malformed."
                )

            if (
                payload.get(
                    "approval_hash"
                )
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

            binding_fields = (
                "code_commit_sha",
                "session_id",
                "focus_symbol",
                "preflight_id",
                "preflight_artifact_hash",
                "session_evidence_hash",
                "request_set_hash",
                "approved_model",
                "approved_reasoning_effort",
                "approved_call_count",
                "approved_call_count_ceiling",
                "approved_max_cost_usd",
                "approved_pricing_version",
                "approved_pricing_hash",
            )

            for field in binding_fields:
                if (
                    payload.get(field)
                    != approval.get(field)
                ):
                    raise ValueError(
                        "Stored Initial approval "
                        f"binding drift: {field}."
                    )

            return _public(
                payload
            )

    approved_at = datetime.fromisoformat(
        approval[
            "approved_at_utc"
        ].replace(
            "Z",
            "+00:00",
        )
    )

    row = InitialStageApproval(
        approval_id=
            approval[
                "approval_id"
            ],

        preflight_id=
            approval[
                "preflight_id"
            ],

        session_id=
            approval[
                "session_id"
            ],

        focus_symbol=
            approval[
                "focus_symbol"
            ],

        code_commit_sha=
            approval[
                "code_commit_sha"
            ],

        preflight_artifact_hash=
            approval[
                "preflight_artifact_hash"
            ],

        evidence_hash=
            approval[
                "session_evidence_hash"
            ],

        request_set_hash=
            approval[
                "request_set_hash"
            ],

        approved_max_cost_usd=
            approval[
                "approved_max_cost_usd"
            ],

        call_count_ceiling=
            approval[
                "approved_call_count_ceiling"
            ],

        status=
            approval[
                "status"
            ],

        approval_hash=
            approval[
                "approval_hash"
            ],

        payload_json=
            _json(
                approval
            ),

        approved_at=
            approved_at,
    )

    with SessionLocal.begin() as db:
        db.add(row)

    return _public(
        approval
    )


def latest_initial_owner_approval() -> dict[str, Any]:
    init_initial_approval_state()

    preflight = (
        latest_initial_preflight()
    )

    if (
        preflight.get("available")
        is not True
    ):
        return {
            "available":
                False,

            "reason":
                "NO_INITIAL_PREFLIGHT",

            "owner_approval_granted":
                False,

            "model_calls_authorized":
                False,

            "paid_execution_ready":
                False,

            "broker_writes":
                0,

            "alpaca_orders":
                0,

            "live_money":
                "PROHIBITED",
        }

    preflight_id = str(
        preflight[
            "preflight_id"
        ]
    )

    with SessionLocal() as db:
        row = db.scalar(
            select(
                InitialStageApproval
            )
            .where(
                InitialStageApproval.preflight_id
                == preflight_id
            )
            .limit(1)
        )

        if row is None:
            return {
                "available":
                    False,

                "reason":
                    "NOT_APPROVED",

                "preflight_id":
                    preflight_id,

                "focus_symbol":
                    preflight[
                        "focus_symbol"
                    ],

                "approved_max_cost_usd":
                    preflight[
                        "estimated_max_cost_usd"
                    ],

                "owner_approval_granted":
                    False,

                "model_calls_authorized":
                    False,

                "paid_execution_ready":
                    False,

                "broker_writes":
                    0,

                "alpaca_orders":
                    0,

                "live_money":
                    "PROHIBITED",
            }

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

        if (
            payload.get(
                "approval_hash"
            )
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

        return _public(
            payload
        )
