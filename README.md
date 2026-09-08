# el-scotto-2

A corrected, independently re-validated version of the **XAUUSD_LOWFREQ v2** gold strategy from
[Ngaakudzwe2/el-scotto](https://github.com/Ngaakudzwe2/el-scotto), plus the MT5 Expert Advisor
that runs it and the full test programme that assessed it.

## Read this before using anything here

**This strategy has no demonstrated edge, and this repository does not claim one.** It is
published because the *negative* results are reproducible and specific, and because the
corrections and the trade-management findings are useful on their own.

The changes made here improved expectancy substantially — from **−0.0055R to +0.1433R** — but
controls show the improvement comes from generic trade management, not from the entry signal.
Against random entries with the same exits, session and costs, the strategy scores **z = +0.37**
on data it has never seen. Expectancy on that unseen window is **+0.0394R with p = 0.607**.

The EA is **demo-only by default** and refuses to initialise on a live account.

## What was wrong with the original

All three of these flatter the reported results, and all three are fixed here.

| Defect | Location | Effect |
|---|---|---|
| **Costs under-charged ~50%** | `entries_v2.py:225` / `:187` | `spread/2 + slippage` charged on entry, only `slippage` on exit. Round trip is $0.30, not the $0.40 the comment claims. Stop fills assumed exact |
| **Fixed-notional sizing** | `entries_v2.py:228` | `qty = notional / fill` makes per-trade risk proportional to ATR, then each `pnl/notional` is treated as an iid return |
| **Pullback has no lower bound** | `entries_v2.py:218` | A bar that never approached the EMA still qualifies as a "pullback". The documented intent is not what the code does |
| **No exit management** | `entries_v2.py:231` | `sl`/`tp` written once, never updated. No trail, breakeven, partial or time stop |
| **Grid search absent from the repo** | `lowfreq_v2_eval.py:148` | `PARAM_GRID` is referenced once, inside a `--benchmark` branch, to estimate runtime. The published numbers are not reproducible from what ships |

**Correcting the cost accounting alone flips the original's training window from profitable to
unprofitable** (PF 1.032 → 0.990).

## What changed

**Subtractions:** the profit target, all hours outside 12:00–16:00 UTC, fixed-notional sizing.
**Additions:** a 3×ATR chandelier trail, the pullback lower bound, risk-based sizing.
**Unchanged:** their daily-SMA trend regime and their ATR-expansion gate.

| Change | Effect on E[R] |
|---|---|
| Remove the profit target (2×ATR stop, no target, 3×ATR trail) | −0.0055 → **+0.0605** |
| Trade only the NY session, 12:00–16:00 UTC | +0.0605 → **+0.1209** |
| Implement the pullback as documented (low must dip *through* the EMA) | PF 1.236 → **1.394** |
| Drop the ATR gate | **+0.0307 — worse.** Their gate helps; it stays |

Removing the target drops win rate from 50% to 35% while raising profit factor — the
trend-following signature. It addresses a diagnosable failure: the original captured **11% of a
151% gold move**.

## What the validation showed

| Gate | Result | |
|---|---|---|
| Backward OOS 2011–2018 (7 years never seen) | PF 1.073, E[R] +0.0394 | **pass** — the original *lost* money here |
| Random-entry placebo, unseen window | **z = +0.37** | **fail** |
| Cross-section, 72 markets, per-instrument costs | 25/72 positive (was 14/72); gold at 97th percentile | **fail** |
| Trend overlay vs vol-matched gold, bootstrap CI on ΔSharpe | **[−0.32, +0.81]** | **fail** — straddles zero |

Four replacement entry signals were also pre-registered and tested (Donchian breakout, corrected
pullback, momentum persistence, and an always-in control). **None beat random entry timing on
unseen data**, and the control that used *no entry timing at all* scored the highest z of the
five.

Full write-ups: [reports/EL_SCOTTO_IMPROVED.md](reports/EL_SCOTTO_IMPROVED.md) and
[reports/EL_SCOTTO_ASSESSMENT.md](reports/EL_SCOTTO_ASSESSMENT.md).

## The locked configuration

Parameters are **locked and must not be optimised.** Every optimisation pass in this project
produced a result that vanished out of sample. Re-tuning a signal already shown to be
indistinguishable from noise fits the noise harder.

```
entry    A2_true_pullback  low dips through EMA21, close recovers above it
regime   H1 close vs daily SMA50
gate     ATR(14) > SMA50(ATR14), 3 consecutive bars
session  12:00-16:00 UTC  (ny_open)
exit     2xATR stop, NO target, 3xATR chandelier trail
sizing   risk-based, 0.5% equity per trade
```

## Layout

```
mql5_ea/ElScotto_Trend_EA.mq5   the EA; demo-gated, mirrors the Python exactly
el_scotto_improved.py           faithfulness check, corrected baseline, the four changes
el_scotto_tradeable.py          four pre-registered replacement entry signals
el_scotto_validate.py           V1-V4 validation gates
el_scotto_overlay.py            trend overlay vs volatility-matched gold
common/                         shared engine: exits, filters, costs, placebo, portfolio
ops_rehearsal.py                pre-deployment checks; places NO orders
tests_exits.py                  exit-policy tests, incl. trail monotonicity
```

`el_scotto_harness.py` and `reproduce_el_scotto.py` import the upstream strategy directly. To run
them, clone the original alongside as `el-scotto-review/`:

```
git clone https://github.com/Ngaakudzwe2/el-scotto.git el-scotto-review
```

## Running it

```bash
pip install -r requirements.txt
python el_scotto_improved.py     # baseline + the four changes
python el_scotto_validate.py     # validation gates
pytest tests_exits.py
```

The EA needs `data_cache/XAUUSD/H1/` populated via `common/data_fetch.py` against a running MT5
terminal. Run `python ops_rehearsal.py` before enabling it — it validates the order path with
`order_check()` without placing anything, and has already caught two deployment-killing bugs
(a hardcoded FOK/IOC filling mode that would have had every order rejected, and an ATR-collapse
sizing defect worth −18R to −21R per trade).

## Credit

The original strategy, data pipeline and evaluation are by
[Ngaakudzwe2](https://github.com/Ngaakudzwe2). Their code reproduces its published results
exactly — the holdout matched to every decimal — and their README leads with the
training/holdout contradiction rather than burying it, which is more discipline than most retail
strategy work. Their own stated conclusion, *"promising and worth continued forward-testing, not
a proven edge ready for capital,"* is correct; this repository supports the cautious half of it.

The failure here is not sloppiness. A real edge is genuinely hard to find.

## Licence

MIT for the code in this repository. The upstream strategy is under its own licence; this
repository contains no upstream source.
