from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
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

from aic.council.bounded_request import (
    assert_bounded_request_invariants,
    build_bounded_initial_request,
)
from aic.council.initial_runtime_cost_v02 import (
    _decimal_text,
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from aic.council.model_policy import (
    INITIAL_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelStage,
    STAGE_MAX_OUTPUT_TOKENS,
)
from aic.council.models import (
    CouncilInputBundle,
    CouncilLane,
)
from aic.council.request import (
    CouncilRequestStage,
)
from aic.domain.canonical import (
    canonical_decimal,
    canonical_sha256,
)

from .product_state import (
    Base,
    InvestmentSession,
    SessionLocal,
    init_product_state,
)


PACKAGE = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]

B2_PATH = (
    REPO_ROOT
    / "config"
    / "event"
    / "b2_real_event_handoff_v0_1.json"
)

BARS_PATH = (
    PACKAGE
    / "data"
    / "historical_bars_snapshot_v1.json"
)

CANDIDATES = (
    "NVDA",
    "MSFT",
    "META",
)

INITIAL_STAGES = (
    (
        CouncilRequestStage.BULL_INITIAL,
        CouncilLane.BULL,
    ),
    (
        CouncilRequestStage.BEAR_INITIAL,
        CouncilLane.BEAR,
    ),
    (
        CouncilRequestStage.RED_TEAM_INITIAL,
        CouncilLane.RED_TEAM,
    ),
)

DATA_GAPS = (
    "V62B_CURRENT_NEWS_NOT_CAPTURED",
    "V62B_VALUATION_NOT_REFRESHED",
)

MANDATE_VERSION = (
    "MARKET_JURY_V62_MONITORED_SET_MANDATE_v0_1"
)

COUNCIL_POLICY_VERSION = (
    "MARKET_JURY_V62_COUNCIL_POLICY_v0_1"
)

JUDGE_POLICY_VERSION = (
    "MARKET_JURY_V62_JUDGE_POLICY_v0_1"
)

PREFLIGHT_VERSION = (
    "MARKET_JURY_V62B_INITIAL_PREFLIGHT_v0_1"
)

PREFLIGHT_STATUS = (
    "READY_FOR_OWNER_APPROVAL"
)


class InitialStagePreflight(Base):
    __tablename__ = "initial_stage_preflights"

    preflight_id: Mapped[str] = mapped_column(
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

    code_commit_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        index=True,
    )

    evidence_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    request_set_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    artifact_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    model: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    reasoning_effort: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )

    call_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    max_cost_usd: Mapped[str] = mapped_column(
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


def normalize_external_numbers(
    value: Any,
) -> Any:
    if isinstance(value, float):
        return canonical_decimal(
            Decimal(str(value))
        )

    if isinstance(value, Decimal):
        return canonical_decimal(value)

    if isinstance(value, Mapping):
        return {
            str(key):
                normalize_external_numbers(child)
            for key, child
            in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            normalize_external_numbers(child)
            for child in value
        ]

    return value


def assert_no_binary_float(
    value: Any,
    *,
    path: str = "$",
) -> None:
    if isinstance(value, float):
        raise ValueError(
            f"binary float remains at {path}"
        )

    if isinstance(value, Mapping):
        for key, child in value.items():
            assert_no_binary_float(
                child,
                path=f"{path}.{key}",
            )

    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_binary_float(
                child,
                path=f"{path}[{index}]",
            )


def init_initial_preflight_state() -> None:
    init_product_state()


def resolve_code_commit_sha() -> str:
    configured = os.environ.get(
        "MARKET_JURY_CODE_SHA",
        "",
    ).strip()

    if re.fullmatch(
        r"[0-9a-f]{40}",
        configured,
    ):
        return configured

    try:
        value = subprocess.check_output(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            cwd=REPO_ROOT,
            text=True,
        ).strip()

    except (
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        raise RuntimeError(
            "Unable to resolve Market Jury code SHA."
        ) from exc

    if not re.fullmatch(
        r"[0-9a-f]{40}",
        value,
    ):
        raise RuntimeError(
            "Market Jury code SHA is invalid."
        )

    return value


def tracked_worktree_clean() -> bool:
    try:
        unstaged = subprocess.run(
            [
                "git",
                "diff",
                "--quiet",
            ],
            cwd=REPO_ROOT,
            check=False,
        ).returncode

        staged = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--quiet",
            ],
            cwd=REPO_ROOT,
            check=False,
        ).returncode

    except OSError:
        return False

    return (
        unstaged == 0
        and staged == 0
    )


