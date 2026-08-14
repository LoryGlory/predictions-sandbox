# Experiment: Favorite-Longshot Bias on Manifold

**Status:** pre-registered, not yet run · **Written:** 2026-08-14 · **Cost to run:** €0 (no LLM calls)

## Why this experiment exists

The bot reaches crowd parity but does not beat the market (best window −0.006 over 672 resolutions; typical −0.06 to −0.16; 28 settled real-mana bets net −M$8.95). The architecture has been trying to win on *processing* — reading the same public information and inferring better than the crowd. That is the hardest of the four possible edges.

This experiment tests a different edge class: **selection**. Not "can Claude estimate better than the market", but "is there a price region where the market is systematically wrong, regardless of what any model thinks".

### Evidence base

Bürgi, Deng & Whelan (UCD working paper, Jan 2026) — [PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf) — analysed 300,000+ transaction-level price observations across 12,403 Kalshi events (Nov 2021 – Apr 2025):

- Contracts priced **under 10¢ lose more than 60%** of the money invested in them
- Contracts priced **above 50¢ earn a small positive return**
- Average return across all Kalshi contracts ≈ **−20%**
- The bias is **diminishing over time** but persists
- Section 6 explains why it has not been competed away: quote-driven microstructure, asymmetric maker/taker fees, and self-selection of heterogeneous beliefs

Counter-evidence worth respecting: Berg & Rietz (2019) found the older, **non-profit** Iowa Electronic Markets did *not* exhibit this bias. That suggests the bias is a property of commercial, fee-charging, quote-driven venues — which is a direct threat to this experiment's external validity (see Limitations).

### The proposed mechanism does NOT transfer to Manifold — stated in advance

Section 6 of the Kalshi paper explains the bias through **quote-driven microstructure with asymmetric maker/taker fees** and self-selection of heterogeneous beliefs. Manifold is a **CPMM automated market maker**: no maker/taker distinction, no trading fees, no order book. None of the proposed machinery exists there.

Only the **behavioral channel** — people enjoy buying cheap longshots — plausibly carries over. That materially **lowers the prior on H1**, and it is written here before the run so that a null can be reported as *predicted* rather than explained after the fact. This is an argument for running the experiment with pre-registration, not an argument against running it.

## Hypothesis (stated before looking at any data)

**H1:** On resolved Manifold binary markets, contracts trading in the lowest price band (< 10%) resolve YES **less often** than their price implies, and the gap is larger than for mid-band contracts.

**H0:** Resolution frequency matches price within noise across all bands — Manifold is calibrated and there is no exploitable band.

**H1 is falsifiable and the falsification is the useful outcome too.** A clean null on a well-powered sample is a publishable result and closes the question.

## Method

### Phase −1 — Thirty-minute directional pilot (do this FIRST)

`predictions.db` stores `market_price` **at prediction time**, not at resolution — that is a genuine pre-resolution snapshot, roughly 1,900 rows deep. It is whitelist-contaminated and therefore **cannot settle the question**, but bucketing it takes about half an hour and answers "does the effect even point in the hypothesised direction?"

If the pilot shows nothing *and* the mechanism does not transfer (see above), that is a legitimate place to stop and write the short "why I did not run this" note instead of spending 6–8 hours. Report the pilot as a pilot, never as evidence.

⚠️ **The pilot does not measure the same thing as Phase 0.** `market_price` is recorded whenever the cron first happened to see the market — sometimes days or weeks before close, never a consistent offset. Phase 0 uses a fixed 24h-before-close cutoff. The two tables are therefore **not comparable and must never be printed side by side** as if they were, whitelist contamination aside. The pilot is a directional smoke test and nothing more.

### Phase 0 — Does the bias exist? (no LLM calls, no capital, no legal question)

This phase is **pure market-price analysis**. It needs no Claude API calls at all: historical prices plus known outcomes.

1. **Sample.** Pull resolved binary markets from the Manifold API directly — **NOT from `predictions.db`**. The existing corpus is contaminated for this purpose: it only contains markets the bot chose to look at under a category whitelist, which is exactly the selection bias that destroyed the earlier category-edge claims. Pull a fresh, unfiltered sample of resolved markets.

