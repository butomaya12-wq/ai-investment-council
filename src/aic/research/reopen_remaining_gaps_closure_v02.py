from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B3_REOPEN_REMAINING_GAPS_CLOSURE_v0_2"
PASS_STATUS = "B3_REOPEN_REMAINING_GAPS_CLOSURE_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_INPUT_OVERLAY_ZERO_CALL"

EXPECTED_RECOVERY_HASH = "6abcb9b51e6001b64d82481ad8156ae0a2a0924f9249ccae73a96b3b4d90244c"
EXPECTED_CLAIM_RECON_HASH = "d4987a581c107f9caf729641a2a972b973995454446c9c2bbd531213e2b6c832"
EXPECTED_PLAN_HASH = "13c6e5da3e5d2b9b2369a8998abb9285d20e91a7c86452539a623301805e4b61"
EXPECTED_SCOPE_HASH = "948d3dbd28200d94726e97e39abd7955a0aa428ece22ee7b1ad6bbec6d20ba4a"
EXPECTED_PRIMITIVES_HASH = "64c76249a36d650c79e95c80720061f3cbe48be900c6d1cdab2fda44240a5ee7"
EXPECTED_SELECTED_HASH = "938b7eecfee58d1074be662d30a1bf183f1133f92815028637de4cd662307f27"
EXPECTED_JUDGE_HASH = "3354123bc0244ec258fad0cdab57d5551d5ed8e5d58088d11482bdcd489d259e"
EXPECTED_REOPEN_ID = "B4_RESEARCH_REOPEN_4dceff8d109cff9642cad677"
EXPECTED_REOPEN_HASH = "eb4c06f47f372413d25b25632ba84a35057fdbb9d244c4f1960f6b7fb40dfeb1"
NEWS_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"
NEWS_CLOSURE = "ALPACA_NEWS_BOUNDED_TOP_N_SATISFIED"
VALUATION_GAP = "VALUATION_SPECIFIC_EVIDENCE_MISSING"
PORTFOLIO_GAP = "PORTFOLIO_INTERACTION_EVIDENCE_MISSING"
REMAINING_REASONS = (VALUATION_GAP, PORTFOLIO_GAP)
CANDIDATES = ("NVDA", "MSFT", "META")

E_MSFT_VAL = "B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z"
E_META_VAL = "B3_REOPEN_EVID_META_VALUATION_20260828T173300Z"
E_META_PORT = "B3_REOPEN_EVID_META_PORTFOLIO_20260827T200000Z"
C_MSFT_VAL = "B3_REOPEN_SUPPLEMENTAL_MSFT_VALUATION_001"
C_META_VAL = "B3_REOPEN_SUPPLEMENTAL_META_VALUATION_001"
C_META_PORT = "B3_REOPEN_SUPPLEMENTAL_META_PORTFOLIO_001"


class ClosureError(ValueError):
    pass


def _read(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosureError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise ClosureError(f"{label} root must be object")
    return value


def _verify(payload: Mapping[str, Any], expected_hash: str, label: str) -> str:
    observed = payload.get("artifact_hash")
    if observed != expected_hash:
        raise ClosureError(f"{label} hash drift")
    if observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise ClosureError(f"{label} self-hash mismatch")
    return str(observed)


def _legacy_manifest(selected: Mapping[str, Any]) -> dict[str, Any]:
    rows = selected.get("candidates")
    if not isinstance(rows, list):
        raise ClosureError("selected reconciliation candidates missing")
    candidates: list[str] = []
    claims: list[Mapping[str, Any]] = []
    ids: list[str] = []
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ClosureError("selected candidate malformed")
        candidate = row.get("candidate")
        material_claims = row.get("material_claims")
        if not isinstance(candidate, str) or not isinstance(material_claims, list):
            raise ClosureError("selected candidate claim surface malformed")
        candidates.append(candidate)
        counts[candidate] = len(material_claims)
        for raw in material_claims:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("claim_id"), str):
                raise ClosureError("legacy MaterialClaim malformed")
            claims.append(raw)
            ids.append(str(raw["claim_id"]))
    if tuple(candidates) != CANDIDATES:
        raise ClosureError("selected candidate order drift")
    if len(claims) != 34 or len(set(ids)) != 34:
        raise ClosureError("legacy MaterialClaim set must remain 34 unique claims")
    return {
        "claim_count": 34,
        "per_candidate_claim_counts": counts,
        "claim_ids": ids,
        "claim_id_manifest_hash": canonical_sha256({"claim_ids": ids}),
        "claim_payload_manifest_hash": canonical_sha256({"material_claims": claims}),
        "ids_mutated": False,
        "payloads_mutated": False,
    }


