from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
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
FROZEN_HEAD = "814895777015cfbf47a1be03c028b65030cab2df"
EXPECTED_RUNTIME_JUDGE_INPUT_HASH = "777c996cb92301fb1fd64a6e89eada81e56404e5b434e1bfe7b4808799b9d2f4"
EXPECTED_RUNTIME_JUDGE_CONTEXT_HASH = "1ea8ab5298b583fa2c62910f3fed43ebcfb5835f6aa352add28d2e7d4701acff"
EXPECTED_RUNTIME_REQUEST_HASH = "1850c20fcf2173381b60d5a16589dcddc9400cb85de03bb74cfd3899ffe1cacd"
RUNTIME_FILES = (
    ".aic-runtime/b4_recovered_decision_ttl_lineage_v0_1__fc4d73a__5500332.json",
    ".aic-runtime/b4_ttl_expiry_review_preflight_zero_call_v0_1__fc4d73a__7d8e0d4.json",
    ".aic-runtime/b3_research_reopen_final_competition_closure_zero_call_v0_1.json",
    ".aic-runtime/b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json",
    ".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json",
    ".aic-runtime/b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json",
    ".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json",
    ".aic-runtime/b4_post_research_reopen_rebuttal_council_freeze_v0_1.json",
    ".aic-runtime/b4_judge_selected_model_authority_v0_1.json",
    ".aic-runtime/b4_judge_model_eval_v0_1.json",
    ".aic-runtime/b4_judge_model_eval_paid_receipts_v0_1.jsonl",
    ".aic-runtime/b4_post_research_reopen_current_judge_raw_response_v0_4__40d7f5c.json",
    ".aic-runtime/b4_post_research_reopen_current_judge_captured_response_recovery_v0_1__442e8d7.json",
)
requires_runtime = pytest.mark.skipif(
    not all((ROOT / path).is_file() for path in RUNTIME_FILES),
    reason="immutable local .aic-runtime evidence is unavailable",
)


def _install_hermetic_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    lineage = SimpleNamespace(
        raw_response_hash=MODULE.HISTORICAL_RAW_RESPONSE_HASH,
        provider_response_id=MODULE.HISTORICAL_PROVIDER_RESPONSE_ID,
        recovered_artifact_hash=MODULE.RECOVERED_B4_ARTIFACT_HASH,
        decision_created_at_utc=datetime.fromisoformat("2026-09-01T08:53:32+00:00"),
    )
    receipt = {"artifact_hash": "a" * 64}
    source_entry = {
        "b3_final_closure_hash": "b" * 64,
        "current_initial_freeze_hash": "c" * 64,
        "current_rebuttal_freeze_hash": "d" * 64,
        "artifact_hash": "e" * 64,
    }
    entry = {"artifact_hash": "f" * 64}
    gate = {"artifact_hash": "0" * 64}
    context = SimpleNamespace(judge_input_hash="1" * 64, context_hash="2" * 64)
    request = SimpleNamespace(
        request_hash="3" * 64,
        request_payload={
            "model": "gpt-5.6-terra",
            "reasoning": {"effort": "medium"},
            "max_output_tokens": 8192,
            "model_run_ref": MODULE.MODEL_RUN_REF,
        },
    )
    monkeypatch.setattr(MODULE, "verify_inactive_proposal", lambda repository: MODULE.PROPOSAL_POLICY_HASH)
    monkeypatch.setattr(
        MODULE,
        "verify_expired_ttl_lineage",
        lambda repository, evaluation_time_utc: (lineage, receipt, "2026-09-01T10:53:32Z"),
    )
    monkeypatch.setattr(MODULE, "verify_canonical_ttl_preflight", lambda repository: "4" * 64)
    monkeypatch.setattr(MODULE, "_source_inputs", lambda repository: (source_entry, context, entry, context, gate))
    monkeypatch.setattr(MODULE, "build_prospective_judge_context", lambda **kwargs: context)
    monkeypatch.setattr(MODULE, "build_prospective_request", lambda **kwargs: request)


def hermetic_readiness(monkeypatch: pytest.MonkeyPatch, head: str = "a" * 40) -> dict[str, object]:
    _install_hermetic_dependencies(monkeypatch)
    return MODULE.build_readiness(
        repository=ROOT,
        evaluation_time_utc=EVALUATION,
        readiness_repository_head=head,
    )


def test_inactive_proposal_is_exact_and_remains_no_authority() -> None:
    assert MODULE.verify_inactive_proposal(ROOT) == MODULE.PROPOSAL_POLICY_HASH


def test_frozen_semantic_source_hashes_are_exact_and_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert MODULE.verify_frozen_judge_source_files(ROOT) == MODULE.FROZEN_JUDGE_SOURCE_FILE_SHA256
    target = "src/aic/council/request.py"
    monkeypatch.setitem(MODULE.FROZEN_JUDGE_SOURCE_FILE_SHA256, target, "0" * 64)
    with pytest.raises(MODULE.ReadinessBlocked, match="BLOCK_FROZEN_JUDGE_SOURCE_DRIFT"):
        MODULE.verify_frozen_judge_source_files(ROOT)


