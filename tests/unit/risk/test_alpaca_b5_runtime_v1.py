from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from aic.risk.alpaca_b5_runtime_v1 import (
    AlpacaReadOnlyCredentials,
    run_b5_read_only_production_path,
)
from aic.risk.options_competition_v1 import (
    B5CompetitionOptionsError,
    load_competition_options_policy,
)


NOW = datetime(2026, 8, 31, 15, 0, 0, tzinfo=timezone.utc)
AS_OF = date(2026, 8, 31)
LATEST_COMPLETED_SESSION = date(2026, 8, 28)
POLICY = load_competition_options_policy(
    Path("config/event/competition_v1_options_policy.json")
)
CREDS = AlpacaReadOnlyCredentials(key_id="paper-key", secret_key="paper-secret")
OPTION = "NVDA261005C00220000"


def final_decision(*, outcome: str = "INVEST"):
    return {
        "decision_id": "decision:runtime",
        "outcome": outcome,
        "primary_candidate_id": "candidate:NVDA",
        "evidence_status": "COMPLETE",
        "blocking_reason_codes": [] if outcome == "INVEST" else ["WATCH_ONLY"],
        "final_decision_hash": "d" * 64,
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "risk_result_id": None,
        "policy_refs": {
            "evidence_policy": {
                "policy_id": "EVIDENCE",
                "version": "v1",
                "policy_hash": "1" * 64,
            },
            "council_policy": {
                "policy_id": "COUNCIL",
                "version": "v1",
                "policy_hash": "2" * 64,
            },
        },
        "decision_lifecycle_policy_ref": {
            "policy_id": "ALPACA_2026_COMPETITION_DECISION_LIFECYCLE",
            "version": "ALPACA_COMPETITION_V1_2026_08_29",
            "policy_hash": "3" * 64,
        },
    }


def account_payload():
    return {
        "id": "paper-account-1",
        "status": "ACTIVE",
        "trading_blocked": False,
        "trade_suspended_by_user": False,
        "account_blocked": False,
        "equity": "100000",
        "cash": "80000",
        "buying_power": "90000",
        "non_marginable_buying_power": "85000",
    }


def option_contracts_payload(*, token=None):
    payload = {
        "option_contracts": [
            {
                "symbol": OPTION,
                "underlying_symbol": "NVDA",
                "type": "call",
                "status": "active",
                "tradable": True,
                "expiration_date": "2026-10-05",
                "style": "american",
                "strike_price": "220",
                "size": "100",
                "open_interest": "500",
                "open_interest_date": "2026-08-28",
            }
        ]
    }
    if token is not None:
        payload["next_page_token"] = token
    return payload


def option_chain_payload(*, token=None, include_option=True):
    snapshots = {}
    if include_option:
        snapshots[OPTION] = {
            "latest_quote": {
                "timestamp": (NOW - timedelta(seconds=5)).isoformat(),
                "bid_price": "9.80",
                "ask_price": "10.00",
            },
            "greeks": {"delta": "0.50"},
        }
    payload = {"snapshots": snapshots}
    if token is not None:
        payload["next_page_token"] = token
    return payload


def json_bytes(value) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def make_transport(*, paginate_chain: bool = False):
    calls: list[str] = []

    def transport(url, headers, timeout_seconds):
        calls.append(url)
        assert headers["APCA-API-KEY-ID"] == "paper-key"
        assert headers["APCA-API-SECRET-KEY"] == "paper-secret"
        assert timeout_seconds == 10.0
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        if parsed.netloc == "paper-api.alpaca.markets" and parsed.path == "/v2/account":
            return 200, json_bytes(account_payload())
        if parsed.netloc == "paper-api.alpaca.markets" and parsed.path == "/v2/positions":
            return 200, json_bytes([])
        if parsed.netloc == "paper-api.alpaca.markets" and parsed.path == "/v2/orders":
            return 200, json_bytes([])
        if parsed.netloc == "paper-api.alpaca.markets" and parsed.path == "/v2/options/contracts":
            return 200, json_bytes(option_contracts_payload())
        if parsed.netloc == "data.alpaca.markets" and parsed.path == "/v1beta1/options/snapshots/NVDA":
            if paginate_chain and "page_token" not in query:
                return 200, json_bytes(
                    option_chain_payload(token="page-2", include_option=False)
                )
            return 200, json_bytes(option_chain_payload())
        raise AssertionError(f"unexpected URL: {url}")

    return transport, calls


def test_non_invest_fails_before_first_network_dispatch():
    calls = []

    def forbidden_transport(url, headers, timeout_seconds):
        calls.append(url)
        raise AssertionError("network dispatch must not occur")

    with pytest.raises(B5CompetitionOptionsError):
        run_b5_read_only_production_path(
            final_decision=final_decision(outcome="WATCH"),
            underlying_symbol="NVDA",
            as_of_date=AS_OF,
            latest_completed_session_date=LATEST_COMPLETED_SESSION,
            policy=POLICY,
            credentials=CREDS,
            transport=forbidden_transport,
            clock=lambda: NOW,
        )
    assert calls == []


def test_read_only_runtime_runs_end_to_end_and_materializes_b6_handoff():
    transport, calls = make_transport()
    result = run_b5_read_only_production_path(
        final_decision=final_decision(),
        underlying_symbol="NVDA",
        as_of_date=AS_OF,
        latest_completed_session_date=LATEST_COMPLETED_SESSION,
        policy=POLICY,
        credentials=CREDS,
        transport=transport,
        clock=lambda: NOW,
    )

    assert result.provider_reads.http_get_count == 5
    assert len(calls) == 5
    assert all(receipt.method == "GET" for receipt in result.provider_reads.receipts)
    assert len({receipt.receipt_id for receipt in result.provider_reads.receipts}) == 5
    assert all(receipt.broker_writes == 0 for receipt in result.provider_reads.receipts)
    assert result.b5.proposal_result.status == "PASS"
    assert result.b5.proposal_result.option_symbol == OPTION
    assert result.b5.proposal_result.quantity == 2
    assert result.b5.artifacts.accepted_proposal is not None
    assert result.b5.artifacts.accepted_proposal.option_symbol == OPTION
    assert result.broker_writes == 0
    assert result.model_calls == 0
    assert result.execution_authority is False
    assert result.approval_authority is False


def test_option_chain_pagination_is_bounded_and_hash_receipted():
    transport, calls = make_transport(paginate_chain=True)
    result = run_b5_read_only_production_path(
        final_decision=final_decision(),
        underlying_symbol="NVDA",
        as_of_date=AS_OF,
        latest_completed_session_date=LATEST_COMPLETED_SESSION,
        policy=POLICY,
        credentials=CREDS,
        transport=transport,
        clock=lambda: NOW,
        max_pages_per_request=3,
    )

    assert result.provider_reads.http_get_count == 6
    assert len(calls) == 6
    chain_receipt = next(
        receipt
        for receipt in result.provider_reads.receipts
        if receipt.request_id == "B5_OPTION_CHAIN"
    )
    assert chain_receipt.page_count == 2
    assert len(chain_receipt.response_sha256s) == 2
    assert result.b5.proposal_result.status == "PASS"
