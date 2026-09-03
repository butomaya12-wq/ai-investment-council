from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from .product_state import (
    Base,
    SessionLocal,
    get_active_session,
    init_product_state,
)


REAL_COUNCIL_CALL_PLAN: dict[str, Any] = {
    "scope": "MONITORED_SET_COMPARISON",
    "candidates": [
        "NVDA",
        "MSFT",
        "META",
    ],
    "initial": {
        "calls": 9,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "max_output_tokens_per_call": 4096,
    },
    "rebuttal": {
        "calls": 3,
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "max_output_tokens_per_call": 6144,
    },
    "judge": {
        "calls": 1,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens_per_call": 8192,
    },
    "total_calls": 13,
    "automatic_retries": 0,
}


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
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

    scope: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    current_stage: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    sequence_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    model_calls_planned: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    model_calls_actual: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    cost_usd_actual: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="0",
    )

    is_canonical: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    call_plan_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    agent_run_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.run_id"),
        nullable=False,
        index=True,
    )

    lane: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    sequence_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    output_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"

    snapshot_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.run_id"),
        nullable=False,
        index=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    source_kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ProductDecision(Base):
    __tablename__ = "decisions"

    decision_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.run_id"),
        nullable=False,
        index=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    outcome: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    directive: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    decision_kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    is_canonical: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


LANES = (
    "BULL",
    "BEAR",
    "RED_TEAM",
    "JUDGE",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def init_analysis_state() -> None:
    init_product_state()


def analysis_preflight() -> dict[str, Any]:
    active = get_active_session()

    return {
        "artifact_version":
            "MARKET_JURY_V62_PRODUCT_PREFLIGHT_v0_1",

        "focus_symbol":
            active["symbol"],

        "session_id":
            active["session_id"],

        "mode":
            "FAKE_TRANSPORT_ONLY",

        "real_council_call_plan":
            REAL_COUNCIL_CALL_PLAN,

        "real_model_calls_authorized":
            False,

        "fake_model_calls":
            0,

        "owner_approval_required_for_real_run":
            True,

        "real_cost_preflight_ready":
            False,

        "reason_real_cost_not_available":
            (
                "Session-bound real request bodies "
                "have not been built yet."
            ),

        "automatic_retries":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",
    }


def _run_payload(
    db,
    run: AnalysisRun,
) -> dict[str, Any]:
    agents = list(
        db.scalars(
            select(AgentRun)
            .where(
                AgentRun.analysis_run_id
                == run.run_id
            )
            .order_by(
                AgentRun.sequence_index.asc()
            )
        )
    )

    decision = db.scalar(
        select(ProductDecision)
        .where(
            ProductDecision.analysis_run_id
            == run.run_id
        )
        .limit(1)
    )

    return {
        "run_id":
            run.run_id,

        "session_id":
            run.session_id,

        "focus_symbol":
            run.focus_symbol,

        "scope":
            run.scope,

        "mode":
            run.mode,

        "status":
            run.status,

        "current_stage":
            run.current_stage,

        "sequence_index":
            run.sequence_index,

        "model_calls_planned":
            run.model_calls_planned,

        "model_calls_actual":
            run.model_calls_actual,

        "cost_usd_actual":
            run.cost_usd_actual,

        "is_canonical":
            run.is_canonical,

        "real_council_call_plan":
            json.loads(run.call_plan_json),

        "created_at":
            run.created_at.isoformat(),

        "started_at":
            run.started_at.isoformat(),

        "completed_at":
            (
                run.completed_at.isoformat()
                if run.completed_at
                else None
            ),

        "agents": [
            {
                "lane":
                    row.lane,

                "status":
                    row.status,

                "sequence_index":
                    row.sequence_index,

                "output":
                    (
                        json.loads(row.output_json)
                        if row.output_json
                        else None
                    ),
            }
            for row in agents
        ],

        "decision":
            (
                {
                    "symbol":
                        decision.symbol,

                    "outcome":
                        decision.outcome,

                    "directive":
                        decision.directive,

                    "decision_kind":
                        decision.decision_kind,

                    "is_canonical":
                        decision.is_canonical,
                }
                if decision
                else None
            ),

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",
    }


def latest_analysis() -> dict[str, Any]:
    init_analysis_state()

    active = get_active_session()

    with SessionLocal() as db:
        run = db.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.session_id
                == active["session_id"]
            )
            .where(
                AnalysisRun.focus_symbol
                == active["symbol"]
            )
            .order_by(
                AnalysisRun.created_at.desc()
            )
            .limit(1)
        )

        if run is None:
            return {
                "available": False,
                "focus_symbol": active["symbol"],
                "session_id": active["session_id"],
                "live_money": "PROHIBITED",
            }

        payload = _run_payload(db, run)
        payload["available"] = True
        return payload


