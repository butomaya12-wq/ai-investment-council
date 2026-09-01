#!/usr/bin/env python3
"""Recover B4 decision TTL lineage from durable local evidence, with zero calls."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import inspect
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence, TextIO

from aic.b5.production_readonly_v1 import parse_recovered_b4_artifact
from aic.council import post_research_reopen_judge_current_v04 as judge
from aic.domain.canonical import canonical_sha256


RAW_CAPTURE_RELATIVE_PATH = Path(".aic-runtime/b4_post_research_reopen_current_judge_raw_response_v0_4__40d7f5c.json")
RECOVERED_RELATIVE_PATH = Path(".aic-runtime/b4_post_research_reopen_current_judge_captured_response_recovery_v0_1__442e8d7.json")
POLICY_RELATIVE_PATH = Path("config/event/decision_lifecycle_policy_competition_v1.json")
EXPECTED_REQUEST_HASH = "2312558ae6e3979d6f8816b6b1c64309750e4e420890c4f6447f755ce4423c53"
EXPECTED_RAW_HASH = "fc4d73a86a178c03e1acbda64f176df4bd4fe225227832fcd5b286fa2c77e37d"
EXPECTED_RESPONSE_ID = "resp_071e5625bc07e951016a96927b756087d28fc9b9eba67c2780"
EXPECTED_RECOVERED_HASH = "f9a9e08a30b58ebf6fcb358c2b35a82717682ddef3ac5fd58c912d518d3fadf0"
POLICY_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"
POLICY_HASH = "cceb58997b139b59039313d892ff330915ff49f2a5a236bcdb57141501bd98ce"
TTL_SECONDS = 7200
TTL_ANCHOR = "APPLICATION_ASSIGNED_CANONICAL_DECISION_CREATED_AT"
RECEIPT_VERSION = "B4_RECOVERED_DECISION_TTL_LINEAGE_v0_1"
RECEIPT_STATUS = "B4_DECISION_TTL_LINEAGE_RECOVERED_ZERO_CALL"


class LineageBlocked(ValueError):
    pass


@dataclass(frozen=True)
class Lineage:
    raw_response_hash: str
    provider_response_id: str
    recovered_artifact_hash: str
    decision_created_at_utc: datetime


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise LineageBlocked(reason)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    _need(isinstance(value, Mapping), f"BLOCK_{field}")
    return value


def _utc_timestamp(value: object, field: str) -> datetime:
    _need(isinstance(value, str) and value == value.strip() and bool(value), f"BLOCK_{field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LineageBlocked(f"BLOCK_{field}") from exc
    _need(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"BLOCK_{field}")
    return parsed.astimezone(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_json(path: Path, field: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), field)
    except (OSError, json.JSONDecodeError) as exc:
        raise LineageBlocked(f"BLOCK_{field}") from exc


def validate_raw_capture(raw: Mapping[str, Any], *, expected_raw_hash: str = EXPECTED_RAW_HASH) -> tuple[str, str, datetime]:
    try:
        observed_hash = judge.verify_raw_capture(raw, request_hash=EXPECTED_REQUEST_HASH)
    except Exception as exc:
        raise LineageBlocked("BLOCK_RAW_CAPTURE") from exc
    _need(observed_hash == expected_raw_hash, "BLOCK_RAW_RESPONSE_HASH")
    _need(raw.get("capture_version") == judge.RAW_CAPTURE_VERSION, "BLOCK_RAW_CAPTURE_VERSION")
    provider_id = raw.get("provider_response_id")
    _need(provider_id == EXPECTED_RESPONSE_ID, "BLOCK_PROVIDER_RESPONSE_ID")
    provider_payload = _mapping(raw.get("raw_response"), "RAW_RESPONSE")
    _need(provider_payload.get("id") == provider_id, "BLOCK_PROVIDER_METADATA")
    captured_at = _utc_timestamp(raw.get("captured_at_utc"), "CAPTURED_AT_UTC")
    return observed_hash, provider_id, captured_at


def validate_recovered_binding(recovered: Mapping[str, Any], *, raw_hash: str, provider_id: str) -> str:
    try:
        parsed = parse_recovered_b4_artifact(recovered)
    except Exception as exc:
        raise LineageBlocked("BLOCK_RECOVERED_B4") from exc
    _need(parsed.artifact_hash == EXPECTED_RECOVERED_HASH, "BLOCK_RECOVERED_HASH")
    _need(recovered.get("source_raw_response_hash") == raw_hash, "BLOCK_RECOVERED_RAW_BINDING")
    _need(recovered.get("source_response_id") == provider_id, "BLOCK_RECOVERED_RESPONSE_BINDING")
    _need(recovered.get("source_request_hash") == EXPECTED_REQUEST_HASH, "BLOCK_RECOVERED_REQUEST_BINDING")
    _need(recovered.get("source_paid_model_calls") == 1, "BLOCK_RECORDED_PAID_CALL_COUNT")
    _need(recovered.get("recovery_model_calls") == 0, "BLOCK_RECOVERY_MODEL_CALLS")
    _need(recovered.get("provider_reads_this_recovery") == 0, "BLOCK_RECOVERY_PROVIDER_READS")
    return parsed.artifact_hash


def validate_policy(policy: Mapping[str, Any]) -> None:
    _need(policy.get("version") == POLICY_VERSION, "BLOCK_POLICY_VERSION")
    _need(policy.get("policy_hash") == POLICY_HASH, "BLOCK_POLICY_HASH")
    _need(policy.get("decision_ttl_seconds") == TTL_SECONDS, "BLOCK_POLICY_TTL")
    _need(policy.get("ttl_anchor") == TTL_ANCHOR, "BLOCK_POLICY_ANCHOR")


def verify_production_timestamp_invariant() -> bool:
    tree = ast.parse(inspect.getsource(judge.execute_paid))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "decision_created_at_utc":
                if (
                    isinstance(value, ast.Subscript)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "capture"
                    and isinstance(value.slice, ast.Constant)
                    and value.slice.value == "captured_at_utc"
                ):
                    return True
    return False


def recover_lineage(*, raw: Mapping[str, Any], recovered: Mapping[str, Any], policy: Mapping[str, Any]) -> Lineage:
    raw_hash, provider_id, captured_at = validate_raw_capture(raw)
    recovered_hash = validate_recovered_binding(recovered, raw_hash=raw_hash, provider_id=provider_id)
    validate_policy(policy)
    _need(verify_production_timestamp_invariant(), "BLOCK_B4_SOURCE_INVARIANT")
    return Lineage(raw_hash, provider_id, recovered_hash, captured_at)


def evaluate_ttl(lineage: Lineage, evaluation_time_utc: datetime) -> tuple[int, str, datetime]:
    _need(
        evaluation_time_utc.tzinfo is not None and evaluation_time_utc.utcoffset() is not None,
        "BLOCK_EVALUATION_TIME_UTC",
    )
    evaluation = evaluation_time_utc.astimezone(UTC)
    age = int((evaluation - lineage.decision_created_at_utc).total_seconds())
    if age < 0:
        raise LineageBlocked("BLOCK_EVALUATION_PRECEDES_DECISION")
    expires = lineage.decision_created_at_utc + timedelta(seconds=TTL_SECONDS)
    return age, "TTL_VALID" if age <= TTL_SECONDS else "TTL_EXPIRED", expires


def build_receipt(lineage: Lineage) -> dict[str, object]:
    expires = lineage.decision_created_at_utc + timedelta(seconds=TTL_SECONDS)
    receipt: dict[str, object] = {
        "artifact_version": RECEIPT_VERSION,
        "status": RECEIPT_STATUS,
        "source_raw_response_sha256": lineage.raw_response_hash,
        "source_provider_response_id": lineage.provider_response_id,
        "recovered_b4_artifact_hash": lineage.recovered_artifact_hash,
        "decision_created_at_utc": _utc_text(lineage.decision_created_at_utc),
        "decision_ttl_seconds": TTL_SECONDS,
        "decision_expires_at_utc": _utc_text(expires),
        "decision_lifecycle_policy_version": POLICY_VERSION,
        "decision_lifecycle_policy_hash": POLICY_HASH,
        "timestamp_derivation": "RAW_CAPTURE_CAPTURED_AT_UTC",
        "b4_outcome": "INVEST",
        "primary_candidate_id": "NVDA",
        "execution_authority": False,
        "broker_write_authority": False,
        "live_execution": False,
        "model_calls": 0,
        "openai_calls": 0,
        "alpaca_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
    }
    receipt["artifact_hash"] = canonical_sha256(receipt, exclude_fields=("artifact_hash",))
    return receipt


def write_receipt_exclusive(path: Path, receipt: Mapping[str, object]) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise LineageBlocked("BLOCK_RECEIPT_EXISTS") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def default_receipt_path(repository: Path) -> Path:
    return repository / ".aic-runtime" / "b4_recovered_decision_ttl_lineage_v0_1__fc4d73a__5500332.json"


def parse_evaluation_time(value: str) -> datetime:
    return _utc_timestamp(value, "EVALUATION_TIME_UTC")


def run(*, repository: Path, evaluation_time_utc: datetime, receipt_path: Path) -> tuple[Lineage, int, str, datetime, dict[str, object]]:
    lineage = recover_lineage(
        raw=load_json(repository / RAW_CAPTURE_RELATIVE_PATH, "RAW_CAPTURE"),
        recovered=load_json(repository / RECOVERED_RELATIVE_PATH, "RECOVERED_B4"),
        policy=load_json(repository / POLICY_RELATIVE_PATH, "LIFECYCLE_POLICY"),
    )
    age, status, expires = evaluate_ttl(lineage, evaluation_time_utc)
    receipt = build_receipt(lineage)
    write_receipt_exclusive(receipt_path, receipt)
    return lineage, age, status, expires, receipt


def main(argv: Sequence[str] | None = None, *, output: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-time-utc", required=True)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--receipt-path", type=Path)
    arguments = parser.parse_args(argv)
    destination = output if output is not None else sys.stdout
    try:
        evaluation = parse_evaluation_time(arguments.evaluation_time_utc)
        receipt_path = arguments.receipt_path or default_receipt_path(arguments.repository)
        lineage, age, status, expires, receipt = run(
            repository=arguments.repository, evaluation_time_utc=evaluation, receipt_path=receipt_path
        )
    except LineageBlocked as exc:
        print(f"TTL_LINEAGE_STATUS={exc}", file=destination)
        return 1
    print(f"DECISION_CREATED_AT_UTC={_utc_text(lineage.decision_created_at_utc)}", file=destination)
    print(f"DECISION_EXPIRES_AT_UTC={_utc_text(expires)}", file=destination)
    print(f"EVALUATION_TIME_UTC={_utc_text(evaluation)}", file=destination)
    print(f"DECISION_AGE_SECONDS={age}", file=destination)
    print(f"TTL_SECONDS={TTL_SECONDS}", file=destination)
    print(f"TTL_STATUS={status}", file=destination)
    print("TIMESTAMP_DERIVATION=RAW_CAPTURE_CAPTURED_AT_UTC", file=destination)
    print("B4_SOURCE_INVARIANT_CHECK=PASS", file=destination)
    print(f"SUPPLEMENTAL_RECEIPT_PATH={receipt_path}", file=destination)
    print(f"SUPPLEMENTAL_RECEIPT_ARTIFACT_HASH={receipt['artifact_hash']}", file=destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
