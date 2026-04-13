# CLAUDE.md — Prediction Sandbox

Project-specific conventions for Claude Code. Read this before touching anything.

---

## What This Project Is

A Raspberry Pi 5-based prediction market pipeline. It polls Manifold Markets (and optionally Polymarket), asks Claude for probability estimates, applies Kelly Criterion for bet sizing, and tracks calibration via Brier scores. Runs headless on a Pi, 24/7.

**Current phase:** Phase 1 complete, Phase 2 (dashboard) deployed, Polymarket paper trading available.

**Repo:** https://github.com/LoryGlory/predictions-sandbox

---

## Architecture

```
Manifold API ─┐
              ├─→ scanner → Claude estimator → Kelly sizing → budget guardian → paper trade → SQLite
Polymarket ───┘                                                                              │
                                                                                   ┌─────────┘
                                                          Nightly report ←── calibration ←── stories
                                                              │
                                                          Telegram
```

- **Pi 5** = secure orchestrator. Holds keys, enforces limits, makes trade decisions. Never runs LLM inference.
- **SQLite on NVMe** for all storage (markets, predictions, trades, calibration, stories, cost log).
- **FastAPI dashboard** for monitoring (read-only, HTTP Basic Auth).
- **Telegram bot** for notifications (daily calibration report, errors, weekly story digest).
- **Future Mac Mini M4** = runs MiroFish + Ollama (Qwen 2.5:14B) locally. Pi calls it over LAN. Not built yet.
- **Future cloud GPU** = burst capacity for high-edge trades via RunPod/Vast.ai. Not built yet.

---

## Key Files

| File | Purpose |
|------|---------|
| `config/settings.py` | Single source of truth for all config. Never hardcode limits elsewhere. |
| `config/prompts/v1_baseline.py` | Original prompt template (kept as A/B baseline). |
| `config/prompts/v2_market_aware.py` | Market-aware prompt with Bayesian prior + deviation justification. |
| `src/analysis/estimator.py` | Claude API wrapper. A/B testing logic. `ProbabilityEstimate` dataclass. |
| `src/analysis/prompts.py` | Prompt router — imports from active version based on config. |
| `src/markets/scanner.py` | Market filtering: whitelist/blacklist categories, low-signal, non-English. |
| `src/markets/manifold.py` | Manifold Markets async API client. |
| `src/markets/polymarket.py` | Polymarket API client (Gamma + CLOB, public endpoints only). |
| `src/db/models.py` | SQLite schema as plain SQL strings. No ORM. |
| `src/trading/risk.py` | Budget guardian + kill switch. Touch carefully. |
| `src/trading/kelly.py` | Kelly Criterion math. Has unit tests — don't break them. |
| `src/trading/executor.py` | Trade execution (paper + Polymarket simulated costs). |
| `src/tracking/calibration.py` | Brier score math. Pure functions, no side effects. |
| `src/content/story_collector.py` | Captures noteworthy events for blog posts. |
| `src/notifications/telegram.py` | Telegram bot (send-only, silently no-ops when unconfigured). |
| `scripts/run_pipeline.py` | One polling cycle. Entry point for systemd timer. |
| `scripts/nightly_calibration.py` | Daily Brier report + weekly story digest (cron at 2am UTC). |
| `scripts/export_stories.py` | Export blog stories as markdown or JSON. |
| `scripts/run_backtest.py` | Backtest against resolved markets. |
| `dashboard/` | FastAPI + Pico CSS dashboard (read-only). |

---

## Conventions

- **Python 3.11+**, type hints everywhere, docstrings on public functions.
- **Async throughout** — `httpx` for HTTP, `aiosqlite` for DB. No blocking calls on the main thread (except the Anthropic SDK — wrapped in `asyncio.to_thread`).
- **Tests for math-critical code** — Kelly, Brier, budget guardian, scanner all have unit tests. Keep them green.
- **All config via `.env`** — never hardcode API keys, limits, or model names. `config/settings.py` is the only place that reads env vars.
- **Feature flags for new functionality** — always add to `.env.example`.
- **Plain SQL** — no ORM. Schema lives in `src/db/models.py` as `SCHEMA` string.
- **`ProbabilityEstimate` dataclass** is the contract between estimators and the rest of the pipeline.
- **Comments explain why, not what.**
- **No `print()` in library code** — use `logging.getLogger(__name__)`.

