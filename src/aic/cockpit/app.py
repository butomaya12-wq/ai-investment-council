"""FastAPI routes for the read-only B7 cockpit."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .presenter import DecisionReadModel, build_b4_research_reopen_projection


PACKAGE_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))


def create_app(*, read_model: DecisionReadModel | None = None) -> FastAPI:
    """Create the B7 UI application with a fixed, read-only projection."""

    projection = read_model or build_b4_research_reopen_projection()
    app = FastAPI(title="Decision Integrity Cockpit", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")

    def render(request: Request, template_name: str, *, page: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context={"page": page, "view": projection},
        )

    @app.get("/", response_class=HTMLResponse, name="cockpit")
    def cockpit(request: Request) -> HTMLResponse:
        return render(request, "cockpit.html", page="cockpit")

    @app.get("/decisions", response_class=HTMLResponse, name="decisions")
    def decisions(request: Request) -> HTMLResponse:
        return render(request, "decisions.html", page="decisions")

    @app.get("/decisions/b4-research-reopen", response_class=HTMLResponse, name="decision_detail")
    def decision_detail(request: Request) -> HTMLResponse:
        return render(request, "decision_detail.html", page="decision_detail")

    return app


app = create_app()
