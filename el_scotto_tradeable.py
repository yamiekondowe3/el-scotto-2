"""Replace the entry signal, keeping the exit/session layer that works.

ESTABLISHED SO FAR: E1_trail (2xATR stop, no target, 3xATR trail) + NY session
+ risk-based sizing + their ATR gate is a genuine improvement, but their
pullback entry adds nothing over random timing on unseen data (z = +0.37).
So the exit layer is kept fixed and the ENTRY is replaced.

PRE-REGISTERED CANDIDATES, fixed before running. Four, so Bonferroni
z >= 2.24. No grid search over parameters -- each candidate uses the
canonical parameter from its own literature, not a tuned one.

  A0 pullback (theirs)  - the baseline being replaced.
  A1 donchian_20        - the canonical trend entry. A priori: trailing exits
                          and breakout entries are the classic Turtle pairing;
                          a trail with no target is designed for the fat right
                          tail a breakout produces.
  A2 true_pullback      - their documented intent, actually implemented. Their
                          code has NO lower bound on the pullback band
                          (entries_v2.py:218), so a bar that never approached
                          the EMA from below still qualifies. This requires the
                          low to dip BELOW the EMA and the close to recover
                          above it -- a real bounce.
  A3 momentum_persist   - close on the trend side of the EMA for 3 consecutive
                          bars, enter on the third. A priori: continuation
                          rather than mean reversion, matching the exit layer.
  A4 always_in          - no entry timing at all; enter whenever flat, in the
                          daily trend direction. This is the CONTROL that says
                          whether entry timing contributes anything at all. If
                          A4 matches the best candidate, entry selection is
                          noise and the honest product is a trend-following
                          overlay, not a signal.

DECISIVE GATE: the random-entry placebo on 2011-2018, data that plays no part
in selection. Full-history z is not accepted -- it was +2.18 for the original
while the unseen window gave +0.37, and the unseen number was right.
"""
import sys
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parent
REPO = ROOT / "el-scotto-review"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

from src.indicators import atr as their_atr, ema as their_ema
from src.backtest_structures import sma as their_sma
from src.medfreq_strategy import align_htf_to_m5
from src.regime_filter import atr_expansion_gate
from src.entries_v2 import ATR_PERIOD

from common.exits import POLICIES
from common.data_fetch import load_parquet
from el_scotto_harness import to_candles, to_daily, CFG
from el_scotto_improved import run, stats, fmt, years_of, RISK_DOLLARS
from el_scotto_validate import placebo, POLICY, SESSION, SEED

DONCHIAN = 20
PERSIST = 3
CANDIDATES = ["A0_pullback", "A1_donchian_20", "A2_true_pullback",
              "A3_momentum_persist", "A4_always_in"]
# 4 real candidates (A4 is a control, not a competitor) -> Bonferroni
Z_THRESHOLD = 2.24


def entry_signals(df, mode, cfg=CFG):
    """All candidates share the SAME regime context: daily-SMA trend direction
    plus their ATR-expansion gate with 3-bar persistence (P4 showed the gate
    helps). Only the trigger differs, so the comparison is clean.

    No lookahead anywhere: bar i uses indicators through bar i and the last
    CLOSED daily bar, exactly as their align_htf_to_m5 provides.
    """
    h1, daily = to_candles(df), to_daily(df)
    daily_sma = their_sma([c.close for c in daily], cfg.trend_sma_period)
    trend = align_htf_to_m5(h1, daily, daily_sma, 1440)
    h1_ema = their_ema([c.close for c in h1], cfg.pullback_ema_period)
    atr_vals = their_atr(h1, ATR_PERIOD)
    tol = cfg.pullback_tolerance_pct / 100.0

    gate = atr_expansion_gate(h1)
    streak, s = [], 0
    for g in gate:
        s = s + 1 if g is True else 0
        streak.append(s)

    c_arr = df["close"].values
    h_arr = df["high"].values
    l_arr = df["low"].values
    n = len(df)

    # Donchian channel of the PREVIOUS DONCHIAN bars, excluding the current one
    prior_high = pd.Series(h_arr).rolling(DONCHIAN).max().shift(1).values
    prior_low = pd.Series(l_arr).rolling(DONCHIAN).min().shift(1).values

    side = np.zeros(n, dtype=int)
    atr_out = np.full(n, np.nan)
    up_run = dn_run = 0
    for i in range(1, n):
        t, e, a = trend[i], h1_ema[i], atr_vals[i]
        if t is None or e is None or a is None or a <= 0:
            up_run = dn_run = 0
            continue
        atr_out[i] = a
        price = c_arr[i]
        # persistence counters must advance regardless of the gate
        up_run = up_run + 1 if price > e else 0
        dn_run = dn_run + 1 if price < e else 0
        if streak[i] < cfg.regime_confirm_bars:
            continue
        long_regime, short_regime = price > t, price < t
        if not (long_regime or short_regime):
            continue

        if mode == "A0_pullback":
            lo = long_regime and l_arr[i] <= e * (1 + tol) and price > e
            sh = short_regime and h_arr[i] >= e * (1 - tol) and price < e
        elif mode == "A1_donchian_20":
            ph, pl = prior_high[i], prior_low[i]
            if not (np.isfinite(ph) and np.isfinite(pl)):
                continue
            lo = long_regime and price > ph
            sh = short_regime and price < pl
        elif mode == "A2_true_pullback":
            # the documented intent: price must actually dip THROUGH the EMA
            # and close back on the trend side
            lo = long_regime and l_arr[i] < e and price > e
            sh = short_regime and h_arr[i] > e and price < e
        elif mode == "A3_momentum_persist":
            lo = long_regime and up_run == PERSIST
            sh = short_regime and dn_run == PERSIST
        elif mode == "A4_always_in":
            lo, sh = long_regime, short_regime
        else:
            raise ValueError(mode)

        if lo:
            side[i] = 1
        elif sh:
            side[i] = -1
    return pd.DataFrame({"side": side, "atr": atr_out}, index=df.index)


