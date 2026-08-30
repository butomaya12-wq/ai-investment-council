from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aic.council.initial_schema_repair_v05 import build_bounded_initial_request_v05
from aic.council.model_input import B4ComputedValueView
from aic.council.model_policy import INITIAL_MODEL_LADDER
from aic.council.models import CouncilInputBundle
from aic.council.reopen_production_cost_preflight import (
    COST_AUTHORITY_MODE,
    EXPECTED_CLOSURE_HASH,
    EXPECTED_OVERLAY_HASH,
    REOPEN_MODEL_INPUT_VERSION,
    _source_material_claim_enum,
    build_reopen_model_input,
    derive_effective_bundle,
    materialize_supplemental_material_claim,
)
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1


def _claim(*, claim_id: str, candidate: str, text: str) -> dict:
    payload = {
        "claim_id": claim_id,
        "candidate_id": candidate,
        "category": "test",
        "claim_text": text,
        "claim_kind": "FACT",
        "materiality": "MATERIAL",
        "evidence_ids": [f"EVID_{claim_id}"],
        "computed_value_ids": [],
        "conflict_ids": [],
        "assumptions": [],
        "support_status": "SUPPORTED",
        "uncertainty_note": None,
    }
    payload["claim_hash"] = canonical_sha256(payload)
    return MATERIAL_CLAIM_V1.model_validate(payload).model_dump(
        mode="json", exclude_none=False, warnings=False
    )


def _bundle(*, claim_ids: tuple[str, ...]) -> CouncilInputBundle:
    return CouncilInputBundle.from_unhashed(
        bundle_id="B4_TEST_BUNDLE",
        candidate_id="MSFT",
        candidate_packet_id="PACKET_MSFT",
        candidate_packet_hash="a" * 64,
        research_snapshot_id="RESEARCH_MSFT",
        research_snapshot_hash="b" * 64,
        b2_snapshot_id="B2_TEST",
        deep_comparison_id="DEEP_TEST",
        mandate_version="MANDATE_TEST",
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        allowed_material_claim_ids=claim_ids,
        allowed_computed_value_ids=(),
        allowed_conflict_ids=(),
        shared_portfolio_context_refs=(),
        created_at=datetime(2026, 8, 29, tzinfo=UTC),
    )


def test_supplemental_closure_fact_materializes_as_canonical_material_claim() -> None:
    raw = {
        "claim_id": "B3_REOPEN_SUPPLEMENTAL_MSFT_VALUATION_001",
        "candidate_id": "MSFT",
        "category": "valuation_context",
        "claim_kind": "FACT",
        "support_status": "SUPPORTED",
        "evidence_ids": ["B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z"],
        "claim_text": "MSFT point-in-time valuation evidence is available.",
    }
    materialized = materialize_supplemental_material_claim(raw)
    assert materialized["claim_id"] == raw["claim_id"]
    assert materialized["candidate_id"] == "MSFT"
    assert materialized["claim_kind"] == "FACT"
    assert materialized["materiality"] == "MATERIAL"
    assert materialized["support_status"] == "SUPPORTED"
    assert materialized["computed_value_ids"] == []
    assert materialized["conflict_ids"] == []
    assert materialized["assumptions"] == []
    assert materialized["uncertainty_note"] is None
    assert materialized["claim_hash"] == canonical_sha256(
        materialized, exclude_fields=("claim_hash",)
    )
    MATERIAL_CLAIM_V1.model_validate(materialized)


def test_effective_bundle_is_additive_and_preserves_historical_candidate_packet_hash() -> None:
    historical = _bundle(claim_ids=("LEGACY_1",))
    effective = derive_effective_bundle(
        historical,
        effective_claim_ids=("LEGACY_1", "SUPPLEMENTAL_1"),
        supplemental_portfolio_context_refs=("PORTFOLIO_EVIDENCE_1",),
    )
    assert effective.bundle_hash != historical.bundle_hash
    assert effective.candidate_packet_hash == historical.candidate_packet_hash
    assert effective.research_snapshot_hash == historical.research_snapshot_hash
    assert effective.allowed_material_claim_ids == ("LEGACY_1", "SUPPLEMENTAL_1")
    assert effective.shared_portfolio_context_refs == ("PORTFOLIO_EVIDENCE_1",)