def _assert_recovery(recovery: Mapping[str, Any]) -> None:
    if recovery.get("status") != "B3_REOPEN_MINIMAL_EXTERNAL_READ_RECOVERY_ZERO_CALL_PASS":
        raise ClosureError("recovery is not PASS")
    if recovery.get("next_gate") != "B3_REOPEN_REMAINING_GAPS_CLOSURE_ZERO_CALL":
        raise ClosureError("recovery next gate drift")
    if recovery.get("valuation_specific_evidence_ready") is not True:
        raise ClosureError("valuation evidence not ready")
    if recovery.get("portfolio_interaction_evidence_ready") is not True:
        raise ClosureError("portfolio evidence not ready")
    if recovery.get("new_provider_dispatch_attempts") != 0 or recovery.get("new_provider_reads") != 0:
        raise ClosureError("recovery is not zero-call")
    if recovery.get("model_calls") != 0 or recovery.get("broker_writes") != 0 or recovery.get("alpaca_orders") != 0:
        raise ClosureError("recovery side-effect boundary drift")
    if recovery.get("live_money") != "PROHIBITED":
        raise ClosureError("recovery live money drift")
    pagination = recovery.get("pagination_recovery")
    if not isinstance(pagination, Mapping):
        raise ClosureError("pagination recovery missing")
    if (
        pagination.get("terminal_page_recovered") is not True
        or pagination.get("observed_next_page_token_representation") != "EMPTY_STRING"
        or pagination.get("pagination_continuation_required") is not False
        or pagination.get("provider_rerun_required") is not False
    ):
        raise ClosureError("terminal market page recovery drift")

    valuations = recovery.get("valuation_recovery")
    if not isinstance(valuations, Mapping):
        raise ClosureError("valuation recovery missing")
    expected = {
        "MSFT": ("17.95", "FY2026", "517.35", "28.821727019499"),
        "META": ("23.49", "FY2025", "576.68", "24.550021285653"),
    }
    for candidate, values in expected.items():
        row = valuations.get(candidate)
        if not isinstance(row, Mapping) or row.get("valuation_evidence_complete") is not True:
            raise ClosureError(f"{candidate} valuation incomplete")
        price = row.get("price")
        if not isinstance(price, Mapping):
            raise ClosureError(f"{candidate} price missing")
        observed = (
            row.get("annual_gaap_diluted_eps"),
            row.get("eps_period"),
            price.get("close"),
            row.get("price_to_eps"),
        )
        if observed != values:
            raise ClosureError(f"{candidate} valuation drift")
        if price.get("bar_timestamp_utc") != "2026-08-28T17:33:00Z" or price.get("feed") != "iex":
            raise ClosureError(f"{candidate} price lineage drift")

    portfolio = recovery.get("portfolio_recovery")
    if not isinstance(portfolio, Mapping) or portfolio.get("portfolio_interaction_evidence_complete") is not True:
        raise ClosureError("META portfolio evidence incomplete")
    if (
        portfolio.get("reconstructed_meta_quantity_at_b2_cutoff") != "0"
        or portfolio.get("reconstructed_meta_market_value_at_b2_cutoff") != "0"
        or portfolio.get("reconstructed_meta_portfolio_weight") != "0.000000000000"
    ):
        raise ClosureError("META zero-exposure reconstruction drift")
    equity = portfolio.get("b2_cutoff_portfolio_equity")
    price = portfolio.get("meta_b2_cutoff_price")
    if not isinstance(equity, Mapping) or not isinstance(price, Mapping):
        raise ClosureError("META portfolio primitives missing")
    if equity.get("selected_equity") != "200000" or equity.get("selected_equity_timestamp_utc") != "2026-08-27T20:00:00Z":
        raise ClosureError("B2 equity drift")
    if price.get("close") != "571.03" or price.get("bar_timestamp_utc") != "2026-08-27T19:59:00Z":
        raise ClosureError("META B2 price drift")


