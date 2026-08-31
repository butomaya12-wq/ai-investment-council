from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_continuation_plan_v01 as plan_v01
from aic.research import reopen_judge_residual_external_read_continuation_preflight_v01 as preflight


HEAD = "a" * 40


def _plan_payload() -> dict:
    return {
        "provider_read_bundles": plan_v01._bundles(),
    }


def test_build_preflight_reuses_five_templates_and_hashes_only_nvda_continuation(monkeypatch) -> None:
    monkeypatch.setattr(preflight, "verify_plan", lambda payload: preflight.EXPECTED_PLAN_HASH)
    monkeypatch.setattr(
        preflight,
        "verify_original_preflight",
        lambda payload: preflight.EXPECTED_ORIGINAL_PREFLIGHT_HASH,
    )

    artifact = preflight.build_preflight(
        plan=_plan_payload(),
        original_preflight={},
        code_commit_sha=HEAD,
    )

    assert artifact["status"] == preflight.PASS_STATUS
    assert artifact["request_template_hashes"][:5] == list(preflight.REUSED_TEMPLATE_HASHES.values())
    assert artifact["request_template_hashes"][5] == artifact["new_nvda_continuation_request_template_hash"]
    assert artifact["reused_original_template_count"] == 5
    assert artifact["new_continuation_template_count"] == 1
    assert artifact["provider_dispatch_attempts_max"] == 11
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["next_gate"] == preflight.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )


def test_nvda_template_hash_changes_if_saved_token_drifts() -> None:
    plan = _plan_payload()
    original = preflight._nvda_request_preflight(plan)

    tampered = deepcopy(plan)
    bundle = tampered["provider_read_bundles"][-1]
    bundle["request_contract"]["start_page_token"] = "DIFFERENT"

    with pytest.raises(
        preflight.ResidualExternalReadContinuationPreflightError,
        match="NVDA start token drift",
    ):
        preflight._nvda_request_preflight(tampered)

    assert original["request_template_hash"] == canonical_sha256(
        {k: v for k, v in original.items() if k != "request_template_hash"}
    )


def test_verify_original_preflight_rejects_reused_template_drift(monkeypatch) -> None:
    rows = []
    original_ids = (
        "ER2_MSFT_NEWS_REFRESH",
        "ER3_META_NEWS_REFRESH",
        "ER4_CURRENT_PAPER_POSITIONS",
        "ER5_CURRENT_PORTFOLIO_EQUITY",
        "ER6_DYNAMIC_MARKET_CONTEXT",
    )
    for continuation_id, original_id in zip(preflight.REUSED_TEMPLATE_HASHES, original_ids, strict=True):
        rows.append(
            {
                "bundle_id": original_id,
                "request_template_hash": preflight.REUSED_TEMPLATE_HASHES[continuation_id],
                "provider_read_authorized": False,
                "model_call_authorized": False,
            }
        )
    rows.insert(
        0,
        {
            "bundle_id": "ER1_NVDA_NEWS_REFRESH",
            "request_template_hash": "b" * 64,
            "provider_read_authorized": False,
            "model_call_authorized": False,
        },
    )
    payload = {
        "request_manifest_hash": preflight.EXPECTED_ORIGINAL_REQUEST_MANIFEST_HASH,
        "reopen_cutoff_utc": preflight.EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_reads_authorized": False,
        "owner_provider_read_approval_present": False,
        "request_preflights": rows,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    monkeypatch.setattr(preflight, "EXPECTED_ORIGINAL_PREFLIGHT_HASH", payload["artifact_hash"])

    preflight.verify_original_preflight(payload)

    bad = deepcopy(payload)
    bad["request_preflights"][1]["request_template_hash"] = "c" * 64
    bad["artifact_hash"] = canonical_sha256(
        bad,
        exclude_fields=("artifact_hash",),
    )
    monkeypatch.setattr(preflight, "EXPECTED_ORIGINAL_PREFLIGHT_HASH", bad["artifact_hash"])
    with pytest.raises(
        preflight.ResidualExternalReadContinuationPreflightError,
        match="original template hash drift",
    ):
        preflight.verify_original_preflight(bad)


def test_runner_is_zero_call_and_has_no_wall_clock_cutoff() -> None:
    source = Path(
        "scripts/b3_research_reopen_residual_external_read_continuation_preflight_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "--execute-provider-reads",
        "alpaca data",
        "alpaca position",
        "alpaca account",
        "OPENAI_API_KEY",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "datetime.now",
        "time.time",
        "submit_order",
        "order submit",
    )
    for token in forbidden:
        assert token not in source
