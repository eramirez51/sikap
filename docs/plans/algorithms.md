# Algorithms — learnings from `algorithms.pdf`

Source: collection of Reddit posts from a senior dev who quit corporate to trade futures full-time. All posts describe the same system in different framings.

**This is order flow / auction-market theory. Not ICT, not SMC.**

---

## The thesis

Stop predicting where price *should* go (visual patterns, fibs, trendlines). Read what participants are *actually doing* (aggressive vs passive volume, where institutional liquidity rests).

The premise: **chart patterns are designed to trap retail.** Static support/resistance lines fail because everyone draws the same lines and algos hunt the resulting stop clusters. The edge lives one layer below — in the order flow that decides whether a level holds or breaks.

## Strategy: mean-reversion at exhausted liquidity zones

A trade only triggers when **all** of these align:

| Condition | What it measures | Concrete threshold |
|---|---|---|
| **Liquidity zone** | Price sitting in a high-volume node (HVN) from a multi-bar volume profile | 60-bar / 50-bin profile, current bin ≥ 1.6× average |
| **VWAP extreme** | Statistical stretch from session mean | Price beyond 1.75–2.0 SD of session VWAP |
| **CVD divergence** | Aggressive flow contradicts price | Price makes new low, cumulative volume delta makes higher low (30–50 bar lookback) |
| **Volumetric order block** | Reversal off a filtered, *non-freshly-formed* order block | — |
| **Regime filter** | Avoid strong trends | ADX < 25; IV filter; Z-score filter |
| **Hurst exponent** | Mean-reverting regime confirmation | added later, "bumped R:R while maintaining PF" |
| **Trigger** | Reversal candle close | 5m candle closes back inside the liquidity zone |

**Exits:**
- **Stop:** hard stop a few ticks behind the absorption wick. If wick breaks, the absorption was fake — cut instantly.
- **Target:** session VWAP first, then opposite 1st SD band. Or fixed RR (≥ 1:2).
- Hard mechanical stop tied to **invalidation of the liquidity zone**, never a dollar amount.

**Risk sizing:** 1–2% account risk per setup. The author claims this is the only knob that matters for scaling — same script runs from a $2k personal account up to a $150k prop-funded account.

**Candidate refinements (from a reproduction attempt, not confirmed by OP):**
- **Momentum confirmation** as final trigger — smoothed rate-of-change oscillator crossing above its signal line, in addition to the 5m close-back-inside-zone.
- **15-minute cooldown** between trades to prevent re-entering a still-failing zone.
- **ATR-based stop fallback** — 1.5× ATR below entry — when no clean absorption wick exists to anchor the stop.

Treat these as systematic alternatives where OP's discretionary heuristics ("a few ticks behind the wick") need a mechanical equivalent.

## Runtime dashboard (the 4 primitives the system actually watches)

The author's gap-day post (NQ weekend gap) lists the dashboard's tracked signals — useful as a concrete feature list for a detector layer:

1. **Sell/buy aggression vs displacement** — aggressive market orders without range expansion ⇒ absorption tag
2. **VWAP reclaim efficiency** — how cleanly price re-enters VWAP after an extension
3. **Liquidity stacking** — passive liquidity heatmap above vs below current price
4. **Speed of tape + order-size divergence** — rate of incoming orders combined with order size; divergence between the two flags institutional vs retail flow

These are not separate strategies — they are the live diagnostic surface that confirms or rejects the entry conditions in the table above.

## The core mechanic: absorption

Auction theory: price moves to advertise for business. Up = looking for sellers, down = looking for buyers.

**Absorption** = aggressive market orders hit a wall of passive limit orders without the price moving. Sellers dump everything they have, but the price won't go lower because passive buyers are eating it all. Once the aggressive side runs out of ammo, the auction has to rotate the other way.

The *signal* of absorption is **delta divergence**:
- Price makes a new low → looks bearish to retail
- But CVD (aggressive buys − aggressive sells) makes a *higher* low → aggressive sellers are exhausted
- ⇒ passive buyers are absorbing → reversal probable

The system also tags absorption when it sees aggressive market sells **without range expansion** — sells are heavy but price is stalling.

## Vocabulary (use this, not ICT terms)

