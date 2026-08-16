<div align="center">

# Prediction Sandbox

**A production LLM evaluation system.** It measures whether a language model's
probability estimates are actually any good — continuously, against ground truth,
with cost controls and safety rails, unattended on a Raspberry Pi since March 2026.

Prediction markets are the test domain, not the point. They were chosen because
they are one of the few settings where an LLM's forecasts get scored against
reality automatically, in public, without anyone hand-labelling anything.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/LoryGlory/predictions-sandbox/actions/workflows/ci.yml/badge.svg)](https://github.com/LoryGlory/predictions-sandbox/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-256%20passing-brightgreen.svg)](tests/)
[![Claude](https://img.shields.io/badge/model-Claude%20Sonnet-8b5cf6.svg)](https://www.anthropic.com/)

[Findings](#headline-finding-the-model-does-not-beat-the-market) ·
[Evaluation methodology](#evaluation-methodology) ·
[What broke in production](#what-broke-in-production) ·
[Write-ups](https://loryglorybuilds.substack.com/) ·
[Architecture](#architecture)

</div>

---

## Headline finding: the model does not beat the market

After ~1,900 resolved predictions, Claude reaches roughly **crowd parity and no
better**. Measured as Brier Skill Score against the market price as baseline
(0 = exactly as good as the crowd, negative = worse):

| Window | Skill score | Resolved predictions |
|---|---|---|
| Best sustained window | **−0.006** | 672 |
| Typical | **−0.06 to −0.16** | varies |
| Live real-mana trading | **−M$8.95 net** | 28 settled bets |

Every apparent edge this project found later died under scrutiny. Category-level
edges (+0.61 on competitive programming, etc.) turned out to rest on N=4–9 **and**
to be circular — those categories had been whitelisted *because* they looked good
in the same data. A confidence-weighting signal looked worth +0.37 at N=15 and
evaporated to −0.08 by N=24. A pre-registered test for favorite-longshot bias
found the effect pointing the wrong way.

The one robust finding is relative rather than absolute: **structured scenario
decomposition improves calibration**. Forcing the model to write out a status-quo
case, an explicit path to NO, an explicit path to YES, and a base rate *before*
committing to a number cut Brier error by ~30% versus the previous prompt —
measured on **88 markets where both prompt versions ran on the same market**, so
the comparison controls for difficulty. It still does not beat the market.

The negative result is the output. The measurement apparatus is what makes it
trustworthy: it killed its own claims four times.

## Evaluation methodology

The parts that generalise beyond prediction markets:

**Scoring against ground truth.** Brier score per prediction, plus Brier Skill
Score against a baseline forecaster (here, the market price). Absolute error is
close to meaningless on its own — a model looks brilliant on easy questions and
terrible on hard ones — so every number is reported relative to what the baseline
achieved on the *same* items.

**Paired A/B prompt testing.** Prompt versions are registered in
`config/prompts/`, and 10% of items are estimated by two versions *simultaneously*.
Comparing aggregate scores across versions is misleading when the versions saw
different item mixes; comparing them item-by-item is not. This distinction
reversed one of the project's conclusions.

**Calibration analysis, not just accuracy.** Predictions are bucketed by stated
probability and checked for whether things predicted at 70% actually happen ~70%
of the time — sliced by category, prompt version, and the model's own
self-reported confidence. Wilson score intervals rather than the normal
approximation, because the interesting buckets sit near p=0 where the normal
interval goes negative.

**Cost accounting per call.** Real token-level cost from the API response, not an
estimate, with a hard daily spend ceiling that halts LLM calls when breached. An
earlier flat-rate estimate understated true spend by 5–8×.

**Pre-registration.** The most recent experiment fixes its hypothesis, bucket
edges, minimum sample size and filter definitions in a document committed to git
*before* any data is collected ([`experiment-longshot-bias.md`](experiment-longshot-bias.md)).
This was a direct response to the earlier circular findings — and it is the first
experiment here that did not produce a false positive.

**Selective tool use.** Web search is enabled only for items a classifier flags as
depending on post-cutoff information, since it costs money and adds latency.
Whether it fired is recorded per prediction so its effect can be measured
separately.

## What broke in production

The most useful thing this project produced is a reminder that green tests are not
evidence a system works. A review after four months of live operation found:

- **The kill switch had never fired.** It existed, was tested, and was structurally
  incapable of triggering — its state lived in memory in a process that exits every
  30 minutes, so it reset before it could ever accumulate anything.
- **Every bet was ~20% smaller than recorded.** `int(round(2.5))` is `2` in Python
  (banker's rounding). Every M$2.50 stake was really M$2, and all P&L inherited the
  error.
- **Every stored bet ID was null.** The API returns the identifier under a different
  key on write than on read.
- **A retry wrapper sat on a money-moving POST.** A network timeout does not mean
  the bet failed — retrying could have placed it two or three times.

256 tests were green throughout. What caught it was reconciling the local ledger
bet-by-bet against the exchange's own records. That reconciliation now runs as a
script ([`scripts/reconcile_manifold.py`](scripts/reconcile_manifold.py)) and exits
non-zero on any discrepancy.

## Write-ups

The [blog series](https://loryglorybuilds.substack.com/) documents this as it
happened, including the parts that did not work and one finding that a later,
larger sample forced a retraction of. Honest reporting of negative results is the
point, not a consolation prize.

## Authorship

**The code in this repository was written by Claude Code sessions, directed by me
across roughly five months.** Stating that plainly because an experienced reader
will recognise it anyway, and because the interesting claim is not "I typed 6,500
lines of Python."

What is mine: the architecture and the decision of what to build; every call about
which hypotheses to test and — more often — which to kill; the experiment design,
including the pre-registration discipline adopted after the early findings proved
circular; and all of the operations. Provisioning, deployment, the hardware
watchdog, diagnosing multi-day outages from logs, and every production command run
against the live system.

Directing coding agents to build and then *operate* an unattended production
system for five months is the skill this repository actually evidences.

## What this is not

No RAG. No vector store or embeddings. No agent orchestration — it is a single
model call per item, not a multi-agent system. No fine-tuning and no model
training; the statistics are classical. If you are looking for those, they are
genuinely not here.

## Architecture

```
Manifold API ─┐
              ├─→ scanner ──→ Claude estimator ──→ Kelly sizing ──→ risk guardian ──→ executor ──→ SQLite
Polymarket ───┘   (filters)   (prompt versions,    (fractional,     (budget, kill    (paper or        │
                               web search, cost)    capped)          switch, caps)    live)           │
                                                                                                      │
                        Telegram ←── nightly report ←── calibration ←── resolver (polls for outcomes) ┘
```

- **Raspberry Pi 5** — orchestrator. Holds credentials, enforces limits, makes
  decisions. Never runs inference locally.
- **Claude (Sonnet)** via the Anthropic SDK — one call per estimate, structured
  JSON output with tolerant parsing.
- **SQLite on NVMe** — markets, predictions, trades, calibration, costs. Plain SQL,
  no ORM, idempotent migrations.
- **FastAPI + Pico CSS** — read-only dashboard behind HTTP Basic Auth, reachable
  over Tailscale.
- **Reliability** — flock against overlapping cron runs, systemd hardware watchdog
  (15s) after a five-day silent outage, network watchdog, Telegram alerting.

Stack: Python 3.11+, async throughout (`httpx`, `aiosqlite`), `tenacity` for
retries on idempotent calls only, `pytest`/`pytest-asyncio`, `ruff`, `mypy`.
~6,500 lines across 71 files.

## Quick start

```bash
git clone https://github.com/LoryGlory/predictions-sandbox.git
cd predictions-sandbox
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # add ANTHROPIC_API_KEY at minimum
python scripts/check_health.py
python scripts/run_pipeline.py
```

Defaults are safe: `BUDGET_DAILY_LIMIT=0` and `MANIFOLD_MODE=paper` mean nothing
is wagered until explicitly enabled.

Backtest against already-resolved markets without waiting for new ones:

```bash
python scripts/run_backtest.py --count 50 --prompt-version v3_scenario --with-market-price
```

## Key scripts

| Script | Purpose |
|--------|---------|
| `scripts/run_pipeline.py` | One cycle: fetch → filter → estimate → size → execute → log |
| `scripts/resolve_predictions.py` | Poll for outcomes, score predictions, settle trades |
| `scripts/reconcile_manifold.py` | Diff the local ledger against the exchange; non-zero exit on drift |
| `scripts/analyze_confidence.py` | Skill score by the model's self-reported confidence |
| `scripts/run_category_analysis.py` | Per-category calibration breakdown |
| `scripts/longshot_pilot.py` | Pre-registered experiment, cheap directional gate |
| `scripts/longshot_phase0.py` | Pre-registered experiment, full analysis |
| `scripts/run_backtest.py` | Calibration backtest on resolved markets |
| `scripts/nightly_calibration.py` | Daily Brier report via Telegram |
| `scripts/setup_pi.sh` | One-shot Pi provisioning |

## Project structure

```
src/
├── analysis/         # Claude estimator, prompt router, ensemble
├── markets/          # Manifold + Polymarket clients, scanner, price history
├── trading/          # Kelly sizing, risk guardian, executor, P&L
├── tracking/         # Brier scoring, calibration, price bucketing, logging
├── db/               # Schema, migrations, connection manager
├── backtesting/      # Retrospective evaluation
├── content/          # Notable-prediction capture for write-ups
└── notifications/    # Telegram (send-only, no-ops when unconfigured)

config/
├── settings.py       # All env parsing — single source of truth
└── prompts/          # v1_baseline · v2_market_aware · v3_scenario

dashboard/            # FastAPI read-only dashboard
scripts/              # CLI entry points
tests/                # 256 tests
```

## Configuration

Everything via `.env` (see [`.env.example`](.env.example)). The ones that matter:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ACTIVE_PROMPT_VERSION` | `v2_market_aware` | Prompt under test (`v3_scenario` is the current best) |
| `DAILY_API_BUDGET` | `3.0` | Hard daily LLM spend ceiling (USD) |
| `BUDGET_DAILY_LIMIT` | `0` | 0 = evaluation only, nothing wagered |
| `BUDGET_TOTAL_LIMIT` | `50` | Max open exposure; also the kill-switch base |
| `KILL_SWITCH_LOSS_PCT` | `0.10` | Halt live trading at this fraction of losses |
| `MANIFOLD_MODE` | `paper` | `live` places real bets, behind four hard caps |
| `KELLY_FRACTION` | `0.25` | Quarter Kelly |
| `MIN_EDGE_THRESHOLD` | `0.05` | Minimum disagreement with the baseline to act |
| `WHITELIST_MODE` | `false` | Restrict to categories with measured positive skill |

## Status

| Phase | | |
|-------|---|---|
| 1 | Done | Pipeline, Manifold, Claude, SQLite, dedup |
| 2 | Done | Dashboard, category filtering, cost guardian |
| 3 | Done | Prompt versioning + paired A/B framework |
| 4 | Done | Polymarket paper trading, story capture |
| 5 | Done | Telegram, resolver, live calibration |
| 6 | Done | Live Manifold trading, ledger reconciliation, working kill switch |
| 7 | Running | Pre-registered favorite-longshot experiment |
| 8 | Future | Multi-model ensemble (needs local inference hardware) |

## License

MIT. See [LICENSE](LICENSE).
