"""FastAPI dashboard — read-only view of pipeline data.

Run locally:
    python -m dashboard.app

Or with uvicorn:
    uvicorn dashboard.app:app --host 0.0.0.0 --port 8000 --reload
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from dashboard.routes import router

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="Predictions Sandbox", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Template filters ──────────────────────────────────────────────────────

def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _usd(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:.3f}"


def _brier_fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4f}"


def _skill_color(value: float | None) -> str:
    if value is None:
        return ""
    if value > 0.05:
        return "color: green"
    if value < -0.05:
        return "color: red"
    return ""


templates.env.filters["pct"] = _pct
templates.env.filters["usd"] = _usd
templates.env.filters["brier"] = _brier_fmt
templates.env.filters["skill_color"] = _skill_color

# Share templates instance with routes
app.state.templates = templates

app.include_router(router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=8000, reload=True)
