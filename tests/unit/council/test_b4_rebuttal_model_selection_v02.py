from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from aic.council import rebuttal_model_selection as v01
from aic.council.rebuttal_model_selection_v02 import (
    REBUTTAL_SELECTED_MODEL_AUTHORITY_VERSION_V02,
    REPLAY_CONTRACT_VERSION,
    RebuttalSelectedModelAuthorityError,
    build_rebuttal_selected_model_authority_v02,
    verify_rebuttal_selected_model_authority_v02,
)
from aic.domain.canonical import canonical_sha256


def _legacy_test_module():
    path = Path("tests/unit/council/test_b4_rebuttal_model_selection.py")
    spec = importlib.util.spec_from_file_location(
        "_aic_rebuttal_model_selection_v01_fixture",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _production_like_fixture() -> tuple[dict, list[dict]]:
    legacy = _legacy_test_module()
    artifact, receipts = legacy._fixture()

    for receipt in receipts:
        assert "result_hash" in receipt
        receipt.pop("result_hash")
        receipt["receipt_hash"] = canonical_sha256(
            receipt,
            exclude_fields=("receipt_hash",),
        )

    receipt_hashes = [row["receipt_hash"] for row in receipts]
    artifact["paid_call_receipt_hashes"] = receipt_hashes
    artifact["receipt_manifest_hash"] = canonical_sha256(
        {"receipt_hashes": receipt_hashes}
    )
    artifact["artifact_hash"] = canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )
    return artifact, receipts


def test_v01_regression_fixture_reproduces_production_result_hash_failure() -> None:
    artifact, receipts = _production_like_fixture()
    with pytest.raises(
        v01.RebuttalSelectedModelAuthorityError,
        match="result_hash is not replayable",
    ):
        v01.build_rebuttal_selected_model_authority(artifact, receipts)


def test_v02_reconstructs_result_hash_from_real_durable_receipt_shape() -> None:
    artifact, receipts = _production_like_fixture()
    assert all("result_hash" not in row for row in receipts)

    authority = build_rebuttal_selected_model_authority_v02(
        artifact,
        receipts,
    )

    assert authority["artifact_version"] == REBUTTAL_SELECTED_MODEL_AUTHORITY_VERSION_V02
    assert authority["replay_contract_version"] == REPLAY_CONTRACT_VERSION
    assert authority["replayed_result_hash_count"] == 12
    assert authority["selected_candidate"] == {
        "candidate_key": "R3",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "ladder_position": 3,
    }
    assert authority["full_ladder_pass_summary"]["R1"]["passed_cases"] == 3
    assert authority["full_ladder_pass_summary"]["R2"]["passed_cases"] == 3
    assert authority["full_ladder_pass_summary"]["R3"]["passed_cases"] == 4
    assert authority["semantic_replay_passed_cases"] == 10
    assert authority["model_calls"] == 0
    assert authority["provider_reads"] == 0
    assert authority["production_rebuttal_authorized"] is False
    assert authority["judge_authorized"] is False
    assert authority["rerun_authorized"] is False
    assert verify_rebuttal_selected_model_authority_v02(authority) == authority[
        "selection_hash"
    ]


def test_v02_rejects_candidate_result_hash_tamper_even_with_rehashed_artifact() -> None:
    artifact, receipts = _production_like_fixture()
    first_record = artifact["candidate_records"][0]
    first_case = first_record["cases"][0]
    first_case["result_hash"] = "f" * 64
    first_record["record_hash"] = canonical_sha256(
        first_record,
        exclude_fields=("record_hash",),
    )
    artifact["artifact_hash"] = canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )

    with pytest.raises(
        RebuttalSelectedModelAuthorityError,
        match="result_hash differs from durable receipt replay",
    ):
        build_rebuttal_selected_model_authority_v02(
            artifact,
            receipts,
        )


def test_v02_freeze_script_is_zero_call_and_uses_real_receipt_contract() -> None:
    text = Path(
        "scripts/b4_freeze_rebuttal_selected_model_v02.py"
    ).read_text(encoding="utf-8")
    assert "build_rebuttal_selected_model_authority_v02" in text
    assert "result_hash\" in receipt" in text
    assert "OPENAI_API_KEY" not in text
    assert "StdlibResponsesTransport" not in text
    assert '"model_calls": 0' in text
    assert '"provider_reads": 0' in text
    assert '"production_rebuttal_authorized": False' in text
    assert '"judge_authorized": False' in text
    assert '"rerun_authorized": False' in text
