from __future__ import annotations

import ast
from argparse import Namespace
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from aic.domain.canonical import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]

SCRIPT = (
    ROOT
    / "scripts"
    / "b4_ttl_judge_paid_executor_v01.py"
)

SPEC = importlib.util.spec_from_file_location(
    "b4_ttl_judge_paid_executor",
    SCRIPT,
)

assert SPEC is not None
assert SPEC.loader is not None

MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

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


def _args(
    tmp_path: Path,
) -> Namespace:
    return Namespace(
        execute_paid_judge=True,
        gate=tmp_path / "gate.json",
        activation_approval=(
            tmp_path / "activation.json"
        ),
        paid_approval=(
            tmp_path / "paid.json"
        ),
        consumption=(
            tmp_path / "consumption.json"
        ),
        raw=tmp_path / "raw.json",
        result=tmp_path / "result.json",
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        request_hash=(
            MODULE.EXPECTED_REQUEST_HASH
        ),
        request_payload={
            "model":
                MODULE.EXPECTED_MODEL,
            "reasoning":
                MODULE.EXPECTED_REASONING,
            "max_output_tokens":
                MODULE.EXPECTED_MAX_OUTPUT_TOKENS,
        },
    )


def _prepared() -> dict[str, object]:
    return {
        "request":
            _request(),

        "context":
            object(),

        "source_gate":
            {},

        "pricing":
            {},

        "ttl_model_run_ref":
            "B4_TTL_REEVALUATION_JUDGE_J1_V01",

        "authority": {
            "request_hash":
                MODULE.EXPECTED_REQUEST_HASH,

            "gate_hash":
                "1" * 64,

            "activation_approval_hash":
                "2" * 64,

            "paid_approval_hash":
                "3" * 64,

            "judge_max_cost_usd":
                MODULE.EXPECTED_MAX_COST_USD,
        },

        "activation_approval": {
            "artifact_hash":
                "2" * 64,
        },

        "paid_approval": {
            "artifact_hash":
                "3" * 64,

            "paid_approval_id":
                "paid-test-001",
        },

        "execution_branch":
            MODULE.CANONICAL_BRANCH,

        "execution_head":
            "a" * 40,
    }


def _sealed_result(
    actual: str = "0.10",
) -> dict[str, object]:
    value: dict[str, object] = {
        "artifact_version":
            MODULE.RESULT_VERSION,

        "actual_cost_usd":
            actual,
    }

    value["artifact_hash"] = canonical_sha256(
        value,
        exclude_fields=("artifact_hash",),
    )

    return value


def test_static_contract_has_ttl_authority_and_no_b5_b6_broker_imports(
) -> None:
    source = SCRIPT.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            imports.append(
                node.module or ""
            )

        elif isinstance(
            node,
            ast.Import,
        ):
            imports.extend(
                alias.name
                for alias in node.names
            )

    lowered = [
        value.lower()
        for value in imports
    ]

    assert all(
        "b5" not in value
        for value in lowered
    )

    assert all(
        "b6" not in value
        for value in lowered
    )

    assert all(
        "broker" not in value
        for value in lowered
    )

    assert (
        "verify_authority_pair"
        in source
    )

    assert (
        "build_prospective_request"
        in source
    )

    assert (
        "BLOCK_EXECUTOR_SOURCE_NOT_COMMITTED"
        in source
    )

    assert (
        "BLOCK_NONCANONICAL_OUTPUT_PATH"
        in source
    )

    assert (
        "canonical_output_paths"
        in source
    )

    assert (
        "remote_canonical_head"
        in source
    )

    assert (
        "BLOCK_REMOTE_CANONICAL_HEAD"
        in source
    )

    assert (
        "http.version=HTTP/1.1"
        in source
    )

    assert (
        "\"ls-remote\""
        in source
    )

    assert (
        source.index(
            "prepared = _prepare"
        )
        < source.index(
            "factory ="
        )
    )


def test_parse_args_requires_all_artifact_paths(
) -> None:
    with pytest.raises(SystemExit):
        MODULE.parse_args([])

    parsed = MODULE.parse_args(
        [
            "--execute-paid-judge",

            "--gate",
            "g.json",

            "--activation-approval",
            "a.json",

            "--paid-approval",
            "p.json",

            "--consumption",
            "c.json",

            "--raw",
            "r.json",

            "--result",
            "z.json",
        ]
    )

    assert (
        parsed.execute_paid_judge
        is True
    )