def active_session_no_touch() -> dict[str, str]:
    init_initial_preflight_state()

    with SessionLocal() as db:
        row = db.scalar(
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

        if row is None:
            raise RuntimeError(
                "No active Investment Session."
            )

        if row.symbol not in CANDIDATES:
            raise RuntimeError(
                "Unsupported active session symbol."
            )

        return {
            "session_id":
                row.session_id,

            "symbol":
                row.symbol,

            "status":
                row.status,
        }


def _candidate_map(
    handoff: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = handoff.get("candidates")

    if not isinstance(rows, list):
        raise ValueError(
            "B2 candidate list missing."
        )

    result: dict[str, dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            continue

        symbol = row.get("symbol")

        if symbol in CANDIDATES:
            result[str(symbol)] = dict(row)

    if set(result) != set(CANDIDATES):
        raise ValueError(
            "B2 monitored candidate set drift."
        )

    return result


def _safe_account_context(
    account_payload: Mapping[str, Any],
) -> dict[str, Any]:
    account = account_payload.get("account")
    positions = account_payload.get(
        "positions"
    )

    if not isinstance(account, Mapping):
        raise ValueError(
            "Live account object missing."
        )

    if not isinstance(positions, list):
        raise ValueError(
            "Live positions array missing."
        )

    return normalize_external_numbers(
        {
            "provider":
                account_payload.get("provider"),

            "environment":
                account_payload.get("environment"),

            "mode":
                account_payload.get("mode"),

            "received_at_utc":
                account_payload.get(
                    "received_at_utc"
                ),

            "account":
                dict(account),

            "position_count":
                account_payload.get(
                    "position_count",
                    len(positions),
                ),

            "positions":
                positions,
        }
    )


def _material_claims(
    *,
    symbol: str,
    b2: Mapping[str, Any],
    live_symbol: Mapping[str, Any],
    bar_count: int,
) -> list[dict[str, Any]]:
    return [
        {
            "material_claim_id":
                f"V62B_{symbol}_SEC_SOURCE",

            "claim_kind":
                "SOURCE_IDENTITY",

            "text":
                (
                    f"{symbol} tracked B2 evidence "
                    "is bound to SEC accession "
                    f"{b2.get('sec_accession')}."
                ),

            "source_ref":
                b2.get("sec_evidence_id"),
        },
        {
            "material_claim_id":
                f"V62B_{symbol}_LIVE_MARKET",

            "claim_kind":
                "LIVE_MARKET_OBSERVATION",

            "text":
                (
                    f"{symbol} has a read-only "
                    "Alpaca IEX market snapshot "
                    "captured for this analysis."
                ),

            "source_ref":
                "ALPACA_IEX_LIVE_SNAPSHOT",

            "observation":
                dict(live_symbol),
        },
        {
            "material_claim_id":
                f"V62B_{symbol}_HISTORICAL_BARS",

            "claim_kind":
                "HISTORICAL_MARKET_EVIDENCE",

            "text":
                (
                    f"{symbol} includes "
                    f"{bar_count} persisted "
                    "historical bars in the "
                    "supplied packet."
                ),

            "source_ref":
                (
                    "MARKET_JURY_"
                    "HISTORICAL_BARS_SNAPSHOT_v1"
                ),
        },
    ]


def _live_computed_values(
    *,
    symbol: str,
    live_symbol: Mapping[str, Any],
) -> list[dict[str, Any]]:
    fields = (
        (
            "LATEST_TRADE_PRICE",
            "latest_trade_price",
            "USD",
        ),
        (
            "BID_PRICE",
            "bid_price",
            "USD",
        ),
        (
            "ASK_PRICE",
            "ask_price",
            "USD",
        ),
        (
            "DAY_CHANGE_PCT",
            "day_change_pct",
            "PERCENT",
        ),
        (
            "PREVIOUS_CLOSE",
            "previous_close",
            "USD",
        ),
    )

    result = []

    for suffix, key, unit in fields:
        value = live_symbol.get(key)

        if value is None:
            continue

        result.append(
            {
                "computed_value_id":
                    f"V62B_{symbol}_{suffix}",

                "metric_id":
                    key,

                "value":
                    value,

                "unit":
                    unit,

                "source_ref":
                    "ALPACA_IEX_LIVE_SNAPSHOT",
            }
        )

    return result


def _request_envelope_payload(
    request,
) -> dict[str, Any]:
    return {
        "request_version":
            request.request_version,

        "prompt_contract_version":
            request.prompt_contract_version,

        "stage":
            request.stage.value,

        "prompt_version":
            request.prompt_version,

        "prompt_hash":
            request.prompt_hash,

        "schema_version":
            request.schema_version,

        "input_hash":
            request.input_hash,

        "model_candidate_key":
            request.model_candidate_key,

        "request_payload":
            dict(request.request_payload),

        "request_hash":
            request.request_hash,
    }


def build_initial_preflight(
    *,
    session: Mapping[str, str],
    market_payload: Mapping[str, Any],
    account_payload: Mapping[str, Any],
    code_commit_sha: str,
    provider_read_delta: Mapping[str, int],
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(
        r"[0-9a-f]{40}",
        code_commit_sha,
    ):
        raise ValueError(
            "Exact code commit SHA required."
        )

    if (
        market_payload.get("mode")
        != "LIVE_READ_ONLY"
        or market_payload.get("provider")
        != "ALPACA"
    ):
        raise ValueError(
            "Market payload is not Alpaca read-only."
        )

    if (
        account_payload.get("mode")
        != "LIVE_READ_ONLY"
        or account_payload.get("provider")
        != "ALPACA"
    ):
        raise ValueError(
            "Account payload is not Alpaca read-only."
        )

    for payload, label in (
        (market_payload, "market"),
        (account_payload, "account"),
    ):
        if payload.get("broker_writes") != 0:
            raise ValueError(
                f"{label} broker-write boundary drift."
            )

        if payload.get("orders_created") != 0:
            raise ValueError(
                f"{label} order boundary drift."
            )

        if payload.get("model_calls") != 0:
            raise ValueError(
                f"{label} model-call boundary drift."
            )

        if (
            payload.get("live_money")
            != "PROHIBITED"
        ):
            raise ValueError(
                f"{label} live-money boundary drift."
            )

    market = normalize_external_numbers(
        dict(market_payload)
    )

    account = normalize_external_numbers(
        dict(account_payload)
    )

    assert_no_binary_float(
        market,
        path="$MARKET",
    )

    assert_no_binary_float(
        account,
        path="$ACCOUNT",
    )

    now = (
        captured_at
        if captured_at is not None
        else _now()
    )

    if (
        now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError(
            "captured_at must be timezone-aware."
        )

    now = now.astimezone(timezone.utc)

    now_text = (
        now.isoformat(
            timespec="seconds"
        )
        .replace(
            "+00:00",
            "Z",
        )
    )

    handoff = json.loads(
        B2_PATH.read_text(
            encoding="utf-8"
        )
    )

    bars_raw = json.loads(
        BARS_PATH.read_text(
            encoding="utf-8"
        )
    )

    bars_artifact = (
        normalize_external_numbers(
            bars_raw
        )
    )

    assert_no_binary_float(
        bars_artifact,
        path="$BARS",
    )

    if (
        handoff.get("top3")
        != list(CANDIDATES)
    ):
        raise ValueError(
            "B2 candidate order drift."
        )

    if (
        handoff.get("handoff_hash")
        != canonical_sha256(
            handoff,
            exclude_fields=(
                "handoff_hash",
            ),
        )
    ):
        raise ValueError(
            "B2 handoff hash drift."
        )

    if (
        bars_artifact.get(
            "artifact_version"
        )
        != (
            "MARKET_JURY_"
            "HISTORICAL_BARS_SNAPSHOT_v1"
        )
    ):
        raise ValueError(
            "Historical bars version drift."
        )

    b2_by_symbol = _candidate_map(
        handoff
    )

    bars_root = bars_artifact.get(
        "bars"
    )

    market_symbols = market.get(
        "symbols"
    )

    if not isinstance(
        bars_root,
        Mapping,
    ):
        raise ValueError(
            "Historical bars root missing."
        )

    if not isinstance(
        market_symbols,
        Mapping,
    ):
        raise ValueError(
            "Live market symbols missing."
        )

    portfolio_context = (
        _safe_account_context(
            account
        )
    )

    read_delta = {
        "market_gets":
            int(
                provider_read_delta.get(
                    "market_gets",
                    0,
                )
            ),

        "account_gets":
            int(
                provider_read_delta.get(
                    "account_gets",
                    0,
                )
            ),

        "positions_gets":
            int(
                provider_read_delta.get(
                    "positions_gets",
                    0,
                )
            ),
    }

    if any(
        value < 0
        for value in read_delta.values()
    ):
        raise ValueError(
            "Provider read delta invalid."
        )

    evidence_root = {
        "artifact_version":
            (
                "MARKET_JURY_V62B_"
                "SESSION_EVIDENCE_v0_1"
            ),

        "code_commit_sha":
            code_commit_sha,

        "session":
            dict(session),

        "focus_symbol":
            session["symbol"],

        "candidate_order":
            list(CANDIDATES),

        "captured_at_utc":
            now_text,

        "source_lineage":
            {
                "b2_handoff_hash":
                    handoff[
                        "handoff_hash"
                    ],

                "b2_snapshot_ref":
                    handoff[
                        "b2_snapshot_ref"
                    ],

                "deep_comparison_ref":
                    handoff[
                        "deep_comparison_ref"
                    ],

                "historical_bars_version":
                    bars_artifact[
                        "artifact_version"
                    ],
            },

        "live_market":
            {
                "provider":
                    market.get("provider"),

                "feed":
                    market.get("feed"),

                "received_at_utc":
                    market.get(
                        "received_at_utc"
                    ),

                "cache_hit":
                    market.get(
                        "cache_hit"
                    ),

                "symbols":
                    market_symbols,
            },

        "paper_portfolio_context":
            portfolio_context,

        "account_cache_hit":
            account.get(
                "cache_hit"
            ),

        "data_gap_refs":
            list(DATA_GAPS),

        "provider_read_delta":
            read_delta,

        "model_calls":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",
    }

    assert_no_binary_float(
        evidence_root,
        path="$EVIDENCE",
    )

    evidence_hash = canonical_sha256(
        evidence_root
    )

    evidence_root[
        "evidence_hash"
    ] = evidence_hash

    selected = INITIAL_MODEL_LADDER[1]

    if not (
        selected.candidate_key == "L2"
        and selected.stage
        == CouncilModelStage.INITIAL
        and selected.model
        == "gpt-5.6-terra"
        and selected.reasoning_effort
        == "low"
    ):
        raise ValueError(
            "Initial model selection drift."
        )

    output_cap = (
        STAGE_MAX_OUTPUT_TOKENS[
            CouncilModelStage.INITIAL
        ]
    )

    if output_cap != 4096:
        raise ValueError(
            "Initial output cap drift."
        )

    pricing = (
        load_initial_runtime_pricing()
    )

    request_rows = []
    model_inputs = {}
    bundles = {}

    total_cost = Decimal("0")
    total_input_upper = 0

    for symbol in CANDIDATES:
        b2 = b2_by_symbol[symbol]

        live_symbol = (
            market_symbols.get(symbol)
        )

        if not isinstance(
            live_symbol,
            Mapping,
        ):
            raise ValueError(
                f"Live payload missing {symbol}."
            )

        if (
            live_symbol.get("symbol")
            != symbol
            or live_symbol.get(
                "available"
            )
            is not True
        ):
            raise ValueError(
                f"Live {symbol} snapshot unavailable."
            )

        raw_bars = bars_root.get(
            symbol
        )

        if (
            not isinstance(
                raw_bars,
                list,
            )
            or not raw_bars
        ):
            raise ValueError(
                f"Historical bars missing {symbol}."
            )

        historical_tail = (
            raw_bars[-20:]
        )

        claims = _material_claims(
            symbol=symbol,
            b2=b2,
            live_symbol=live_symbol,
            bar_count=len(
                historical_tail
            ),
        )

        b2_metrics = b2.get(
            "metrics"
        )

        if not isinstance(
            b2_metrics,
            list,
        ):
            raise ValueError(
                f"B2 metrics missing {symbol}."
            )

        computed_values = (
            list(b2_metrics)
            + _live_computed_values(
                symbol=symbol,
                live_symbol=live_symbol,
            )
        )

        material_ids = tuple(
            str(
                item[
                    "material_claim_id"
                ]
            )
            for item in claims
        )

        computed_ids = tuple(
            str(
                item[
                    "computed_value_id"
                ]
            )
            for item in computed_values
        )

        candidate_packet = {
            "packet_version":
                (
                    "MARKET_JURY_V62B_"
                    "CANDIDATE_PACKET_v0_1"
                ),

            "candidate_id":
                symbol,

            "focus_candidate":
                (
                    symbol
                    == session["symbol"]
                ),

            "comparison_universe":
                list(CANDIDATES),

            "tracked_b2_evidence":
                {
                    "sec_accession":
                        b2.get(
                            "sec_accession"
                        ),

                    "sec_source_uri":
                        b2.get(
                            "sec_source_uri"
                        ),

                    "sec_evidence_id":
                        b2.get(
                            "sec_evidence_id"
                        ),

                    "computed_values":
                        b2_metrics,
                },

            "live_market_evidence":
                dict(live_symbol),

            "historical_market_evidence":
                {
                    "source":
                        (
                            "PERSISTED_REAL_"
                            "ALPACA_EVIDENCE"
                        ),

                    "bar_count":
                        len(
                            historical_tail
                        ),

                    "bars":
                        historical_tail,
                },

            "material_claims":
                claims,

            "computed_values":
                computed_values,

            "conflicts":
                [],

            "data_gap_refs":
                list(DATA_GAPS),

            "evidence_constraints":
                {
                    "current_news_captured":
                        False,

                    "valuation_refreshed":
                        False,

                    "new_external_research_allowed":
                        False,

                    "model_may_browse":
                        False,

                    "model_may_use_tools":
                        False,

                    "model_has_trade_authority":
                        False,
                },
        }

        assert_no_binary_float(
            candidate_packet,
            path=f"$PACKET.{symbol}",
        )

        candidate_packet_hash = (
            canonical_sha256(
                candidate_packet
            )
        )

        research_snapshot = {
            "candidate_id":
                symbol,

            "session_evidence_hash":
                evidence_hash,

            "candidate_packet_hash":
                candidate_packet_hash,

            "live_market_received_at_utc":
                market.get(
                    "received_at_utc"
                ),

            "data_gap_refs":
                list(DATA_GAPS),
        }

        research_snapshot_hash = (
            canonical_sha256(
                research_snapshot
            )
        )

        bundle = (
            CouncilInputBundle
            .from_unhashed(
                bundle_id=(
                    "V62B_BUNDLE_"
                    + symbol
                    + "_"
                    + research_snapshot_hash[
                        :12
                    ]
                ),

                candidate_id=
                    symbol,

                candidate_packet_id=(
                    "V62B_PACKET_"
                    + symbol
                    + "_"
                    + candidate_packet_hash[
                        :12
                    ]
                ),

                candidate_packet_hash=
                    candidate_packet_hash,

                research_snapshot_id=(
                    "V62B_RESEARCH_"
                    + symbol
                    + "_"
                    + research_snapshot_hash[
                        :12
                    ]
                ),

                research_snapshot_hash=
                    research_snapshot_hash,

                b2_snapshot_id=
                    str(
                        handoff[
                            "b2_snapshot_ref"
                        ]
                    ),

                deep_comparison_id=
                    str(
                        handoff[
                            "deep_comparison_ref"
                        ]
                    ),

                mandate_version=
                    MANDATE_VERSION,

                council_policy_version=
                    COUNCIL_POLICY_VERSION,

                judge_policy_version=
                    JUDGE_POLICY_VERSION,

                model_policy_version=
                    MODEL_POLICY_VERSION,

                allowed_material_claim_ids=
                    material_ids,

                allowed_computed_value_ids=
                    computed_ids,

                allowed_conflict_ids=
                    (),

                shared_portfolio_context_refs=
                    (
                        "V62B_ALPACA_PAPER_"
                        "PORTFOLIO_CONTEXT",
                    ),

                created_at=
                    now,
            )
        )

        bundles[symbol] = (
            bundle.model_dump(
                mode="json",
                exclude_none=False,
            )
        )

        model_input_base = {
            "model_input_version":
                (
                    "MARKET_JURY_V62B_"
                    "INITIAL_MODEL_INPUT_v0_1"
                ),

            "session_id":
                session[
                    "session_id"
                ],

            "focus_symbol":
                session[
                    "symbol"
                ],

            "candidate_id":
                symbol,

            "comparison_scope":
                "MONITORED_SET_COMPARISON",

            "candidate_order":
                list(CANDIDATES),

            "candidate_packet":
                candidate_packet,

            "paper_portfolio_context":
                portfolio_context,

            "source_lineage":
                {
                    "session_evidence_hash":
                        evidence_hash,

                    "candidate_packet_hash":
                        candidate_packet_hash,

                    "research_snapshot_hash":
                        research_snapshot_hash,

                    "b2_handoff_hash":
                        handoff[
                            "handoff_hash"
                        ],

                    "historical_bars_version":
                        bars_artifact[
                            "artifact_version"
                        ],
                },

            "material_claim_ids":
                list(material_ids),

            "computed_value_ids":
                list(computed_ids),

            "conflict_ids":
                [],

            "data_gap_refs":
                list(DATA_GAPS),

            "authority_boundaries":
                {
                    "risk_authority":
                        False,

                    "approval_authority":
                        False,

                    "execution_authority":
                        False,

                    "broker_write_authority":
                        False,

                    "order_authority":
                        False,

                    "live_money":
                        "PROHIBITED",
                },
        }

        assert_no_binary_float(
            model_input_base,
            path=(
                f"$MODEL_INPUT.{symbol}"
            ),
        )

        model_input_hash = (
            canonical_sha256(
                model_input_base
            )
        )

        model_input = {
            **model_input_base,

            "model_input_hash":
                model_input_hash,
        }

        model_inputs[
            symbol
        ] = model_input

        for stage, lane in INITIAL_STAGES:
            run_ref = (
                "MARKET_JURY_V62B_INITIAL_"
                + symbol
                + "_"
                + lane.value
                + "_L2_"
                + model_input_hash[:12]
            )

            request = (
                build_bounded_initial_request(
                    stage=stage,

                    model_candidate=
                        selected,

                    bundle=
                        bundle,

                    model_run_ref=
                        run_ref,

                    model_input=
                        model_input,

                    allowed_data_gap_refs=
                        DATA_GAPS,
                )
            )

            assert_bounded_request_invariants(
                request
            )

            request_payload = dict(
                request.request_payload
            )

            serialized = json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")

            input_upper = len(
                serialized
            )

            per_call_cost = (
                runtime_cost_upper_bound_usd(
                    model=
                        selected.model,

                    input_tokens_upper_bound=
                        input_upper,

                    output_tokens_upper_bound=
                        output_cap,

                    call_count=
                        1,

                    pricing=
                        pricing,
                )
            )

            total_cost += (
                per_call_cost
            )

            total_input_upper += (
                input_upper
            )

            request_rows.append(
                {
                    "candidate":
                        symbol,

                    "lane":
                        lane.value,

                    "stage":
                        stage.value,

                    "request_envelope":
                        _request_envelope_payload(
                            request
                        ),

                    "request_payload_hash":
                        canonical_sha256(
                            request_payload
                        ),

                    "model_input_hash":
                        model_input_hash,

                    "input_tokens_upper_bound":
                        input_upper,

                    "output_tokens_upper_bound":
                        output_cap,

                    "max_cost_usd":
                        _decimal_text(
                            per_call_cost
                        ),
                }
            )

    if len(request_rows) != 9:
        raise ValueError(
            "Initial request count drift."
        )

    hashes = [
        row[
            "request_envelope"
        ][
            "request_hash"
        ]
        for row in request_rows
    ]

    if len(set(hashes)) != 9:
        raise ValueError(
            "Initial request hashes are not unique."
        )

    request_set_hash = (
        canonical_sha256(
            [
                {
                    "candidate":
                        row["candidate"],

                    "lane":
                        row["lane"],

                    "request_hash":
                        row[
                            "request_envelope"
                        ][
                            "request_hash"
                        ],

                    "request_payload_hash":
                        row[
                            "request_payload_hash"
                        ],
                }
                for row in request_rows
            ]
        )
    )

    preflight_id = str(
        uuid4()
    )

    artifact = {
        "artifact_version":
            PREFLIGHT_VERSION,

        "status":
            PREFLIGHT_STATUS,

        "preflight_id":
            preflight_id,

        "code_commit_sha":
            code_commit_sha,

        "created_at_utc":
            now_text,

        "session_id":
            session[
                "session_id"
            ],

        "focus_symbol":
            session[
                "symbol"
            ],

        "candidate_order":
            list(CANDIDATES),

        "session_evidence_hash":
            evidence_hash,

        "session_evidence":
            evidence_root,

        "bundles_by_candidate":
            bundles,

        "model_inputs_by_candidate":
            model_inputs,

        "request_set_hash":
            request_set_hash,

        "initial_requests":
            request_rows,

        "call_count_planned":
            9,

        "call_count_ceiling":
            9,

        "model":
            selected.model,

        "reasoning_effort":
            selected.reasoning_effort,

        "maximum_output_tokens_per_call":
            output_cap,

        "maximum_output_tokens_total":
            output_cap * 9,

        "estimated_input_tokens_upper_bound_total":
            total_input_upper,

        "estimated_max_cost_usd":
            _decimal_text(
                total_cost
            ),

        "pricing_version":
            pricing[
                "pricing_version"
            ],

        "pricing_hash":
            pricing[
                "pricing_hash"
            ],

        "numeric_normalization":
            (
                "EXTERNAL_BINARY_FLOAT_"
                "TO_CANONICAL_DECIMAL_STRING"
            ),

        "input_upper_bound_method":
            (
                "CONSERVATIVE_ONE_UTF8_BYTE_"
                "PER_INPUT_TOKEN_AND_ALL_INPUT_"
                "CACHE_WRITE_BILLED"
            ),

        "owner_approval_required":
            True,

        "owner_approval_granted":
            False,

        "model_calls_authorized":
            False,

        "paid_execution_ready":
            False,

        "automatic_retries":
            0,

        "model_calls_this_step":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "cost_usd_this_step":
            "0",

        "live_money":
            "PROHIBITED",

        "next_gate":
            "EXPLICIT_OWNER_APPROVAL",
    }

    artifact[
        "artifact_hash"
    ] = canonical_sha256(
        artifact,
        exclude_fields=(
            "artifact_hash",
        ),
    )

    return artifact


def _public_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = payload.get(
        "session_evidence",
        {},
    )

    if not isinstance(
        evidence,
        Mapping,
    ):
        evidence = {}

    live_market = evidence.get(
        "live_market",
        {},
    )

    if not isinstance(
        live_market,
        Mapping,
    ):
        live_market = {}

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

        "preflight_id":
            payload[
                "preflight_id"
            ],

        "code_commit_sha":
            payload[
                "code_commit_sha"
            ],

        "created_at_utc":
            payload[
                "created_at_utc"
            ],

        "session_id":
            payload[
                "session_id"
            ],

        "focus_symbol":
            payload[
                "focus_symbol"
            ],

        "candidate_order":
            list(
                payload[
                    "candidate_order"
                ]
            ),

        "session_evidence_hash":
            payload[
                "session_evidence_hash"
            ],

        "request_set_hash":
            payload[
                "request_set_hash"
            ],

        "artifact_hash":
            payload[
                "artifact_hash"
            ],

        "call_count_planned":
            payload[
                "call_count_planned"
            ],

        "call_count_ceiling":
            payload[
                "call_count_ceiling"
            ],

        "model":
            payload[
                "model"
            ],

        "reasoning_effort":
            payload[
                "reasoning_effort"
            ],

        "estimated_input_tokens_upper_bound_total":
            payload[
                "estimated_input_tokens_upper_bound_total"
            ],

        "maximum_output_tokens_total":
            payload[
                "maximum_output_tokens_total"
            ],

        "estimated_max_cost_usd":
            payload[
                "estimated_max_cost_usd"
            ],

        "pricing_version":
            payload[
                "pricing_version"
            ],

        "market_received_at_utc":
            live_market.get(
                "received_at_utc"
            ),

        "market_cache_hit":
            live_market.get(
                "cache_hit"
            ),

        "account_cache_hit":
            evidence.get(
                "account_cache_hit"
            ),

        "provider_read_delta":
            evidence.get(
                "provider_read_delta",
                {},
            ),

        "owner_approval_required":
            True,

        "owner_approval_granted":
            False,

        "model_calls_authorized":
            False,

        "paid_execution_ready":
            False,

        "automatic_retries":
            0,

        "model_calls_this_step":
            0,

        "broker_writes":
            0,

        "alpaca_orders":
            0,

        "live_money":
            "PROHIBITED",
    }


def create_initial_preflight(
    *,
    market_payload: Mapping[str, Any],
    account_payload: Mapping[str, Any],
    code_commit_sha: str,
    provider_read_delta: Mapping[str, int],
) -> dict[str, Any]:
    init_initial_preflight_state()

    session = active_session_no_touch()

    payload = build_initial_preflight(
        session=session,
        market_payload=market_payload,
        account_payload=account_payload,
        code_commit_sha=code_commit_sha,
        provider_read_delta=provider_read_delta,
    )

    created_at = datetime.fromisoformat(
        str(
            payload[
                "created_at_utc"
            ]
        ).replace(
            "Z",
            "+00:00",
        )
    )

    with SessionLocal.begin() as db:
        row = InitialStagePreflight(
            preflight_id=
                payload[
                    "preflight_id"
                ],

            session_id=
                payload[
                    "session_id"
                ],

            focus_symbol=
                payload[
                    "focus_symbol"
                ],

            code_commit_sha=
                payload[
                    "code_commit_sha"
                ],

            status=
                payload[
                    "status"
                ],

            evidence_hash=
                payload[
                    "session_evidence_hash"
                ],

            request_set_hash=
                payload[
                    "request_set_hash"
                ],

            artifact_hash=
                payload[
                    "artifact_hash"
                ],

            model=
                payload[
                    "model"
                ],

            reasoning_effort=
                payload[
                    "reasoning_effort"
                ],

            call_count=
                payload[
                    "call_count_planned"
                ],

            max_cost_usd=
                payload[
                    "estimated_max_cost_usd"
                ],

            payload_json=
                _json(payload),

            created_at=
                created_at,
        )

        db.add(row)

    return _public_payload(
        payload
    )


def load_initial_preflight_payload(
    preflight_id: str,
) -> dict[str, Any]:
    init_initial_preflight_state()

    with SessionLocal() as db:
        row = db.get(
            InitialStagePreflight,
            preflight_id,
        )

        if row is None:
            raise ValueError(
                "Initial preflight not found."
            )

        payload = json.loads(
            row.payload_json
        )

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "Initial preflight payload malformed."
            )

        if (
            payload.get(
                "artifact_hash"
            )
            != canonical_sha256(
                payload,
                exclude_fields=(
                    "artifact_hash",
                ),
            )
        ):
            raise ValueError(
                "Initial preflight artifact hash mismatch."
            )

        return payload


def latest_initial_preflight() -> dict[str, Any]:
    init_initial_preflight_state()

    session = active_session_no_touch()

    with SessionLocal() as db:
        row = db.scalar(
            select(
                InitialStagePreflight
            )
            .where(
                InitialStagePreflight.session_id
                == session[
                    "session_id"
                ]
            )
            .where(
                InitialStagePreflight.focus_symbol
                == session[
                    "symbol"
                ]
            )
            .order_by(
                InitialStagePreflight.created_at.desc()
            )
            .limit(1)
        )

        if row is None:
            return {
                "available":
                    False,

                "session_id":
                    session[
                        "session_id"
                    ],

                "focus_symbol":
                    session[
                        "symbol"
                    ],

                "owner_approval_granted":
                    False,

                "model_calls_authorized":
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
                "Initial preflight payload malformed."
            )

        return _public_payload(
            payload
        )
