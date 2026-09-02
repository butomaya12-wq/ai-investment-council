from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from aic.domain.canonical import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = (
    ROOT
    / "scripts"
    / "b4_ttl_judge_production_gate_zero_call_v01.py"
)

SPEC = importlib.util.spec_from_file_location(
    "b4_ttl_judge_production_gate",
    SCRIPT,
)

assert SPEC is not None
assert SPEC.loader is not None

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

EVALUATION = datetime.fromisoformat(
    "2026-09-01T19:45:35+00:00"
)

RUNTIME_SENTINEL = (
    ROOT
    / ".aic-runtime"
    / (
        "b4_recovered_decision_ttl_lineage_"
        "v0_1__fc4d73a__5500332.json"
    )
)

requires_runtime = pytest.mark.skipif(
    not RUNTIME_SENTINEL.is_file(),
    reason="immutable local runtime evidence unavailable",
)


def _seal(
    value: dict[str, object],
) -> dict[str, object]:
    value["artifact_hash"] = canonical_sha256(
        value,
        exclude_fields=("artifact_hash",),
    )
    return value


def _readiness(
    head: str,
) -> dict[str, object]:
    value: dict[str, object] = {
        "status":
            "PASS_ZERO_CALL_TTL_FRESH_JUDGE_ACTIVATION_READINESS",

        "readiness_repository_head":
            head,

        "source_judge_code_commit_sha":
            MODULE.FROZEN_JUDGE_SOURCE_COMMIT_SHA,

        "request_identity_independent_of_readiness_repository_head":
            True,

        "ttl_status":
            "TTL_EXPIRED",
        "trigger":
            "TTL_EXPIRY",

        "proposal_policy_hash":
            MODULE.PROPOSAL_POLICY_HASH,
        "proposal_active":
            False,
        "proposal_status":
            "DRAFT_NOT_AUTHORITY",

        "prospective_model_run_ref":
            MODULE.PROSPECTIVE_MODEL_RUN_REF,
        "prospective_judge_input_hash":
            MODULE.PROSPECTIVE_JUDGE_INPUT_HASH,
        "prospective_request_hash":
            MODULE.PROSPECTIVE_REQUEST_HASH,

        "historical_request_hash_reused":
            False,
        "historical_judge_response_in_model_input":
            False,
        "historical_judge_raw_hash_in_model_input":
            False,

        "request_body_utf8_bytes":
            MODULE.REQUEST_BODY_UTF8_BYTES,
        "input_tokens_upper_bound":
            MODULE.INPUT_TOKENS_UPPER_BOUND,
        "max_output_tokens":
            MODULE.MAX_OUTPUT_TOKENS,
        "max_call_count":
            MODULE.MAX_CALL_COUNT,
        "automatic_retries":
            MODULE.AUTOMATIC_RETRIES,

        "judge_max_cost_usd":
            MODULE.JUDGE_MAX_COST_USD,

        "pricing_hash":
            MODULE.EXPECTED_PRICING_HASH,
        "pricing_version":
            MODULE.EXPECTED_PRICING_VERSION,

        "provider_refresh_required_before_model":
            False,
        "provider_reads_authorized":
            False,

        "activation_status":
            "NOT_GRANTED",
        "cost_approval_status":
            "NOT_GRANTED",

        "owner_activation_required":
            True,
        "owner_paid_approval_required":
            True,

        "model_calls_authorized":
            False,
        "broker_write_authority":
            False,
        "live_execution":
            False,

        "watch_b5_started":
            False,
        "abstain_b5_started":
            False,

        "fresh_invest_requires_fresh_b5":
            True,
        "historical_b5_selection_is_lineage_only":
            True,

        "model_calls":
            0,
        "openai_calls":
            0,
        "provider_reads":
            0,
        "alpaca_reads":
            0,
        "network_calls":
            0,
        "broker_writes":
            0,
        "alpaca_orders":
            0,

        "paid_llm_cost_usd":
            "0",

        "b6_started":
            False,
        "paper_order_sent":
            False,

        "live_money":
            "PROHIBITED",
    }

    return _seal(value)