def _evidence(recovery_hash: str, primitives_hash: str) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": E_MSFT_VAL,
            "candidate_id": "MSFT",
            "category": "valuation_context",
            "evidence_kind": "POINT_IN_TIME_VALUATION_MULTIPLE",
            "source_refs": [
                f"RECOVERY:{recovery_hash}",
                f"PRIMITIVES:{primitives_hash}",
                "SEC_EVIDENCE:B3_SEC_MSFT_N3_SEC_MDA_1",
                "RAW_CAPTURE:R4_MSFT_META_POINT_IN_TIME_BARS:74a808a5a5d9a66aca9585ffe05d120ab6227fdef4937e73e8629ee4a88e8638",
            ],
            "observed": {
                "price": "517.35",
                "price_timestamp_utc": "2026-08-28T17:33:00Z",
                "price_feed": "iex",
                "annual_gaap_diluted_eps": "17.95",
                "eps_period": "FY2026",
                "price_to_eps": "28.821727019499",
            },
        },
        {
            "evidence_id": E_META_VAL,
            "candidate_id": "META",
            "category": "valuation_context",
            "evidence_kind": "POINT_IN_TIME_VALUATION_MULTIPLE",
            "source_refs": [
                f"RECOVERY:{recovery_hash}",
                f"PRIMITIVES:{primitives_hash}",
                "SEC_EVIDENCE:B3_SEC_META_META_N3_SEC_MDA_1",
                "RAW_CAPTURE:R4_MSFT_META_POINT_IN_TIME_BARS:74a808a5a5d9a66aca9585ffe05d120ab6227fdef4937e73e8629ee4a88e8638",
            ],
            "observed": {
                "price": "576.68",
                "price_timestamp_utc": "2026-08-28T17:33:00Z",
                "price_feed": "iex",
                "annual_gaap_diluted_eps": "23.49",
                "eps_period": "FY2025",
                "price_to_eps": "24.550021285653",
            },
        },
        {
            "evidence_id": E_META_PORT,
            "candidate_id": "META",
            "category": "portfolio_interaction",
            "evidence_kind": "HISTORICAL_DIRECT_PORTFOLIO_EXPOSURE",
            "source_refs": [
                f"RECOVERY:{recovery_hash}",
                "RAW_CAPTURE:R1_CURRENT_POSITIONS_ANCHOR:37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
                "RAW_CAPTURE:R2_POST_CUTOFF_ACCOUNT_ACTIVITIES_FIRST_PAGE:3af30aff3449021972a46789c7b7f513afd1098ae79231ba34a91a0f6c211384",
                "RAW_CAPTURE:R3_B2_CUTOFF_PORTFOLIO_EQUITY:725272b34d623d71770817efcf04585ceef2ad228df40d4852b448225108aed6",
                "RAW_CAPTURE:R4_MSFT_META_POINT_IN_TIME_BARS:74a808a5a5d9a66aca9585ffe05d120ab6227fdef4937e73e8629ee4a88e8638",
            ],
            "observed": {
                "b2_cutoff_utc": "2026-08-27T20:00:00Z",
                "portfolio_equity": "200000",
                "meta_price": "571.03",
                "meta_price_timestamp_utc": "2026-08-27T19:59:00Z",
                "meta_quantity": "0",
                "meta_market_value": "0",
                "meta_portfolio_weight": "0.000000000000",
                "direct_position_exposure": "ZERO",
            },
        },
    ]


def _claims() -> list[dict[str, Any]]:
    return [
        {
            "claim_id": C_MSFT_VAL,
            "candidate_id": "MSFT",
            "category": "valuation_context",
            "claim_kind": "FACT",
            "support_status": "SUPPORTED",
            "evidence_ids": [E_MSFT_VAL],
            "claim_text": "At the research cutoff, MSFT's IEX 1-minute close was USD 517.35; against FY2026 GAAP diluted EPS of USD 17.95, price-to-latest-reported-annual-GAAP-diluted-EPS was 28.821727019499x.",
        },
        {
            "claim_id": C_META_VAL,
            "candidate_id": "META",
            "category": "valuation_context",
            "claim_kind": "FACT",
            "support_status": "SUPPORTED",
            "evidence_ids": [E_META_VAL],
            "claim_text": "At the research cutoff, META's IEX 1-minute close was USD 576.68; against FY2025 GAAP diluted EPS of USD 23.49, price-to-latest-reported-annual-GAAP-diluted-EPS was 24.550021285653x.",
        },
        {
            "claim_id": C_META_PORT,
            "candidate_id": "META",
            "category": "portfolio_interaction",
            "claim_kind": "FACT",
            "support_status": "SUPPORTED",
            "evidence_ids": [E_META_PORT],
            "claim_text": "At the B2 cutoff, deterministic reverse reconstruction found zero META shares, USD 0 META market value and direct portfolio weight 0.000000000000; direct existing-position exposure to META was zero.",
        },
    ]