def main():
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    train = gold.loc["2018-01-01":"2025-07-31"]
    unseen = gold.loc["2011-01-01":"2017-12-31"]
    full = gold.loc["2011-01-01":]

    print("Exit layer FIXED: E1_trail + NY session + risk-based sizing + their ATR gate")
    print(f"Bonferroni threshold for {len(CANDIDATES)-1} candidates: z >= {Z_THRESHOLD}\n")

    print("=" * 104)
    print("STEP 1 — SELECTION WINDOW 2018-2025 ONLY (unseen data plays no part here)")
    print("=" * 104)
    sel = {}
    for m in CANDIDATES:
        s = stats(run(train, entry_signals(train, m), POLICY, session=SESSION),
                  years_of(train))
        sel[m] = s
        print(fmt(m, s))

    real = [m for m in CANDIDATES if m != "A4_always_in" and sel.get(m)]
    best = max(real, key=lambda m: sel[m]["er"])
    a4 = sel.get("A4_always_in")
    print(f"\n  best candidate: {best}  (E[R] {sel[best]['er']:+.4f})")
    if a4:
        print(f"  control A4_always_in: E[R] {a4['er']:+.4f}  -> entry timing worth "
              f"{sel[best]['er'] - a4['er']:+.4f}R over no timing at all")

    print("\n" + "=" * 104)
    print("STEP 2 — THE DECISIVE GATE: placebo on 2011-2018, data never used to choose anything")
    print("=" * 104)
    print(f"  {'candidate':22s} {'n':>5s} {'E[R]':>9s} {'placebo':>10s} {'+/-':>8s} {'z':>7s}   verdict")
    print("  " + "-" * 84)
    rows = []
    for m in CANDIDATES:
        sg = entry_signals(unseen, m)
        tr = run(unseen, sg, POLICY, session=SESSION)
        st = stats(tr, years_of(unseen))
        if not st:
            print(f"  {m:22s} too few trades"); continue
        lr = float((tr["side"] > 0).mean())
        pl = placebo(unseen, sg, st["n"], lr)
        if not pl or pl["std"] <= 0:
            print(f"  {m:22s} placebo unavailable"); continue
        z = (st["er"] - pl["mean"]) / pl["std"]
        t, p = sps.ttest_1samp(tr["r"].values, 0)
        ok = z >= Z_THRESHOLD
        rows.append({"candidate": m, "n": st["n"], "er": st["er"], "z": float(z),
                     "t": float(t), "p": float(p), "pass": bool(ok)})
        print(f"  {m:22s} {st['n']:5d} {st['er']:+9.4f} {pl['mean']:+10.4f} "
              f"{pl['std']:8.4f} {z:+7.2f}   {'PASS' if ok else 'fail'}  (p={p:.3f})")

    winners = [r for r in rows if r["pass"]]
    print("\n" + "=" * 104)
    if not winners:
        bestrow = max(rows, key=lambda r: r["z"]) if rows else None
        print("RESULT: no candidate beats random entry timing on unseen data.")
        if bestrow:
            print(f"        closest was {bestrow['candidate']} at z={bestrow['z']:+.2f} "
                  f"(needed {Z_THRESHOLD}).")
        print("        The exit/session layer is the product; the entry signal is not.")
    else:
        print("RESULT: candidate(s) passed the unseen-window placebo — proceeding to "
              "cross-section:")
        for r in winners:
            print(f"        {r['candidate']}  z={r['z']:+.2f}  p={r['p']:.3f}")
    print("=" * 104)

    json.dump({"selection": sel, "unseen": rows, "best": best,
               "threshold": Z_THRESHOLD},
              open(ROOT / "el_scotto_tradeable_results.json", "w"), indent=2, default=str)
    print("Saved el_scotto_tradeable_results.json")
    return winners


if __name__ == "__main__":
    main()
