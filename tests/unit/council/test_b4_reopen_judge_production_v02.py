from __future__ import annotations

import pytest

from aic.council import reopen_judge_production_v02 as v02
from aic.council.proposal import JudgeDecisionProposalDraft
from aic.domain.canonical import canonical_sha256


def _context() -> v02.ReopenJudgeContext:
    return v02.ReopenJudgeContext(
        candidate_ids=("NVDA", "MSFT", "META"),
        mandate_version="MANDATE",
        deep_comparison_id="DEEP",
        judge_input_hash="a" * 64,
        model_input={
            "council_policy_version": "C",
            "judge_policy_version": "J",
            "model_policy_version": "M",
        },
        allowed_claim_ids=("CLM_NVDA", "CLM_MSFT", "CLM_META"),
        allowed_dispute_refs=(),
        allowed_conflict_refs=(),
        allowed_unknown_refs=(),
        allowed_condition_refs=(
            "CLM_NVDA",
            "CLM_MSFT",
            "CLM_META",
            "Q4_RECENT_DEVELOPMENTS",
            "MSFT_REOPEN_A",
        ),
        allowed_reopen_reason_codes=("Q4_RECENT_DEVELOPMENTS", "MSFT_REOPEN_A"),
        context_hash="b" * 64,
    )


def _proposal(
    *,
    outcome: str,
    primary: str | None,
    watch: list[str],
    reopen: bool,
    reasons: list[str],
    directive: str,
) -> JudgeDecisionProposalDraft:
    changes = []
    if outcome == "WATCH":
        changes = [
            {
                "condition_id": "COND_1",
                "condition_text": "Resolve the active frozen research-reopen item.",
                "source_or_claim_refs": ["CLM_NVDA"],
            }
        ]
    return JudgeDecisionProposalDraft.model_validate(
        {
            "b4_decision_id": "B4_DECISION_TEST",
            "outcome": outcome,
            "primary_candidate_id": primary,
            "watch_candidate_ids": watch,
            "mandate_version": "MANDATE",
            "deep_comparison_id": "DEEP",
            "judge_input_hash": "a" * 64,
            "council_policy_version": "C",
            "judge_policy_version": "J",
            "model_policy_version": "M",
            "selected_candidate_basis_claim_ids": [
                "CLM_META" if primary == "META" else "CLM_NVDA"
            ],
            "why_not_other_candidates": [
                {
                    "candidate_id": "NVDA" if primary != "NVDA" else "MSFT",
                    "claim_ids": ["CLM_NVDA" if primary != "NVDA" else "CLM_MSFT"],
                    "reason_codes": ["ACTIVE_REOPEN"],
                }
            ],
            "unresolved_dispute_refs": [],
            "material_conflict_refs": [],
            "material_unknown_refs": [],
            "blocking_reason_codes": [],
            "research_reopen_required": reopen,
            "research_reopen_reason_codes": reasons,
            "what_would_change_decision": changes,
            "invalidation_condition_refs": [],
            "evidence_status": "COMPLETE" if outcome == "INVEST" else "PARTIAL",
            "execution_authority": False,
            "next_directive": directive,
            "model_run_ref": v02.MODEL_RUN_REF,
        }
    )


def test_event_validator_accepts_invest_meta() -> None:
    v02.validate_event_proposal(
        _proposal(
            outcome="INVEST",
            primary="META",
            watch=["NVDA", "MSFT"],
            reopen=False,
            reasons=[],
            directive="PROMOTE_FINAL_DECISION",
        ),
        context=_context(),
    )


@pytest.mark.parametrize("candidate", ["NVDA", "MSFT"])
def test_event_validator_rejects_invest_for_active_reopen_candidate(
    candidate: str,
) -> None:
    with pytest.raises(v02.ReopenJudgeV02Error, match="INVEST is allowed only for META"):
        v02.validate_event_proposal(
            _proposal(
                outcome="INVEST",
                primary=candidate,
                watch=[],
                reopen=False,
                reasons=[],
                directive="PROMOTE_FINAL_DECISION",
            ),
            context=_context(),
        )


