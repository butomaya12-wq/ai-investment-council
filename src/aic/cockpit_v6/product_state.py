from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Final
from uuid import uuid4

from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)


SUPPORTED_SYMBOLS: Final[tuple[str, ...]] = (
    "NVDA",
    "MSFT",
    "META",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = REPO_ROOT / ".aic-runtime"

DEFAULT_DB_PATH = (
    RUNTIME_DIR
    / "market_jury_product_v1.sqlite3"
)

DB_PATH = Path(
    os.environ.get(
        "MARKET_JURY_PRODUCT_DB",
        str(DEFAULT_DB_PATH),
    )
).expanduser().resolve()


class Base(DeclarativeBase):
    pass


class InvestmentSession(Base):
    __tablename__ = "investment_sessions"

    session_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()

    if value not in SUPPORTED_SYMBOLS:
        raise ValueError(
            "Unsupported Market Jury symbol: "
            f"{value or '<empty>'}. "
            "Allowed: "
            + ", ".join(SUPPORTED_SYMBOLS)
        )

    return value


def _ensure_parent() -> None:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )


def _engine():
    _ensure_parent()

    return create_engine(
        f"sqlite+pysqlite:///{DB_PATH}",
        connect_args={
            "check_same_thread": False,
        },
        future=True,
    )


ENGINE = _engine()

SessionLocal = sessionmaker(
    bind=ENGINE,
    expire_on_commit=False,
)


def init_product_state() -> None:
    Base.metadata.create_all(ENGINE)


def _payload(
    row: InvestmentSession,
) -> dict[str, object]:
    return {
        "session_id":
            row.session_id,

        "symbol":
            row.symbol,

        "status":
            row.status,

        "created_at":
            row.created_at.isoformat(),

        "updated_at":
            row.updated_at.isoformat(),

        "last_opened_at":
            row.last_opened_at.isoformat(),

        "supported_symbols":
            list(SUPPORTED_SYMBOLS),

        "persistence":
            "SQLITE",

        "database_version":
            "MARKET_JURY_PRODUCT_DB_v1",

        "live_money":
            "PROHIBITED",
    }


def _active_row(db) -> InvestmentSession | None:
    statement = (
        select(InvestmentSession)
        .where(
            InvestmentSession.status
            == "ACTIVE"
        )
        .order_by(
            InvestmentSession.updated_at.desc()
        )
        .limit(1)
    )

    return db.scalar(statement)


def get_active_session() -> dict[str, object]:
    init_product_state()

    now = _utc_now()

    with SessionLocal.begin() as db:
        row = _active_row(db)

        if row is None:
            row = InvestmentSession(
                session_id=str(uuid4()),
                symbol="NVDA",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
                last_opened_at=now,
            )

            db.add(row)
            db.flush()

        else:
            row.last_opened_at = now

        payload = _payload(row)

    return payload


def select_active_symbol(
    symbol: str,
) -> dict[str, object]:
    normalized = _normalized_symbol(symbol)

    init_product_state()

    now = _utc_now()

    with SessionLocal.begin() as db:
        row = _active_row(db)

        if row is None:
            row = InvestmentSession(
                session_id=str(uuid4()),
                symbol=normalized,
                status="ACTIVE",
                created_at=now,
                updated_at=now,
                last_opened_at=now,
            )

            db.add(row)
            db.flush()

        else:
            row.symbol = normalized
            row.updated_at = now
            row.last_opened_at = now

        payload = _payload(row)

    return payload
