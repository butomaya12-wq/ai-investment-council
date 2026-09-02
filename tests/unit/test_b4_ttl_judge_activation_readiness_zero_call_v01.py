from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

from aic.domain.canonical import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/b4_ttl_judge_activation_readiness_zero_call_v01.py"
SPEC = importlib.util.spec_from_file_location("b4_ttl_judge_activation_readiness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
EVALUATION = datetime.fromisoformat("2026-09-01T19:45:35+00:00")
BASE_HEAD = "814895777015cfbf47a1be03c028b65030cab2df"


def readiness() -> dict[str, object]:
    return MODULE.build_readiness(
        repository=ROOT,
        evaluation_time_utc=EVALUATION,
        canonical_head=BASE_HEAD,
    )


def request_payload(value: dict[str, object]) -> str:
    # The readiness record intentionally persists hashes only, so rebuild the
    # deterministic bounded request from its immutable local source inputs.
    source_entry, source_context, entry, context, _ = MODULE._source_inputs(ROOT, code_commit_sha=BASE_HEAD)
    prospective = MODULE.build_prospective_judge_context(
        source_entry=source_entry,
        source_context=source_context,
        entry=entry,
        context=context,
        policy_hash=str(value["proposal_policy_hash"]),
        ttl_receipt_hash=str(value["source_ttl_lineage_receipt_hash"]),
    )
    request = MODULE.build_prospective_request(entry=entry, context=prospective)
    return json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_readiness_binds_exact_inactive_policy_and_expired_ttl_lineage() -> None:
    value = readiness()
    assert value["proposal_policy_hash"] == MODULE.PROPOSAL_POLICY_HASH
    assert value["proposal_active"] is False
    assert value["proposal_status"] == "DRAFT_NOT_AUTHORITY"
    assert value["ttl_status"] == "TTL_EXPIRED"
    assert value["trigger"] == "TTL_EXPIRY"
    assert value["source_raw_b4_canonical_hash"] == MODULE.HISTORICAL_RAW_RESPONSE_HASH
    assert value["source_recovered_b4_artifact_hash"] == MODULE.RECOVERED_B4_ARTIFACT_HASH
    assert value["decision_created_at_utc"] == "2026-09-01T08:53:32Z"
    assert value["decision_expires_at_utc"] == "2026-09-01T10:53:32Z"


def test_existing_ttl_preflight_remains_underspecified_because_proposal_is_inactive() -> None:
    value = readiness()
    assert value["source_ttl_expiry_preflight_hash"] == MODULE.verify_canonical_ttl_preflight(ROOT)
    assert MODULE.verify_inactive_proposal(ROOT) == MODULE.PROPOSAL_POLICY_HASH


def test_fresh_judge_request_uses_ttl_identity_and_excludes_historical_judge_output() -> None:
    value = readiness()
    payload = request_payload(value)
    assert value["prospective_model_run_ref"] == MODULE.MODEL_RUN_REF
    assert MODULE.MODEL_RUN_REF in payload
    assert MODULE.HISTORICAL_MODEL_RUN_REF not in payload
    assert MODULE.HISTORICAL_PROVIDER_RESPONSE_ID not in payload
    assert MODULE.HISTORICAL_RAW_RESPONSE_HASH not in payload
    assert value["historical_request_hash_reused"] is False
    assert value["prospective_request_hash"] != MODULE.HISTORICAL_REQUEST_HASH
    assert value["historical_judge_response_in_model_input"] is False
    assert value["historical_judge_raw_hash_in_model_input"] is False


def test_b3_initial_and_rebuttal_lineage_and_request_cost_are_deterministic() -> None:
    one = readiness()
    two = readiness()
    assert one == two
    assert all(isinstance(one[field], str) and len(one[field]) == 64 for field in (
        "source_b3_final_closure_hash", "source_initial_freeze_hash", "source_rebuttal_freeze_hash",
        "prospective_judge_input_hash", "prospective_request_hash",
    ))
    assert one["request_body_utf8_bytes"] == one["input_tokens_upper_bound"]
    assert one["max_output_tokens"] == 8192
    assert one["max_call_count"] == 1
    assert one["automatic_retries"] == 0
    assert isinstance(one["judge_max_cost_usd"], str)


def test_activation_cost_provider_and_post_judge_boundaries_are_not_granted() -> None:
    value = readiness()
    assert value["activation_status"] == "NOT_GRANTED"
    assert value["cost_approval_status"] == "NOT_GRANTED"
    assert value["owner_activation_required"] is True
    assert value["owner_paid_approval_required"] is True
    assert value["provider_refresh_required_before_model"] is False
    assert value["model_calls_authorized"] is False
    assert value["provider_reads_authorized"] is False
    assert value["broker_write_authority"] is False
    assert value["live_execution"] is False
    assert value["watch_b5_started"] is False
    assert value["abstain_b5_started"] is False
    assert value["fresh_invest_requires_fresh_b5"] is True
    assert value["historical_b5_selection_is_lineage_only"] is True
    assert all(value[field] == 0 for field in (
        "model_calls", "openai_calls", "provider_reads", "alpaca_reads", "network_calls", "broker_writes", "alpaca_orders",
    ))


def test_readiness_self_hash_verification_and_exclusive_persistence(tmp_path: Path) -> None:
    value = readiness()
    assert MODULE.verify_readiness(value, repository=ROOT, evaluation_time_utc=EVALUATION, canonical_head=BASE_HEAD) == value["artifact_hash"]
    assert value["artifact_hash"] == canonical_sha256(value, exclude_fields=("artifact_hash",))
    target = tmp_path / "readiness.json"
    MODULE.write_artifact_exclusive(target, value)
    assert json.loads(target.read_text(encoding="utf-8")) == value
    with pytest.raises(MODULE.ReadinessBlocked, match="BLOCK_ARTIFACT_EXISTS"):
        MODULE.write_artifact_exclusive(target, value)


def test_tampered_readiness_fails_closed() -> None:
    altered = deepcopy(readiness())
    altered["activation_status"] = "GRANTED"
    with pytest.raises(MODULE.ReadinessBlocked):
        MODULE.verify_readiness(altered, repository=ROOT, evaluation_time_utc=EVALUATION, canonical_head=BASE_HEAD)


def test_source_has_no_network_model_provider_or_broker_capability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not item.startswith(("requests", "http", "urllib", "socket", "openai", "alpaca")) for item in imports)
    for prohibited in ("urlopen(", "requests.", "TradingClient", "submit_order", "create_order", "OPENAI_API_KEY"):
        assert prohibited not in source


def test_cli_writes_a_metadata_only_readiness_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "current_head", lambda repository: BASE_HEAD)
    output = io.StringIO()
    target = tmp_path / "readiness.json"
    assert MODULE.main(["--repository", str(ROOT), "--artifact-path", str(target)], output=output) == 0
    text = output.getvalue()
    assert "TTL_STATUS=TTL_EXPIRED" in text
    assert "MAX_CALL_COUNT=1" in text
    assert json.loads(target.read_text(encoding="utf-8"))["activation_status"] == "NOT_GRANTED"
