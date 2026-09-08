# Improving el-scotto: what worked, what didn't, and the verdict

Four changes were fixed in advance, each with an a priori reason, and tested only on
el-scotto's own 2018–2025 training window. The 2011–2018 window — seven years their design has
never seen — was held back for validation and played no part in any choice.

**The changes produced the largest improvement measured anywhere in this project: expectancy
went from −0.0055R to +0.1433R.** It is still not tradeable, and the reason is precise and worth
understanding.

---

## Answer first

| | |
|---|---|
| Did the improvements work? | **Yes, substantially.** −0.0055R → +0.1433R over the full history |
| Is their entry signal doing the work? | **No.** Against random entries with the same exits and session, z = **+0.37** on unseen data |
| Is it tradeable? | **No** |
| What is actually worth keeping? | The **trailing exit** and the **NY session filter** — both generic, neither dependent on their signal |

---

## Faithfulness check

Their `simulate()` is monolithic, so swapping the exit layer required recomputing their entry
trigger from **their own indicator functions**. That extraction was verified before anything
else: on 2018–2025 it produced **1,065 trades against their 1,065** — exact. Every number below
is about their signal, not a reimplementation of it.

---

## Phase 1 — Fixing the yardstick

Three defects in the original all flatter it. Correcting them changes the baseline before any
improvement is attempted:

1. **Costs under-charged by ~50%.** `entries_v2.py:225` charges `spread/2 + slippage` on entry;
   line 187 charges only `slippage` on exit. Round trip is $0.30, not the $0.40 the field
   comment calls round-trip. Stop fills are also assumed exact at the level.
2. **Fixed-notional sizing.** `qty = notional / fill` makes per-trade risk proportional to ATR,
   and `compute_metrics` then treats each `pnl/notional` as an iid return.
3. **Mis-annualised Sharpe.** Per-trade population stdev scaled by `√(trades/yr)`.

| Window | As they measure it | Corrected |
|---|---|---|
| 2011–2018 (unseen) | PF 0.958, E[R] −0.0240 | PF **0.911**, E[R] **−0.0520** |
| 2018–2025 (their training) | PF 1.032, E[R] +0.0178 | PF **0.990**, E[R] **−0.0055** |
| 2025-08+ (their holdout) | PF 1.523, E[R] +0.2358 | PF 1.509, E[R] +0.2309 |

**Correcting the cost accounting alone flips their training window from marginally profitable to
marginally unprofitable.** Their reported +6.00% training P&L does not survive charging the
spread on both sides.

---

## Phase 2 — The four pre-registered changes

Tested on 2018–2025 only. Results in R-multiples, constant $100 risk per trade.

### P1 — Exits (the one that mattered)

| Exit | n | WR | PF | E[R] |
|---|---|---|---|---|
| E0_fixed (control, ≈theirs) | 1154 | 50.1% | 0.941 | −0.0304 |
| **E1_trail** — 2×ATR stop, **no target**, 3×ATR trail | 882 | 35.3% | **1.114** | **+0.0605** |
| E2_scale_trail | 1033 | 49.6% | 0.992 | −0.0044 |
| E3_be_target | 970 | 27.0% | 1.020 | +0.0102 |
| E4_time | 1183 | 50.0% | 0.919 | −0.0390 |

Removing the profit target entirely is worth **+0.066R**. Win rate *falls* from 50% to 35% while
profit factor *rises* — the classic trend-following signature, and exactly what the diagnosis
predicted: a system that captured 11% of a 151% gold move was being capped by its 2.5×ATR target.

### P3 — Session filter

| Session | n | PF | E[R] |
|---|---|---|---|
| All hours | 882 | 1.114 | +0.0605 |
| **NY open 12:00–16:00 UTC** | 460 | **1.236** | **+0.1209** |
| London open 07:00–11:00 UTC | 240 | 1.247 | +0.1286 |

Trading only the NY window **doubles expectancy** while halving trade count.

### P4 — Drop the ATR-expansion gate — **failed**

| | n | PF | E[R] |
|---|---|---|---|
| Gate ON (theirs) | 882 | 1.114 | **+0.0605** |
| Gate OFF | 1524 | 1.057 | +0.0307 |

Their gate **helps**, contradicting our prior from the volatility-regime work, where such filters
were harmful. Reported as a negative result rather than quietly dropped. Combining "gate off"
with the other two changes was also worse (+0.0263), so the gate stays.

**Locked configuration:** their entry (gate ON) + E1_trail + NY session + risk-based sizing.
Selection-window result: **n=460, PF 1.236, E[R] +0.1209, Sharpe +0.56.**

---

## Phase 3 — Validation

### V1 — Backward out-of-sample: **PASS**

