from dataclasses import asdict, fields
from pathlib import Path
import re

from aic.cockpit.app import PACKAGE_ROOT, create_app
from aic.cockpit.presenter import build_b4_research_reopen_projection
from aic.council.judge_entry_preflight import (
    EXPECTED_CANDIDATE_ORDER,
    JUDGE_ENTRY_PREFLIGHT_STATUS,
)
from aic.council.judge_production import EXPECTED_REQUIRED_UNKNOWN_REFS
from aic.research.reopen_s00 import REQUIRED_KNOWN_GAP


PRESENTATION_READ_MODEL_FIELDS = {
    "projection_id",
    "source_status",
    "downstream_authorization_state",
    "candidates",
    "lanes",
    "unknown_refs",
    "trace_bindings",
}
DOWNSTREAM_AUTHORITATIVE_FIELDS = {
    "risk_result",
    "approval_decision",
    "approval_state",
    "execution_state",
    "execution_result",
    "broker_order",
    "order_status",
    "capital_authorization_result",
}
TERMINAL_AUTHORIZATION_OUTCOMES = {
    "DENIED",
    "REJECTED",
    "APPROVED",
    "AUTHORIZED",
    "RELEASED",
    "BLOCKED_BY_RISK",
    "FAILED_EXECUTION",
}
GLOBAL_EVIDENCE_STATUS_FIELDS = {"evidence_status", "global_evidence_status"}
GLOBAL_EVIDENCE_OUTCOMES = {
    "STALE",
    "INCOMPLETE",
    "STALE_INCOMPLETE",
    "COMPLETE",
    "VALID",
    "INVALID",
    "PASS",
    "FAIL",
}


def test_projection_preserves_branch_local_b4_boundary_without_final_decision() -> None:
    view = build_b4_research_reopen_projection()

    assert view.candidates == EXPECTED_CANDIDATE_ORDER
    assert view.source_status == JUDGE_ENTRY_PREFLIGHT_STATUS
    assert view.unknown_refs == EXPECTED_REQUIRED_UNKNOWN_REFS
    assert view.unknown_refs == (REQUIRED_KNOWN_GAP,)
    assert view.downstream_authorization_state == "NOT REACHED"
    assert [lane.name for lane in view.lanes] == ["Bull", "Bear", "Red Team", "Judge"]
    assert view.lanes[2].role.endswith("not a third vote")
    assert view.lanes[3].state == "PENDING / NOT AUTHORIZED"


def test_read_model_schema_excludes_authoritative_downstream_state() -> None:
    view = build_b4_research_reopen_projection()
    field_names = {field.name for field in fields(type(view))}
    serialized = asdict(view)

    assert field_names == PRESENTATION_READ_MODEL_FIELDS
    assert field_names.isdisjoint(DOWNSTREAM_AUTHORITATIVE_FIELDS)
    assert set(serialized) == PRESENTATION_READ_MODEL_FIELDS
    assert set(serialized).isdisjoint(DOWNSTREAM_AUTHORITATIVE_FIELDS)
    assert all(lane.state != "INVEST" for lane in view.lanes)
    assert view.projection_id != "FINAL_DECISION"


def test_absent_b5_b6_state_can_only_be_presented_as_not_reached_or_unavailable() -> None:
    view = build_b4_research_reopen_projection()

    assert view.downstream_authorization_state in {"NOT REACHED", "UNAVAILABLE"}
    assert view.downstream_authorization_state not in TERMINAL_AUTHORIZATION_OUTCOMES


def test_b4_unknown_and_research_reopen_do_not_create_global_evidence_verdict() -> None:
    view = build_b4_research_reopen_projection()
    field_names = {field.name for field in fields(type(view))}

    assert view.unknown_refs == (REQUIRED_KNOWN_GAP,)
    assert view.unknown_refs == EXPECTED_REQUIRED_UNKNOWN_REFS
    assert view.source_status == JUDGE_ENTRY_PREFLIGHT_STATUS
    assert field_names.isdisjoint(GLOBAL_EVIDENCE_STATUS_FIELDS)
    assert view.source_status not in GLOBAL_EVIDENCE_OUTCOMES



def test_templates_have_a_bounded_second_layer_forbidden_semantic_guard() -> None:
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(PACKAGE_ROOT / "templates").glob("*.html")
    )
    for forbidden in ("USD_NOTIONAL", "MARKET"):
        assert forbidden not in template_text
    for invalid_assertion in (
        "CAPITAL RELEASE DENIED",
        "Capital release denied",
        "Capital access</span><strong>DENIED",
        "Capital cannot be released",
        "STALE/INCOMPLETE",
    ):
        assert invalid_assertion not in template_text
    for forbidden in (
        "REJECTED",
        "APPROVED",
        "RELEASED",
        "BLOCKED_BY_RISK",
        "FAILED_EXECUTION",
        "STALE_INCOMPLETE",
        "<strong>STALE</strong>",
        "<strong>INCOMPLETE</strong>",
        "<strong>COMPLETE</strong>",
        "<strong>VALID</strong>",
        "<strong>INVALID</strong>",
    ):
        assert forbidden not in template_text
    assert re.search(r"(?<!NOT )\bAUTHORIZED\b", template_text) is None
    assert "CAPITAL AUTHORIZATION — {{ view.downstream_authorization_state }}" in template_text
    assert "B5/B6 authorization: {{ view.downstream_authorization_state }}" in template_text
    assert "RESEARCH REOPEN REQUIRED" in template_text
    assert "overall evidence-status verdict" in template_text
    assert "intentionally omitted" in template_text


def test_app_exposes_only_read_only_html_surfaces() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert {"/", "/decisions", "/decisions/b4-research-reopen"}.issubset(paths)
    assert not any("approve" in path or "order" in path or "alpaca" in path for path in paths)


def test_browser_asset_only_changes_navigation_state() -> None:
    client_script = (Path(PACKAGE_ROOT) / "static" / "cockpit.js").read_text(encoding="utf-8")

    for forbidden in ("fetch(", "XMLHttpRequest", "crypto.subtle", "FormData"):
        assert forbidden not in client_script