def test_reopen_model_input_retains_historical_packet_but_closes_effective_gaps() -> None:
    legacy_claim = _claim(claim_id="LEGACY_1", candidate="MSFT", text="Legacy fact.")
    supplemental_claim = _claim(
        claim_id="SUPPLEMENTAL_1", candidate="MSFT", text="Supplemental fact."
    )
    effective = derive_effective_bundle(
        _bundle(claim_ids=("LEGACY_1",)),
        effective_claim_ids=("LEGACY_1", "SUPPLEMENTAL_1"),
        supplemental_portfolio_context_refs=(),
    )
    legacy = SimpleNamespace(
        candidate_id="MSFT",
        candidate_packet={"candidate_id": "MSFT", "source_gaps": ["OLD_GAP"]},
        material_claims=(legacy_claim,),
        computed_values=(
            B4ComputedValueView(
                computed_value_id="CV1",
                metric_id="METRIC1",
                value="1",
                unit="USD",
            ),
        ),
        data_gap_refs=("OLD_GAP",),
    )
    payload = build_reopen_model_input(
        legacy_model_input=legacy,
        effective_bundle=effective,
        effective_material_claims=(legacy_claim, supplemental_claim),
        supplemental_evidence_units=(
            {
                "evidence_id": "E1",
                "candidate_id": "MSFT",
                "category": "valuation_context",
            },
        ),
    )
    assert payload["model_input_version"] == REOPEN_MODEL_INPUT_VERSION
    assert payload["candidate_packet"]["source_gaps"] == ["OLD_GAP"]
    assert payload["data_gap_refs"] == []
    assert payload["reopen_overlay"][
        "historical_candidate_packet_source_gaps_are_effectively_closed"
    ] is True
    assert payload["reopen_overlay"]["effective_unresolved_data_gap_refs"] == []
    assert payload["reopen_overlay"]["effective_unresolved_reopen_reason_codes"] == []
    assert payload["reopen_overlay"]["source_b3_reopen_closure_hash"] == EXPECTED_CLOSURE_HASH
    assert payload["reopen_overlay"]["source_b4_reopen_input_overlay_hash"] == EXPECTED_OVERLAY_HASH
    assert payload["model_input_hash"] == canonical_sha256(
        payload, exclude_fields=("model_input_hash",)
    )


def test_real_v05_initial_schema_allows_supplemental_fact_reference() -> None:
    legacy = _claim(claim_id="LEGACY_1", candidate="MSFT", text="Legacy fact.")
    raw_supplemental = {
        "claim_id": "SUPPLEMENTAL_1",
        "candidate_id": "MSFT",
        "category": "valuation_context",
        "claim_kind": "FACT",
        "support_status": "SUPPORTED",
        "evidence_ids": ["EVID_SUPPLEMENTAL_1"],
        "claim_text": "Supplemental valuation fact.",
    }
    supplemental = materialize_supplemental_material_claim(raw_supplemental)
    bundle = _bundle(claim_ids=("LEGACY_1", "SUPPLEMENTAL_1"))
    model_input = {
        "model_input_version": REOPEN_MODEL_INPUT_VERSION,
        "candidate_id": "MSFT",
        "council_input_bundle": bundle.model_dump(mode="json", exclude_none=False),
        "candidate_packet": {
            "candidate_id": "MSFT",
            "packet_hash": bundle.candidate_packet_hash,
        },
        "material_claims": [legacy, supplemental],
        "computed_values": [],
        "data_gap_refs": [],
        "reopen_overlay": {
            "source_b3_reopen_closure_hash": EXPECTED_CLOSURE_HASH,
            "effective_unresolved_data_gap_refs": [],
        },
    }
    model_input["model_input_hash"] = canonical_sha256(model_input)
    l2 = next(item for item in INITIAL_MODEL_LADDER if item.candidate_key == "L2")
    request = build_bounded_initial_request_v05(
        stage=CouncilRequestStage.BULL_INITIAL,
        model_candidate=l2,
        bundle=bundle,
        model_run_ref="B4_REOPEN_INITIAL_MSFT_BULL_L2_TEST",
        model_input=model_input,
        allowed_data_gap_refs=(),
    )
    schema = request.request_payload["text"]["format"]["schema"]
    allowed = _source_material_claim_enum(schema)
    assert "LEGACY_1" in allowed
    assert "SUPPLEMENTAL_1" in allowed
    assert request.request_payload["max_output_tokens"] == 4096
    assert request.request_payload["store"] is False
    assert request.request_payload["tools"] == []


def test_cost_authority_is_staged_exact_not_all_thirteen() -> None:
    assert COST_AUTHORITY_MODE == "STAGED_EXACT"


def test_zero_call_runner_has_no_execution_surface() -> None:
    text = Path(
        "scripts/b4_reopen_production_cost_preflight_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    lowered = text.lower()
    assert "load_openai_api_key" not in text
    assert "responsestransport" not in lowered
    assert "urllib" not in lowered
    assert "requests." not in lowered
    assert '["alpaca"' not in lowered
    assert "--execute" not in lowered
    assert "MODEL_CALLS=0" in text
    assert "PROVIDER_READS=0" in text
    assert "COST_AUTHORITY_MODE=STAGED_EXACT" in text