| Window | n | WR | PF | E[R] | Sharpe |
|---|---|---|---|---|---|
| **2011–2018 (never seen)** | 397 | 35.0% | **1.073** | **+0.0394** | +0.19 |
| 2018–2025 (selection) | 460 | 36.5% | 1.236 | +0.1209 | +0.56 |
| 2025-08+ (holdout) | 40 | 47.5% | 4.132 | +1.1990 | +3.05 |

The original **lost money** on 2011–2018 (PF 0.911). The improved version makes money there
without re-tuning. That is a genuine result and the strongest single piece of evidence in favour.

### V2 — Random-entry placebo: **FAIL**

Same trade count, direction mix, exits, session, costs and sizing; entry *timing* randomised.

| Window | Strategy E[R] | Random entry E[R] | z |
|---|---|---|---|
| Full history 2011+ | +0.1433 | +0.0198 ± 0.0566 | **+2.18** |
| **2011–2018 (unseen)** | +0.0394 | +0.0082 ± 0.0837 | **+0.37** |
| 2018–2025 (selection) | +0.1209 | +0.0029 ± 0.0717 | +1.65 |

This is the decisive result. On the full history z = +2.18, which clears an uncorrected 1.645 but
misses the Bonferroni threshold of 2.24 (four change families) — a hair's breadth, and I would
not want to rest a decision on which side of it landed.

**The unseen window removes that ambiguity: z = +0.37.** On data that played no part in
selection, their pullback trigger is indistinguishable from entering at random within the same
regime, at the same times, with the same exits.

The improvement is real. It just isn't theirs. **The trailing exit and the session filter are
doing all of the work**, and they do it equally well for random entries.

Consistent with this, expectancy on the unseen window is not significantly different from zero:
**t = +0.51, p = 0.607** (n=397). The full-history t = +2.56 (p = 0.011) is inflated by the
selection window.

### V3 — Cross-sectional replication: **FAIL**

72 other markets, each charged **its own measured median spread** (applying gold's $0.40 to
EURUSD is hundreds of R per trade — an error made once in this project and corrected).

| | Original | Improved |
|---|---|---|
| Positive E[R] | 14/72 (19%) | **25/72 (35%)** |
| Median E[R] | −0.048 | −0.074 |
| XAUUSD percentile | 85th | **97th** |

The pass rate nearly doubles — consistent with the exits being a genuine general improvement —
but the median remains negative and gold sits at the 97th percentile of 72 markets. Being the
best of 72 is what selection looks like.

### V4 — Versus holding gold

| Window | Strategy | Gold B&H | |
|---|---|---|---|
| 2011–2018 (gold falling) | **+15.6%** | −8.1% | **ahead** |
| 2018–2025 (gold rising) | +55.6% | +151.2% | behind |
| Full 2011+ | +131.7% | +212.7% | behind |

Genuinely interesting: it made money over seven years in which gold *fell*. But over the full
history it captures well under half of what simply holding gold returned.

---

## Verdict

**Not tradeable — but for a different and more interesting reason than before.**

The original failed because it lost money. This version doesn't: it is positive on data it has
never seen, it survived a cost correction that flipped the original negative, and it replicates
on nearly twice as many markets. Those are real gains.

It fails because **the entry signal contributes nothing measurable.** Against random entries
with identical exits, session and costs, it scores z = +0.37 on unseen data. Everything gained
came from two generic trade-management changes — remove the profit target, trade only the NY
session — that would improve almost any long-biased gold system, and that work just as well
without their pullback logic.

Trading it would mean paying for a signal that does no work, on an instrument selected as the
best of 72, at an expectancy statistically indistinguishable from zero out of sample.

## What is genuinely worth keeping

1. **Remove the profit target.** Worth +0.066R here, and it addresses a diagnosable failure —
   capping winners in a trending instrument.
2. **Trade the NY window only.** Doubles expectancy, halves trade count. Market structure, not
   edge, which is why it transfers.
3. **Keep their ATR gate.** It helps. Our prior against volatility-regime filters was wrong here.
4. **Fix the cost accounting and use risk-based sizing** before evaluating anything further —
   otherwise every subsequent number is measured against a broken yardstick.

---

# Round 2 — replacing the entry signal

Given the exit/session layer works and their entry does not, the obvious move is to keep the
former and replace the latter. Four candidates, pre-registered before running, each using the
canonical parameter from its own literature rather than a tuned one. Bonferroni z ≥ 2.24.

## Selection window (2018–2025)

| Candidate | n | WR | PF | E[R] |
|---|---|---|---|---|
| A0 pullback (theirs) | 460 | 36.5% | 1.236 | +0.1209 |
| A1 Donchian-20 breakout | 230 | 30.0% | 1.021 | +0.0111 |
| **A2 true pullback** (their documented intent, actually implemented) | 315 | 40.0% | **1.394** | **+0.1920** |
| A3 momentum persistence | 133 | 29.3% | 1.033 | +0.0177 |
| A4 **always-in** (control — no entry timing at all) | 955 | 34.1% | 1.135 | +0.0709 |