def build_closure(
    *,
    code_commit_sha: str,
    recovery_path: str | Path,
    claim_reconciliation_path: str | Path,
    evidence_plan_path: str | Path,
    scope_path: str | Path,
    primitives_path: str | Path,
    selected_reconciliation_path: str | Path,
    judge_result_path: str | Path,
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise ClosureError("code_commit_sha must be lowercase 40-char SHA")

    recovery = _read(recovery_path, "recovery")
    recovery_hash = _verify(recovery, EXPECTED_RECOVERY_HASH, "recovery")
    _assert_recovery(recovery)

    claim_recon = _read(claim_reconciliation_path, "claim reconciliation")
    claim_hash = _verify(claim_recon, EXPECTED_CLAIM_RECON_HASH, "claim reconciliation")
    if claim_recon.get("status") != "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL_PASS":
        raise ClosureError("claim reconciliation is not PASS")
    if claim_recon.get("news_gap_closed") is not True or claim_recon.get("closure_evidence_ref") != NEWS_CLOSURE:
        raise ClosureError("news closure missing")
    if tuple(claim_recon.get("remaining_reopen_reason_codes", [])) != REMAINING_REASONS:
        raise ClosureError("claim reconciliation remaining reasons drift")

    plan = _read(evidence_plan_path, "evidence plan")
    plan_hash = _verify(plan, EXPECTED_PLAN_HASH, "evidence plan")
    if plan.get("status") != "B3_REOPEN_REMAINING_GAPS_EVIDENCE_PLAN_ZERO_CALL_PASS":
        raise ClosureError("evidence plan is not PASS")
    if plan.get("target_candidates") != ["MSFT", "META"] or plan.get("non_target_candidate_ids") != ["NVDA"]:
        raise ClosureError("evidence plan target scope drift")

    scope = _read(scope_path, "remaining-gap scope")
    scope_hash = _verify(scope, EXPECTED_SCOPE_HASH, "remaining-gap scope")
    if scope.get("status") != "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL_PASS":
        raise ClosureError("remaining-gap scope is not PASS")
    if tuple(scope.get("remaining_reopen_reason_codes", [])) != REMAINING_REASONS:
        raise ClosureError("remaining-gap scope reason drift")

    primitives = _read(primitives_path, "local primitives")
    primitives_hash = _verify(primitives, EXPECTED_PRIMITIVES_HASH, "local primitives")

    selected = _read(selected_reconciliation_path, "selected B3 reconciliation")
    selected_hash = _verify(selected, EXPECTED_SELECTED_HASH, "selected B3 reconciliation")
    if claim_recon.get("source_b3_selected_model_reconciliation_hash") != selected_hash:
        raise ClosureError("selected B3 lineage mismatch")
    legacy = _legacy_manifest(selected)

    judge = _read(judge_result_path, "production Judge")
    judge_hash = _verify(judge, EXPECTED_JUDGE_HASH, "production Judge")
    if judge.get("status") != "B4_COMPLETE_RESEARCH_REOPEN_REQUESTED":
        raise ClosureError("production Judge status drift")
    if claim_recon.get("source_production_judge_result_hash") != judge_hash:
        raise ClosureError("production Judge lineage mismatch")
    if judge.get("final_decision_created") is not False or judge.get("b5_handoff_created") is not False:
        raise ClosureError("historical Judge unexpectedly advanced")
    if judge.get("rerun_authorized") is not False:
        raise ClosureError("historical Judge rerun boundary drift")

    reopen = judge.get("research_reopen_request")
    if not isinstance(reopen, Mapping):
        raise ClosureError("reopen request missing")
    if reopen.get("reopen_request_id") != EXPECTED_REOPEN_ID:
        raise ClosureError("reopen request id drift")
    if reopen.get("request_hash") != EXPECTED_REOPEN_HASH:
        raise ClosureError("canonical reopen request hash drift")
    if judge.get("research_reopen_request_hash") != EXPECTED_REOPEN_HASH:
        raise ClosureError("production result reopen hash drift")
    if reopen.get("request_hash") != canonical_sha256(reopen, exclude_fields=("request_hash",)):
        raise ClosureError("canonical reopen request self-hash mismatch")
    if tuple(reopen.get("reason_codes", [])) != (NEWS_GAP, VALUATION_GAP, PORTFOLIO_GAP):
        raise ClosureError("reopen request reason scope drift")

    structured = judge.get("structured_output")
    condition_rows = structured.get("what_would_change_decision") if isinstance(structured, Mapping) else None
    if not isinstance(condition_rows, list):
        raise ClosureError("Judge conditions missing")
    condition_ids = [row.get("condition_id") for row in condition_rows if isinstance(row, Mapping)]
    if condition_ids != ["CONDITION_001", "CONDITION_002", "CONDITION_003"]:
        raise ClosureError("Judge condition ids drift")

    evidence = _evidence(recovery_hash, primitives_hash)
    supplemental_claims = _claims()
    if set(row["claim_id"] for row in supplemental_claims) & set(legacy["claim_ids"]):
        raise ClosureError("supplemental claim id collision")

    condition_closure = [
        {
            "condition_id": "CONDITION_001",
            "candidate_id": "NVDA",
            "satisfied": True,
            "closure_basis_refs": [NEWS_CLOSURE, f"CLAIM_RECONCILIATION:{claim_hash}"],
        },
        {
            "condition_id": "CONDITION_002",
            "candidate_id": "MSFT",
            "satisfied": True,
            "closure_basis_refs": [NEWS_CLOSURE, E_MSFT_VAL, C_MSFT_VAL],
        },
        {
            "condition_id": "CONDITION_003",
            "candidate_id": "META",
            "satisfied": True,
            "closure_basis_refs": [NEWS_CLOSURE, E_META_VAL, C_META_VAL, E_META_PORT, C_META_PORT],
        },
    ]

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovery_hash": recovery_hash,
        "source_claim_reconciliation_hash": claim_hash,
        "source_evidence_plan_hash": plan_hash,
        "source_remaining_gaps_scope_hash": scope_hash,
        "source_local_primitives_hash": primitives_hash,
        "source_selected_b3_reconciliation_hash": selected_hash,
        "source_production_judge_result_hash": judge_hash,
        "source_reopen_request_id": EXPECTED_REOPEN_ID,
        "source_reopen_request_hash": EXPECTED_REOPEN_HASH,
        "legacy_material_claims": legacy,
        "legacy_frozen_artifacts_mutated": False,
        "legacy_material_claim_payloads_mutated": False,
        "reopen_overlay_is_additive": True,
        "supplemental_evidence_units": evidence,
        "supplemental_evidence_unit_count": 3,
        "supplemental_claims": supplemental_claims,
        "supplemental_claim_count": 3,
        "supplemental_claims_are_separate_from_legacy_material_claims": True,
        "judge_condition_closure": condition_closure,
        "all_judge_conditions_satisfied": True,
        "closed_reopen_reason_codes": [NEWS_GAP, VALUATION_GAP, PORTFOLIO_GAP],
        "reason_closure": [
            {"reason_code": NEWS_GAP, "closed": True, "closure_refs": [NEWS_CLOSURE]},
            {"reason_code": VALUATION_GAP, "closed": True, "candidate_ids": ["MSFT", "META"], "closure_refs": [E_MSFT_VAL, E_META_VAL]},
            {"reason_code": PORTFOLIO_GAP, "closed": True, "candidate_ids": ["META"], "closure_refs": [E_META_PORT]},
        ],
        "remaining_reopen_reason_codes": [],
        "news_gap_state": "CLOSED",
        "valuation_gap_state": "CLOSED",
        "portfolio_interaction_gap_state": "CLOSED",
        "research_reopen_request_satisfied": True,
        "overall_research_reopen_complete": True,
        "historical_provider_reads_reused": 4,
        "new_provider_dispatch_attempts": 0,
        "new_provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "historical_production_judge_rerun_authorized": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
