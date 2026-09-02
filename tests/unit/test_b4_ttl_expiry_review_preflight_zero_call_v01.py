from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from aic.domain.canonical import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/b4_ttl_expiry_review_preflight_zero_call_v01.py"
SPEC = importlib.util.spec_from_file_location("b4_ttl_expiry_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _fake_ttl_module() -> SimpleNamespace:
    lineage = SimpleNamespace(
        raw_response_hash="fc4d73a86a178c03e1acbda64f176df4bd4fe225227832fcd5b286fa2c77e37d",
        recovered_artifact_hash="f9a9e08a30b58ebf6fcb358c2b35a82717682ddef3ac5fd58c912d518d3fadf0",
        decision_created_at_utc=datetime(2026, 9, 1, 8, 53, 32, tzinfo=UTC),
    )

    def evaluate(item, when):
        expiry = item.decision_created_at_utc + timedelta(seconds=7200)
        return "39123", "TTL_EXPIRED" if when > expiry else "TTL_VALID", expiry

    return SimpleNamespace(
        RAW_CAPTURE_RELATIVE_PATH=Path("ignored-raw.json"),
        RECOVERED_RELATIVE_PATH=Path("ignored-recovered.json"),
        POLICY_RELATIVE_PATH=Path("ignored-policy.json"),
        load_json=lambda path, field: {},
        recover_lineage=lambda **kwargs: lineage,
        evaluate_ttl=evaluate,
    )


def _receipt(tmp_path: Path) -> Path:
    receipt = {
        "artifact_version": "B4_RECOVERED_DECISION_TTL_LINEAGE_v0_1",
        "source_raw_response_sha256": "fc4d73a86a178c03e1acbda64f176df4bd4fe225227832fcd5b286fa2c77e37d",
        "recovered_b4_artifact_hash": "f9a9e08a30b58ebf6fcb358c2b35a82717682ddef3ac5fd58c912d518d3fadf0",
        "decision_expires_at_utc": "2026-09-01T10:53:32Z",
    }
    receipt["artifact_hash"] = canonical_sha256(receipt, exclude_fields=("artifact_hash",))
    path = tmp_path / "ttl-lineage.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _artifact(tmp_path: Path) -> dict[str, object]:
    return MODULE.build_preflight(
        repository=ROOT,
        evaluation_time_utc=datetime(2026, 9, 1, 19, 45, 35, tzinfo=UTC),
        canonical_head=MODULE.CANONICAL_HEAD,
        ttl_module=_fake_ttl_module(),
        lineage_receipt_path=_receipt(tmp_path),
    )


def test_exact_frozen_policies_self_hash_and_commit_ttl_requirement() -> None:
    frozen = MODULE.verify_frozen_authorities(ROOT)
    assert "decision_ttl_valid_required=true" in frozen["options_policy"]


def test_review_schemas_prove_ttl_trigger_timestamp_and_reevaluation_workflow() -> None:
    contracts = MODULE.verify_canonical_review_contracts(ROOT)
    assert contracts["ttl_expiry_review_trigger"].endswith("NRT-V01")
    assert "REEVALUATION_STARTED" in contracts["review_workflow"]


def test_expired_lineage_requires_new_decision_and_never_timestamp_refresh(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    assert artifact["ttl_status"] == "TTL_EXPIRED"
    assert artifact["review_trigger_required"] is True
    assert artifact["new_decision_required"] is True
    assert artifact["old_decision_can_be_made_valid_zero_call"] is False
    assert artifact["new_final_decision_by_timestamp_refresh_allowed"] is False
    assert artifact["b6_ready_for_paper_send"] is False


def test_authority_gap_fails_closed_as_underspecified(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    assert artifact["provider_refresh_required_before_model"] == "UNDERSPECIFIED"
    assert artifact["model_stage_scope_required"] == "UNDERSPECIFIED"
    assert artifact["preflight_outcome"] == "TTL_REVIEW_SCOPE_UNDERSPECIFIED"


def test_reuse_matrix_is_complete_and_deterministic() -> None:
    matrix = MODULE.reuse_matrix()
    assert len(matrix) == 15
    assert matrix["b4_judge_output"]["classification"] == "FRESH_MODEL_DECISION_REQUIRED"
    assert matrix["b4_recovered_decision_artifact"]["classification"] == "REUSABLE_AS_IMMUTABLE_LINEAGE"
    assert matrix["b5_option_quote_snapshots"]["classification"] == "FRESH_PROVIDER_READ_REQUIRED"
    assert matrix["human_approval"]["classification"] == "NOT_APPLICABLE"
    assert all("authority_invariant" in item for item in matrix.values())


def test_artifact_has_canonical_self_hash_and_zero_capabilities(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert artifact["model_calls_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert all(artifact[key] == 0 for key in ("model_calls", "openai_calls", "alpaca_reads", "broker_writes", "alpaca_orders", "network_calls"))


def test_exclusive_persistence_blocks_overwrite(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    output = tmp_path / "artifact.json"
    MODULE.write_artifact_exclusive(output, artifact)
    with pytest.raises(MODULE.PreflightBlocked, match="BLOCK_ARTIFACT_EXISTS"):
        MODULE.write_artifact_exclusive(output, artifact)


def test_wrong_canonical_head_blocks_before_artifact_creation(tmp_path: Path) -> None:
    with pytest.raises(MODULE.PreflightBlocked, match="BLOCK_CANONICAL_HEAD"):
        MODULE.build_preflight(
            repository=ROOT,
            evaluation_time_utc=datetime(2026, 9, 1, 19, 45, 35, tzinfo=UTC),
            canonical_head="0" * 40,
            ttl_module=_fake_ttl_module(),
            lineage_receipt_path=_receipt(tmp_path),
        )


def test_script_has_no_network_model_provider_or_broker_execution_capability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not module.startswith(("requests", "http", "urllib", "socket", "openai", "alpaca")) for module in imports)
    for prohibited in ("urlopen(", "requests.", "TradingClient", "submit_order", "create_order", "OPENAI_API_KEY"):
        assert prohibited not in source
    assert "TTL_REVIEW_SCOPE_UNDERSPECIFIED" in source


def test_cli_prints_underspecified_zero_call_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setattr(MODULE, "build_preflight", lambda **kwargs: artifact)
    monkeypatch.setattr(MODULE, "current_head", lambda repository: MODULE.CANONICAL_HEAD)
    output = io.StringIO()
    destination = tmp_path / "cli-artifact.json"
    assert MODULE.main(["--repository", str(ROOT), "--artifact-path", str(destination)], output=output) == 0
    assert "PREFLIGHT_OUTCOME=TTL_REVIEW_SCOPE_UNDERSPECIFIED" in output.getvalue()
    assert destination.is_file()
