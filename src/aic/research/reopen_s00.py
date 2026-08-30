from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aic.council.judge_production import verify_judge_production_success_artifact
from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import RESEARCH_REOPEN_REQUEST_V1


REOPEN_AUTHORITY_VERSION = "B3_RESEARCH_REOPEN_AUTHORITY_v0_1"
REOPEN_S00_ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_S00_LINK_ARTIFACT_v0_1"
REOPEN_S00_STATUS = "B3_RESEARCH_REOPEN_S00_LINKED"
DEFAULT_REOPEN_AUTHORITY_PATH = Path("config/event/b3_research_reopen_authority_v1.json")
REQUIRED_KNOWN_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"


class ResearchReopenS00Error(ValueError):
    pass


class B3ResearchReopenAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_version: str
    source_production_judge_result_hash: str
    source_research_reopen_request_hash: str
    required_source_ref_ids: tuple[str, ...]
    expected_new_run_start_state: str
    expected_next_lifecycle: str
    final_decision_allowed: bool
    b5_handoff_allowed: bool
    paid_model_calls_authorized_at_s00: bool
    provider_reads_authorized_at_s00: bool
    broker_writes_authorized: bool
    alpaca_orders_authorized: bool
    live_money: str

    @field_validator(
        "source_production_judge_result_hash",
        "source_research_reopen_request_hash",
    )
    @classmethod
    def _hash(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("reopen authority hashes must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _event_contract(self) -> Self:
        if self.authority_version != REOPEN_AUTHORITY_VERSION:
            raise ValueError("unexpected B3 reopen authority version")
        if not self.required_source_ref_ids:
            raise ValueError("reopen authority requires at least one source ref")
        if len(set(self.required_source_ref_ids)) != len(self.required_source_ref_ids):
            raise ValueError("reopen authority source refs must be unique")
        if REQUIRED_KNOWN_GAP not in self.required_source_ref_ids:
            raise ValueError("reopen authority lost required Alpaca pagination gap")
        if self.expected_new_run_start_state != "S00":
            raise ValueError("reopen authority must start linked run at S00")
        if self.expected_next_lifecycle != "B3_RESEARCH_REOPEN_LINKED_S00":
            raise ValueError("reopen authority lifecycle drift")
        if (
            self.final_decision_allowed
            or self.b5_handoff_allowed
            or self.paid_model_calls_authorized_at_s00
            or self.provider_reads_authorized_at_s00
            or self.broker_writes_authorized
            or self.alpaca_orders_authorized
        ):
            raise ValueError("S00 reopen authority must be zero-call/read/write authority")
        if self.live_money != "PROHIBITED":
            raise ValueError("live money must remain prohibited")
        return self

    @property
    def authority_hash(self) -> str:
        return canonical_sha256(self)


def load_reopen_authority(
    path: Path = DEFAULT_REOPEN_AUTHORITY_PATH,
) -> B3ResearchReopenAuthority:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ResearchReopenS00Error("reopen authority must be a JSON object")
    return B3ResearchReopenAuthority.model_validate(dict(payload))


def build_research_reopen_s00_artifact(
    production_result: Mapping[str, Any],
    *,
    authority: B3ResearchReopenAuthority,
    code_commit_sha: str,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is None:
        raise ResearchReopenS00Error("code_commit_sha must be a lowercase git SHA")

    source_hash = verify_judge_production_success_artifact(production_result)
    if source_hash != authority.source_production_judge_result_hash:
        raise ResearchReopenS00Error("production Judge result hash does not match reopen authority")
    if production_result.get("research_reopen_request_hash") != authority.source_research_reopen_request_hash:
        raise ResearchReopenS00Error("production Judge reopen hash does not match reopen authority")
    if production_result.get("new_run_start_state") != authority.expected_new_run_start_state:
        raise ResearchReopenS00Error("production Judge S00 lifecycle drift")
    if production_result.get("next_lifecycle") != authority.expected_next_lifecycle:
        raise ResearchReopenS00Error("production Judge linked lifecycle drift")
    if production_result.get("final_decision_created") is not False:
        raise ResearchReopenS00Error("reopen source unexpectedly created FinalDecision")
    if production_result.get("b5_handoff_created") is not False:
        raise ResearchReopenS00Error("reopen source unexpectedly created B5 handoff")

    raw_reopen = production_result.get("research_reopen_request")
    if not isinstance(raw_reopen, Mapping):
        raise ResearchReopenS00Error("production Judge reopen request is missing")
    reopen = RESEARCH_REOPEN_REQUEST_V1.model_validate(dict(raw_reopen))
    if reopen.request_hash != authority.source_research_reopen_request_hash:
        raise ResearchReopenS00Error("canonical reopen request hash drift")
    missing_refs = tuple(
        ref for ref in authority.required_source_ref_ids if ref not in reopen.source_ref_ids
    )
    if missing_refs:
        raise ResearchReopenS00Error(
            "canonical reopen request lost required source refs: " + ",".join(missing_refs)
        )

    artifact: dict[str, Any] = {
        "artifact_version": REOPEN_S00_ARTIFACT_VERSION,
        "status": REOPEN_S00_STATUS,
        "code_commit_sha": code_commit_sha,
        "reopen_authority_version": authority.authority_version,
        "reopen_authority_hash": authority.authority_hash,
        "source_production_judge_result_hash": source_hash,
        "source_production_judge_run_id": production_result.get("run_id"),
        "source_research_reopen_request_hash": reopen.request_hash,
        "research_reopen_request": reopen.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        "reason_codes": list(reopen.reason_codes),
        "source_ref_ids": list(reopen.source_ref_ids),
        "required_source_ref_ids": list(authority.required_source_ref_ids),
        "new_run_start_state": "S00",
        "next_lifecycle": authority.expected_next_lifecycle,
        "final_decision_allowed": False,
        "b5_handoff_allowed": False,
        "paid_model_calls_authorized": False,
        "provider_reads_authorized_at_s00": False,
        "next_gate": "B3_REOPEN_PAGINATION_ZERO_CALL_ENGINEERING",
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
