from __future__ import annotations

import ast
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

from aic.council import post_research_reopen_judge_current_v04 as judge
from aic.domain.canonical import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "b4_recovered_decision_ttl_lineage_zero_call_v01.py"
RAW_PATH = ROOT / ".aic-runtime/b4_post_research_reopen_current_judge_raw_response_v0_4__40d7f5c.json"
RECOVERED_PATH = ROOT / ".aic-runtime/b4_post_research_reopen_current_judge_captured_response_recovery_v0_1__442e8d7.json"
POLICY_PATH = ROOT / "config/event/decision_lifecycle_policy_competition_v1.json"

SPEC = importlib.util.spec_from_file_location("b4_ttl_lineage", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _event_inputs() -> tuple[dict, dict, dict]:
    if not RAW_PATH.is_file() or not RECOVERED_PATH.is_file():
        pytest.skip("requires local immutable B4 production runtime evidence")
    return (
        json.loads(RAW_PATH.read_text(encoding="utf-8")),
        json.loads(RECOVERED_PATH.read_text(encoding="utf-8")),
        json.loads(POLICY_PATH.read_text(encoding="utf-8")),
    )


def _synthetic_lineage() -> MODULE.Lineage:
    return MODULE.Lineage(
        raw_response_hash=MODULE.EXPECTED_RAW_HASH,
        provider_response_id=MODULE.EXPECTED_RESPONSE_ID,
        recovered_artifact_hash=MODULE.EXPECTED_RECOVERED_HASH,
        decision_created_at_utc=datetime(2026, 9, 1, 8, 53, 32, tzinfo=UTC),
    )


def _tracked_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _rehash_raw(raw: dict) -> None:
    raw["raw_response_hash"] = judge.external_provider_json_sha256(
        {key: value for key, value in raw.items() if key != "raw_response_hash"}
    )


def _rehash_recovered(recovered: dict) -> None:
    recovered["artifact_hash"] = canonical_sha256(recovered, exclude_fields=("artifact_hash",))


def test_script_has_no_network_or_provider_execution_capability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not module.startswith(("requests", "http", "urllib", "socket", "openai", "alpaca")) for module in imports)
    for prohibited in ("urlopen(", "requests.", "TradingClient", "submit_order", "create_order", "POST", "PUT", "PATCH", "DELETE"):
        assert prohibited not in source
    assert "--evaluation-time-utc" in source


def test_actual_lineage_uses_internal_raw_authority_and_recovered_draft() -> None:
    raw, recovered, policy = _event_inputs()
    lineage = MODULE.recover_lineage(raw=raw, recovered=recovered, policy=policy)
    assert lineage.raw_response_hash == MODULE.EXPECTED_RAW_HASH
    assert lineage.provider_response_id == MODULE.EXPECTED_RESPONSE_ID
    assert lineage.recovered_artifact_hash == MODULE.EXPECTED_RECOVERED_HASH
    assert lineage.decision_created_at_utc == datetime(2026, 9, 1, 8, 53, 32, tzinfo=UTC)
    assert MODULE.verify_production_timestamp_invariant() is True


def test_raw_file_bytes_are_not_used_as_internal_raw_authority() -> None:
    raw, _, _ = _event_inputs()
    assert raw["raw_response_hash"] == MODULE.EXPECTED_RAW_HASH
    assert MODULE.EXPECTED_RAW_HASH != "d3030ecfaf99a2ec39f4dc6420cbabdb6ad5dd414c7f932d7ff94b756abdb7d0"
    assert judge.verify_raw_capture(raw, request_hash=MODULE.EXPECTED_REQUEST_HASH) == MODULE.EXPECTED_RAW_HASH


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda raw: raw.__setitem__("raw_response_hash", "0" * 64), "BLOCK_RAW_CAPTURE"),
        (lambda raw: raw.pop("captured_at_utc"), "BLOCK_CAPTURED_AT_UTC"),
        (lambda raw: raw.__setitem__("captured_at_utc", "2026-09-01T08:53:32"), "BLOCK_CAPTURED_AT_UTC"),
    ],
)
def test_invalid_raw_capture_blocks(mutate, expected: str) -> None:
    raw, _, _ = _event_inputs()
    raw = deepcopy(raw)
    mutate(raw)
    if expected == "BLOCK_CAPTURED_AT_UTC":
        _rehash_raw(raw)
        expected_hash = raw["raw_response_hash"]
    else:
        expected_hash = MODULE.EXPECTED_RAW_HASH
    with pytest.raises(MODULE.LineageBlocked, match=expected):
        MODULE.validate_raw_capture(raw, expected_raw_hash=expected_hash)


