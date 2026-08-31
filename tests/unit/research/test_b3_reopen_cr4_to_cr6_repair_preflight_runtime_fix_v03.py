from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aic.data.providers.alpaca_news import AlpacaNewsArticle
from aic.data.providers.alpaca_news_reopen import (
    ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
    AlpacaNewsReopenRead,
)
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v03 as fix
from aic.research import reopen_judge_cr4_to_cr6_repair_preflight_v01 as base
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v02 as durable_v02


def _synthetic_reopen_read() -> AlpacaNewsReopenRead:
    ts = datetime(2026, 8, 31, 8, 58, 17, tzinfo=UTC)
    article = AlpacaNewsArticle(
        article_id=1,
        headline="Synthetic NVDA headline",
        summary="Synthetic summary",
        content="Synthetic content",
        author="Synthetic Author",
        source="synthetic",
        url="https://example.com/nvda",
        symbols=("NVDA",),
        created_at=ts,
        updated_at=ts,
        content_hash="0" * 64,
    )
    return AlpacaNewsReopenRead.build(
        pagination_version=ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
        symbol="NVDA",
        window_start=datetime(2026, 8, 28, 17, 34, tzinfo=UTC),
        window_end=ts,
        retrieved_at=datetime(2026, 8, 31, 9, 31, 21, 488444, tzinfo=UTC),
        page_size=5,
        page_count=2,
        max_pages=2,
        articles=(article,),
        page_raw_payload_hashes=("1" * 64, "2" * 64),
        terminal_next_page_token="token",
        pagination_complete=False,
    )


def test_v03_regression_preserves_typed_hash_surface_for_json_serialized_payload() -> None:
    read = _synthetic_reopen_read()
    serialized = read.model_dump(mode="json")

    assert canonical_sha256(
        serialized,
        exclude_fields=("aggregate_payload_hash",),
    ) != read.aggregate_payload_hash
    assert AlpacaNewsReopenRead.model_validate(serialized).aggregate_payload_hash == read.aggregate_payload_hash


def test_v03_original_result_routes_to_corrected_durable_v02_verifier(monkeypatch) -> None:
    observed = {}

    def fake_verify(payload):
        observed["payload"] = payload
        return {
            "result_artifact_hash": base.EXPECTED_ORIGINAL_RESULT_HASH,
            "nvda_terminal_next_page_token": base.EXPECTED_NVDA_CONTINUATION_TOKEN,
            "nvda_retained_article_count": base.EXPECTED_NVDA_RETAINED_ARTICLE_COUNT,
            "nvda_aggregate_validation_surface": fix.ORIGINAL_RESULT_VALIDATION_SURFACE,
        }

    monkeypatch.setattr(durable_v02, "verify_result_v02", fake_verify)
    payload = {"saved": "json-provider-result"}
    summary = fix.verify_original_result_v03(payload)

    assert observed["payload"] is payload
    assert summary["nvda_aggregate_validation_surface"] == fix.ORIGINAL_RESULT_VALIDATION_SURFACE


def test_v03_build_scopes_legacy_dependency_override_and_restores_it(monkeypatch) -> None:
    original = base.verify_original_result
    probe = {
        "capability_probe_hash": "a" * 64,
        "alpaca_binary_sha256": "b" * 64,
    }

    def fake_build(**kwargs):
        assert base.verify_original_result is fix.verify_original_result_v03
        return {
            "artifact_version": base.ARTIFACT_VERSION,
            "status": base.PASS_STATUS,
            "code_commit_sha": kwargs["code_commit_sha"],
            "artifact_hash": "0" * 64,
        }

    monkeypatch.setattr(base, "build_preflight", fake_build)
    artifact = fix.build_preflight(
        reconciliation={},
        original_result={},
        code_commit_sha="f" * 40,
        capability_probe=probe,
    )

    assert base.verify_original_result is original
    assert artifact["preflight_runtime_fix_version"] == fix.RUNTIME_FIX_VERSION
    assert artifact["original_result_validation_surface"] == fix.ORIGINAL_RESULT_VALIDATION_SURFACE
    assert artifact["legacy_v01_json_dict_rehash_allowed"] is False
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )


def test_v03_runner_is_zero_call_and_uses_new_output_path() -> None:
    source = Path(
        "scripts/b3_research_reopen_cr4_to_cr6_repair_preflight_zero_call_v03.py"
    ).read_text(encoding="utf-8")
    assert "--execute-provider-reads" not in source
    assert '"order", "submit"' not in source
    assert "preflight_zero_call_v0_3.json" in source
    assert "ORIGINAL_RESULT_TYPED_VALIDATION=PASS" in source


def test_v03_source_cannot_route_original_result_to_legacy_v01_verify_result() -> None:
    source = Path(
        "src/aic/research/reopen_judge_cr4_to_cr6_repair_preflight_runtime_fix_v03.py"
    ).read_text(encoding="utf-8")
    assert "durable_v02.verify_result_v02" in source
    assert "original_failure_v01.verify_result" not in source
    assert "legacy_v01_json_dict_rehash_allowed" in source
