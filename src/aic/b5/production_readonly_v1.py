"""Production B4-to-B5 read-only selection boundary.

This module consumes only a pinned recovered B4 record and normalized option
market input.  It deliberately contains no provider client and grants neither
execution nor broker-write authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import json
from math import ceil
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from aic.b5.competition_v1 import (
    B5Blocked,
    OptionContract,
    calculate_premium_risk,
    relative_spread_from_quote,
    select_contract,
)
from aic.domain.canonical import canonical_sha256
from aic.research.mandate import COMPETITION_MANDATE_VERSION, COMPETITION_OPTIONS_POLICY_HASH


RECOVERED_B4_ARTIFACT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_CAPTURED_RESPONSE_RECOVERY_v0_1"
RECOVERED_B4_ARTIFACT_HASH = "f9a9e08a30b58ebf6fcb358c2b35a82717682ddef3ac5fd58c912d518d3fadf0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_NVDA_OPTION_SYMBOL_RE = re.compile(r"NVDA\d{6}[CP]\d{8}")


class B5ProductionBlocked(ValueError):
    """A fail-closed production B5 boundary or input rejection."""


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise B5ProductionBlocked(f"{field} must be an object")
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B5ProductionBlocked(f"{field} must be a non-empty trimmed string")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise B5ProductionBlocked(f"{field} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - Decimal exposes several error types
        raise B5ProductionBlocked(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise B5ProductionBlocked(f"{field} must be a finite decimal")
    return result


def _non_negative_decimal(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise B5ProductionBlocked(f"{field} must not be negative")
    return result


def _positive_decimal(value: object, field: str) -> Decimal:
    result = _decimal(value, field)
    if result <= 0:
        raise B5ProductionBlocked(f"{field} must be positive")
    return result


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise B5ProductionBlocked(f"{field} must be a positive integer")
    return value


def _parse_timestamp(value: object, field: str) -> datetime:
    text = _require_string(value, field).replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise B5ProductionBlocked(f"{field} must be an RFC3339 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise B5ProductionBlocked(f"{field} must be timezone-aware")
    return result.astimezone(UTC)


def _parse_date(value: object, field: str) -> date:
    text = _require_string(value, field)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise B5ProductionBlocked(f"{field} must be an ISO date") from exc


@dataclass(frozen=True)
class RecoveredB4Decision:
    artifact_hash: str
    record_hash: str
    judge_proposal_hash: str
    primary_candidate_id: str


@dataclass(frozen=True)
class B5Entry:
    status: str
    b4_artifact_hash: str
    b4_record_hash: str
    b4_judge_proposal_hash: str
    primary_candidate_id: str
    mandate_version: str
    options_policy_hash: str
    b5_code_commit_sha: str
    execution_authority: bool
    broker_write_authority: bool
    live_execution: bool
    entry_hash: str

    def payload(self, *, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "b4_artifact_hash": self.b4_artifact_hash,
            "b4_record_hash": self.b4_record_hash,
            "b4_judge_proposal_hash": self.b4_judge_proposal_hash,
            "primary_candidate_id": self.primary_candidate_id,
            "mandate_version": self.mandate_version,
            "options_policy_hash": self.options_policy_hash,
            "b5_code_commit_sha": self.b5_code_commit_sha,
            "execution_authority": self.execution_authority,
            "broker_write_authority": self.broker_write_authority,
            "live_execution": self.live_execution,
        }
        if include_hash:
            result["entry_hash"] = self.entry_hash
        return result


@dataclass(frozen=True)
class NormalizedAccount:
    account_equity: Decimal
    cash_available: Decimal
    current_same_underlying_premium_risk: Decimal
    current_aggregate_option_premium_risk: Decimal
    broker_capacity: Decimal

    def risk_mapping(self) -> dict[str, Decimal]:
        return {
            "account_equity": self.account_equity,
            "cash_available": self.cash_available,
            "current_same_underlying_premium_risk": self.current_same_underlying_premium_risk,
            "current_aggregate_option_premium_risk": self.current_aggregate_option_premium_risk,
            "broker_capacity": self.broker_capacity,
        }


@dataclass(frozen=True)
class NormalizedOptionContract:
    selector_contract: OptionContract
    quote_timestamp: datetime
    open_interest_as_of_date: date


@dataclass(frozen=True)
class NormalizedOptionMarketInput:
    snapshot_timestamp: datetime
    as_of_date: date
    underlying_symbol: str
    account: NormalizedAccount
    contracts: tuple[NormalizedOptionContract, ...]


@dataclass(frozen=True)
class B5Candidate:
    status: str
    b5_entry_hash: str
    underlying: str
    option_symbol: str
    expiration: str
    strike: Decimal
    bid: Decimal
    ask: Decimal
    delta: Decimal
    open_interest: int
    relative_spread: Decimal
    dte: int
    quantity: int
    premium_per_contract: Decimal
    max_loss_usd: Decimal
    same_underlying_risk_after: Decimal
    aggregate_option_risk_after: Decimal
    cash_reserve_after: Decimal
    environment: str
    order_type: str
    time_in_force: str
    extended_hours: bool
    execution_authority: bool
    broker_write_authority: bool


@dataclass(frozen=True)
class B5SelectionResult:
    status: str
    reason: str | None
    entry: B5Entry | None
    candidate: B5Candidate | None


def parse_recovered_b4_artifact(payload: object) -> RecoveredB4Decision:
    """Validate B4 recovery integrity and its terminal INVEST/NVDA semantics."""
    artifact = _require_mapping(payload, "B4 artifact")
    if artifact.get("artifact_version") != RECOVERED_B4_ARTIFACT_VERSION:
        raise B5ProductionBlocked("B4 artifact version mismatch")
    artifact_hash = artifact.get("artifact_hash")
    if artifact_hash != RECOVERED_B4_ARTIFACT_HASH or not isinstance(artifact_hash, str):
        raise B5ProductionBlocked("B4 artifact hash is not the recovered authority")
    if canonical_sha256(dict(artifact), exclude_fields=("artifact_hash",)) != artifact_hash:
        raise B5ProductionBlocked("B4 artifact self-hash is invalid")
    if artifact.get("status") != "B4_CAPTURED_RESPONSE_RECOVERED_ZERO_CALL":
        raise B5ProductionBlocked("B4 recovery status mismatch")
    if artifact.get("repaired_validation") != "PASS":
        raise B5ProductionBlocked("B4 repaired validation did not pass")
    for field in ("recovery_model_calls", "broker_writes", "alpaca_orders"):
        if artifact.get(field) != 0:
            raise B5ProductionBlocked(f"B4 {field} must be zero")

    record = _require_mapping(artifact.get("processed_record"), "B4 processed_record")
    if record.get("outcome") != "INVEST" or record.get("next_directive") != "PROMOTE_FINAL_DECISION":
        raise B5ProductionBlocked("B4 terminal result is not eligible for B5")
    record_hash = _require_string(record.get("record_hash"), "B4 record_hash")
    if _SHA256_RE.fullmatch(record_hash) is None:
        raise B5ProductionBlocked("B4 record_hash must be a SHA-256")
    proposal = _require_mapping(record.get("frozen_judge_proposal"), "B4 frozen_judge_proposal")
    judge_proposal_hash = _require_string(proposal.get("judge_proposal_hash"), "B4 judge_proposal_hash")
    if _SHA256_RE.fullmatch(judge_proposal_hash) is None:
        raise B5ProductionBlocked("B4 judge_proposal_hash must be a SHA-256")
    draft = _require_mapping(proposal.get("draft"), "B4 frozen judge draft")
    if draft.get("outcome") != "INVEST" or draft.get("next_directive") != "PROMOTE_FINAL_DECISION":
        raise B5ProductionBlocked("B4 draft terminal result is not eligible for B5")
    if draft.get("primary_candidate_id") != "NVDA":
        raise B5ProductionBlocked("B4 primary candidate is not NVDA")
    if draft.get("research_reopen_required") is not False:
        raise B5ProductionBlocked("B4 research reopen remains required")
    if draft.get("blocking_reason_codes") != []:
        raise B5ProductionBlocked("B4 blocking reasons are present")
    if draft.get("execution_authority") is not False:
        raise B5ProductionBlocked("B4 must not grant execution authority")
    return RecoveredB4Decision(artifact_hash, record_hash, judge_proposal_hash, "NVDA")


def load_recovered_b4_artifact(path: Path) -> RecoveredB4Decision:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B5ProductionBlocked("B4 recovered artifact cannot be loaded") from exc
    return parse_recovered_b4_artifact(payload)


def create_b5_entry(decision: RecoveredB4Decision, *, b5_code_commit_sha: str) -> B5Entry:
    if _COMMIT_RE.fullmatch(b5_code_commit_sha) is None:
        raise B5ProductionBlocked("B5 code commit SHA must be a lowercase Git SHA")
    values: dict[str, object] = {
        "status": "B5_ENTRY_READY",
        "b4_artifact_hash": decision.artifact_hash,
        "b4_record_hash": decision.record_hash,
        "b4_judge_proposal_hash": decision.judge_proposal_hash,
        "primary_candidate_id": "NVDA",
        "mandate_version": COMPETITION_MANDATE_VERSION,
        "options_policy_hash": COMPETITION_OPTIONS_POLICY_HASH,
        "b5_code_commit_sha": b5_code_commit_sha,
        "execution_authority": False,
        "broker_write_authority": False,
        "live_execution": False,
    }
    draft = B5Entry(entry_hash="", **values)  # type: ignore[arg-type]
    return B5Entry(entry_hash=canonical_sha256(draft.payload(include_hash=False)), **values)  # type: ignore[arg-type]


def normalize_market_input(payload: Mapping[str, Any]) -> NormalizedOptionMarketInput:
    """Normalize one complete read-only NVDA option/account snapshot fail-closed."""
    snapshot_timestamp = _parse_timestamp(payload.get("snapshot_timestamp"), "snapshot_timestamp")
    as_of_date = _parse_date(payload.get("as_of_date"), "as_of_date")
    if snapshot_timestamp.date() != as_of_date:
        raise B5ProductionBlocked("snapshot timestamp and as_of_date disagree")
    if payload.get("underlying_symbol") != "NVDA":
        raise B5ProductionBlocked("production B5 accepts NVDA only")
    account_raw = _require_mapping(payload.get("account"), "account")
    account = NormalizedAccount(
        account_equity=_positive_decimal(account_raw.get("account_equity"), "account_equity"),
        cash_available=_non_negative_decimal(account_raw.get("cash_available"), "cash_available"),
        current_same_underlying_premium_risk=_non_negative_decimal(
            account_raw.get("current_same_underlying_premium_risk"), "current_same_underlying_premium_risk"
        ),
        current_aggregate_option_premium_risk=_non_negative_decimal(
            account_raw.get("current_aggregate_option_premium_risk"), "current_aggregate_option_premium_risk"
        ),
        broker_capacity=_non_negative_decimal(account_raw.get("broker_capacity"), "broker_capacity"),
    )
    raw_contracts = payload.get("option_contracts")
    if not isinstance(raw_contracts, Sequence) or isinstance(raw_contracts, (str, bytes)) or not raw_contracts:
        raise B5ProductionBlocked("option_contracts must be a non-empty array")
    normalized: list[NormalizedOptionContract] = []
    for index, raw in enumerate(raw_contracts):
        item = _require_mapping(raw, f"option_contracts[{index}]")
        symbol = _require_string(item.get("option_symbol"), f"option_contracts[{index}].option_symbol")
        if _NVDA_OPTION_SYMBOL_RE.fullmatch(symbol) is None:
            raise B5ProductionBlocked("option symbol is malformed or not NVDA")
        quote_timestamp = _parse_timestamp(item.get("quote_timestamp"), f"option_contracts[{index}].quote_timestamp")
        quote_age = (snapshot_timestamp - quote_timestamp).total_seconds()
        if quote_age < 0:
            raise B5ProductionBlocked("quote timestamp is ambiguous")
        quote_age_seconds = ceil(quote_age)
        if quote_age_seconds > 60:
            raise B5ProductionBlocked("selection quote is stale")
        oi_as_of = _parse_date(item.get("open_interest_as_of_date"), f"option_contracts[{index}].open_interest_as_of_date")
        if oi_as_of > as_of_date:
            raise B5ProductionBlocked("open interest timestamp is in the future")
        if item.get("open_interest_current_for_latest_completed_session") is not True:
            raise B5ProductionBlocked("open interest is not current for latest completed session")
        contract_type = _require_string(item.get("contract_type"), f"option_contracts[{index}].contract_type")
        opening_direction = _require_string(item.get("opening_direction"), f"option_contracts[{index}].opening_direction")
        expiration = _parse_date(item.get("expiration"), f"option_contracts[{index}].expiration")
        multiplier = _positive_int(item.get("multiplier"), f"option_contracts[{index}].multiplier")
        bid = _positive_decimal(item.get("bid"), f"option_contracts[{index}].bid")
        ask = _positive_decimal(item.get("ask"), f"option_contracts[{index}].ask")
        delta = _decimal(item.get("delta"), f"option_contracts[{index}].delta")
        open_interest = _positive_int(item.get("open_interest"), f"option_contracts[{index}].open_interest")
        if item.get("active") is not True or item.get("tradable") is not True or item.get("greeks_present") is not True:
            raise B5ProductionBlocked("option must be active, tradable, and have greeks")
        normalized.append(
            NormalizedOptionContract(
                selector_contract=OptionContract(
                    option_symbol=symbol,
                    contract_type=contract_type,
                    opening_direction=opening_direction,
                    expiration=expiration,
                    strike=_positive_decimal(item.get("strike"), f"option_contracts[{index}].strike"),
                    multiplier=multiplier,
                    bid=bid,
                    ask=ask,
                    delta=delta,
                    open_interest=open_interest,
                    active=item["active"],
                    tradable=item["tradable"],
                    greeks_present=item["greeks_present"],
                    quote_age_seconds=quote_age_seconds,
                    open_interest_current_for_latest_completed_session=True,
                ),
                quote_timestamp=quote_timestamp,
                open_interest_as_of_date=oi_as_of,
            )
        )
    return NormalizedOptionMarketInput(snapshot_timestamp, as_of_date, "NVDA", account, tuple(normalized))


def select_readonly_b5(entry: B5Entry, market: NormalizedOptionMarketInput) -> B5SelectionResult:
    """Run frozen selector/risk semantics without creating any execution capability."""
    if entry.execution_authority or entry.broker_write_authority or entry.live_execution:
        return B5SelectionResult("BLOCK_B5_ENTRY_AUTHORITY", "BLOCK_B5_ENTRY_AUTHORITY", entry, None)
    try:
        selected = select_contract([item.selector_contract for item in market.contracts], as_of=market.as_of_date)
    except B5Blocked:
        return B5SelectionResult("BLOCK_INCOMPLETE_OPTION_MARKET", "BLOCK_INCOMPLETE_OPTION_MARKET", entry, None)
    premium_per_contract = selected.ask * Decimal(selected.multiplier)
    risk = calculate_premium_risk(market.account.risk_mapping(), premium_per_contract)
    if risk.status != "PASS" or risk.quantity < 1:
        reason = risk.reason or "BLOCK_RISK"
        return B5SelectionResult(reason, reason, entry, None)
    candidate = B5Candidate(
        status="B5_READY_FOR_APPROVAL",
        b5_entry_hash=entry.entry_hash,
        underlying="NVDA",
        option_symbol=selected.option_symbol,
        expiration=selected.expiration.isoformat(),
        strike=selected.strike,
        bid=selected.bid,
        ask=selected.ask,
        delta=selected.delta if selected.delta is not None else Decimal("0"),
        open_interest=selected.open_interest,
        relative_spread=relative_spread_from_quote(selected.bid, selected.ask),
        dte=(selected.expiration - market.as_of_date).days,
        quantity=risk.quantity,
        premium_per_contract=premium_per_contract,
        max_loss_usd=risk.max_loss_usd,
        same_underlying_risk_after=risk.same_underlying_risk_after,
        aggregate_option_risk_after=risk.aggregate_option_risk_after,
        cash_reserve_after=risk.cash_reserve_after,
        environment="PAPER",
        order_type="LIMIT",
        time_in_force="DAY",
        extended_hours=False,
        execution_authority=False,
        broker_write_authority=False,
    )
    return B5SelectionResult("B5_READY_FOR_APPROVAL", None, entry, candidate)
