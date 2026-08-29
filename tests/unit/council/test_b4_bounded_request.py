from __future__ import annotations

import pytest

from aic.council.bounded_request import (
    _apply_output_bound,
    assert_bounded_request_invariants,
)
from aic.council.model_policy import STAGE_MAX_OUTPUT_TOKENS, CouncilModelStage
from aic.council.request import CouncilRequestEnvelope, CouncilRequestError, CouncilRequestStage
from aic.domain.canonical import canonical_sha256


_STAGE_MODEL_STAGE = {
    CouncilRequestStage.BULL_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.REBUTTAL: CouncilModelStage.REBUTTAL,
    CouncilRequestStage.JUDGE: CouncilModelStage.JUDGE,
}


def _base_request(stage: CouncilRequestStage) -> CouncilRequestEnvelope:
    schema = {
        "type": "object",
        "properties": (
            {"claim_local_ref": {"type": "string"}}
            if stage != CouncilRequestStage.JUDGE
            else {}
        ),
        "required": (["claim_local_ref"] if stage != CouncilRequestStage.JUDGE else []),
        "additionalProperties": False,
    }
    payload = {
        "model": "gpt-5.6-terra",
        "reasoning": {"effort": "medium"},
        "instructions": "bounded test",
        "input": "{}",
        "store": False,
        "tools": [],
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "b4_test",
                "strict": True,
                "schema": schema,
            }
        },
    }
    body = {
        "request_version": "B4_RESPONSES_REQUEST_v0_1",
        "prompt_contract_version": "P-B4-PROMPTS-v0.2",
        "stage": stage.value,
        "prompt_version": "TEST",
        "prompt_hash": "0" * 64,
        "schema_version": "TEST",
        "input_hash": "1" * 64,
        "model_candidate_key": "TEST",
        "request_payload": payload,
    }
    return CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))


@pytest.mark.parametrize("stage", list(_STAGE_MODEL_STAGE))
def test_bounded_request_applies_exact_stage_output_cap(stage: CouncilRequestStage) -> None:
    bounded = _apply_output_bound(_base_request(stage))
    expected = STAGE_MAX_OUTPUT_TOKENS[_STAGE_MODEL_STAGE[stage]]
    assert bounded.request_payload["max_output_tokens"] == expected
    assert_bounded_request_invariants(bounded)


def test_unbounded_request_fails_bounded_invariant() -> None:
    with pytest.raises(CouncilRequestError, match="max_output_tokens=4096"):
        assert_bounded_request_invariants(_base_request(CouncilRequestStage.BULL_INITIAL))