- **CVD** — cumulative volume delta = sum of (aggressive buy volume − aggressive sell volume) over time
- **HVN / LVN** — high / low volume node from a volume profile. HVNs are magnets; LVNs are gaps price travels through quickly
- **POC** — point of control, the price with most volume in a profile
- **VWAP SD bands** — anchored session VWAP ± k·σ. 1.75–2.0 SD = "outlier territory"
- **Volumetric order block** — a filtered order block sized by volume, not just price structure. Must not be freshly formed
- **Absorption** — aggressive flow hitting passive liquidity without price displacement
- **Displacement** — price actually moving when aggressive flow hits (the *opposite* of absorption)
- **Speed of tape** — rate of incoming orders; combined with order size for divergence detection
- **Liquidity stacking** — passive liquidity heatmap above vs below current price

Note the "order block" in this framework is **volumetric** (sized by transacted volume in a zone), not the ICT structural concept. Don't conflate.

## Reported performance

Numbers vary by post (different time windows):

| Metric | Range |
|---|---|
| Win rate | 40–55% (edge is in R:R, not win rate) |
| Avg R:R | 1:2.5 to 1:3.9 |
| Profit factor | 2.1–2.53 |
| Max drawdown | ~5.8–6.8% live; 20–25% in 5y backtest |
| Frequency | ~0.8 trades/day; can sit flat for a week |
| Reported 2025 | gross $162.3k, net ~$127k from $2k start |

**Reproduction benchmark (from a commenter, MNQ, 1y backtest):**

| Metric | Value |
|---|---|
| Trades | 117 (~1 every 2 days) |
| Win rate | 39% |
| Profit factor | 1.25 |
| Net | +$1,872 on 1 MNQ contract |
| Max drawdown | $1,870 |

The commenter implemented ADX<25 + 60-bar/50-bin HVN + 1.75 SD VWAP + CVD divergence + ROC momentum confirm — i.e. most of the table above, **minus** the volumetric order block and Hurst exponent. PF dropped from 2.1+ to 1.25. Useful as a negative control: those two filters appear to carry a meaningful share of the edge.

## Implementation stack (per the author)

- **Execution:** TradingView Pinescript `strategy()` script. Alerts route via JSON to a custom Node listener → IBKR REST API.
- **Data:** TradingView's $8/month CME real-time feed. **Tick data not required** for mean reversion — second-based is sufficient. Level 2 / DOM not needed (only HFT needs it).
- **Backtesting:** Pandas + NumPy first to find optimal SD settings, then SierraChart for tick-based backtests, then Pinescript for live.
- **Took ~8 months of iteration** to filter "fake" signals during chop.
- **Tax:** futures (ES/NQ) over SPY options. Section 1256 → 60% long-term cap gains regardless of holding period. No wash-sale rule. State tax in VA: 5.75%.

## What to ignore (per the author)

- Visual chart patterns (H&S, wedges, flags, triangles) — pure noise
- Static horizontal S/R lines without volumetric basis
- "Trend confirmation" entries — confirmation = late = bad RR
- Wide stops on mean reversion — defeats the asymmetry
- 80% win-rate aspirations — the real edge is asymmetric R:R at 40–50%
- Fully discretionary "feel" trading — if you can't code it, you don't have a strategy

## Relevance to this project

The detectable primitives map cleanly onto the `detectors` crate:

1. **CVD divergence detector** — needs aggressor-side data (bid-hit vs ask-lift). **Current parquet schema does not carry this.** Either ingest tick data with aggressor side, or estimate from second-bar up/down volume.
2. **VWAP SD band detector** — pure derivation from OHLCV, no schema change needed.
3. **Volume profile HVN/LVN detector** — pure derivation from OHLCV.
4. **ADX / Hurst regime filter** — pure derivation from OHLCV.
5. **Absorption detector** — needs aggressor-side flow + price-displacement comparison.
6. **Volumetric order block** — needs volume profile + price structure + "freshness" tracking.

**Schema gap to flag:** the current `core::storage` parquet schema (`ts`, `timeframe`, OHLCV) is missing the aggressor-side delta needed for CVD. To implement the full system, the `magsi ingest` pipeline needs to either:
- ingest tick data with bid/ask classification, or
- ingest sub-minute up-volume / down-volume columns (TradingView-style estimate), or
- approximate CVD from intrabar tick direction at ingest time.

The author explicitly says **second-based data is enough** for this strategy — no need to commit to a tick-data pipeline.

## Anti-pattern: don't share the alpha

The author refuses to publish source. Stated reason: *"If 50,000 people front-run the exact same tick divergence signal, the alpha disappears. Same thing happened with ICT and SMC — initial iterations worked, but flatlined after tens of thousands started entering at the same level."*

Worth noting as a project consideration: any strategy that becomes detectable in retail tooling decays.