def test_canonical_output_paths_are_globally_bound_to_full_request_hash(
) -> None:
    paths = MODULE.canonical_output_paths(
        ROOT
    )

    assert set(paths) == {
        "consumption",
        "raw",
        "result",
    }

    assert len(
        {
            path.resolve()
            for path in paths.values()
        }
    ) == 3

    for path in paths.values():
        assert (
            MODULE.EXPECTED_REQUEST_HASH
            in path.name
        )


def test_remote_canonical_head_rejects_wrong_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = SimpleNamespace(
        returncode=0,
        stdout=(
            ("a" * 40)
            + "\trefs/heads/not-canonical\n"
        ),
        stderr="",
    )

    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *args, **kwargs: completed,
    )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match="BLOCK_REMOTE_CANONICAL_REF",
    ):
        MODULE.remote_canonical_head(
            ROOT
        )


def test_remote_canonical_head_mismatch_blocks_before_authority_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)

    head = "a" * 40
    remote_head = "b" * 40

    def fake_git(
        repository: Path,
        *values: str,
    ) -> str:
        if values == (
            "branch",
            "--show-current",
        ):
            return (
                MODULE.CANONICAL_BRANCH
            )

        if values == (
            "rev-parse",
            "HEAD",
        ):
            return head

        raise AssertionError(values)

    monkeypatch.setattr(
        MODULE,
        "_git",
        fake_git,
    )

    monkeypatch.setattr(
        MODULE,
        "_tracked_worktree_clean",
        lambda _: True,
    )

    monkeypatch.setattr(
        MODULE,
        "_source_matches_committed_head",
        lambda *_: True,
    )

    monkeypatch.setattr(
        MODULE,
        "remote_canonical_head",
        lambda _: remote_head,
    )

    monkeypatch.setattr(
        MODULE,
        "_load_script",
        pytest.fail,
    )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match="BLOCK_REMOTE_CANONICAL_HEAD",
    ):
        MODULE._prepare(
            args,
            repository=ROOT,
        )


def test_noncanonical_output_path_blocks_before_authority_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    head = "a" * 40

    def fake_git(
        repository: Path,
        *values: str,
    ) -> str:
        if values == (
            "branch",
            "--show-current",
        ):
            return (
                MODULE.CANONICAL_BRANCH
            )

        if values == (
            "rev-parse",
            "HEAD",
        ):
            return head

        raise AssertionError(values)

    monkeypatch.setattr(
        MODULE,
        "_git",
        fake_git,
    )

    monkeypatch.setattr(
        MODULE,
        "_tracked_worktree_clean",
        lambda _: True,
    )

    monkeypatch.setattr(
        MODULE,
        "_source_matches_committed_head",
        lambda *_: True,
    )

    monkeypatch.setattr(
        MODULE,
        "remote_canonical_head",
        lambda _: head,
    )

    monkeypatch.setattr(
        MODULE,
        "_load_script",
        pytest.fail,
    )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match=(
            "BLOCK_NONCANONICAL_OUTPUT_PATH:"
            "consumption"
        ),
    ):
        MODULE._prepare(
            args,
            repository=ROOT,
        )


def test_uncommitted_executor_source_blocks_before_artifact_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    head = "a" * 40

    def fake_git(
        repository: Path,
        *values: str,
    ) -> str:
        if values == (
            "branch",
            "--show-current",
        ):
            return (
                MODULE.CANONICAL_BRANCH
            )

        if values == (
            "rev-parse",
            "HEAD",
        ):
            return head

        raise AssertionError(values)

    monkeypatch.setattr(
        MODULE,
        "_git",
        fake_git,
    )

    monkeypatch.setattr(
        MODULE,
        "_tracked_worktree_clean",
        lambda _: True,
    )

    monkeypatch.setattr(
        MODULE,
        "_source_matches_committed_head",
        lambda *_: False,
    )

    monkeypatch.setattr(
        MODULE,
        "_load_script",
        pytest.fail,
    )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match=(
            "BLOCK_EXECUTOR_SOURCE_NOT_COMMITTED"
        ),
    ):
        MODULE._prepare(
            args,
            repository=ROOT,
        )


def test_transport_factory_is_not_created_when_prepare_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    calls = 0

    def fail_prepare(
        *args: object,
        **kwargs: object,
    ) -> dict:
        raise (
            MODULE
            .PaidJudgeExecutionBlocked(
                "BLOCK_TEST_PREPARE"
            )
        )

    def factory():
        nonlocal calls
        calls += 1
        return pytest.fail

    monkeypatch.setattr(
        MODULE,
        "_prepare",
        fail_prepare,
    )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match="BLOCK_TEST_PREPARE",
    ):
        MODULE.run(
            args,
            repository=ROOT,
            transport_factory=factory,
        )

    assert calls == 0
    assert not args.consumption.exists()