def test_repository_head_is_outer_provenance_not_request_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    one = hermetic_readiness(monkeypatch, "a" * 40)
    two = hermetic_readiness(monkeypatch, "b" * 40)
    stable = (
        "prospective_judge_input_hash",
        "prospective_judge_context_hash",
        "prospective_request_hash",
        "request_body_utf8_bytes",
        "input_tokens_upper_bound",
        "judge_max_cost_usd",
    )
    assert all(one[field] == two[field] for field in stable)
    assert one["readiness_repository_head"] != two["readiness_repository_head"]
    assert one["artifact_hash"] != two["artifact_hash"]
    assert one["request_identity_independent_of_readiness_repository_head"] is True
    assert one["canonical_base_head"] == FROZEN_HEAD
    assert one["source_judge_code_commit_sha"] == FROZEN_HEAD


def test_hermetic_artifact_preserves_no_authority_cost_and_post_judge_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    value = hermetic_readiness(monkeypatch)
    assert value["ttl_status"] == "TTL_EXPIRED"
    assert value["trigger"] == "TTL_EXPIRY"
    assert value["proposal_active"] is False
    assert value["proposal_status"] == "DRAFT_NOT_AUTHORITY"
    assert value["activation_status"] == "NOT_GRANTED"
    assert value["cost_approval_status"] == "NOT_GRANTED"
    assert value["max_call_count"] == 1
    assert value["automatic_retries"] == 0
    assert value["model_calls_authorized"] is False
    assert value["provider_reads_authorized"] is False
    assert value["broker_write_authority"] is False
    assert value["live_execution"] is False
    assert value["watch_b5_started"] is False
    assert value["abstain_b5_started"] is False
    assert value["fresh_invest_requires_fresh_b5"] is True
    assert value["pricing_hash"] == MODULE.EXPECTED_PRICING_HASH
    assert value["pricing_version"] == MODULE.EXPECTED_PRICING_VERSION
    assert value["input_token_upper_bound_method"] == "CONSERVATIVE_ONE_UTF8_BYTE_PER_INPUT_TOKEN_ALL_INPUT_CACHE_WRITE"
    assert value["long_context_multiplier_applied"] is False
    assert all(value[field] == 0 for field in (
        "model_calls", "openai_calls", "provider_reads", "alpaca_reads", "network_calls", "broker_writes", "alpaca_orders",
    ))


def test_pricing_loader_and_upper_bound_are_cache_write_aware() -> None:
    pricing = MODULE.load_initial_runtime_pricing(ROOT / MODULE.PRICING_PATH)
    assert pricing["pricing_hash"] == MODULE.EXPECTED_PRICING_HASH
    assert pricing["pricing_version"] == MODULE.EXPECTED_PRICING_VERSION
    cost = MODULE.runtime_cost_upper_bound_usd(
        model="gpt-5.6-terra",
        input_tokens_upper_bound=155454,
        output_tokens_upper_bound=8192,
        call_count=1,
        pricing=pricing,
    )
    assert format(cost, "f") == "0.486939"


def test_ttl_request_identity_and_historical_identifier_guards_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    context = SimpleNamespace(
        model_input={"semantic": "fresh"},
        judge_input_hash="4" * 64,
        mandate_version="M1",
        deep_comparison_id="D1",
        allowed_claim_ids=(),
        allowed_dispute_refs=(),
        allowed_conflict_refs=(),
        allowed_unknown_refs=(),
        allowed_condition_refs=(),
    )
    entry = {
        "candidate_order": ["NVDA"],
        "council_policy_version": "C1",
        "judge_policy_version": "J1",
        "model_policy_version": "P1",
    }
    fresh = SimpleNamespace(
        request_hash="5" * 64,
        request_payload={
            "model": "gpt-5.6-terra", "reasoning": {"effort": "medium"}, "max_output_tokens": 8192,
            "response_format": {"model_run_ref": MODULE.MODEL_RUN_REF},
        },
    )
    monkeypatch.setattr(MODULE, "build_bounded_judge_request", lambda **kwargs: fresh)
    monkeypatch.setattr(MODULE, "assert_bounded_request_invariants", lambda request: None)
    assert MODULE.build_prospective_request(entry=entry, context=context) is fresh
    reused = deepcopy(fresh)
    reused.request_hash = MODULE.HISTORICAL_REQUEST_HASH
    monkeypatch.setattr(MODULE, "build_bounded_judge_request", lambda **kwargs: reused)
    with pytest.raises(MODULE.ReadinessBlocked, match="BLOCK_HISTORICAL_REQUEST_REUSE"):
        MODULE.build_prospective_request(entry=entry, context=context)


