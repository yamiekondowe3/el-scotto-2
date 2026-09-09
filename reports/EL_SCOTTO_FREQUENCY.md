# More frequency, better win rate, lower drawdown — what is actually available

Three requests. One is arithmetic, one is already answered by the design, and one was a real
research question that has now been tested and **failed**.

## Answer first

| Request | Verdict |
|---|---|
| **Lower drawdown** | **Yes — a dial, not research.** Drawdown scales linearly with `Risk_Pct` |
| **Better win rate** | **No — 40% is the optimum.** Raising it undoes the change that made the strategy work |
| **More frequency** | **No route survives.** Both tested paths fail, and both make win rate *worse* |

---

## 1. Frequency — both routes tested, both closed

### Route A: more instruments — fails

A **returns-blind** portfolio of the 12 cheapest deep-history markets, ranked on
spread/(2×ATR) during the NY session and **never on performance**:

| | |
|---|---|
| Trades | **586/year — 13.6× gold** |
| Median E[R] | **−0.0258** |
| Mean E[R] | −0.027 |
| Positive | **6 / 12** |
| Win rates | **28–36%** (gold: 40%) |

It buys the frequency and loses money doing it. The earlier "25/72 positive" cross-section was
selection — the eighth time in this project a promising result evaporated once the choosing was
done without looking at returns.

### Route B: lower timeframe — fails

M15 was the promising candidate: 4× the bars, friction only +0.023R/trade, and — decisively —
the **full 15.7 years**, so the 2011–2018 unseen window is still available. Two variants, fixed
in advance, because "ATR(14)" means something different at 15 minutes:

**Selection window 2018–2025:**

| Variant | n | /yr | WR | PF | E[R] | maxDD |
|---|---|---|---|---|---|---|
| **H1 baseline** | 321 | 42 | **40.2%** | **1.376** | **+0.1833** | **−14.4R** |
| M15-A same bars (ATR14/EMA21/SMA50) | 2070 | **273** | 31.6% | 0.902 | **−0.0576** | −169.7R |
| M15-B same horizon (ATR56/EMA84/SMA200) | 737 | 97 | 30.3% | 0.781 | **−0.1301** | −106.0R |

**Unseen window 2011–2018:**

| Variant | n | /yr | WR | PF | E[R] |
|---|---|---|---|---|---|
| **H1 baseline** | 282 | 40 | **37.6%** | **1.170** | **+0.0873** |
| M15-A | 1766 | 253 | 33.1% | 0.924 | **−0.0435** |
| M15-B | 593 | 85 | 28.8% | 0.836 | **−0.1026** |

**Both variants lose money on both windows.** This is not a selection artifact — they fail
in-sample too. M15-A delivers the 6.4× frequency and turns +0.18R into −0.06R; M15-B is worse.

Worth noting they fail on **all three** of the stated goals at once: lower win rate (31% vs
40%), negative expectancy, and 8–12× the drawdown in R terms.

The pre-registered kill rule applies: M15 is abandoned. Not "try M5" — M5 doubles friction again
(4.12% of the stop vs H1's 0.87%), and the failure is not marginal.

### Cost sensitivity (unseen window)

| Variant | 1× | 2× | 3× |
|---|---|---|---|
| **H1** | **+0.0873** | +0.0083 | −0.0660 |
| M15-A | −0.0435 | −0.1968 | −0.3557 |
| M15-B | −0.1026 | −0.2582 | −0.4334 |

H1 survives a doubling of costs and goes negative at 3×. That is a genuine fragility worth
knowing, and it is the reason the M15 friction penalty is fatal rather than merely unhelpful.

---

## 2. Win rate — 40% is the answer, not a problem

Win rate here is an artifact of the exit profile, not a quality measure. Removing the profit
target is what made the strategy work, and it **lowered** win rate:

| Exit | WR | PF | E[R] |
|---|---|---|---|
| E0_fixed (target restored) | 50.1% | 0.941 | −0.0304 |
| **E1_trail (locked config)** | **35.3%** | **1.114** | **+0.0605** |
| E2_scale_trail | 49.6% | 0.992 | −0.0044 |

A 40% win rate at PF 1.38 beats a 60% win rate at PF 1.0. Every route to a higher win rate
measured here costs more expectancy than it buys.

---

## 3. Drawdown — the one thing you can simply have

Full history 2011–2026, H1 locked config: **650 trades, 41/yr, WR 39.8%, PF 1.459,
E[R] +0.2259, total +146.8R, max drawdown −20.0R.**

Drawdown scales linearly with risk. Pick a row:

| `Risk_Pct` | Max drawdown | Return/yr |
|---|---|---|
| 0.50% (current) | 9.98% | 4.69% |
| 0.35% | 6.99% | 3.28% |
| **0.25%** | **4.99%** | **2.34%** |
| 0.20% | 3.99% | 1.87% |
| 0.15% | 3.00% | 1.41% |

No test required — this is division. Note the Strategy Tester's *equity* drawdown ran ~1.7× the
balance figure (9.76% vs 5.74%), because the trail gives back open profit by design; budget
against the equity column.

---

## What this leaves

The locked H1 configuration, at whatever risk level you choose. It is verified end to end — the
EA reconciles with the model at 100.00% per-bar agreement and 320/320 matched trades — and it
still has **no demonstrated edge**: out of sample the entry is indistinguishable from random
timing (z = +0.37, p = 0.607). See `EL_SCOTTO_IMPROVED.md`.

Reproduce: `python el_scotto_m15.py`
