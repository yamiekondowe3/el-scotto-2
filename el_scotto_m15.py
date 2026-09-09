"""Can the frequency come from timeframe rather than from more instruments?

The multi-instrument route is closed. A RETURNS-BLIND portfolio of the 12
cheapest deep-history markets -- ranked on spread/(2xATR) in the NY session,
never on performance -- gives 586 trades/year, 13.6x gold, at median E[R]
-0.0258 with 6/12 positive and win rates of 28-36%, worse than gold's 40%. It
buys the frequency and loses money doing it. The earlier "25/72 positive"
cross-section was selection, for the eighth time in this project.

So this tests the other route: the same instrument, sampled finer.

  timeframe   bars 2011-2026   spread/(2xATR), NY session
  H1                  91,883                      0.87%
  M15                365,562                      2.02%
  M5               1,094,386                      4.12%

M15 costs ~+0.023R per trade more than H1, affordable against a measured
+0.183R, and it carries the FULL 15.7 years -- so the 2011-2018 window the
design has never seen is still available to validate on. That is the whole
reason M15 and not M5.

TWO VARIANTS, FIXED IN ADVANCE, because "ATR(14)" means something different at
15 minutes and there are exactly two defensible readings:

  M15-A "same bars"    ATR 14, EMA 21, ATR-SMA 50   -- the literal config at a
                       finer resolution; a genuinely faster strategy
  M15-B "same horizon" ATR 56, EMA 84, ATR-SMA 200  -- periods x4 so each
                       indicator spans the same wall-clock window as on H1;
                       the same strategy, sampled finer

Everything else is UNCHANGED AND LOCKED: A2_true_pullback entry, daily SMA50
regime, ATR gate with 3-bar confirm, 2xATR stop, NO target, 3xATR trail,
12:00-16:00 UTC, risk-based sizing. No search. Two variants -> Bonferroni
z >= 2.24.

Costs are held at el-scotto's own $0.40 spread / $0.05 slippage for BOTH
timeframes deliberately: the dollar spread does not change with timeframe, so
holding it fixed lets M15's friction disadvantage emerge on its own through
the smaller ATR, rather than being assumed into the model.
"""
import sys
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT / "el-scotto-review"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

from src.indicators import atr as their_atr, ema as their_ema
from src.backtest_structures import sma as their_sma
from src.medfreq_strategy import align_htf_to_m5
from src.regime_filter import atr_expansion_gate

from common.data_fetch import load_parquet
from el_scotto_improved import run, stats, years_of, to_candles, to_daily
from el_scotto_validate import POLICY, SESSION

CONFIRM_BARS = 3
TREND_SMA = 50          # DAILY bars -- unchanged at every timeframe

VARIANTS = {
    "H1_baseline":      dict(tf="H1",  atr=14, ema=21, atr_sma=50),
    "M15-A_same_bars":  dict(tf="M15", atr=14, ema=21, atr_sma=50),
    "M15-B_same_horiz": dict(tf="M15", atr=56, ema=84, atr_sma=200),
}


def signals(df, atr_period, ema_period, atr_sma_period, trend_sma=TREND_SMA):
    """A2_true_pullback with the indicator periods exposed.

    Deliberately NOT a change to el_scotto_tradeable.py::entry_signals, whose
    behaviour is pinned by the passing test suite. The logic mirrors it exactly;
    only the periods become arguments. atr_expansion_gate already accepts them.
    """
    bars, daily = to_candles(df), to_daily(df)
    daily_sma = their_sma([c.close for c in daily], trend_sma)
    trend = align_htf_to_m5(bars, daily, daily_sma, 1440)
    ema = their_ema([c.close for c in bars], ema_period)
    atr_vals = their_atr(bars, atr_period)

    gate = atr_expansion_gate(bars, atr_period, atr_sma_period)
    streak, s = [], 0
    for g in gate:
        s = s + 1 if g is True else 0
        streak.append(s)

    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    side = np.zeros(len(df), dtype=int)
    atr_out = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        t, e, a = trend[i], ema[i], atr_vals[i]
        if t is None or e is None or a is None or a <= 0:
            continue
        atr_out[i] = a
        if streak[i] < CONFIRM_BARS:
            continue
        # A2: the low must dip THROUGH the EMA and the close recover above it
        if c[i] > t and l[i] < e and c[i] > e:
            side[i] = 1
        elif c[i] < t and h[i] > e and c[i] < e:
            side[i] = -1
    return pd.DataFrame({"side": side, "atr": atr_out}, index=df.index)


def evaluate(spec, a, b, cost_mult=1.0):
    df = load_parquet("XAUUSD", spec["tf"], root=ROOT / "data_cache")
    warm_start = str(pd.Timestamp(a) - pd.Timedelta(days=400))[:10]
    sub = df.loc[warm_start:b]
    if len(sub) < 5000:
        return None
    sig = signals(sub, spec["atr"], spec["ema"], spec["atr_sma"])
    tr = run(sub, sig, POLICY, session=SESSION,
             half_spread=0.20 * cost_mult, slip=0.05 * cost_mult)
    tr = tr[pd.to_datetime(tr["entry_ts"]) >= a]
    if len(tr) < 20:
        return None
    return stats(tr, years_of(df.loc[a:b]))


def line(name, s):
    if s is None:
        return f"  {name:20s} (too few trades)"
    return (f"  {name:20s} n={s['n']:5d} {s['tpy']:5.0f}/yr WR={s['wr']:5.1f}% "
            f"PF={s['pf']:6.3f} E[R]={s['er']:+.4f} Sh={s['sharpe']:+6.2f} "
            f"maxDD={s['maxdd_r']:+7.1f}R")


def main():
    print("=" * 104)
    print("PHASE 1 - SELECTION WINDOW 2018-2025 ONLY (2011-2018 held back)")
    print("=" * 104)
    sel = {}
    for name, spec in VARIANTS.items():
        sel[name] = evaluate(spec, "2018-01-01", "2025-07-31")
        print(line(name, sel[name]), flush=True)

    base = sel.get("H1_baseline")
    if base:
        for k, v in sel.items():
            if k.startswith("M15") and v:
                print(f"\n  {k}: {v['tpy']/base['tpy']:.1f}x H1 frequency, "
                      f"E[R] {v['er']:+.4f} ({v['er']-base['er']:+.4f} vs H1)")

    print("\n" + "=" * 104)
    print("PHASE 2 - BACKWARD OUT-OF-SAMPLE 2011-2018 (played no part in any choice)")
    print("=" * 104)
    unseen = {}
    for name, spec in VARIANTS.items():
        unseen[name] = evaluate(spec, "2011-01-01", "2017-12-31")
        print(line(name, unseen[name]), flush=True)

    print("\n" + "=" * 104)
    print("COST SENSITIVITY on the unseen window (friction is the risk of dropping TF)")
    print("=" * 104)
    for name, spec in VARIANTS.items():
        row = []
        for mult in (1.0, 2.0, 3.0):
            s = evaluate(spec, "2011-01-01", "2017-12-31", cost_mult=mult)
            row.append(f"{mult:.0f}x {s['er']:+.4f}" if s else f"{mult:.0f}x n/a")
        print(f"  {name:20s} " + "   ".join(row), flush=True)

    json.dump({"selection": sel, "unseen": unseen},
              open(ROOT / "el_scotto_m15_results.json", "w"), indent=2, default=str)
    print("\nSaved el_scotto_m15_results.json")
    print("\nMore trades is nearly certain -- M15 has 4x the bars, and that is not a finding.")
    print("The question is whether expectancy SURVIVES 2011-2018 and beats random timing there.")


if __name__ == "__main__":
    main()