def start_fake_analysis() -> dict[str, Any]:
    init_analysis_state()

    active = get_active_session()
    now = _now()

    with SessionLocal.begin() as db:
        existing = db.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.session_id
                == active["session_id"]
            )
            .where(
                AnalysisRun.focus_symbol
                == active["symbol"]
            )
            .where(
                AnalysisRun.status
                == "RUNNING"
            )
            .order_by(
                AnalysisRun.created_at.desc()
            )
            .limit(1)
        )

        if existing is not None:
            return _run_payload(
                db,
                existing,
            )

        run = AnalysisRun(
            run_id=str(uuid4()),
            session_id=str(
                active["session_id"]
            ),
            focus_symbol=str(
                active["symbol"]
            ),
            scope=
                "MONITORED_SET_COMPARISON",
            mode="FAKE_TRANSPORT",
            status="RUNNING",
            current_stage="EVIDENCE_CAPTURE",
            sequence_index=0,
            model_calls_planned=13,
            model_calls_actual=0,
            cost_usd_actual="0",
            is_canonical=False,
            call_plan_json=_json(
                REAL_COUNCIL_CALL_PLAN
            ),
            created_at=now,
            started_at=now,
            completed_at=None,
        )

        db.add(run)
        db.flush()

        snapshot = EvidenceSnapshot(
            snapshot_id=str(uuid4()),
            analysis_run_id=run.run_id,
            symbol=run.focus_symbol,
            source_kind=
                "LOCAL_FAKE_ZERO_CALL_INPUT",
            payload_json=_json(
                {
                    "focus_symbol":
                        run.focus_symbol,

                    "scope":
                        run.scope,

                    "provider_reads":
                        0,

                    "model_calls":
                        0,

                    "canonical_evidence":
                        False,
                }
            ),
            created_at=now,
        )

        db.add(snapshot)

        for index, lane in enumerate(
            LANES,
            start=1,
        ):
            db.add(
                AgentRun(
                    agent_run_id=
                        str(uuid4()),

                    analysis_run_id=
                        run.run_id,

                    lane=lane,

                    sequence_index=index,

                    status="PENDING",

                    output_json=None,

                    started_at=None,

                    completed_at=None,
                )
            )

        db.flush()

        return _run_payload(
            db,
            run,
        )


def _fake_output(
    lane: str,
    symbol: str,
) -> dict[str, Any]:
    if lane == "BULL":
        return {
            "kind": "FAKE_TRANSPORT_OUTPUT",
            "summary":
                (
                    f"{symbol}: upside lane "
                    "wiring validated."
                ),
        }

    if lane == "BEAR":
        return {
            "kind": "FAKE_TRANSPORT_OUTPUT",
            "summary":
                (
                    f"{symbol}: downside lane "
                    "wiring validated."
                ),
        }

    if lane == "RED_TEAM":
        return {
            "kind": "FAKE_TRANSPORT_OUTPUT",
            "summary":
                (
                    f"{symbol}: challenge lane "
                    "wiring validated."
                ),
        }

    return {
        "kind": "FAKE_TRANSPORT_OUTPUT",
        "summary":
            (
                f"{symbol}: Judge UI and "
                "persistence wiring validated."
            ),
    }


def advance_fake_analysis(
    run_id: str,
) -> dict[str, Any]:
    init_analysis_state()

    now = _now()

    with SessionLocal.begin() as db:
        run = db.get(
            AnalysisRun,
            run_id,
        )

        if run is None:
            raise ValueError(
                "Analysis run not found."
            )

        if run.mode != "FAKE_TRANSPORT":
            raise ValueError(
                "Only fake runs may use "
                "the fake step endpoint."
            )

        if run.status == "COMPLETED":
            return _run_payload(
                db,
                run,
            )

        next_index = (
            run.sequence_index
            + 1
        )

        if next_index > len(LANES):
            raise ValueError(
                "Fake run sequence drift."
            )

        lane = LANES[
            next_index - 1
        ]

        agent = db.scalar(
            select(AgentRun)
            .where(
                AgentRun.analysis_run_id
                == run.run_id
            )
            .where(
                AgentRun.sequence_index
                == next_index
            )
            .limit(1)
        )

        if agent is None:
            raise ValueError(
                "Agent run state missing."
            )

        agent.status = "COMPLETED"
        agent.started_at = now
        agent.completed_at = now
        agent.output_json = _json(
            _fake_output(
                lane,
                run.focus_symbol,
            )
        )

        run.sequence_index = next_index

        if lane == "JUDGE":
            run.status = "COMPLETED"
            run.current_stage = "COMPLETE"
            run.completed_at = now

            existing_decision = db.scalar(
                select(ProductDecision)
                .where(
                    ProductDecision.analysis_run_id
                    == run.run_id
                )
                .limit(1)
            )

            if existing_decision is None:
                db.add(
                    ProductDecision(
                        decision_id=
                            str(uuid4()),

                        analysis_run_id=
                            run.run_id,

                        symbol=
                            run.focus_symbol,

                        outcome=
                            "SIMULATED_WATCH",

                        directive=
                            "NO_AUTHORITY_FAKE_TRANSPORT",

                        decision_kind=
                            "SIMULATION_ONLY",

                        is_canonical=
                            False,

                        created_at=now,
                    )
                )

                db.flush()

        else:
            run.current_stage = (
                LANES[next_index]
            )

        db.flush()

        return _run_payload(
            db,
            run,
        )