def test_hermetic_self_hash_exclusive_persistence_and_granted_tamper_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    value = hermetic_readiness(monkeypatch)
    assert MODULE.verify_readiness(value, repository=ROOT, evaluation_time_utc=EVALUATION, readiness_repository_head="a" * 40) == value["artifact_hash"]
    assert value["artifact_hash"] == canonical_sha256(value, exclude_fields=("artifact_hash",))
    target = tmp_path / "readiness.json"
    MODULE.write_artifact_exclusive(target, value)
    assert json.loads(target.read_text(encoding="utf-8")) == value
    with pytest.raises(MODULE.ReadinessBlocked, match="BLOCK_ARTIFACT_EXISTS"):
        MODULE.write_artifact_exclusive(target, value)
    altered = deepcopy(value)
    altered["activation_status"] = "GRANTED"
    with pytest.raises(MODULE.ReadinessBlocked):
        MODULE.verify_readiness(altered, repository=ROOT, evaluation_time_utc=EVALUATION, readiness_repository_head="a" * 40)


def test_source_has_no_network_model_provider_or_broker_capability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not item.startswith(("requests", "http", "urllib", "socket", "openai", "alpaca")) for item in imports)
    for prohibited in ("urlopen(", "requests.", "TradingClient", "submit_order", "create_order", "OPENAI_API_KEY"):
        assert prohibited not in source


@requires_runtime
def test_runtime_readiness_binds_exact_inactive_policy_and_expired_ttl_lineage() -> None:
    value = MODULE.build_readiness(repository=ROOT, evaluation_time_utc=EVALUATION, readiness_repository_head="9" * 40)
    assert value["source_raw_b4_canonical_hash"] == MODULE.HISTORICAL_RAW_RESPONSE_HASH
    assert value["source_recovered_b4_artifact_hash"] == MODULE.RECOVERED_B4_ARTIFACT_HASH
    assert value["decision_created_at_utc"] == "2026-09-01T08:53:32Z"
    assert value["decision_expires_at_utc"] == "2026-09-01T10:53:32Z"
    assert value["frozen_judge_source_verified"] is True
    assert value["source_judge_code_commit_sha"] == FROZEN_HEAD


@requires_runtime
def test_runtime_request_reconstruction_has_exact_stable_prospective_values() -> None:
    value = MODULE.build_readiness(repository=ROOT, evaluation_time_utc=EVALUATION, readiness_repository_head="9" * 40)
    source_entry, source_context, entry, context, _ = MODULE._source_inputs(ROOT)
    prospective = MODULE.build_prospective_judge_context(
        source_entry=source_entry,
        source_context=source_context,
        entry=entry,
        context=context,
        policy_hash=str(value["proposal_policy_hash"]),
        ttl_receipt_hash=str(value["source_ttl_lineage_receipt_hash"]),
    )
    request = MODULE.build_prospective_request(entry=entry, context=prospective)
    payload = json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert value["prospective_model_run_ref"] == MODULE.MODEL_RUN_REF
    assert value["prospective_judge_input_hash"] == prospective.judge_input_hash == EXPECTED_RUNTIME_JUDGE_INPUT_HASH
    assert value["prospective_judge_context_hash"] == prospective.context_hash == EXPECTED_RUNTIME_JUDGE_CONTEXT_HASH
    assert value["prospective_request_hash"] == request.request_hash == EXPECTED_RUNTIME_REQUEST_HASH
    assert value["request_body_utf8_bytes"] == value["input_tokens_upper_bound"] == 155454
    assert value["judge_max_cost_usd"] == "0.486939"
    assert MODULE.HISTORICAL_PROVIDER_RESPONSE_ID not in payload
    assert MODULE.HISTORICAL_RAW_RESPONSE_HASH not in payload
    assert MODULE.HISTORICAL_MODEL_RUN_REF not in payload
    assert request.request_hash != MODULE.HISTORICAL_REQUEST_HASH


@requires_runtime
def test_runtime_request_identity_is_stable_across_outer_repository_heads() -> None:
    one = MODULE.build_readiness(repository=ROOT, evaluation_time_utc=EVALUATION, readiness_repository_head="1" * 40)
    two = MODULE.build_readiness(repository=ROOT, evaluation_time_utc=EVALUATION, readiness_repository_head="2" * 40)
    for field in (
        "prospective_judge_input_hash", "prospective_judge_context_hash", "prospective_request_hash",
        "request_body_utf8_bytes", "input_tokens_upper_bound", "judge_max_cost_usd",
    ):
        assert one[field] == two[field]
    assert one["artifact_hash"] != two["artifact_hash"]


@requires_runtime
def test_runtime_cli_writes_a_metadata_only_readiness_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MODULE, "current_head", lambda repository: "9" * 40)
    output = io.StringIO()
    target = tmp_path / "readiness.json"
    assert MODULE.main(["--repository", str(ROOT), "--artifact-path", str(target)], output=output) == 0
    assert "TTL_STATUS=TTL_EXPIRED" in output.getvalue()
    assert json.loads(target.read_text(encoding="utf-8"))["activation_status"] == "NOT_GRANTED"