def _gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str,
    head: str = "a" * 40,
    clean: bool = True,
) -> dict[str, object]:
    monkeypatch.setattr(
        MODULE,
        "build_source_readiness",
        lambda **kwargs: _readiness(
            kwargs["readiness_repository_head"]
        ),
    )

    monkeypatch.setattr(
        MODULE,
        "tracked_worktree_clean",
        lambda repository: clean,
    )

    return MODULE.build_gate(
        repository=ROOT,
        evaluation_time_utc=EVALUATION,
        gate_repository_head=head,
        gate_branch=branch,
    )


def _activation(
    gate: dict[str, object],
    head: str,
) -> dict[str, object]:
    return _seal(
        {
            "artifact_version":
                MODULE.ACTIVATION_APPROVAL_VERSION,

            "owner_activation_granted":
                True,

            "activation_scope":
                MODULE.ACTIVATION_SCOPE,

            "gate_hash":
                gate["artifact_hash"],

            "request_hash":
                MODULE.PROSPECTIVE_REQUEST_HASH,

            "approved_executor_code_commit_sha":
                head,

            "activation_approval_id":
                "owner-activation-001",

            "activation_approval_at_utc":
                "2026-09-02T12:00:00Z",

            "standalone_model_call_authority":
                False,

            "paid_call_authority":
                False,

            "provider_read_authority":
                False,

            "broker_write_authority":
                False,

            "live_money":
                "PROHIBITED",
        }
    )


def _paid(
    gate: dict[str, object],
    activation: dict[str, object],
    head: str,
) -> dict[str, object]:
    return _seal(
        {
            "artifact_version":
                MODULE.PAID_APPROVAL_VERSION,

            "owner_paid_approval_granted":
                True,

            "paid_scope":
                MODULE.PAID_SCOPE,

            "gate_hash":
                gate["artifact_hash"],

            "activation_approval_hash":
                activation["artifact_hash"],

            "request_hash":
                MODULE.PROSPECTIVE_REQUEST_HASH,

            "approved_executor_code_commit_sha":
                head,

            "paid_approval_id":
                "owner-paid-001",

            "paid_approval_at_utc":
                "2026-09-02T12:01:00Z",

            "approved_judge_max_cost_usd":
                MODULE.JUDGE_MAX_COST_USD,

            "new_paid_call_count":
                1,

            "new_paid_call_count_ceiling":
                1,

            "max_output_tokens":
                MODULE.MAX_OUTPUT_TOKENS,

            "automatic_retries":
                MODULE.AUTOMATIC_RETRIES,

            "requires_activation_approval":
                True,

            "standalone_model_call_authority":
                False,

            "provider_read_authority":
                False,

            "broker_write_authority":
                False,

            "live_money":
                "PROHIBITED",
        }
    )


def test_feature_branch_gate_is_exact_and_not_approval_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _gate(
        monkeypatch,
        branch=MODULE.FEATURE_BRANCH,
    )

    assert (
        gate["prospective_request_hash"]
        == MODULE.PROSPECTIVE_REQUEST_HASH
    )

    assert (
        gate["prospective_judge_input_hash"]
        == MODULE.PROSPECTIVE_JUDGE_INPUT_HASH
    )

    assert gate["judge_max_cost_usd"] == "0.486939"
    assert gate["max_call_count"] == 1
    assert gate["automatic_retries"] == 0

    assert gate["approval_eligible"] is False

    assert gate["activation_status"] == "NOT_GRANTED"
    assert gate["paid_approval_status"] == "NOT_GRANTED"

    assert gate["model_calls_authorized"] is False
    assert gate["provider_reads_authorized"] is False
    assert gate["broker_write_authority"] is False

    assert (
        MODULE.SUPERSEDED_PROSPECTIVE_REQUEST_HASH
        in gate["blocked_request_hashes"]
    )

    assert (
        MODULE.HISTORICAL_JUDGE_REQUEST_HASH
        in gate["blocked_request_hashes"]
    )

    assert gate["fresh_judge_allowed_outcomes"] == [
        "INVEST",
        "WATCH",
        "ABSTAIN",
    ]

    assert gate["watch_is_terminal_without_b5"] is True
    assert gate["abstain_is_terminal_without_b5"] is True

    assert gate["invest_requires_fresh_b5"] is True

    assert (
        gate["historical_b5_selection_reuse_allowed"]
        is False
    )

    assert gate["b6_auto_start_allowed"] is False


