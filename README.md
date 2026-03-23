# Prediction Sandbox

A Raspberry Pi 5-based prediction market trading pipeline. Polls Manifold Markets, asks Claude for probability estimates, applies Kelly Criterion for bet sizing, and tracks calibration via Brier scores.

## What it does

1. Polls Manifold Markets every 30 minutes for active binary markets
2. Skips re-estimation if a market was predicted <4h ago and price moved <3pp (dedup)
3. Sends market questions to Claude (Sonnet) for probability estimates with full reasoning
4. Compares Claude's estimate against the market price to find edges
5. Applies fractional Kelly Criterion for bet sizing (conservative 0.25x)
6. Enforces hard budget limits — starts in calibration mode (no real bets)
7. Logs all predictions, trades, and daily API cost to SQLite
8. Tracks calibration via Brier scores, sliceable by market category

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

After setup, the pipeline runs every 30 minutes via systemd timer.

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
Manifold API → scanner → dedup check → Claude estimator → Kelly sizing → budget guardian → paper trade → SQLite
```

**Two-machine design (current + future):**
- **Raspberry Pi 5** — secure orchestrator: holds API keys, enforces budget, makes trade decisions. Never runs LLM inference locally.
- **Mac Mini M4** *(future)* — runs MiroFish swarm intelligence + Ollama locally. Pi calls it over LAN for additional probability signals.
- **Cloud GPU** *(future)* — burst capacity for high-edge trades via RunPod/Vast.ai.

**Phases:**

| Phase | Status | What |
|-------|--------|------|
| 1 | ✅ Done | CLI pipeline, Manifold, Claude, SQLite, paper trades, dedup, retries |
| 2 | Planned | FastAPI dashboard (local network, Tailscale for phone access) |
| 3 | Planned | Live Manifold trades (real mana) |
| 4 | Planned | Telegram notifications + cost tracking alerts |
| 5 | Future | MiroFish swarm consensus (requires Mac Mini M4) |
| 6 | Future | Polymarket live trading (real USDC on Polygon) |

## Configuration

All settings via `.env` (see `.env.example`). Key ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BUDGET_DAILY_LIMIT` | `0` | 0 = calibration mode, no real bets |
| `KELLY_FRACTION` | `0.25` | Quarter Kelly (conservative) |
| `MIN_EDGE_THRESHOLD` | `0.05` | Min edge to bet (5%) |
| `MAX_MARKETS_PER_CYCLE` | `20` | Markets per polling cycle |
| `POLL_INTERVAL_SECONDS` | `1800` | Polling interval (30 min default) |

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
