# Assessment: el-scotto (XAUUSD_LOWFREQ v2)

Independent evaluation of `github.com/Ngaakudzwe2/el-scotto` using this project's control
stack. Their `simulate()` and `compute_metrics()` are imported and used **unmodified**, so what
is assessed is their actual strategy, not a reimplementation.

## Verdict: not tradeable

The strategy is not profitable on data it has not seen, does not beat a random-entry control,
and does not beat simply holding gold in its own best year.

**The author's own conclusion — "promising and worth continued forward-testing, not a proven
edge ready for capital" — is correct, and this testing supports the cautious half of it more
than the promising half.**

## Reproduction: exact

Before critiquing, the published table was reproduced from their own code and bundled
Dukascopy cache:

| Window | Metric | Reproduced | Reported |
|---|---|---|---|
| Training | trades / WR / PF / Sharpe | 1,072 / 45.1% / 1.020 / −0.510 | 1,073 / 45.1% / 1.018 / −0.519 |
| Holdout | trades / WR / PF / Sharpe | 132 / 56.8% / 1.544 / **1.843** | 132 / 56.8% / 1.544 / **1.843** |

The holdout matches to every decimal. Their numbers are real and their code does what it says.

**Documentation issue worth fixing:** the README directs readers to `src/entries_v2.py` for
the parameters, but the `LowfreqV2Config` defaults (`sma100`, `tol0.15`, `sl1.5`) are **not**
the evaluated configuration. The published results come from `sma50 / ema21 / tol0.20 /
sl2.0 / tp2.5 / confirm3`. Running the repo as-is reproduces a different and materially worse
strategy — holdout Sharpe −0.14 rather than +1.843. Note `tol=0.15` is not even in the search
grid (`[0.10, 0.20]`), which is the giveaway.

**Data handling is sound.** Re-running their strategy on our independent Deriv feed over their
training window gives n=1,065 / WR 45.6% / PF 1.033 / Sharpe −0.44, against their Dukascopy
n=1,072 / 45.1% / 1.020 / −0.51. Vendor-robust, which is not a given and reflects well on the
data pipeline.

## Test 1 — Backward out-of-sample: the decisive new evidence

Their data begins in 2018. Our Deriv history begins 2011, giving **seven years the design has
never seen.** Same rules, same config, unchanged:

| Window | n | WR | PF | Sharpe | Strategy | Gold itself |
|---|---|---|---|---|---|---|
| **2011–2018 (never seen)** | 978 | 43.9% | **0.963** | **−0.80** | **−12.3%** | −8.1% |
| 2018–2025 (their training) | 1,065 | 45.6% | 1.033 | −0.44 | +10.8% | **+151.2%** |
| 2025-08+ (their holdout) | 100 | 55.0% | 1.447 | +1.44 | +22.2% | **+23.1%** |

Three things follow:

1. **On the seven unseen years it loses money** — PF 0.963, Sharpe −0.80 — and loses more than
   gold did over the same period.
2. **Across their 7.5-year training window, gold rose 151% while the strategy made 11%.** A
   long-biased system that captures 11% of a 151% move is not extracting an edge from the
   trend; it is being ground down by costs and stop-outs while the asset runs away from it.
3. **In its best year, it roughly matches buy-and-hold.** On our data +22.2% against gold's
   +23.1% — slightly behind. On their Dukascopy data it was +31.2% against +23.0%, so ahead by
   ~8 points. The comparison is vendor-sensitive and not robust either way, but "the strategy's
   standout year" and "gold's standout year" are the same year.

## Test 2 — Random-entry placebo

Same trade count, direction mix, ATR exits, gate, costs and sizing; entry timing randomised.
This isolates whether the pullback trigger adds anything over the trend regime plus the exits.

| | E[R] |
|---|---|
| Strategy (full history, n=2,202) | **+0.0085** |
| Random entry (100 runs) | −0.0283 ± 0.0277 |
| **z** | **+1.33** — below the 1.645 threshold |

Directionally favourable but not significant. The pullback trigger is not demonstrably better
than entering at random within the same regime.

## Test 3 — Cross-sectional replication

Identical rules on 72 other markets (FX, crypto, energy), with the cost model scaled per
instrument. *(Their fixed $0.40 spread is right for gold but nonsensical elsewhere — on EURUSD
at $10,000 notional it is roughly 250R per trade. Applying it unscaled produced a 3% pass rate
that was an artifact of my setup, not their strategy, so it was corrected.)*

| | |
|---|---|
| Positive E[R] | **14/72 (19%)** |
| Median E[R] | **−0.048** |
| XAUUSD | +0.014 → **85th percentile** |

A genuine trend-pullback edge should appear broadly. This one is concentrated in the single
instrument it was developed on.

## What the holdout's significance test does and does not establish

Their t=2.16, p=0.033, the outlier-removal check and the 5,000-path Monte Carlo are all
legitimate and correctly executed. They establish that **those 132 trades were unlikely to
arise from a zero-mean process.**

They do not address the two questions that decide tradeability:
- **Selection.** The configuration was chosen by grid search over 48 combinations on the
  training data. The holdout tests the winner of that search, not the search itself.
- **Drift.** A long-biased system in a year gold rose 23% will show a positive result whether
  or not it has skill. The placebo and buy-and-hold comparisons are what separate those, and
  it does not clear either.

An annualised Sharpe measured over a single year carries a standard error of roughly ±1.6, so
+1.843 and the training estimate of −0.519 are not statistically separable observations.

## What would change the verdict

- Positive expectancy on the 2011–2018 window without re-tuning to it.
- Beating the random-entry placebo at z > 1.645 on the full history.
- Replicating on instruments it was not developed on.

Forward-testing more of 2026 will not settle it: at ~130 trades per year, distinguishing the
holdout's apparent edge from the training estimate needs several more years.

## Credit where due

This is more disciplined than most retail strategy work: a deliberately small 48-combination
grid with stated anti-overfitting reasoning, median-of-fold rather than mean-of-fold scoring,
parameter sensitivity and regime breakdowns, a genuinely locked one-shot holdout, no synthetic
data, and a README that leads with the training/holdout contradiction instead of burying it.
The failure here is not sloppiness — it is that a real edge is genuinely hard to find, which
this project's own thirteen rounds of negative results independently confirm.

Raw data: `el_scotto_harness_results.json`, `el_scotto_crosssection_fair.json`,
`el_scotto_harness.py`, `reproduce_el_scotto.py`.