2. **Price snapshot — THE CRITICAL STEP. Read this before writing any code.**

   ⚠️ **`/v0/markets` returns `probability` as the CURRENT value. For a resolved market that is the POST-RESOLUTION price** — near 1.0 for YES, near 0.0 for NO. The existing `fetch_resolved_markets` reads exactly this field and its comment describes it as "the probability at resolution time", which is wrong for this purpose.

   **Bucketing on that field would produce a colossal, beautiful, entirely fake longshot bias** — every NO-resolving market sitting in the lowest bucket by construction. It would look like the strongest finding this project has ever produced and it would be an artifact of reading the outcome as the input.

   The genuine pre-close price must be **reconstructed from trade history**: `/v0/bets?contractId=…`, reading `probAfter` with timestamps, and taking the last value strictly before the chosen cutoff. Requirements:
   - Cutoff defined **once, here, before the run: 24 hours before market close.** Markets with no trade before that cutoff are excluded and the exclusion count is reported.
   - One or more API calls per market across 3,000+ markets → **cache raw responses to disk** so a failed run does not re-pull.
   - The existing `get_bets` is user-scoped; this needs a contract-scoped sibling.
   - The current fetcher caps at 10 pages. Reaching 300 per bucket across ten buckets needs far more — pagination must be fixed, not worked around.

   Realistic effort for Phase 0 done properly: **6–8 hours**, of which roughly five are price reconstruction and pagination.

3. **Junk-market handling — decided now, not after seeing the shape.** Manifold's sub-10% band is thick with joke markets, `dailycoinflip` spam, and unresolvable whimsy ("will my brain be uploaded to ASI"), all priced low and all resolving NO. Measuring unfiltered finds a "bias" that is really market-quality artifact with zero tradeable capacity. Filtering reintroduces the selection problem this document exists to avoid. There is no clean answer, so:

   **Report both tables as co-primary results** — unfiltered, and filtered using the existing domain-neutral `is_low_signal` regex. The filter definition is frozen as of today and must not be tuned after seeing results. Disagreement between the two tables is itself a finding and must be reported, not resolved by picking the nicer one.

4. **Bucket** by the reconstructed price: 0–5%, 5–10%, 10–20%, 20–35%, 35–50%, 50–65%, 65–80%, 80–90%, 90–95%, 95–100%.

5. **Measure** per bucket: N, mean price, realized YES frequency, difference (realized − price), and a binomial confidence interval on the difference.

6. **Read the shape.** A favorite-longshot bias appears as realized frequency *below* price in low buckets and *at or above* price in high buckets.

### Phase 1 — Would a strategy have made money? (only if Phase 0 shows a real gap)

Backtest via the existing harness: bet against the low band (sell/NO the sub-10% contracts) sized with fractional Kelly on the *measured* gap, not the theoretical one. Account for Manifold's fee structure and for the fact that thin markets move when you trade them. Report Brier and realized P&L against a do-nothing baseline.

### Phase 2 — Paper-trade forward (only if Phase 1 is positive)

Run live, no money, until a pre-declared number of *new* resolutions accumulates. Out-of-sample or it does not count.

### Phase 3 — Anything involving real money

Out of scope for this document. See `/Users/laura/vsc/shmoney/leads/prediction-market-bot-research.md` for the German regulatory position: Polymarket is classified by the GGL as illegal gambling under GlüStV 2021 and geoblocks Germany; Kalshi's status for German residents is unresolved. Nothing here justifies touching that until Phases 0–2 have produced a real, out-of-sample result.

## Pre-registration rules (the whole point)

The category-edge claims (competitive-gaming +0.26 etc.) died because they rested on N=4–9 **and** were circular: those categories were whitelisted *because* they looked good in the same data. This experiment does not get to make that mistake.

1. **Buckets, price-snapshot definition, and minimum N are fixed above, before any data is pulled.** Changing them after seeing results invalidates the run.
2. **Minimum sample: 300 resolved markets per bucket** for any bucket that gets reported as evidence. Buckets below that are reported as "underpowered", never as findings.
3. **No sub-slicing to rescue a null.** If H1 fails overall, it fails. Going hunting for the category or time window where it worked is exactly the earlier error.
4. **The primary result is the whole-sample bucket table.** Everything else is exploratory and must be labelled as such.
5. **Publish the null if it is a null.** That is the honest-measurement stance the project already stands for, and it is the more interesting blog post.

## Limitations (state these in any write-up)

- **Play money ≠ real money.** Manifold runs on mana. Berg & Rietz found the non-profit IEM lacked the bias entirely, which raises a real possibility that the bias is a feature of commercial venues with real stakes and fee asymmetries. A null on Manifold therefore does **not** disprove the Kalshi finding, and a positive result does **not** guarantee it transfers.
- **The edge is reportedly eroding** as it becomes better known. A result on 2021–2024 data may not describe 2026.
- **Capacity.** Even a confirmed bias is bounded by how much can be traded into thin books without moving the price. Measure this before drawing any income conclusions.

## Outcomes and what each is worth

| Result | Meaning | Next |
|---|---|---|
| Clear bias, well-powered | Selection edge exists on Manifold | Phase 1 backtest |
| Null, well-powered | Manifold is calibrated in the bands tested | Write it up — a clean null against a published bias is a genuinely good post |
| Underpowered | Not enough resolved markets in the low bands | Report honestly, do not extrapolate |

Either of the first two is publishable and feeds the blog series. Neither requires spending money, and neither depends on the bot getting smarter.
