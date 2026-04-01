"""Dashboard route handlers — all read-only."""
import math

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dashboard import queries

router = APIRouter()
PER_PAGE = 20


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = await queries.get_overview_stats()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "index.html", stats)


@router.get("/markets", response_class=HTMLResponse)
async def markets(request: Request, page: int = 1):
    rows, total = await queries.get_markets(page=page, per_page=PER_PAGE)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "markets.html", {
        "markets": rows,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    })


@router.get("/markets/{market_id}", response_class=HTMLResponse)
async def market_detail(request: Request, market_id: int):
    market = await queries.get_market_detail(market_id)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "market_detail.html", {
        "market": market,
    })


@router.get("/calibration", response_class=HTMLResponse)
async def calibration(request: Request):
    overview = await queries.get_calibration_overview()
    categories = await queries.get_category_stats(min_count=3)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "calibration.html", {
        **overview,
        "categories": categories,
    })


@router.get("/trades", response_class=HTMLResponse)
async def trades(request: Request, page: int = 1):
    rows, summary, total = await queries.get_trades(page=page, per_page=PER_PAGE)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "trades.html", {
        "trades": rows,
        "summary": summary,
        "page": page,
        "total_pages": total_pages,
    })


@router.get("/costs", response_class=HTMLResponse)
async def costs(request: Request):
    rows, total = await queries.get_cost_log()
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "costs.html", {
        "cost_log": rows,
        "total_cost": total,
    })
