from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.research.mandate import (
    COMPETITION_MANDATE_HASH,
    COMPETITION_OPTIONS_POLICY_HASH,
    load_competition_investment_mandate,
)
from aic.research.model_eval import (
    EXPECTED_CASE_IDS,
    aggregate_candidate,
    load_pricing_authority,
    select_from_candidate_runs,
)
from aic.research.model_eval_runtime import EVAL_VERSION, build_eval_cases, run_case
from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.prompts import (
    PLANNER_PROMPT_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    SYNTHESIS_REPAIR_PROMPT_VERSION,
    planner_prompt_hash,
    synthesis_prompt_hash,
    synthesis_repair_prompt_hash,
)
from aic.research.runtime import load_openai_api_key


ARTIFACT_VERSION = "B3_MODEL_EVAL_ARTIFACT_v0_2"
DEFAULT_OUTPUT = Path(".aic-runtime/b3_model_eval.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen B3 E1-E12 representative eval against the full "
            "M1/M2/M3 model ladder. Read-only OpenAI Responses calls only; "
            "no hosted tools and no broker capability."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _case_record(case) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "name": case.name,
        "stage": case.stage,
        "critical_safety": case.critical_safety,
        "passed": case.passed,
        "findings": list(case.findings),
        "response_ids": list(case.response_ids),
        "requested_model": case.requested_model,
        "effective_models": list(case.effective_models),
        "model_calls": case.model_calls,
        "repair_attempts": case.repair_attempts,
        "latency_ms": case.latency_ms,
        "input_tokens": case.input_tokens,
        "cached_tokens": case.cached_tokens,
        "output_tokens": case.output_tokens,
        "reasoning_tokens": case.reasoning_tokens,
        "estimated_cost_usd": str(case.estimated_cost_usd),
        "output_hashes": list(case.output_hashes),
        "result_hash": case.result_hash,
    }