def test_canonical_gate_requires_clean_tracked_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        clean=True,
    )

    dirty = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        clean=False,
    )

    assert clean["approval_eligible"] is True
    assert dirty["approval_eligible"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "prospective_request_hash",
            MODULE.SUPERSEDED_PROSPECTIVE_REQUEST_HASH,
        ),
        (
            "judge_max_cost_usd",
            "0.486940",
        ),
        (
            "max_call_count",
            2,
        ),
        (
            "automatic_retries",
            1,
        ),
        (
            "provider_reads_authorized",
            True,
        ),
    ],
)
def test_source_readiness_drift_fails_closed(
    field: str,
    value: object,
) -> None:
    readiness = _readiness(
        "a" * 40
    )

    readiness[field] = value
    _seal(readiness)

    with pytest.raises(
        MODULE.ProductionGateBlocked,
    ):
        MODULE.verify_source_readiness(
            readiness,
            expected_repository_head="a" * 40,
        )


def test_gate_tamper_fails_against_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "a" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.FEATURE_BRANCH,
        head=head,
    )

    altered = deepcopy(gate)
    altered["judge_max_cost_usd"] = "0.486940"
    _seal(altered)

    with pytest.raises(
        MODULE.ProductionGateBlocked,
        match="BLOCK_GATE_DRIFT",
    ):
        MODULE.verify_gate(
            altered,
            repository=ROOT,
            evaluation_time_utc=EVALUATION,
            gate_repository_head=head,
            gate_branch=MODULE.FEATURE_BRANCH,
        )


def test_valid_distinct_approval_pair_authorizes_only_one_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "b" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        head=head,
        clean=True,
    )

    activation = _activation(
        gate,
        head,
    )

    paid = _paid(
        gate,
        activation,
        head,
    )

    authority = MODULE.verify_authority_pair(
        repository=ROOT,
        evaluation_time_utc=EVALUATION,
        gate=gate,
        activation_approval=activation,
        paid_approval=paid,
        execution_branch=
            MODULE.REQUIRED_EXECUTION_BRANCH,
        execution_head=head,
    )

    assert authority["model_call_authorized"] is True
    assert authority["max_call_count"] == 1
    assert authority["automatic_retries"] == 0

    assert (
        authority["provider_reads_authorized"]
        is False
    )

    assert (
        authority["broker_write_authority"]
        is False
    )


def test_feature_branch_cannot_be_activated_even_with_approval_shaped_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "c" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.FEATURE_BRANCH,
        head=head,
        clean=True,
    )

    activation = _activation(
        gate,
        head,
    )

    paid = _paid(
        gate,
        activation,
        head,
    )

    with pytest.raises(
        MODULE.ProductionGateBlocked,
    ):
        MODULE.verify_authority_pair(
            repository=ROOT,
            evaluation_time_utc=EVALUATION,
            gate=gate,
            activation_approval=activation,
            paid_approval=paid,
            execution_branch=
                MODULE.FEATURE_BRANCH,
            execution_head=head,
        )


