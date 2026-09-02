from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

from aic.domain.canonical import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/b4_ttl_reevaluation_policy_proposal_zero_call_v01.py"
POLICY_PATH = ROOT / "config/event/decision_ttl_reevaluation_policy_competition_v1.json"
SPEC = importlib.util.spec_from_file_location("b4_ttl_reevaluation_policy", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def policy() -> dict[str, object]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_inactive_ttl_only_policy_has_exact_proposed_semantics_and_self_hash() -> None:
    value = policy()
    assert MODULE.verify_inactive_proposal(value) == MODULE.POLICY_HASH
    assert value["policy_hash"] == canonical_sha256(value, exclude_fields=("policy_hash",))
    assert value["active"] is False
    assert value["status"] == "DRAFT_NOT_AUTHORITY"
    assert value["trigger"] == "TTL_EXPIRY"
    assert value["proposed_model_stage_scope"] == "FRESH_JUDGE_ONLY"
    assert value["provider_refresh_required_before_model"] is False


def test_historical_inputs_are_lineage_only_not_stage_or_judge_reactivation_authority() -> None:
    value = policy()
    assert value["historical_b3_input_lineage_allowed"] is True
    assert value["historical_initial_input_lineage_allowed"] is True
    assert value["historical_rebuttal_input_lineage_allowed"] is True
    assert value["historical_lineage_authorizes_stage_skip"] is False
    assert value["historical_judge_semantic_input_allowed"] is False
    assert value["historical_judge_reactivation_allowed"] is False
    assert value["historical_ttl_refresh_allowed"] is False
    assert value["fresh_semantic_decision_required"] is True


def test_post_judge_and_paid_boundaries_remain_inactive_and_fail_closed() -> None:
    value = policy()
    assert value["fresh_b5_required_after_fresh_invest"] is True
    assert value["fresh_watch_or_abstain_b5_started"] is False
    assert value["proposed_max_fresh_judge_calls"] == 1
    assert value["automatic_retries"] == 0
    assert value["model_calls_authorized"] is False
    assert value["provider_reads_authorized"] is False
    assert value["broker_write_authority"] is False
    assert value["live_execution"] is False
    assert value["owner_activation_required"] is True
    assert value["owner_paid_approval_required"] is True
    assert value["cost_preflight_required"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", True),
        ("status", "ACTIVE"),
        ("proposed_model_stage_scope", "FULL_B4_REFRESH"),
        ("provider_refresh_required_before_model", True),
        ("historical_judge_semantic_input_allowed", True),
        ("model_calls_authorized", True),
        ("provider_reads_authorized", True),
        ("broker_write_authority", True),
    ],
)
def test_mutated_proposal_fails_closed(field: str, value: object) -> None:
    altered = deepcopy(policy())
    altered[field] = value
    with pytest.raises(MODULE.ProposalBlocked):
        MODULE.verify_inactive_proposal(altered)


def test_verifier_has_no_network_model_provider_or_broker_capability() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not item.startswith(("requests", "http", "urllib", "socket", "openai", "alpaca")) for item in imports)
    for prohibited in ("urlopen(", "requests.", "TradingClient", "submit_order", "create_order", "OPENAI_API_KEY"):
        assert prohibited not in source


def test_cli_reports_inactive_zero_call_policy() -> None:
    output = io.StringIO()
    assert MODULE.main(["--repository", str(ROOT)], output=output) == 0
    text = output.getvalue()
    assert "PROPOSAL_ACTIVE=false" in text
    assert "PROPOSAL_STATUS=DRAFT_NOT_AUTHORITY" in text
    assert "MODEL_CALLS_AUTHORIZED=false" in text
    assert "PROVIDER_READS_AUTHORIZED=false" in text