def test_wrong_provider_response_identity_blocks_after_valid_raw_verification() -> None:
    raw, _, _ = _event_inputs()
    raw = deepcopy(raw)
    raw["provider_response_id"] = "resp_wrong"
    raw["raw_response"]["id"] = "resp_wrong"
    _rehash_raw(raw)
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_PROVIDER_RESPONSE_ID"):
        MODULE.validate_raw_capture(raw, expected_raw_hash=raw["raw_response_hash"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda recovered: recovered.__setitem__("artifact_hash", "0" * 64),
        lambda recovered: recovered["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("outcome", "WATCH"),
        lambda recovered: recovered["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("primary_candidate_id", "AAPL"),
        lambda recovered: recovered["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("research_reopen_required", True),
        lambda recovered: recovered["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("execution_authority", True),
        lambda recovered: recovered["processed_record"]["frozen_judge_proposal"]["draft"].__setitem__("blocking_reason_codes", ["BLOCKED"]),
    ],
)
def test_recovered_primary_draft_mutations_block(mutate) -> None:
    raw, recovered, _ = _event_inputs()
    recovered = deepcopy(recovered)
    mutate(recovered)
    if recovered["artifact_hash"] != "0" * 64:
        _rehash_recovered(recovered)
    with pytest.raises(MODULE.LineageBlocked):
        MODULE.validate_recovered_binding(
            recovered,
            raw_hash=raw["raw_response_hash"],
            provider_id=raw["provider_response_id"],
        )


def test_altered_recovered_self_hash_blocks() -> None:
    raw, recovered, _ = _event_inputs()
    recovered = deepcopy(recovered)
    recovered["recovery_model_calls"] = 1
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_RECOVERED_B4"):
        MODULE.validate_recovered_binding(
            recovered,
            raw_hash=raw["raw_response_hash"],
            provider_id=raw["provider_response_id"],
        )


def test_policy_requires_correct_frozen_lifecycle_authority() -> None:
    policy = _tracked_policy()
    MODULE.validate_policy(policy)
    assert policy["version"] == MODULE.POLICY_VERSION
    assert policy["policy_hash"] == MODULE.POLICY_HASH
    assert canonical_sha256(policy, exclude_fields=("policy_hash",)) == MODULE.POLICY_HASH
    assert policy["active"] is True
    assert policy["next_review_trigger_mode"] == "TTL_EXPIRY"
    assert policy["decision_ttl_seconds"] == 7200
    assert policy["ttl_anchor"] == MODULE.TTL_ANCHOR


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda policy: policy.__setitem__("decision_ttl_seconds", 7199), "BLOCK_POLICY_CANONICAL_SELF_HASH"),
        (lambda policy: policy.__setitem__("active", False), "BLOCK_POLICY_CANONICAL_SELF_HASH"),
        (lambda policy: policy.__setitem__("next_review_trigger_mode", "MANUAL"), "BLOCK_POLICY_CANONICAL_SELF_HASH"),
    ],
)
def test_policy_mutation_with_retained_frozen_hash_blocks(mutate, expected: str) -> None:
    policy = deepcopy(_tracked_policy())
    mutate(policy)
    with pytest.raises(MODULE.LineageBlocked, match=expected):
        MODULE.validate_policy(policy)


def test_ttl_arithmetic_is_deterministic_and_truthfully_expired() -> None:
    lineage = _synthetic_lineage()
    age, status, expires = MODULE.evaluate_ttl(lineage, datetime(2026, 9, 1, 19, 45, 35, tzinfo=UTC))
    assert age == "39123"
    assert status == "TTL_EXPIRED"
    assert expires == datetime(2026, 9, 1, 10, 53, 32, tzinfo=UTC)
    assert MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc)[1] == "TTL_VALID"
    assert MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc + timedelta(seconds=7200))[1] == "TTL_VALID"
    assert MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc + timedelta(seconds=7201))[1] == "TTL_EXPIRED"