def test_transport_factory_failure_does_not_consume_paid_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)

    monkeypatch.setattr(
        MODULE,
        "_prepare",
        lambda *a, **k: _prepared(),
    )

    def factory():
        raise RuntimeError(
            "missing credential"
        )

    with pytest.raises(
        RuntimeError,
        match="missing credential",
    ):
        MODULE.run(
            args,
            repository=ROOT,
            transport_factory=factory,
        )

    assert not args.consumption.exists()
    assert not args.raw.exists()
    assert not args.result.exists()


def test_one_shot_reservation_exists_before_only_sender_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    prepared = _prepared()
    calls = 0

    monkeypatch.setattr(
        MODULE,
        "_process_response",
        lambda **_: _sealed_result(),
    )

    def sender(
        payload: object,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1

        assert args.consumption.is_file()

        consumption = json.loads(
            args.consumption.read_text(
                encoding="utf-8"
            )
        )

        assert (
            consumption["status"]
            == "DISPATCH_STARTED_UNKNOWN"
        )

        assert (
            consumption[
                "model_calls_attempted"
            ]
            == 1
        )

        assert (
            consumption[
                "automatic_retry_permitted"
            ]
            is False
        )

        return {
            "id": "resp_fake",
            "status": "completed",
        }

    result = MODULE._execute_once(
        prepared,
        consumption_path=args.consumption,
        raw_path=args.raw,
        result_path=args.result,
        sender=sender,
    )

    assert calls == 1

    assert (
        result["actual_cost_usd"]
        == "0.10"
    )

    consumption = json.loads(
        args.consumption.read_text(
            encoding="utf-8"
        )
    )

    assert (
        consumption["status"]
        == "COMPLETED"
    )

    assert (
        consumption[
            "automatic_retries"
        ]
        == 0
    )

    assert (
        consumption[
            "automatic_retry_permitted"
        ]
        is False
    )

    assert args.raw.is_file()
    assert args.result.is_file()

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match="BLOCK_EXCLUSIVE_OUTPUT_EXISTS",
    ):
        MODULE._execute_once(
            prepared,
            consumption_path=args.consumption,
            raw_path=args.raw,
            result_path=args.result,
            sender=pytest.fail,
        )

    assert calls == 1


def test_transport_exception_is_ambiguous_and_never_retryable(
    tmp_path: Path,
) -> None:
    args = _args(tmp_path)
    calls = 0

    def sender(
        _: object,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1

        raise RuntimeError(
            "simulated disconnect"
        )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match=(
            "BLOCK_AMBIGUOUS_PROVIDER_OUTCOME"
        ),
    ):
        MODULE._execute_once(
            _prepared(),
            consumption_path=args.consumption,
            raw_path=args.raw,
            result_path=args.result,
            sender=sender,
        )

    assert calls == 1

    consumption = json.loads(
        args.consumption.read_text(
            encoding="utf-8"
        )
    )

    assert (
        consumption["status"]
        == "DISPATCH_STARTED_UNKNOWN"
    )

    assert (
        consumption[
            "automatic_retry_permitted"
        ]
        is False
    )

    assert (
        consumption["stop_reason"]
        == (
            "AMBIGUOUS_PROVIDER_OUTCOME:"
            "RuntimeError"
        )
    )

    assert not args.raw.exists()
    assert not args.result.exists()