def test_activation_approval_cannot_also_claim_paid_or_model_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "d" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        head=head,
        clean=True,
    )

    activation = _activation(
        gate,
        head,
    )

    activation["paid_call_authority"] = True
    _seal(activation)

    with pytest.raises(
        MODULE.ProductionGateBlocked,
    ):
        MODULE.verify_activation_approval(
            activation,
            gate=gate,
            execution_head=head,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "request_hash",
            MODULE.SUPERSEDED_PROSPECTIVE_REQUEST_HASH,
        ),
        (
            "approved_judge_max_cost_usd",
            "0.486940",
        ),
        (
            "new_paid_call_count",
            2,
        ),
        (
            "new_paid_call_count_ceiling",
            2,
        ),
        (
            "automatic_retries",
            1,
        ),
        (
            "provider_read_authority",
            True,
        ),
    ],
)
def test_paid_approval_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    head = "e" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        head=head,
        clean=True,
    )

    activation = _activation(
        gate,
        head,
    )

    paid = _paid(
        gate,
        activation,
        head,
    )

    paid[field] = value
    _seal(paid)

    with pytest.raises(
        MODULE.ProductionGateBlocked,
    ):
        MODULE.verify_paid_approval(
            paid,
            gate=gate,
            activation_approval_hash=
                activation["artifact_hash"],
            execution_head=head,
        )


def test_paid_approval_cannot_precede_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "1" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        head=head,
        clean=True,
    )

    activation = _activation(
        gate,
        head,
    )

    paid = _paid(
        gate,
        activation,
        head,
    )

    paid["paid_approval_at_utc"] = (
        "2026-09-02T11:59:59Z"
    )

    _seal(paid)

    with pytest.raises(
        MODULE.ProductionGateBlocked,
        match="BLOCK_PAID_APPROVAL_PRECEDES_ACTIVATION",
    ):
        MODULE.verify_authority_pair(
            repository=ROOT,
            evaluation_time_utc=EVALUATION,
            gate=gate,
            activation_approval=activation,
            paid_approval=paid,
            execution_branch=
                MODULE.REQUIRED_EXECUTION_BRANCH,
            execution_head=head,
        )


def test_approval_shapes_reject_extra_authority_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "f" * 40

    gate = _gate(
        monkeypatch,
        branch=MODULE.REQUIRED_EXECUTION_BRANCH,
        head=head,
        clean=True,
    )

    activation = _activation(
        gate,
        head,
    )

    activation["model_call_authority"] = True
    _seal(activation)

    with pytest.raises(
        MODULE.ProductionGateBlocked,
        match="BLOCK_ACTIVATION_APPROVAL_SHAPE",
    ):
        MODULE.verify_activation_approval(
            activation,
            gate=gate,
            execution_head=head,
        )


@requires_runtime
def test_runtime_gate_reconstructs_exact_frozen_identity_without_authority(
) -> None:
    head = subprocess.run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    branch = subprocess.run(
        [
            "git",
            "branch",
            "--show-current",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    gate = MODULE.build_gate(
        repository=ROOT,
        evaluation_time_utc=EVALUATION,
        gate_repository_head=head,
        gate_branch=branch,
    )

    assert (
        gate["prospective_request_hash"]
        == MODULE.PROSPECTIVE_REQUEST_HASH
    )

    assert (
        gate["prospective_judge_input_hash"]
        == MODULE.PROSPECTIVE_JUDGE_INPUT_HASH
    )

    assert (
        gate["judge_max_cost_usd"]
        == MODULE.JUDGE_MAX_COST_USD
    )

    assert gate["max_call_count"] == 1
    assert gate["automatic_retries"] == 0

    assert gate["model_calls"] == 0
    assert gate["provider_reads"] == 0
    assert gate["broker_writes"] == 0

    assert gate["b6_started"] is False


def test_source_has_no_model_provider_or_broker_transport_capability(
) -> None:
    source = SCRIPT.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    imported: list[str] = []

    for node in ast.walk(tree):
        if isinstance(
            node,
            ast.Import,
        ):
            imported.extend(
                alias.name
                for alias in node.names
            )

        elif isinstance(
            node,
            ast.ImportFrom,
        ):
            imported.append(
                node.module or ""
            )

    assert all(
        not name.startswith(
            (
                "requests",
                "http",
                "urllib",
                "socket",
                "openai",
                "alpaca",
            )
        )
        for name in imported
    )

    for prohibited in (
        "urlopen(",
        "requests.",
        "StdlibResponsesTransport",
        "load_openai_api_key",
        "TradingClient",
        "submit_order",
    ):
        assert prohibited not in source
