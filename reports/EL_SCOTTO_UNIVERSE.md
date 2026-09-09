# The locked config on seven named instruments

Run before starting the demo monitor, on instruments **you specified** — so this is not a
selection exercise. Costs are per instrument (each market's own median recorded spread).

## Answer first

**Only gold beats random entry. One of eight.** The recent exceptional result does not
generalise, and the FX pairs went *negative* over the very window gold went +1.32R.

---

## Random-entry placebo, recent window (2025-07 → 2026-09)

Same trade count, direction mix, session, exits and per-instrument costs; entry timing randomised.

| Symbol | Strategy E[R] | Random E[R] | z | |
|---|---|---|---|---|
| **XAUUSD** | **+1.3247** | +0.2233 ± 0.2547 | **+4.32** | **BEATS** |
| USDJPY | +0.1924 | −0.0026 ± 0.2012 | +0.97 | — |
| US100 | +0.1301 | −0.0106 ± 0.1940 | +0.73 | — |
| BTCUSD | +0.1268 | +0.0425 ± 0.1921 | +0.44 | — |
| US500 | −0.0933 | −0.0089 ± 0.1972 | −0.43 | — |
| GBPUSD | −0.2032 | −0.0452 ± 0.1832 | −0.86 | — |
| GBPJPY | −0.3082 | −0.0892 ± 0.1674 | −1.31 | — |
| EURUSD | −0.3512 | −0.0132 ± 0.1874 | −1.80 | — |

*(A first pass produced z-scores of +27 and +35 on the FX pairs. That was the per-instrument
cost bug: `placebo()` charged gold's $0.20 half-spread on EURUSD, giving random entries an E[R]
of −114.99R and making anything look significant by comparison. The control has to be costed
like the thing it controls for. Fixed, and the corrected numbers are above.)*

## Full results by window

| Symbol | Window | n | /yr | WR | PF | E[R] | B&H |
|---|---|---|---|---|---|---|---|
| **XAUUSD** | 2011-2018 unseen | 282 | 40 | 38.3% | 1.229 | **+0.1142** | −8.1% |
| | 2018-2025 select | 320 | 42 | 41.2% | 1.506 | **+0.2381** | +151.2% |
| | 2025-07+ recent | 49 | 42 | 51.0% | 4.520 | **+1.3247** | +33.7% |
| BTCUSD | 2018-2025 select | 288 | 50 | 31.9% | 1.049 | +0.0270 | +1169.1% |
| | 2025-07+ recent | 66 | 56 | 37.9% | 1.279 | +0.1268 | −25.5% |
| EURUSD | 2011-2018 unseen | 23 | 24 | 56.5% | 1.859 | +0.3246 | +14.1% |
| | 2018-2025 select | 394 | 52 | 32.2% | 1.056 | +0.0306 | −4.9% |
| | 2025-07+ recent | 62 | 52 | 24.2% | 0.423 | **−0.3512** | −1.6% |
| GBPUSD | 2011-2018 unseen | 36 | 37 | 27.8% | 0.545 | −0.2449 | +11.4% |
| | 2018-2025 select | 428 | 56 | 36.7% | 1.171 | +0.0845 | −2.3% |
| | 2025-07+ recent | 56 | 47 | 30.4% | 0.663 | **−0.2032** | −1.7% |
| GBPJPY | 2011-2018 unseen | 34 | 35 | 35.3% | 0.736 | −0.1494 | +7.8% |
| | 2018-2025 select | 383 | 51 | 36.8% | 1.391 | +0.1853 | +30.9% |
| | 2025-07+ recent | 61 | 51 | 29.5% | 0.427 | **−0.3082** | +6.8% |
| USDJPY | 2011-2018 unseen | 34 | 35 | 29.4% | 1.055 | +0.0290 | −3.3% |
| | 2018-2025 select | 322 | 42 | 32.9% | 1.198 | +0.1044 | +34.0% |
| | 2025-07+ recent | 52 | 44 | 38.5% | 1.461 | +0.1924 | +8.7% |
| US100 | 2018-2025 select | 47 | 31 | 25.5% | 0.835 | −0.0941 | +33.1% |
| | 2025-07+ recent | 50 | 42 | 40.0% | 1.264 | +0.1301 | +29.6% |
| US500 | 2018-2025 select | 40 | 26 | 25.0% | 0.956 | −0.0238 | +30.5% |
| | 2025-07+ recent | 51 | 43 | 37.3% | 0.806 | −0.0933 | +23.2% |

**Gold is the best instrument in all three windows.** By itself that is expected — the strategy
was developed on gold. The question is whether that is skill or selection, and the placebo
answers it: 1 of 8.

| Window | Positive | Median E[R] | Gold |
|---|---|---|---|
| 2011-2018 unseen | 3/5 | +0.0290 | **+0.1142** |
| 2018-2025 select | 6/8 | +0.0575 | **+0.2381** |
| 2025-07+ recent | 4/8 | +0.0168 | **+1.3247** |

## What this does to the recent result

The +1.32R window looked like it might be a regime shift in trend-following. **It is not a
regime shift — it is gold.** Over the identical window:

- EURUSD **−0.3512**, GBPJPY **−0.3082**, GBPUSD **−0.2032**
- Median across all eight instruments: **+0.0168**, versus gold's +1.3247

A genuine change in how trends behave would show up broadly. This shows up in one instrument,
which is the signature of an outlier window rather than a new regime.

Two honest counterpoints, recorded rather than buried:

- **USDJPY is positive in every window** (+0.029 / +0.104 / +0.192) and rising. z = +0.97 is not
  significant, but it is the only other instrument that never loses.
- **BTCUSD made +0.1268R while bitcoin fell 25.5%.** Positive in a falling market is not drift
  capture. Also not significant (z = +0.44).

## Caveats on the indices

US100 and US500 have only **2.6 years** of H1 (2024-01 onward), so their "2018-2025" rows cover
just 2024-2025 and their recent rows overlap the same regime window entirely. Neither can be an
independent check on it.

## Verdict

Unchanged, and now better supported: **no generalisable edge.** The strategy is gold-specific,
and gold is where it was built. The recent result remains the most encouraging thing here and
still fails the test that matters most — it does not replicate anywhere else.

Reproduce: `python el_scotto_universe.py`