@pytest.mark.parametrize(
    "offset, expected",
    [
        (timedelta(seconds=7200, microseconds=1), "TTL_EXPIRED"),
        (timedelta(seconds=7200, microseconds=500_000), "TTL_EXPIRED"),
        (timedelta(seconds=7201), "TTL_EXPIRED"),
    ],
)
def test_ttl_expiry_does_not_truncate_subseconds(offset: timedelta, expected: str) -> None:
    lineage = _synthetic_lineage()
    age, status, _ = MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc + offset)
    assert status == expected
    assert age == {1: "7200.000001", 500_000: "7200.5", 0: "7201"}[offset.microseconds]


def test_ttl_rejects_naive_or_predecision_evaluation_time() -> None:
    lineage = _synthetic_lineage()
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_EVALUATION_TIME_UTC"):
        MODULE.evaluate_ttl(lineage, datetime(2026, 9, 1, 19, 45, 35))
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_EVALUATION_PRECEDES_DECISION"):
        MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc - timedelta(seconds=1))
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_EVALUATION_PRECEDES_DECISION"):
        MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc - timedelta(microseconds=1))
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_EVALUATION_PRECEDES_DECISION"):
        MODULE.evaluate_ttl(lineage, lineage.decision_created_at_utc - timedelta(microseconds=500_000))
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_EVALUATION_TIME_UTC"):
        MODULE.parse_evaluation_time("2026-09-01T19:45:35")


def test_receipt_is_metadata_only_self_hashed_and_exclusive(tmp_path: Path) -> None:
    lineage = _synthetic_lineage()
    receipt = MODULE.build_receipt(lineage)
    assert receipt["artifact_hash"] == canonical_sha256(receipt, exclude_fields=("artifact_hash",))
    assert receipt["timestamp_derivation"] == "RAW_CAPTURE_CAPTURED_AT_UTC"
    assert receipt["execution_authority"] is False
    assert receipt["broker_write_authority"] is False
    assert receipt["live_execution"] is False
    assert "raw_response" not in receipt
    destination = tmp_path / "receipt.json"
    MODULE.write_receipt_exclusive(destination, receipt)
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(MODULE.LineageBlocked, match="BLOCK_RECEIPT_EXISTS"):
        MODULE.write_receipt_exclusive(destination, receipt)


def test_cli_requires_explicit_evaluation_time_and_emits_ttl_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit):
        MODULE.main([])
    monkeypatch.setattr(MODULE, "load_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(MODULE, "recover_lineage", lambda **_kwargs: _synthetic_lineage())
    output = io.StringIO()
    receipt_path = tmp_path / "lineage-receipt.json"
    assert MODULE.main(
        [
            "--repository",
            str(ROOT),
            "--evaluation-time-utc",
            "2026-09-01T19:45:35Z",
            "--receipt-path",
            str(receipt_path),
        ],
        output=output,
    ) == 0
    lines = output.getvalue()
    assert "TTL_STATUS=TTL_EXPIRED" in lines
    assert "DECISION_AGE_SECONDS=39123" in lines
    assert receipt_path.is_file()