def test_captured_invalid_response_stops_without_second_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args(tmp_path)
    calls = 0

    def sender(
        _: object,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1

        return {
            "id": "resp_invalid",
            "status": "completed",
        }

    def reject(
        **_: object,
    ) -> dict[str, object]:
        raise ValueError(
            "invalid proposal"
        )

    monkeypatch.setattr(
        MODULE,
        "_process_response",
        reject,
    )

    with pytest.raises(
        MODULE.PaidJudgeExecutionBlocked,
        match="BLOCK_RESPONSE_NOT_ACCEPTED",
    ):
        MODULE._execute_once(
            _prepared(),
            consumption_path=args.consumption,
            raw_path=args.raw,
            result_path=args.result,
            sender=sender,
        )

    assert calls == 1

    consumption = json.loads(
        args.consumption.read_text(
            encoding="utf-8"
        )
    )

    assert (
        consumption["status"]
        == "RESPONSE_CAPTURED_NOT_ACCEPTED"
    )

    assert (
        consumption[
            "automatic_retry_permitted"
        ]
        is False
    )

    assert args.raw.is_file()
    assert not args.result.exists()


def test_ttl_model_run_ref_is_preserved_while_v04_validation_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared()

    ttl_ref = str(
        prepared["ttl_model_run_ref"]
    )

    seen: dict[str, str] = {}

    class FakeProposal:
        model_run_ref = ttl_ref

        outcome = (
            MODULE
            .v04
            .JudgeOutcome
            .WATCH
        )

        next_directive = (
            MODULE
            .v04
            .JudgeNextDirective
            .MONITOR
        )

        def model_copy(
            self,
            *,
            update: dict[str, object],
        ):
            seen["adapted_ref"] = str(
                update["model_run_ref"]
            )

            return SimpleNamespace(
                model_run_ref=(
                    update["model_run_ref"]
                )
            )

    class FakeFrozen:
        def model_dump(
            self,
            **_: object,
        ) -> dict[str, object]:
            return {
                "model_run_ref":
                    ttl_ref
            }

    proposal = FakeProposal()

    monkeypatch.setattr(
        MODULE.v04,
        "parse_council_responses_payload",
        lambda *a, **k: (
            SimpleNamespace(
                response_id="resp_1"
            ),
            proposal,
        ),
    )

    def validate(
        adapted: object,
        **_: object,
    ) -> None:
        seen["validated_ref"] = str(
            getattr(
                adapted,
                "model_run_ref",
            )
        )

    monkeypatch.setattr(
        MODULE.v04,
        "validate_proposal",
        validate,
    )

    monkeypatch.setattr(
        MODULE.v04,
        "FrozenJudgeDecisionProposal",
        SimpleNamespace(
            from_draft=lambda _: FakeFrozen()
        ),
    )

    monkeypatch.setattr(
        MODULE.v04,
        "actual_cost_usd",
        lambda *a, **k: Decimal("0.10"),
    )

    capture = {
        "raw_response_hash":
            "4" * 64,

        "captured_at_utc":
            "2026-09-02T18:30:00Z",
    }

    result = MODULE._process_response(
        prepared=prepared,
        raw={"id": "resp_1"},
        capture=capture,
    )

    assert (
        seen["adapted_ref"]
        == MODULE.v04.MODEL_RUN_REF
    )

    assert (
        seen["validated_ref"]
        == MODULE.v04.MODEL_RUN_REF
    )

    assert (
        result[
            "processed_record"
        ][
            "model_run_ref"
        ]
        == ttl_ref
    )

    assert (
        result[
            "watch_terminal_without_b5"
        ]
        is True
    )

    assert (
        result["b5_handoff_created"]
        is False
    )

    assert (
        result["b6_started"]
        is False
    )

    assert (
        result["broker_writes"]
        == 0
    )


@requires_runtime
def test_reconstructed_ttl_request_is_exact_frozen_request(
) -> None:
    readiness = MODULE._load_script(
        ROOT,
        "ttl_executor_test_readiness",
        MODULE.READINESS_SCRIPT,
    )

    evaluation = readiness._utc(
        readiness.DEFAULT_EVALUATION_TIME_UTC
    )

    bundle = (
        MODULE
        ._reconstruct_request_bundle(
            ROOT,
            readiness,
            evaluation,
        )
    )

    request = bundle["request"]

    encoded = json.dumps(
        request.request_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert (
        request.request_hash
        == MODULE.EXPECTED_REQUEST_HASH
    )

    assert (
        bundle["context"].judge_input_hash
        == MODULE.EXPECTED_JUDGE_INPUT_HASH
    )

    assert (
        request.request_payload["model"]
        == MODULE.EXPECTED_MODEL
    )

    assert (
        request.request_payload["reasoning"]
        == MODULE.EXPECTED_REASONING
    )

    assert (
        request.request_payload[
            "max_output_tokens"
        ]
        == MODULE.EXPECTED_MAX_OUTPUT_TOKENS
    )

    assert (
        readiness.MODEL_RUN_REF
        in encoded
    )

    assert (
        readiness.HISTORICAL_MODEL_RUN_REF
        not in encoded
    )

    assert (
        readiness.HISTORICAL_PROVIDER_RESPONSE_ID
        not in encoded
    )

    assert (
        readiness.HISTORICAL_RAW_RESPONSE_HASH
        not in encoded
    )
