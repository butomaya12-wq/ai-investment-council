from __future__ import annotations

from pathlib import Path

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_final_competition_closure_v01 as runtime


class _Article:
    def __init__(self, article_id: int):
        self.article_id = article_id


class _Read:
    def __init__(self, ids):
        self.articles = tuple(_Article(value) for value in ids)


def test_build_final_closure_uses_decision_usable_negative_epistemic_status(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "_verify_s00", lambda _p: runtime.EXPECTED_S00_HASH)
    valuation = {
        "msft": {"price_to_reported_annual_gaap_diluted_eps": "28.821727019499"},
        "meta": {"price_to_reported_annual_gaap_diluted_eps": "24.550021285653"},
        "derived_relative_view": {"msft_pe_premium_vs_meta_ratio": "0.174000082694118851"},
        "interpretive_boundary": "RELATIVE_POINT_IN_TIME_CONTEXT_ONLY; DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS, FORWARD_EARNINGS_POWER, OR FAIR_VALUE",
    }
    monkeypatch.setattr(runtime, "_verify_local_replay", lambda _p: (runtime.EXPECTED_LOCAL_REPLAY_HASH, valuation))
    monkeypatch.setattr(runtime, "_verify_original_nvda", lambda _p: _Read(range(1, 11)))
    monkeypatch.setattr(runtime, "_verify_wire_msft", lambda _p: _Read(range(101, 109)))
    rr1 = {"response_payload": {"base_value": 100000, "timeframe": "1D", "equity": [0, 0, 100000, 100000, 100000]}}
    monkeypatch.setattr(runtime, "_verify_repair_result", lambda _r, _a: (runtime.EXPECTED_REPAIR_RESULT_HASH, rr1))
    monkeypatch.setattr(
        runtime,
        "_salvaged_nvda",
        lambda _d: (
            runtime.EXPECTED_RR3_SALVAGED_SHA,
            {"news": [{"id": value} for value in range(11, 16)], "next_page_token": "TOKEN"},
        ),
    )

    artifact = runtime.build_final_closure(
        code_commit_sha="a" * 40,
        s00={},
        local_replay={},
        original_result={},
        wire_v02_result={},
        repair_result={},
        repair_authorization={},
        repair_raw_dir=tmp_path,
    )

    assert artifact["status"] == runtime.PASS_STATUS
    assert artifact["remaining_canonical_reopen_requirement_ids"] == []
    assert artifact["canonical_research_reopen_closed"] is True
    assert artifact["additional_provider_read_required_before_b4"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["next_gate"] == runtime.NEXT_GATE

    closures = {row["requirement_id"]: row for row in artifact["requirement_closures"]}
    assert closures["NVDA_CURRENT_DEVELOPMENTS_Q4"]["closure_status"] == "CLOSED_DECISION_USABLE_NONEXHAUSTIVE"
    assert closures["NVDA_CURRENT_DEVELOPMENTS_Q4"]["combined_unique_article_count"] == 15
    assert closures["MSFT_VALUATION_CONTEXT_DEPTH"]["closure_status"] == "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED"
    assert closures["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]["closure_status"] == "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK"
    assert "NO_POSITIVE_EXTRAPOLATION" in closures["MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]["decision_rule"]

    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert runtime.verify_final_closure(artifact, expected_code_commit_sha="a" * 40) == artifact["artifact_hash"]


def test_closure_preserves_meta_conditions_as_b4_context_not_canonical_reopen(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "_verify_s00", lambda _p: runtime.EXPECTED_S00_HASH)
    valuation = {
        "msft": {"price_to_reported_annual_gaap_diluted_eps": "28.821727019499"},
        "meta": {"price_to_reported_annual_gaap_diluted_eps": "24.550021285653"},
        "derived_relative_view": {"msft_pe_premium_vs_meta_ratio": "0.174000082694118851"},
        "interpretive_boundary": "RELATIVE_POINT_IN_TIME_CONTEXT_ONLY; DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS, FORWARD_EARNINGS_POWER, OR FAIR_VALUE",
    }
    monkeypatch.setattr(runtime, "_verify_local_replay", lambda _p: (runtime.EXPECTED_LOCAL_REPLAY_HASH, valuation))
    monkeypatch.setattr(runtime, "_verify_original_nvda", lambda _p: _Read(range(1, 11)))
    monkeypatch.setattr(runtime, "_verify_wire_msft", lambda _p: _Read(range(101, 109)))
    monkeypatch.setattr(runtime, "_verify_repair_result", lambda _r, _a: (runtime.EXPECTED_REPAIR_RESULT_HASH, {"response_payload": {"base_value": 100000, "timeframe": "1D", "equity": [0, 0, 100000, 100000, 100000]}}))
    monkeypatch.setattr(runtime, "_salvaged_nvda", lambda _d: (runtime.EXPECTED_RR3_SALVAGED_SHA, {"news": [{"id": value} for value in range(11, 16)], "next_page_token": "TOKEN"}))

    artifact = runtime.build_final_closure(
        code_commit_sha="b" * 40,
        s00={}, local_replay={}, original_result={}, wire_v02_result={}, repair_result={}, repair_authorization={}, repair_raw_dir=tmp_path,
    )
    assert artifact["judge_meta_change_conditions_preserved_as_b4_decision_context"] is True
    assert artifact["judge_meta_change_conditions_reclassified_as_canonical_reopen_requirements"] is False
    assert artifact["rr2_dynamic_market_context_transport_failure_is_canonical_blocker"] is False


def test_zero_call_runner_has_no_provider_or_model_execution_switch():
    text = Path("scripts/b3_research_reopen_close_to_b4_zero_call_v01.py").read_text(encoding="utf-8")
    assert "--execute-provider-reads" not in text
    assert '"data", "news"' not in text
    assert '"data", "multi-bars"' not in text
    assert '"account", "portfolio"' not in text
    assert "openai" not in text.lower()
    assert "PROVIDER_READS=0" in text
    assert "MODEL_CALLS=0" in text
