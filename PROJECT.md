# Prediction Sandbox — Project Brief

*Living document. Updated as the project evolves.*

---

## What This Is

An always-on prediction market trading pipeline running on a **Raspberry Pi 5** (8GB, 512GB NVMe, headless). Polls Manifold Markets, asks Claude for probability estimates, applies Kelly Criterion for bet sizing, tracks calibration via Brier scores, and eventually trades real money on Polymarket.

**Current status:** Phase 1 complete. Pi arriving ~2026-03-24. Ready to clone and run.

**Repo:** https://github.com/LoryGlory/predictions-sandbox

---

## Strategic Vision

### Two-machine architecture

| Machine | Role |
|---------|------|
| **Raspberry Pi 5** | Secure orchestrator — holds keys, enforces budget, makes trade decisions. Never runs LLM inference. |
| **Mac Mini M4 24GB** *(future, ~€600)* | Runs MiroFish + Ollama (Qwen 2.5:14B) locally. Pi calls it over LAN. |
| **Cloud GPU** *(future)* | Burst capacity for high-edge trades (RunPod/Vast.ai). "Spend €2 to validate a €50+ EV trade." |

**Trust boundary:** Wallet credentials and blockchain signing never leave the Pi.

### MiroFish (Phase 5+)

Swarm intelligence engine — spawns hundreds of AI agents with unique personalities, simulates social debate, and produces a consensus probability. Plugs into the ensemble module as an additional signal alongside Claude. Requires Mac Mini M4.

Formula: `final_estimate = weighted_average(claude_estimate, mirofish_consensus, ...)` where weights are determined by per-source Brier scores.

### Polymarket endgame (Phase 6+)

Moves from Manifold play money to Polymarket real USDC on Polygon. The `TradeExecutor` interface is already designed to swap backends without touching pipeline logic.

### Open source + blog series

Code quality and commit history matter from the start. Will be open-sourced with a write-up.

---

## Phase Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| **1** | ✅ Done | CLI pipeline: Manifold → Claude → Kelly → SQLite. Dedup, retries, category tagging, cost tracking. |
| **2** | Planned | Read-only FastAPI dashboard on local network. Phone-accessible via Tailscale. |
| **3** | Planned | Live Manifold trades (real mana, still no real money). |
| **4** | Planned | Telegram notifications: errors, high-edge alerts, daily summary. |
| **5** | Future | MiroFish swarm consensus (needs Mac Mini M4). |
| **6** | Future | Polymarket live trading (real USDC). |

---

## Tech Stack

- **Python 3.11+**, async throughout (`httpx`, `aiosqlite`)
- **Anthropic Python SDK** — Claude Sonnet for probability estimates
- **SQLite** on NVMe — predictions, trades, calibration, daily API cost log
- **tenacity** — exponential backoff retry on all external API calls
- **FastAPI** — Phase 2 dashboard (stub exists)
- **systemd timer** — 30-minute polling cycle on Pi
- **UFW** — firewall (SSH only inbound, dashboard port blocked until Phase 2)

---

## Architecture

```
Manifold API
    │
    ▼
Market Scanner (binary only, 5–95% prob, 24h+ to close)
    │
    ▼
Dedup check (skip if <4h since last estimate AND price moved <3pp)
    │
    ▼
Claude Estimator (JSON-structured output, full reasoning stored)
    │
    ▼
Kelly Criterion (0.25x fractional, max 5% bankroll per bet)
    │
    ▼
Budget Guardian (daily + total hard limits, kill switch at 10% bankroll loss)
    │
    ▼
Trade Executor (paper mode only in Phase 1)
    │
    ▼
SQLite (markets, predictions, trades, calibration, api_cost_log)
```

---

## What's Built (Phase 1)

| Module | Files | Notes |
|--------|-------|-------|
| DB layer | `src/db/` | Schema, migrations, async connection manager |
| Kelly Criterion | `src/trading/kelly.py` | Fractional Kelly with bankroll cap |
| Budget guardian | `src/trading/risk.py` | Daily/total limits, kill switch |
| Manifold client | `src/markets/manifold.py` | Async httpx, retry on `httpx.HTTPError` |
| Market scanner | `src/markets/scanner.py` | Filters to tradeable binary markets + `get_tags()` |
| Claude estimator | `src/analysis/estimator.py` | Retry on `anthropic.APIError`, `_call_api` helper |
| Prompt templates | `src/analysis/prompts.py` | JSON-structured output, chain-of-thought |
| Trade executor | `src/trading/executor.py` | Paper mode only, integrates budget guardian |
| Brier score | `src/tracking/calibration.py` | Brier score, mean Brier, Brier Skill Score |
| Performance | `src/tracking/performance.py` | P&L, win rate, ROI from trade records |
| Logger | `src/tracking/logger.py` | Structured logging from `settings.log_level` |
| Backtest | `src/backtesting/backtest.py` | Tests calibration against resolved markets |
| Pipeline | `scripts/run_pipeline.py` | Full cycle with dedup + cost tracking |
| Backtest CLI | `scripts/run_backtest.py` | `--count N` resolved markets |
| Health check | `scripts/check_health.py` | API keys + DB, exit 0/1 |
| Pi setup | `scripts/setup_pi.sh` | venv, systemd, UFW, 30-min timer |

**Test coverage:** 74 tests, all green. CI on Python 3.11 + 3.12.

---

## Key Design Decisions

**Dedup:** Skip re-estimating if predicted <4h ago AND price moved <3pp. Reduces Claude calls from ~5,760/day to ~€8–15/month.

**Polling:** 30-minute systemd timer (down from 5 min).

**Cost tracking:** `api_cost_log` table — daily call count + estimated USD spend. Separate from trading budget guardian.

**Category tagging:** Manifold `groupSlugs` stored as JSON in `markets.tags`. Enables per-category Brier score analysis.

**Retry:** `tenacity` with 3-attempt exponential backoff (2–10s) on all Manifold and Claude API calls. Failures logged, pipeline continues.

**Estimator interface:** `ProbabilityEstimate` dataclass is the contract between estimators and the pipeline. Any future source (MiroFish, second LLM) returns this type. Ensemble module combines them with configurable weights.

**Executor interface:** `TradeExecutor` wraps Manifold today. Polymarket replaces it in Phase 6 — no other files change.

---

## Known Gaps (next to implement)

| Priority | Task | Notes |
|----------|------|-------|
| 🟡 Medium | Telegram notifications | Errors, high-edge alerts (>15%), daily summary |
| 🟡 Medium | Daily cost halt | Stop Claude calls if daily token budget exceeded |
| 🟢 Later | Phase 2 dashboard | FastAPI + HTML templates, Tailscale for remote access |
| 🟢 Later | Live Manifold trades | After calibration validates over ~100 resolved predictions |

---

## First Steps When Pi Arrives

```bash
git clone https://github.com/LoryGlory/predictions-sandbox.git ~/predictions-sandbox
cd ~/predictions-sandbox
cp .env.example .env
# Add ANTHROPIC_API_KEY and MANIFOLD_API_KEY
bash scripts/setup_pi.sh
python scripts/check_health.py
python scripts/run_backtest.py --count 30   # validate calibration first
# Pipeline starts automatically every 30 min via systemd
journalctl -u prediction-pipeline -f         # watch logs
```

---

## My Background

Senior Software Engineer, frontend/platform focus (TypeScript, React). Python-comfortable. Ramping up on AI engineering (LangChain, agents, MCP, embeddings). This is a learning project that should actually work.

**Coding preferences:** Clean over clever. Type hints everywhere. Comments explain why. Tests for math-critical code. No over-engineering.