---

## Key Design Decisions

- **Fractional Kelly (0.25x)** — conservative bet sizing, capped at 5% of bankroll per position.
- **Market price as Bayesian prior** in v2 prompt — Claude starts from market consensus, must justify deviations >15pp.
- **Category filtering** — whitelist/blacklist based on calibration data. Untagged markets are skipped.
- **Dedup** — skip markets estimated within 4hrs unless price moved >3pp.
- **Daily API budget cap** ($3 default) — halts Claude calls when exceeded.
- **Paper trading only on Polymarket** — simulates spread + gas costs, no wallet integration.
- **A/B testing** — 10% of markets get both v1 and v2 prompts for comparison.
- **Story collector** — passively captures notable predictions for blog content.

---

## Active Feature Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `CATEGORY_FILTER_ENABLED` | `true` | Filter markets by whitelist/blacklist categories |
| `NIGHTLY_REPORT_ENABLED` | `true` | Send daily calibration report via Telegram |
| `POLYMARKET_ENABLED` | `false` | Scan and paper-trade Polymarket markets |
| `ACTIVE_PROMPT_VERSION` | `v2_market_aware` | Which prompt template to use (`v1_baseline` / `v2_market_aware`) |
| `DAILY_API_BUDGET` | `3.0` | Max daily Claude API spend in USD |

---

## Future-Proofing Rules (keep these doors open)

**Estimator interface must stay pluggable.** The `Estimator` class wraps Claude today. MiroFish swarm and a second LLM will be added later. All estimators return `ProbabilityEstimate`. The ensemble module combines them with configurable weights.

**Executor interface must stay swappable.** `TradeExecutor` calls Manifold today, paper-trades Polymarket. The interface shouldn't change — just the backend.

**Wallet/signing logic lives only on the Pi.** Never put blockchain signing, private keys, or wallet logic in the Mac Mini or cloud GPU code paths.

**Ensemble weights will eventually be auto-calibrated** by per-source Brier scores. Design with that in mind — don't hardcode weights into logic.

---

## What NOT to Build Yet

- **MiroFish integration** — no Mac Mini yet. Stub the interface only.
- **Polymarket live executor** — no wallet yet. Paper mode only.
- **React dashboard** — FastAPI + simple HTML templates is fine.
- **Multi-model ensemble** — stub exists, but only Claude is active.
- **Auto-calibration of ensemble weights** — manual weights first.
- **Auto-prompt-iteration** — too risky. Manual prompt versions with A/B testing only.
- **Local LLM inference on the Pi** — Pi is the orchestrator, not the inference node.
- **Don't use Postgres** — SQLite is intentional for simplicity.
- **Don't remove the budget guardian or kill switch safety limits.**

---

## CI

GitHub Actions on every PR. Runs on Python 3.11 and 3.12.

```bash
ruff check .          # linting
mypy src/ config/     # type checking
pytest tests/ -v      # 116 tests (all must pass)
```

Exit code 5 from pytest (no tests collected) is handled — treated as pass.

---

## Running Locally

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # add API keys
python scripts/check_health.py
python scripts/run_backtest.py --count 20   # validate calibration first
python scripts/run_pipeline.py              # one cycle
python scripts/nightly_calibration.py       # test nightly report
python scripts/export_stories.py --format markdown  # export blog stories
```

---

## Phase Roadmap

| Phase | Status | What |
|-------|--------|------|
| 1 | Done | CLI pipeline, Manifold, Claude, SQLite, paper trades |
| 2 | Done | FastAPI dashboard, category filtering, dedup, API cost guardian |
| 3 | Done | Prompt v2 (market-aware), A/B testing framework |
| 4 | Done | Polymarket paper trading, blog story collector |
| 5 | Pending | Live Manifold trades (real mana, still fake money) |
| 6 | Pending | Telegram notifications + cost tracking improvements |
| 7 | Future | MiroFish swarm consensus (requires Mac Mini M4) |
| 8 | Future | Polymarket live trading (real USDC) |
