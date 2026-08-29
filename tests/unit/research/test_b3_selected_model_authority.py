import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.model_selection import (
    DEFAULT_SELECTED_MODEL_AUTHORITY_PATH,
    SelectedModelAuthority,
    load_selected_model_authority,
)


def test_selected_model_authority_freezes_eval_selected_m2() -> None:
    authority = load_selected_model_authority()
    assert authority.model_eval_artifact_hash == "842677125a3f80c73b6d5db23d557a2ba0e2a28384c095d064438f8c3236f336"
    assert authority.selected_candidate.candidate_key == "M2"
    assert authority.selected_candidate.model == "gpt-5.6-terra"
    assert authority.selected_candidate.reasoning_effort == "medium"
    assert authority.selected_eval_metrics.passed_cases == 12
    assert authority.selected_eval_metrics.critical_safety_failures == 0
    assert set(authority.full_ladder_pass_summary) == {"M1", "M2", "M3"}


def test_selected_model_authority_recomputes_selection_rule_after_tamper(tmp_path: Path) -> None:
    raw = json.loads(DEFAULT_SELECTED_MODEL_AUTHORITY_PATH.read_text(encoding="utf-8"))
    raw["full_ladder_pass_summary"]["M1"]["estimated_cost_usd"] = "0.001"
    raw["selection_hash"] = canonical_sha256(raw, exclude_fields=("selection_hash",))
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate disagrees with frozen selection rule"):
        SelectedModelAuthority.model_validate(raw)


def test_selected_model_authority_hash_fails_closed_on_payload_drift() -> None:
    raw = json.loads(DEFAULT_SELECTED_MODEL_AUTHORITY_PATH.read_text(encoding="utf-8"))
    raw["selected_eval_metrics"]["latency_ms"] += 1
    with pytest.raises(ValueError, match="selection_hash"):
        SelectedModelAuthority.model_validate(raw)