A2 is a genuine bug fix: their code has **no lower bound** on the pullback band
(`entries_v2.py:218`), so a bar that never approached the EMA from below still qualifies.
Requiring the low to dip *through* the EMA and the close to recover above it — their stated
intent — lifts PF from 1.236 to 1.394 on the selection window.

## The unseen window (2011–2018) — every candidate fails

| Candidate | n | E[R] | Random entry | z | p |
|---|---|---|---|---|---|
| A0 pullback | 397 | +0.0394 | +0.0082 ± 0.0837 | +0.37 | 0.607 |
| A1 Donchian-20 | 178 | −0.0381 | +0.0213 ± 0.1296 | −0.46 | 0.714 |
| A2 true pullback | 282 | +0.0873 | +0.0186 ± 0.0912 | +0.75 | 0.334 |
| A3 momentum | 105 | +0.0461 | +0.0036 ± 0.1569 | +0.27 | 0.745 |
| **A4 always-in (control)** | 780 | +0.0564 | +0.0115 ± 0.0577 | **+0.78** | 0.328 |

A2's PF 1.394 does not survive: z = +0.75, p = 0.334 out of sample. And the decisive
observation is the control — **A4, which uses no entry timing whatsoever, scores the highest z
of all five.** Entry selection is noise.

---

# Round 3 — the trend overlay, and the final answer

If no entry signal works, the only remaining candidate is the regime + risk layer itself: long
above the daily SMA50, short below, ATR gate on, NY session only, trailing exit, constant risk.
For an always-in system the placebo is meaningless, so the benchmark is **gold held at the same
realised volatility**.

| Window | | CAGR | Sharpe | maxDD |
|---|---|---|---|---|
| **2011–2018 (unseen)** | overlay | +5.01% | **+0.34** | **−26.8%** |
| | vol-matched gold | — | +0.01 | −46.1% |
| 2018–2025 (selection) | overlay | +7.62% | +0.45 | −26.9% |
| | vol-matched gold | — | **+0.80** | −25.9% |
| Full 2011+ | overlay | +10.46% | +0.58 | −37.7% |
| | vol-matched gold | — | +0.47 | −46.9% |

This was the most promising thing in the project: it **beats vol-matched gold on the unseen
window** by +0.33 Sharpe and 19.3 points of drawdown, and it does *better* out of sample than in
the selection window — the opposite of the overfitting signature.

**The block bootstrap ends it.** Confidence intervals on the Sharpe difference versus
vol-matched gold, resampling quarterly blocks to respect volatility clustering:

| Window | 95% CI on ΔSharpe | |
|---|---|---|
| 2011–2018 (unseen) | **[−0.32, +0.81]** | not significant |
| 2018–2025 | [−0.72, +0.26] | not significant |
| Full 2011+ | [−0.30, +0.46] | not significant |

All three straddle zero. The overlay's apparent advantage is within what seven years of gold
data can produce by chance. It also *lost* to vol-matched gold across 2018–2025, so the profile
is regime-dependent — it helps when gold chops or falls and hurts when gold trends hard.

## Final verdict: I could not make it tradeable

Everything was tried that the evidence pointed at: the cost and sizing defects were fixed, five
exit mechanisms tested, session filters applied, their regime gate validated (it helps), four
replacement entries pre-registered, and the whole thing reframed as a volatility-matched overlay.
The improvements are real and large — expectancy went from −0.0055R to +0.1433R — but they are
generic trade management, they work equally well with random entries, and the one construction
that beat its benchmark out of sample could not clear a confidence interval.

**The honest description of what exists:** a defensive gold trend overlay that cut maximum
drawdown from 46% to 27% on unseen data. The drawdown reduction is large and mechanically
explicable (it is flat much of the time). The *return* advantage is not demonstrable.

## What would change the verdict

- Beating the random-entry placebo at z ≥ 2.24 **on the 2011–2018 window**, not the full history.
- A cross-sectional median above zero, rather than gold at the 97th percentile.
- A bootstrap CI on ΔSharpe versus vol-matched gold that excludes zero — which, at gold's
  volatility, needs materially more than 15 years of data, or the same overlay applied across
  many uncorrelated instruments so the effective sample size rises.

## Reproduce

```
python el_scotto_improved.py    # faithfulness check, Phase 1 baseline, Phase 2 changes
python el_scotto_validate.py    # V1-V4 validation gates
```

Config locked before validation ran. All RNGs seeded (`SEED = 20260908`, 200 placebo runs).
Their `simulate()` is imported unmodified for the faithfulness check; the extracted signal
reproduces it exactly (1,065 vs 1,065 trades).
