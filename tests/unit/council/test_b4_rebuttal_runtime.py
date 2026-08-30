from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from aic.council import rebuttal_runtime as runtime
from aic.council.models import CouncilLane


def _request(candidate_id: str, *, request_hash: str, byte_count: int):
    payload = {
        "model": "gpt-5.6-sol",
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 6144,
        "candidate": candidate_id,
        "padding": "x" * max(0, byte_count - 300),
    }
    return SimpleNamespace(request_hash=request_hash, request_payload=payload)


def _context(candidate_id: str, index: int) -> dict:
    raw = {
        "candidate_id": candidate_id,
        "model_input": {"candidate_id": candidate_id, "frozen": True},
        "initial_opinion_ids": [f"{candidate_id}_B", f"{candidate_id}_BE", f"{candidate_id}_R"],
        "initial_opinion_hashes": [str(index + 1) * 64, str(index + 2) * 64, str(index + 3) * 64],
        "opposing_claim_ids_by_lane": {
            "BULL": [f"{candidate_id}_BEAR_C", f"{candidate_id}_RED_C"],
            "BEAR": [f"{candidate_id}_BULL_C", f"{candidate_id}_RED_C"],
            "RED_TEAM": [f"{candidate_id}_BULL_C", f"{candidate_id}_BEAR_C"],
        },
        "allowed_uncertainty_refs": [f"GAP:{candidate_id}"],
        "required_unknown_refs": [f"GAP:{candidate_id}"],
    }
    from aic.domain.canonical import canonical_sha256
    raw["context_hash"] = canonical_sha256(raw)
    return raw


def _setup(monkeypatch):
    candidates = ("NVDA", "MSFT", "META")
    freeze = SimpleNamespace(
        artifact_hash="f" * 64,
        candidate_order=candidates,
        bundles=tuple(SimpleNamespace(candidate_id=candidate) for candidate in candidates),
    )
    contexts = tuple(_context(candidate, index) for index, candidate in enumerate(candidates))
    selection = {
        "selection_hash": "8db38779171e0dcfc2e0325581192116b17adf98a1140950ffcbe5ce4698a882",
        "selected_candidate": {
            "candidate_key": "R3",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "medium",
            "ladder_position": 3,
        },
    }
    selected_model = SimpleNamespace(
        candidate_key="R3",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        ladder_position=3,
    )
    request_rows = {}
    variants = []
    for index, candidate in enumerate(candidates, start=1):
        request_hash = (hex(index)[2:] * 64)[:64]
        request_rows[candidate] = [request_hash, None]
        variants.append({
            "candidate": candidate,
            "request_hash": request_hash,
            "request_body_utf8_bytes": 0,
        })
    preflight = {
        "candidate_order": list(candidates),
        "selected_model_authority_selection_hash": selection["selection_hash"],
        "selected_request_variants": variants,
    }

    monkeypatch.setattr(runtime, "verify_rebuttal_runtime_request_preflight", lambda _: "p" * 64)
    monkeypatch.setattr(
        runtime,
        "verify_rebuttal_selected_model_authority_v02",
        lambda _: selection["selection_hash"],
    )
    monkeypatch.setattr(runtime, "_selected_model", lambda: selected_model)

    def fake_builder(*, model_candidate, bundle, model_input, initial_opinion_ids, initial_opinion_hashes, opposing_claim_ids_by_lane, allowed_uncertainty_refs):
        assert model_candidate is selected_model
        assert bundle.candidate_id == model_input["candidate_id"]
        assert len(initial_opinion_ids) == 3
        assert len(initial_opinion_hashes) == 3
        assert set(opposing_claim_ids_by_lane) == {CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM}
        assert allowed_uncertainty_refs == (f"GAP:{bundle.candidate_id}",)
        row = request_rows[bundle.candidate_id]
        request = _request(bundle.candidate_id, request_hash=row[0], byte_count=600)
        from aic.council.initial_runtime import request_body_utf8_bytes
        row[1] = request_body_utf8_bytes(request.request_payload)
        return request

    monkeypatch.setattr(runtime, "build_bounded_rebuttal_request_v01", fake_builder)
    return freeze, contexts, selection, preflight, request_rows


def test_runtime_plan_reconstructs_exact_three_selected_requests(monkeypatch) -> None:
    freeze, contexts, selection, preflight, rows = _setup(monkeypatch)
    for candidate, variant in zip(freeze.candidate_order, preflight["selected_request_variants"], strict=True):
        request = _request(candidate, request_hash=variant["request_hash"], byte_count=600)
        from aic.council.initial_runtime import request_body_utf8_bytes
        variant["request_body_utf8_bytes"] = request_body_utf8_bytes(request.request_payload)
    plan = runtime.build_rebuttal_runtime_plan(
        freeze=freeze,
        contexts=contexts,
        runtime_preflight=preflight,
        selection_authority=selection,
    )
    assert len(plan) == 3
    assert [item.dispatch_index for item in plan] == [1, 2, 3]
    assert [item.candidate_id for item in plan] == ["NVDA", "MSFT", "META"]
    assert all(item.request.request_payload["model"] == "gpt-5.6-sol" for item in plan)
    assert all(item.request.request_payload["reasoning"]["effort"] == "medium" for item in plan)
    assert all(item.request.request_payload["max_output_tokens"] == 6144 for item in plan)


def test_runtime_plan_fails_when_preflight_request_hash_is_tampered(monkeypatch) -> None:
    freeze, contexts, selection, preflight, rows = _setup(monkeypatch)
    for candidate, variant in zip(freeze.candidate_order, preflight["selected_request_variants"], strict=True):
        request = _request(candidate, request_hash=variant["request_hash"], byte_count=600)
        from aic.council.initial_runtime import request_body_utf8_bytes
        variant["request_body_utf8_bytes"] = request_body_utf8_bytes(request.request_payload)
    preflight["selected_request_variants"][1]["request_hash"] = "0" * 64
    with pytest.raises(runtime.RebuttalRuntimeError, match="reconstructed request hash"):
        runtime.build_rebuttal_runtime_plan(
            freeze=freeze,
            contexts=contexts,
            runtime_preflight=preflight,
            selection_authority=selection,
        )


def test_runtime_plan_fails_when_preflight_request_bytes_are_tampered(monkeypatch) -> None:
    freeze, contexts, selection, preflight, rows = _setup(monkeypatch)
    for candidate, variant in zip(freeze.candidate_order, preflight["selected_request_variants"], strict=True):
        request = _request(candidate, request_hash=variant["request_hash"], byte_count=600)
        from aic.council.initial_runtime import request_body_utf8_bytes
        variant["request_body_utf8_bytes"] = request_body_utf8_bytes(request.request_payload)
    preflight["selected_request_variants"][2]["request_body_utf8_bytes"] += 1
    with pytest.raises(runtime.RebuttalRuntimeError, match="byte count"):
        runtime.build_rebuttal_runtime_plan(
            freeze=freeze,
            contexts=contexts,
            runtime_preflight=preflight,
            selection_authority=selection,
        )


def test_runtime_dry_script_is_zero_call_and_does_not_claim_execution_authority() -> None:
    text = Path("scripts/b4_rebuttal_runtime_dry_v01.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in text
    assert "StdlibResponsesTransport" not in text
    assert "--execute" not in text
    assert '"model_calls": 0' in text
    assert '"provider_reads": 0' in text
    assert '"production_rebuttal_authorized": False' in text
    assert '"judge_authorized": False' in text
    assert '"rerun_authorized": False' in text
