from pathlib import Path

from aic.cockpit.app import PACKAGE_ROOT, create_app
from aic.cockpit.presenter import build_b4_research_reopen_projection
from aic.council.judge_entry_preflight import (
    EXPECTED_CANDIDATE_ORDER,
    JUDGE_ENTRY_PREFLIGHT_STATUS,
)
from aic.council.judge_production import EXPECTED_REQUIRED_UNKNOWN_REFS


def test_projection_preserves_branch_local_b4_boundary_without_final_decision() -> None:
    view = build_b4_research_reopen_projection()

    assert view.candidates == EXPECTED_CANDIDATE_ORDER
    assert view.source_status == JUDGE_ENTRY_PREFLIGHT_STATUS
    assert view.unknown_refs == EXPECTED_REQUIRED_UNKNOWN_REFS
    assert [lane.name for lane in view.lanes] == ["Bull", "Bear", "Red Team", "Judge"]
    assert view.lanes[2].role.endswith("not a third vote")
    assert view.lanes[3].state == "PENDING / NOT AUTHORIZED"


def test_projection_never_invents_downstream_execution_or_options_fields() -> None:
    view = build_b4_research_reopen_projection()
    assert all(lane.state != "INVEST" for lane in view.lanes)
    assert view.projection_id != "FINAL_DECISION"

    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(PACKAGE_ROOT / "templates").glob("*.html")
    )
    for forbidden in ("USD_NOTIONAL", "MARKET"):
        assert forbidden not in template_text
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