def test_event_validator_accepts_watch_with_frozen_reopen_reason() -> None:
    v02.validate_event_proposal(
        _proposal(
            outcome="WATCH",
            primary=None,
            watch=["NVDA", "MSFT"],
            reopen=True,
            reasons=["Q4_RECENT_DEVELOPMENTS"],
            directive="RESEARCH_REOPEN_REQUEST",
        ),
        context=_context(),
    )


def test_event_validator_rejects_watch_that_suppresses_reopen() -> None:
    with pytest.raises(v02.ReopenJudgeV02Error, match="must preserve active reopen"):
        v02.validate_event_proposal(
            _proposal(
                outcome="WATCH",
                primary=None,
                watch=["NVDA"],
                reopen=False,
                reasons=[],
                directive="MONITOR",
            ),
            context=_context(),
        )


def test_event_validator_rejects_unfrozen_reopen_reason() -> None:
    with pytest.raises(v02.ReopenJudgeV02Error, match="reopen reason outside"):
        v02.validate_event_proposal(
            _proposal(
                outcome="WATCH",
                primary=None,
                watch=["NVDA"],
                reopen=True,
                reasons=["NOT_FROZEN"],
                directive="RESEARCH_REOPEN_REQUEST",
            ),
            context=_context(),
        )


def test_build_request_preserves_j1_api_invariants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = {
        "artifact_hash": "c" * 64,
        "selected_candidate": dict(v02.EXPECTED_SELECTED_JUDGE),
    }
    monkeypatch.setattr(
        v02,
        "verify_judge_selected_model_authority",
        lambda payload: payload["artifact_hash"],
    )
    request = v02.build_request(_context(), selection)
    assert request.request_payload["model"] == "gpt-5.6-terra"
    assert request.request_payload["reasoning"] == {"effort": "medium"}
    assert request.request_payload["max_output_tokens"] == 8192
    assert request.request_payload["store"] is False
    assert request.request_payload["tools"] == []
    assert request.request_payload["parallel_tool_calls"] is False
    assert request.request_payload["truncation"] == "disabled"


def _dry_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_version": v02.DRY_VERSION,
        "status": v02.DRY_STATUS,
        "code_commit_sha": "d" * 40,
        "rebuttal_council_freeze_artifact_hash": v02.EXPECTED_REBUTTAL_FREEZE_HASH,
        "judge_entry_preflight_artifact_hash": "1" * 64,
        "judge_selected_model_authority_hash": "2" * 64,
        "selected_candidate": dict(v02.EXPECTED_SELECTED_JUDGE),
        "judge_input_hash": "3" * 64,
        "judge_context_hash": "4" * 64,
        "request_preflight_artifact_hash": "5" * 64,
        "request_manifest_hash": "6" * 64,
        "request_hash": "7" * 64,
        "cost_preflight_artifact_hash": "8" * 64,
        "cost_ceiling_usd": "0.5",
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": 8192,
        "allowed_outcomes": ["INVEST", "WATCH", "ABSTAIN"],
        "research_reopen_required_candidates": ["NVDA", "MSFT"],
        "invest_eligible_candidates": ["META"],
        "invest_blocked_candidates": ["NVDA", "MSFT"],
        "source_successful_credential_probe_v02_result_artifact_hash": v02.EXPECTED_CREDENTIAL_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": v02.EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
        "paid_authorization_requires_current_credential_sha256_match": True,
        "owner_approval_required": True,
        "consumption_rule": "CONSUMED_ON_FIRST_DURABLE_JUDGE_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "production_judge_authorized": False,
        "rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": v02.NEXT_GATE,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_dry_verifier_preserves_cost_and_credential_gates() -> None:
    payload = _dry_payload()
    assert v02.verify_dry(payload, head="d" * 40) == payload["artifact_hash"]


def test_dry_verifier_rejects_credential_fingerprint_drift() -> None:
    payload = _dry_payload()
    payload["replacement_credential_fingerprint_sha256"] = "0" * 64
    payload["artifact_hash"] = canonical_sha256(
        payload, exclude_fields=("artifact_hash",)
    )
    with pytest.raises(v02.ReopenJudgeV02Error, match="dry drift"):
        v02.verify_dry(payload)
