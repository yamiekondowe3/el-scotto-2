"""el-scotto, with the measurement fixed and four pre-registered changes tested.

PHASE 1 fixes the yardstick. Three defects in the original make its reported
numbers unusable as a baseline, and all three flatter it:

  1. COSTS ARE UNDER-CHARGED BY ~HALF. entries_v2.py charges
     `spread/2 + slippage` on entry (line 225) but only `slippage` on exit
     (187). Round trip is $0.30, not the $0.40 the field comment calls
     round-trip. Stop fills are also assumed exact at the level.
  2. SIZING IS FIXED-NOTIONAL. `qty = notional / fill` (228) makes per-trade
     risk proportional to ATR, then compute_metrics treats each pnl/notional
     as an iid return. The same defect moved a median from -19.55R to -1.001R
     in our own engine once it was fixed.
  3. SHARPE IS MIS-ANNUALISED. Per-trade population stdev scaled by
     sqrt(trades/yr), assuming iid evenly-spaced trades.

PHASE 2 tests four changes, FIXED IN ADVANCE, each with an a priori reason.
No grid search: el-scotto already searched 48 combinations, and every variant
we add deepens the same multiple-comparisons problem that produced seven false
positives in this project. Four families -> Bonferroni z >= 2.24.

  P1 exits        - captured 11% of a 151% gold move; the 2.5xATR target is
                    the mechanism capping winners. Highest expected payoff.
  P2 risk sizing  - carried from Phase 1.
  P3 NY session   - better in 5/5 configurations in our own testing.
  P4 drop ATR gate- our volatility-regime testing found such filters harmful,
                    and this one is undirectional.

FAITHFULNESS: their entry trigger is recomputed here from THEIR OWN indicator
functions (their ema/sma/atr/align_htf_to_m5/atr_expansion_gate), because
swapping the exit layer requires intercepting entries, which their monolithic
simulate() does not expose. Test A below proves the extraction is correct by
reproducing their own simulate() trade-for-trade under their own rules. If
that check fails, nothing downstream means anything.
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

from src.entries_v2 import (LowfreqV2Config, simulate, DEFAULT_COSTS, Direction,
                            ATR_PERIOD)
from src.indicators import atr as their_atr, ema as their_ema
from src.backtest_structures import sma as their_sma
from src.medfreq_strategy import align_htf_to_m5
from src.regime_filter import atr_expansion_gate

from common.exits import ExitPolicy, POLICIES, open_position, process_bar
from common.filters import named_session_mask
from common.data_fetch import load_parquet
from el_scotto_harness import to_candles, to_daily, CFG

RISK_DOLLARS = 100.0        # constant risk per trade -> results are R-multiples
EQUITY = 10_000.0

# Their spread, charged correctly: half on EACH side, plus slippage per side.
# Their own value is kept so the comparison isolates the accounting error
# rather than confounding it with a different cost assumption.
HALF_SPREAD = DEFAULT_COSTS.spread / 2
SLIP = DEFAULT_COSTS.slippage_per_side
STOP_SLIP_EXTRA = 0.05      # stops fill through the level, not exactly at it


def their_signals(df, cfg=CFG, use_gate=None):
    """Recompute their entry trigger using THEIR indicator code.

    Returns a DataFrame indexed like df with columns: side (+1/-1/0), atr.
    Mirrors entries_v2.simulate() lines 165-219 exactly, including the
    `close vs daily SMA` comparison and the absence of a lower bound on the
    pullback band.
    """
    h1, daily = to_candles(df), to_daily(df)
    daily_sma = their_sma([c.close for c in daily], cfg.trend_sma_period)
    trend = align_htf_to_m5(h1, daily, daily_sma, 1440)
    h1_ema = their_ema([c.close for c in h1], cfg.pullback_ema_period)
    atr_vals = their_atr(h1, ATR_PERIOD)
    tol = cfg.pullback_tolerance_pct / 100.0

    gate_on = cfg.use_regime_filter if use_gate is None else use_gate
    gate = atr_expansion_gate(h1)
    streak, s = [], 0
    for g in gate:
        s = s + 1 if g is True else 0
        streak.append(s)

    side = np.zeros(len(h1), dtype=int)
    atr_out = np.full(len(h1), np.nan)
    for i in range(1, len(h1)):
        t, e, a = trend[i], h1_ema[i], atr_vals[i]
        if t is None or e is None or a is None or a <= 0:
            continue
        atr_out[i] = a
        if gate_on and streak[i] < cfg.regime_confirm_bars:
            continue
        c = h1[i]
        price = c.close
        if price > t and c.low <= e * (1 + tol) and price > e:
            side[i] = 1
        elif price < t and c.high >= e * (1 - tol) and price < e:
            side[i] = -1
    return pd.DataFrame({"side": side, "atr": atr_out}, index=df.index)


def run(df, sig, policy: ExitPolicy, session=None, risk_based=True,
        fixed_notional=EQUITY, correct_costs=True, half_spread=None, slip=None):
    """Trade loop with OUR exit/sizing layer over THEIR signal.

    Mirrors their sequencing exactly: exits are checked before entries, and an
    entry may open on the same bar a position closed (entries_v2.py:198-200).

    `half_spread`/`slip` override the gold-calibrated defaults. They MUST be
    set per instrument for any cross-sectional test: their fixed $0.40 spread
    is right for gold and nonsensical on EURUSD, where it is hundreds of R per
    trade. Getting this wrong once already produced a fake 3% pass rate.
    """
    global HALF_SPREAD, SLIP
    hs = HALF_SPREAD if half_spread is None else half_spread
    sl_ = SLIP if slip is None else slip
    o = df["open"].values; h = df["high"].values
    l = df["low"].values;  c = df["close"].values
    side_a, atr_a = sig["side"].values, sig["atr"].values
    idx = df.index
    ok_session = (named_session_mask(idx, session).values if session
                  else np.ones(len(df), dtype=bool))

    exit_slip = sl_ + (hs if correct_costs else 0.0)
    pos, trades = None, []
    for i in range(1, len(df)):
        bar = {"high": h[i], "low": l[i], "close": c[i]}
        if pos is not None:
            for ex in process_bar(pos, bar, idx[i], policy):
                lvl = ex["level"]
                extra = (STOP_SLIP_EXTRA * (sl_ / SLIP)) if ex["reason"] == "stop" else 0.0
                fill = lvl - pos.side * (exit_slip + extra)
                pnl = (fill - pos.entry_price) * pos.side * pos.size * ex["fraction"]
                pos.realized_pnl += pnl
            if pos.remaining <= 1e-9:
                trades.append({"r": pos.realized_pnl / pos.risk_amount,
                               "pnl": pos.realized_pnl, "side": pos.side,
                               "ts": idx[i], "bars": pos.bars_held})
                pos = None
        if pos is not None or side_a[i] == 0 or not ok_session[i]:
            continue
        a = atr_a[i]
        if not np.isfinite(a) or a <= 0:
            continue
        s = int(side_a[i])
        fill = c[i] + s * (hs + sl_)
        risk_unit = policy.stop_atr * a
        if risk_unit <= 0:
            continue
        size = (RISK_DOLLARS / risk_unit) if risk_based else (fixed_notional / fill)
        risk_amt = risk_unit * size
        pos = open_position(s, fill, a, policy, size, idx[i], EQUITY, risk_amt)
    return pd.DataFrame(trades)


def stats(tr, years):
    if tr is None or len(tr) < 20:
        return None
    r = tr["r"].values
    n = len(r)
    tpy = n / years if years > 0 else np.nan
    sd = r.std(ddof=1)
    wins, losses = r[r > 0], r[r <= 0]
    gp, gl = wins.sum(), -losses.sum()
    eq = np.cumsum(r)
    dd = float((eq - np.maximum.accumulate(eq)).min())
    return {"n": n, "wr": float((r > 0).mean() * 100),
            "pf": float(gp / gl) if gl > 0 else np.nan,
            "er": float(r.mean()),
            # annualised on the TRUE trade rate, not a hardcoded constant
            "sharpe": float(r.mean() / sd * np.sqrt(tpy)) if sd > 0 else np.nan,
            "total_r": float(r.sum()), "maxdd_r": dd,
            "tpy": float(tpy)}


def fmt(label, s, width=34):
    if s is None:
        return f"  {label:{width}s} (too few trades)"
    return (f"  {label:{width}s} n={s['n']:5d} WR={s['wr']:5.1f}% PF={s['pf']:6.3f} "
            f"E[R]={s['er']:+.4f} Sh={s['sharpe']:+6.2f} totR={s['total_r']:+8.1f}")


def years_of(df):
    return (df.index[-1] - df.index[0]).days / 365.25


def main():
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    train = gold.loc["2018-01-01":"2025-07-31"]

    print("=" * 100)
    print("TEST A — FAITHFULNESS: does the extracted signal reproduce their own simulate()?")
    print("=" * 100)
    their_trades = simulate(to_candles(train), to_daily(train), CFG, EQUITY)
    sig = their_signals(train)
    their_pol = ExitPolicy(mode="fixed", stop_atr=CFG.atr_sl_mult,
                           target_atr=CFG.atr_tp_mult)
    mine = run(train, sig, their_pol, risk_based=False, correct_costs=False)
    n_theirs, n_mine = len(their_trades), len(mine)
    print(f"  their simulate()      : {n_theirs} trades")
    print(f"  extracted signal      : {n_mine} trades")
    diff = abs(n_theirs - n_mine) / max(n_theirs, 1)
    print(f"  difference            : {diff:.2%}  "
          f"-> {'FAITHFUL' if diff < 0.03 else 'EXTRACTION MISMATCH — STOP'}")
    if diff >= 0.03:
        print("\n  Refusing to continue: downstream results would not be about their strategy.")
        return

    print("\n" + "=" * 100)
    print("PHASE 1 — THE CORRECTED BASELINE (their rules, honest measurement)")
    print("=" * 100)
    windows = [("2011-2018 UNSEEN", gold.loc["2011-01-01":"2017-12-31"]),
               ("2018-2025 their train", train),
               ("2025-08+ their holdout", gold.loc["2025-08-01":"2026-07-31"])]
    base = {}
    for lbl, d in windows:
        if len(d) < 2000:
            print(f"  {lbl:24s} insufficient data"); continue
        s_sig = their_signals(d)
        y = years_of(d)
        as_is = stats(run(d, s_sig, their_pol, risk_based=False, correct_costs=False), y)
        fixed = stats(run(d, s_sig, their_pol, risk_based=True, correct_costs=True), y)
        base[lbl] = fixed
        print(f"\n  {lbl}")
        print(fmt("as they measure it", as_is))
        print(fmt("corrected costs + risk sizing", fixed))

    print("\n" + "=" * 100)
    print("PHASE 2 — PRE-REGISTERED CHANGES (selection window 2018-2025 ONLY)")
    print("=" * 100)
    y = years_of(train)
    sig_gate = their_signals(train)
    sig_nogate = their_signals(train, use_gate=False)

    print("\n  P1 — EXITS (their entry, our five pre-registered exit mechanisms)")
    p1 = {}
    for name, pol in POLICIES.items():
        s = stats(run(train, sig_gate, pol), y)
        p1[name] = s
        print(fmt(f"{name}: {pol.describe()[:40]}", s, width=48))

    best_exit = max((k for k in p1 if p1[k]), key=lambda k: p1[k]["er"])
    print(f"\n  best by E[R]: {best_exit}")

    print("\n  P3 — NY SESSION FILTER (12:00-16:00 UTC), applied to best exit")
    for sess in [None, "ny_open", "london_open"]:
        s = stats(run(train, sig_gate, POLICIES[best_exit], session=sess), y)
        print(fmt(f"session={sess or 'all hours'}", s))

    print("\n  P4 — DROP THE ATR-EXPANSION GATE, applied to best exit")
    for lbl, sg in [("gate ON (theirs)", sig_gate), ("gate OFF", sig_nogate)]:
        s = stats(run(train, sg, POLICIES[best_exit]), y)
        print(fmt(lbl, s))

    print("\n  COMBINED (best exit + NY session + gate off)")
    combo = stats(run(train, sig_nogate, POLICIES[best_exit], session="ny_open"), y)
    print(fmt("combined", combo))

    json.dump({"baseline": base, "p1": p1, "best_exit": best_exit,
               "combined": combo},
              open(ROOT / "el_scotto_improved_results.json", "w"), indent=2, default=str)
    print("\nSaved el_scotto_improved_results.json")


if __name__ == "__main__":
    main()
