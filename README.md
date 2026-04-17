# Prediction Sandbox

A Raspberry Pi 5-based prediction market pipeline. Polls Manifold Markets and Polymarket, asks Claude for probability estimates, applies Kelly Criterion for bet sizing, and tracks calibration via Brier scores.

**Status:** Paper trading only. No real money. See [my blog post](https://loryglorybuilds.substack.com/) for the calibration results so far.

## What it does

1. Polls Manifold Markets and Polymarket every 30 minutes for active binary markets
2. Skips re-estimation if a market was predicted <4h ago and price moved <3pp (dedup)
3. Filters markets by category whitelist/blacklist based on observed calibration
4. Sends market questions to Claude (Sonnet) for probability estimates with full reasoning
5. A/B tests prompt versions (`v1_baseline` vs `v2_market_aware`)
6. Compares Claude's estimate against the market price to find edges
7. Applies fractional Kelly Criterion for bet sizing (conservative 0.25x, capped at 5% of bankroll)
8. Enforces hard budget limits — calibration mode available via `BUDGET_DAILY_LIMIT=0`
9. Polls for resolution of past predictions and computes Brier scores
10. Logs everything to SQLite: markets, predictions, trades, calibration, stories, costs
11. Read-only FastAPI dashboard with per-platform breakdown and filtered calibration views
12. Nightly Telegram report with daily Brier scores and weekly blog-story digest

## Quick start (local dev)

```bash
git clone https://github.com/LoryGlory/predictions-sandbox.git
cd predictions-sandbox
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and optionally MANIFOLD_API_KEY, TELEGRAM_*
python scripts/check_health.py
python scripts/run_pipeline.py
```

## Raspberry Pi setup

```bash
git clone https://github.com/LoryGlory/predictions-sandbox.git ~/predictions-sandbox
cd ~/predictions-sandbox
cp .env.example .env
# Edit .env — add your API keys
bash scripts/setup_pi.sh
```

Typical cron layout:

```
*/30 * * * * cd ~/predictions-sandbox && .venv/bin/python scripts/run_pipeline.py
0 */6 * * * cd ~/predictions-sandbox && .venv/bin/python scripts/resolve_predictions.py
0 2 * * *  cd ~/predictions-sandbox && .venv/bin/python scripts/nightly_calibration.py
0 9 * * *  cd ~/predictions-sandbox && .venv/bin/python scripts/run_daily_summary.py
@reboot    cd ~/predictions-sandbox && .venv/bin/python -m dashboard.app
```

## Backtesting

Validate Claude's calibration against already-resolved markets — no waiting required:

```bash
python scripts/run_backtest.py --count 50
python scripts/run_backtest.py --count 30 --prompt-version v2_market_aware --with-market-price
```

Outputs a calibration report comparing Claude's Brier score against the market-price baseline.

## Running tests

```bash
pytest tests/ -v
```

## Key scripts

| Script | Purpose |
|--------|---------|
| `scripts/run_pipeline.py` | One polling cycle (fetch → estimate → size → log) |
| `scripts/resolve_predictions.py` | Poll APIs for resolution of past predictions |
| `scripts/nightly_calibration.py` | Daily Brier report + weekly story digest (Telegram) |
| `scripts/run_daily_summary.py` | Morning summary of yesterday's activity |
| `scripts/run_backtest.py` | Calibration backtest on resolved markets |
| `scripts/export_stories.py` | Export noteworthy predictions as blog content |
| `scripts/check_health.py` | Health check (API keys, DB access) |
| `scripts/setup_pi.sh` | One-shot Pi setup (venv, systemd, UFW) |

## Architecture

```
Manifold API ─┐
              ├─→ scanner → Claude estimator → Kelly sizing → budget guardian → paper trade → SQLite
Polymarket ───┘                                                                              │
                                                                                  ┌──────────┘
                                                         Nightly report ←── calibration ←── stories
                                                              │
                                                          Telegram
```

- **Raspberry Pi 5** — secure orchestrator: holds API keys, enforces budget, makes trade decisions. Never runs LLM inference locally.
- **Claude API** — single LLM call per estimate via the Anthropic SDK.
- **SQLite on NVMe** — all storage (markets, predictions, trades, calibration, stories, costs).
- **FastAPI + Pico CSS** — read-only dashboard over HTTP Basic Auth.
- **Telegram** — notifications only, silently no-ops when unconfigured.

## Phase roadmap

| Phase | Status | What |
|-------|--------|------|
| 1 | ✅ Done | CLI pipeline, Manifold, Claude, SQLite, paper trades, dedup |
| 2 | ✅ Done | FastAPI dashboard, category filtering, API cost guardian |
| 3 | ✅ Done | Prompt v2 (market-aware), A/B testing framework |
| 4 | ✅ Done | Polymarket paper trading, blog story collector |
| 5 | ✅ Done | Telegram notifications, resolver for live calibration |
| 6 | Pending | Live Manifold trades (real mana, still fake money) |
| 7 | Future | MiroFish swarm consensus (requires Mac Mini M4) |
| 8 | Future | Polymarket live trading (real USDC on Polygon) |

## Configuration

All settings via `.env` (see `.env.example`). Key ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BUDGET_DAILY_LIMIT` | `0` | 0 = calibration mode, no real bets |
| `KELLY_FRACTION` | `0.25` | Quarter Kelly (conservative) |
| `MIN_EDGE_THRESHOLD` | `0.05` | Min edge to bet (5%) |
| `MAX_MARKETS_PER_CYCLE` | `20` | Markets per polling cycle |
| `ACTIVE_PROMPT_VERSION` | `v2_market_aware` | Prompt template version |
| `CATEGORY_FILTER_ENABLED` | `true` | Filter markets by whitelist/blacklist |
| `DAILY_API_BUDGET` | `3.0` | Daily Claude API spend cap (USD) |
| `POLYMARKET_ENABLED` | `false` | Enable Polymarket paper trading |
| `NIGHTLY_REPORT_ENABLED` | `true` | Send nightly calibration report |

## Project structure

```
src/
├── markets/          # Manifold + Polymarket API clients, market scanner
├── analysis/         # Claude estimator, prompt router, ensemble stub
├── trading/          # Kelly criterion, budget guardian, trade executor
├── tracking/         # Brier score, calibration, logging
├── content/          # Blog story collector
├── notifications/    # Telegram bot (send-only)
├── db/               # SQLite schema, migrations, connection manager
└── backtesting/      # Backtest against resolved markets

config/
├── settings.py       # All env var parsing (single source of truth)
└── prompts/          # v1_baseline, v2_market_aware prompt templates

dashboard/            # FastAPI + Pico CSS read-only dashboard
scripts/              # CLI entry points (pipeline, resolver, reports, backtest)
tests/                # 123 tests covering math, scanner, estimator, telegram, ...
```

## License

MIT. See [LICENSE](LICENSE).
