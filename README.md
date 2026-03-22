# Prediction Sandbox

A Raspberry Pi 5-based prediction market trading pipeline. Polls Manifold Markets, asks Claude for probability estimates, applies Kelly Criterion for bet sizing, and tracks calibration via Brier scores.

## What it does

1. Polls Manifold Markets every 5 minutes for active binary markets
2. Sends market questions to Claude (Sonnet) for probability estimates
3. Compares Claude's estimate against the market price to find edges
4. Applies fractional Kelly Criterion for bet sizing (conservative 0.25x)
5. Enforces hard budget limits — starts in calibration mode (no real bets)
6. Logs all predictions and trades to SQLite
7. Tracks calibration via Brier scores

## Quick start (local dev)

```bash
git clone https://github.com/LoryGlory/predictions-sandbox.git
cd predictions-sandbox
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and MANIFOLD_API_KEY
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

After setup, the pipeline runs every 5 minutes via systemd timer.

## Backtesting

Validate Claude's calibration against already-resolved markets — no waiting required:

```bash
python scripts/run_backtest.py --count 50
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
| `scripts/run_backtest.py` | Calibration backtest on resolved markets |
| `scripts/check_health.py` | Health check (API keys, DB access) |
| `scripts/setup_pi.sh` | One-shot Pi setup (venv, systemd, UFW) |

## Architecture

```
Manifold API → scanner → Claude estimator → Kelly sizing → budget guardian → paper trade → SQLite
```

- **Phase 1 (current):** Calibration mode — no real bets, logging only
- **Phase 2:** FastAPI dashboard for local network monitoring
- **Phase 3:** Live trading on Manifold (after calibration validates)

## Configuration

All settings via `.env` (see `.env.example`). Key ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BUDGET_DAILY_LIMIT` | `0` | 0 = calibration mode, no real bets |
| `KELLY_FRACTION` | `0.25` | Quarter Kelly (conservative) |
| `MIN_EDGE_THRESHOLD` | `0.05` | Min edge to bet (5%) |
| `MAX_MARKETS_PER_CYCLE` | `20` | Markets per polling cycle |

## Project structure

```
src/
├── markets/       # Manifold API client, market scanner
├── analysis/      # Claude estimator, prompt templates
├── trading/       # Kelly criterion, budget guardian, trade executor
├── tracking/      # Brier score, calibration, performance, logging
├── db/            # SQLite schema, migrations, connection manager
└── backtesting/   # Backtest against resolved markets
scripts/
├── run_pipeline.py    # Main entry point
├── run_backtest.py    # Calibration backtest
├── check_health.py    # Health check
└── setup_pi.sh        # Pi setup
```
