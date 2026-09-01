"""Offline, test-only B6 approval/revalidation adapter with a fake-only transport."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from aic.b5.competition_v1 import CompetitionV1OptionIntent, TEST_MODE
from aic.domain.canonical import canonical_sha256


APPROVAL_STATE_PATH = (
    "PROPOSED",
    "APPROVAL_REQUIRED",
    "APPROVED",
    "COMMIT_REVALIDATION",
    "READY_FOR_PAPER_SEND",
)


class B6Blocked(ValueError):
    """Fail-closed B6 test adapter rejection."""


@dataclass(frozen=True)
class TestOwnerApproval:
    """A test-only approval that never authenticates a production owner."""

    approval_type: str
    mode: str
    intent_payload_hash: str
    economics_hash: str
    state: str = "APPROVED"


@dataclass(frozen=True)
class CommitRevalidation:
    state: str
    reason: str | None


class FakePaperTransport:
    """Captures a would-submit payload without making a network or broker call."""

    def __init__(self) -> None:
        self.calls = 0

    def would_submit(self, intent: CompetitionV1OptionIntent) -> dict[str, object]:
        self.calls += 1
        return {
            "status": "WOULD_SUBMIT_PAPER_ORDER",
            "environment": "PAPER",
            "action": "BUY_TO_OPEN",
            "option_symbol": intent.option_symbol,
            "qty": intent.quantity,
            "order_type": "LIMIT",
            "limit_price": str(intent.approved_limit_price),
            "time_in_force": "DAY",
            "extended_hours": False,
            "broker_writes": 0,
            "alpaca_orders": 0,
        }


def _economics_hash(intent: CompetitionV1OptionIntent) -> str:
    return canonical_sha256(
        {
            "option_symbol": intent.option_symbol,
            "quantity": intent.quantity,
            "approved_limit_price": str(intent.approved_limit_price),
            "final_decision_hash": intent.final_decision_hash,
            "options_policy_hash": intent.options_policy_hash,
        }
    )


def create_test_approval(intent: CompetitionV1OptionIntent, *, mode: str) -> TestOwnerApproval:
    if mode != TEST_MODE:
        raise B6Blocked("TEST_OWNER_APPROVAL is forbidden outside TEST_FIXTURE mode")
    return TestOwnerApproval(
        approval_type="TEST_OWNER_APPROVAL",
        mode=mode,
        intent_payload_hash=intent.payload_hash,
        economics_hash=_economics_hash(intent),
    )


def validate_approval(intent: CompetitionV1OptionIntent, approval: TestOwnerApproval, *, mode: str) -> None:
    if mode != TEST_MODE or approval.mode != TEST_MODE or approval.approval_type != "TEST_OWNER_APPROVAL":
        raise B6Blocked("test approval authority is invalid outside TEST_FIXTURE")
    if approval.intent_payload_hash != intent.payload_hash or approval.economics_hash != _economics_hash(intent):
        raise B6Blocked("approval does not bind exact option intent")


def commit_revalidate(
    intent: CompetitionV1OptionIntent,
    approval: TestOwnerApproval,
    state: Mapping[str, Any],
    *,
    mode: str,
) -> CommitRevalidation:
    """Perform the bounded commit-time checks before fake transport can be called."""
    try:
        validate_approval(intent, approval, mode=mode)
    except B6Blocked as exc:
        return CommitRevalidation("BLOCK_COMMIT_REVALIDATION", str(exc))
    required_exact = {
        "decision_id": intent.decision_id,
        "final_decision_hash": intent.final_decision_hash,
        "option_symbol": intent.option_symbol,
        "options_policy_hash": intent.options_policy_hash,
        "mandate_version": intent.mandate_version,
        "approved_limit_price": str(intent.approved_limit_price),
        "risk_status": "PASS",
        "no_conflicting_fixture_state": True,
    }
    for key, value in required_exact.items():
        if state.get(key) != value:
            return CommitRevalidation("BLOCK_COMMIT_REVALIDATION", f"commit state drift: {key}")
    if state.get("contract_active") is not True or state.get("contract_tradable") is not True:
        return CommitRevalidation("BLOCK_COMMIT_REVALIDATION", "contract is inactive or untradable")
    if not 21 <= int(state.get("dte", -1)) <= 49:
        return CommitRevalidation("BLOCK_COMMIT_REVALIDATION", "DTE is outside policy")
    bid = Decimal(str(state.get("bid", "0")))
    ask = Decimal(str(state.get("ask", "0")))
    if bid <= 0 or ask <= 0 or ask < bid or (ask - bid) / ask > Decimal("0.10"):
        return CommitRevalidation("BLOCK_COMMIT_REVALIDATION", "commit quote is invalid")
    if int(state.get("quote_age_seconds", 16)) > 15:
        return CommitRevalidation("BLOCK_COMMIT_REVALIDATION", "commit quote is stale")
    return CommitRevalidation("READY_FOR_PAPER_SEND", None)


def send_after_commit(
    intent: CompetitionV1OptionIntent,
    approval: TestOwnerApproval,
    state: Mapping[str, Any],
    transport: FakePaperTransport,
    *,
    mode: str,
) -> tuple[CommitRevalidation, dict[str, object] | None]:
    result = commit_revalidate(intent, approval, state, mode=mode)
    if result.state != "READY_FOR_PAPER_SEND":
        return result, None
    return result, transport.would_submit(intent)
