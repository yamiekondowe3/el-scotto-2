# The locked config on 2025-07 → 2026-09

The configuration was frozen using 2018-2025 (selection) and 2011-2018 (validation). Everything
from 2025-08 onward postdates that freeze, so this is a genuine out-of-sample read — the closest
thing to a forward test available without waiting.

**It is the strongest result this project has produced. It does not overturn the verdict, and
the reasons why are specific.**

## Results

| | |
|---|---|
| Trades | **49** (42/yr — unchanged frequency) |
| Win rate | **51.0%** |
| Profit factor | **4.477** |
| Expectancy | **+1.3180R**  — 95% CI **[+0.60, +2.03]** |
| t vs zero | t = +3.61, **p = 0.001** |
| Total | +64.6R |
| Max drawdown | **−3.4R** |
| Sharpe | +3.33 |

At 0.5% risk: **+32.3% return, −1.7% drawdown.** At 0.25%: +16.2% / −0.85%.

**It beat the random-entry placebo: z = +4.55** (strategy +1.3180 vs random +0.1715 ± 0.2521,
200 runs, same trade count, direction mix, session and exits). On the 2011-2018 window this same
test gave z = +0.37. That is a real difference, not a restatement.

## Four reasons not to get carried away

**1. Buy-and-hold still won.** Gold rose **+33.7%** over the window (3315 → 4430) against the
strategy's +32.3% at 0.5% risk. The strategy ran 69% long into a 34% bull market. It did so with
a −1.7% drawdown against gold's own path, which is the real argument in its favour — but on
return alone, doing nothing beat it.

**2. This is a different distribution from everything before it.**

| Window | n | E[R] |
|---|---|---|
| 2011-2018 (unseen) | 282 | +0.0873 |
| 2018-2025 (selection) | 321 | +0.1833 |
| **2025-07 → 2026-09** | **49** | **+1.3180** |

Welch t = +3.18, **p = 0.0025** against the prior 601 trades. A 7-15× jump in expectancy is not
the same process performing well; it is either a regime change or an outlier window. Fourteen
months cannot distinguish those, and "the regime has changed" is the single most common way a
strategy gets deployed just before it stops working.

**3. The distribution is extremely skewed.** Median trade **+0.019R** against a mean of +1.318R.
The top 5 trades are 50% of the total, the top 3 are 35%. That is the expected signature of a
trailing exit with no target, but it means the result rests on a short right tail — remove five
trades from a sample of 49 and half the profit goes.

**4. n = 49.** The 95% CI is [+0.60, +2.03] — the estimate spans a factor of three.

## The one thing that argues the other way

Yearly breakdown against gold's own move:

| Year | n | Total R | E[R] | Gold |
|---|---|---|---|---|
| 2023 | 38 | +13.4R | +0.353 | +12.7% |
| 2024 | 48 | +15.5R | +0.322 | +27.2% |
| 2025 | 45 | +33.7R | +0.749 | **+64.6%** |
| **2026** | 23 | **+37.0R** | **+1.607** | **+1.9%** |

**2026 is the strongest year in the sample and gold barely moved.** That cuts against the
"it's just riding gold" explanation, which is the criticism that has killed most of this
project's positive results. Correlation between yearly strategy R and yearly gold return is
**+0.54** — real, but far from the ~1.0 that pure drift capture would imply. And because results
are in R-multiples, they are already normalised for volatility, so a high-ATR regime does not
mechanically inflate them.

Against that: 2026 is n = 23. In 2017 the strategy lost 0.199R/trade while gold rose 13.2%; in
2013 it made +0.068R while gold fell 27.9%. The relationship is noisy in both directions.

## Verdict

Unchanged: **no demonstrated edge, and this window does not establish one.** But it is the first
genuinely encouraging out-of-sample result here, it clears the placebo test that everything else
failed, and the 2026 sub-window is not explained by gold's drift.

The correct response is not to redeploy conviction — it is to keep collecting. This is precisely
what forward-testing on demo is for, and the pre-registered review points in `monitor.py` are
already set: 3 months (E[R] within ±1 SE of the modelled +0.087R) and 12 months (cumulative R vs
the modelled distribution). Note this window came in **15× the modelled +0.087R**, which by the
pre-registered standard is *also* a flag — a result that far outside the expected band means the
model of the strategy is wrong, in either direction.

Reproduce: `python el_scotto_recent.py`