def _candidate_record(run) -> dict[str, Any]:
    record = {
        "candidate_key": run.candidate.candidate_key,
        "model": run.candidate.model,
        "reasoning_effort": run.candidate.reasoning_effort,
        "ladder_position": run.candidate.ladder_position,
        "cases": [_case_record(case) for case in run.cases],
        "all_required_checks_passed": run.eval_result.all_required_checks_passed,
        "critical_safety_failures": run.eval_result.critical_safety_failures,
        "estimated_cost_usd": str(run.eval_result.estimated_cost_usd),
        "latency_ms": run.eval_result.latency_ms,
        "total_tokens": run.eval_result.total_tokens,
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def _public_summary(artifact: dict[str, Any], output: Path) -> dict[str, Any]:
    candidates = []
    for candidate in artifact["candidates"]:
        candidates.append(
            {
                "candidate_key": candidate["candidate_key"],
                "model": candidate["model"],
                "reasoning_effort": candidate["reasoning_effort"],
                "passed_cases": sum(1 for case in candidate["cases"] if case["passed"]),
                "required_cases": len(candidate["cases"]),
                "critical_safety_failures": candidate["critical_safety_failures"],
                "estimated_cost_usd": candidate["estimated_cost_usd"],
                "latency_ms": candidate["latency_ms"],
                "total_tokens": candidate["total_tokens"],
                "record_hash": candidate["record_hash"],
            }
        )
    return {
        "artifact_version": artifact["artifact_version"],
        "eval_version": artifact["eval_version"],
        "pricing_version": artifact["pricing_version"],
        "pricing_hash": artifact["pricing_hash"],
        "mandate_version": artifact["mandate_version"],
        "prompt_manifest": artifact["prompt_manifest"],
        "candidates": candidates,
        "selection": artifact["selection"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(output),
    }


def main() -> int:
    args = _args()
    api_key = load_openai_api_key()
    mandate = load_competition_investment_mandate()
    pricing = load_pricing_authority()
    cases = build_eval_cases(mandate.version)
    if tuple(case.case_id for case in cases) != EXPECTED_CASE_IDS:
        print("B3 eval fixture set is not exact E1-E12", file=sys.stderr)
        return 2

    candidate_runs = []
    for candidate in MODEL_CANDIDATE_LADDER:
        print(
            f"[B3 EVAL] START {candidate.candidate_key} "
            f"{candidate.model}/{candidate.reasoning_effort}",
            file=sys.stderr,
            flush=True,
        )
        case_runs = []
        for index, case in enumerate(cases, start=1):
            print(
                f"[B3 EVAL] {candidate.candidate_key} {case.case_id} "
                f"({index}/{len(cases)}) {case.name} ...",
                file=sys.stderr,
                flush=True,
            )
            result = run_case(
                case,
                model_candidate=candidate,
                mandate_version=mandate.version,
                api_key=api_key,
                pricing=pricing,
            )
            case_runs.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(
                f"[B3 EVAL] {candidate.candidate_key} {case.case_id} {status} "
                f"calls={result.model_calls} repair={result.repair_attempts} "
                f"latency_ms={result.latency_ms} cost_usd={result.estimated_cost_usd}",
                file=sys.stderr,
                flush=True,
            )
            if result.findings and not result.passed:
                for finding in result.findings:
                    print(
                        f"[B3 EVAL] {candidate.candidate_key} {case.case_id} FINDING "
                        f"{finding}",
                        file=sys.stderr,
                        flush=True,
                    )
        candidate_run = aggregate_candidate(candidate, tuple(case_runs))
        candidate_runs.append(candidate_run)
        print(
            f"[B3 EVAL] END {candidate.candidate_key} "
            f"all_required={candidate_run.eval_result.all_required_checks_passed} "
            f"critical_failures={candidate_run.eval_result.critical_safety_failures} "
            f"cost_usd={candidate_run.eval_result.estimated_cost_usd}",
            file=sys.stderr,
            flush=True,
        )

    selection = select_from_candidate_runs(tuple(candidate_runs))
    selected = (
        None
        if selection.selected_candidate is None
        else {
            "candidate_key": selection.selected_candidate.candidate_key,
            "model": selection.selected_candidate.model,
            "reasoning_effort": selection.selected_candidate.reasoning_effort,
            "ladder_position": selection.selected_candidate.ladder_position,
        }
    )
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "run_class": "B3_REAL_REPRESENTATIVE_MODEL_EVAL",
        "eval_version": EVAL_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "case_ids": list(EXPECTED_CASE_IDS),
        "selection_rule": (
            "ALL_E1_E12_PASS_AND_ZERO_CRITICAL_SAFETY_FAILURES; "
            "LOWEST_ESTIMATED_COST_THEN_LATENCY_THEN_TOTAL_TOKENS"
        ),
        "mandate_version": mandate.version,
        "mandate_hash": COMPETITION_MANDATE_HASH,
        "options_policy_hash": COMPETITION_OPTIONS_POLICY_HASH,
        "prompt_manifest": {
            "planner_prompt_version": PLANNER_PROMPT_VERSION,
            "planner_prompt_hash": planner_prompt_hash(),
            "synthesis_prompt_version": SYNTHESIS_PROMPT_VERSION,
            "synthesis_prompt_hash": synthesis_prompt_hash(),
            "synthesis_repair_prompt_version": SYNTHESIS_REPAIR_PROMPT_VERSION,
            "synthesis_repair_prompt_hash": synthesis_repair_prompt_hash(),
        },
        "pricing_version": pricing.pricing_version,
        "pricing_hash": pricing.pricing_hash,
        "pricing_observed_at": pricing.observed_at,
        "pricing_sources": dict(pricing.sources),
        "candidates": [_candidate_record(run) for run in candidate_runs],
        "selection": {
            "status": selection.status.value,
            "selected_candidate": selected,
            "reason_code": selection.reason_code,
        },
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "shell_or_code_interpreter": False,
            "broker_api": False,
        },
        "external_writes": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(_public_summary(artifact, args.output), indent=2, ensure_ascii=False))

    if selection.selected_candidate is None:
        print(
            "[B3 EVAL] BLOCKED: no frozen model configuration passed all E1-E12 cases.",
            file=sys.stderr,
        )
        return 1
    print(
        f"[B3 EVAL] SELECTED {selection.selected_candidate.candidate_key} "
        f"{selection.selected_candidate.model}/"
        f"{selection.selected_candidate.reasoning_effort}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
