"""Run el-scotto's OWN simulate() through this project's control stack.

Their code is imported and used unmodified, so what is assessed is their
actual strategy rather than a reimplementation that might differ subtly.
The config is the one confirmed to reproduce their published table exactly:
sma50 / ema21 / tol0.20 / sl2.0 / tp2.5 / regime gate with 3 confirm bars.

Three things their evaluation does not include:

  1. BACKWARD OUT-OF-SAMPLE. Their data starts 2018; our Deriv history starts
     2011. Seven years the design has never seen, from a different vendor --
     which also tests robustness to the price source.
  2. RANDOM-ENTRY PLACEBO. Same trade count, direction mix, exits, costs and
     sizing; entry timing randomised. Answers whether the pullback trigger
     adds anything over the trend regime and the ATR exits. This control
     killed a PF 1.53 result earlier in this project.
  3. CROSS-SECTIONAL REPLICATION. The identical rules on FX and crypto pairs.
     A real trend-pullback edge should generalise; a gold-only one is
     selection.

Plus the benchmark their README omits: what gold itself did over the same
windows.
"""
import sys
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent
REPO = ROOT / "el-scotto-review"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

from src.candle import Candle
from src.entries_v2 import (LowfreqV2Config, simulate, compute_metrics,
                            DEFAULT_COSTS, Direction, TradeRecordV2)
from src.indicators import atr as their_atr
from src.backtest_structures import sma
from src.medfreq_strategy import align_htf_to_m5
from src.regime_filter import atr_expansion_gate
from src.indicators import ema

from common.data_fetch import load_parquet

# Confirmed to reproduce the published table exactly.
CFG = LowfreqV2Config(trend_sma_period=50, pullback_ema_period=21,
                      pullback_tolerance_pct=0.20, atr_sl_mult=2.0,
                      atr_tp_mult=2.5, use_regime_filter=True,
                      regime_confirm_bars=3)
NOTIONAL = 10_000.0


def to_candles(df):
    """Our parquet -> their Candle list (open_time in epoch MILLISECONDS).

    The unit is derived, not assumed: this index is datetime64[ms] under
    pandas 3, so an unconditional `// 10**6` (correct for nanoseconds)
    silently produced 1970 timestamps and zero trades.
    """
    ms = df.index.tz_convert("UTC").tz_localize(None).astype("datetime64[ms]").astype("int64")
    return [Candle(int(t), float(o), float(h), float(l), float(c), float(v))
            for t, o, h, l, c, v in zip(ms, df["open"], df["high"], df["low"],
                                        df["close"], df["volume"])]


def to_daily(df):
    d = df.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                               "close": "last", "volume": "sum"}).dropna()
    return to_candles(d)


def metrics_of(trades):
    if len(trades) < 20:
        return None
    m = compute_metrics(trades, NOTIONAL)
    # R-multiple: pnl relative to the risk actually taken (stop distance x qty),
    # so results are comparable across instruments of wildly different price.
    rs = []
    for t in trades:
        risk = abs(t.entry_price - t.stop) * t.qty
        if risk > 0:
            rs.append(t.pnl / risk)
    m["expectancy_r"] = float(np.mean(rs)) if rs else np.nan
    m["r_series"] = rs
    return m


def run_strategy(df, cfg=CFG):
    h1, daily = to_candles(df), to_daily(df)
    return simulate(h1, daily, cfg, NOTIONAL)


def run_placebo(df, n_target, long_ratio, cfg=CFG, n_runs=100, seed=11):
    """Their exact exit/cost/sizing machinery, with entry TIMING randomised.

    Mirrors simulate()'s inner loop exactly except that the pullback trigger is
    replaced by a random draw from bars that were eligible (warm indicators,
    gate satisfied), preserving trade count and direction mix.
    """
    h1, daily = to_candles(df), to_daily(df)
    daily_sma = sma([c.close for c in daily], cfg.trend_sma_period)
    trend = align_htf_to_m5(h1, daily, daily_sma, 1440)
    atr_vals = their_atr(h1, 14)
    gate = atr_expansion_gate(h1)
    streak, s = [], 0
    for g in gate:
        s = s + 1 if g is True else 0
        streak.append(s)

    eligible = [i for i in range(1, len(h1))
                if trend[i] is not None and atr_vals[i] is not None
                and atr_vals[i] > 0 and streak[i] >= cfg.regime_confirm_bars]
    if len(eligible) < n_target * 2:
        return None

    rng = random.Random(seed)
    out = []
    for _ in range(n_runs):
        picks = sorted(rng.sample(eligible, min(n_target, len(eligible))))
        n_long = int(round(len(picks) * long_ratio))
        dirs = [Direction.LONG] * n_long + [Direction.SHORT] * (len(picks) - n_long)
        rng.shuffle(dirs)
        trades, pos, di = [], None, 0
        pick_set = dict(zip(picks, dirs))
        for i in range(1, len(h1)):
            c = h1[i]
            if pos is not None:
                ep = er = None
                if pos["d"] == Direction.LONG:
                    if c.low <= pos["sl"]: ep, er = pos["sl"], "SL"
                    elif c.high >= pos["tp"]: ep, er = pos["tp"], "TP"
                else:
                    if c.high >= pos["sl"]: ep, er = pos["sl"], "SL"
                    elif c.low <= pos["tp"]: ep, er = pos["tp"], "TP"
                if ep is not None:
                    fill = ep - DEFAULT_COSTS.slippage_per_side if pos["d"] == Direction.LONG \
                        else ep + DEFAULT_COSTS.slippage_per_side
                    gross = ((fill - pos["e"]) if pos["d"] == Direction.LONG
                             else (pos["e"] - fill)) * pos["q"]
                    trades.append(TradeRecordV2(
                        direction=pos["d"],
                        entry_time=datetime.fromtimestamp(pos["t"] / 1000, timezone.utc),
                        entry_price=pos["e"], stop=pos["sl"], target=pos["tp"],
                        exit_time=datetime.fromtimestamp(c.open_time / 1000, timezone.utc),
                        exit_price=fill, exit_reason=er, qty=pos["q"], pnl=gross))
                    pos = None
            if pos is not None or i not in pick_set:
                continue
            a = atr_vals[i]
            if a is None or a <= 0:
                continue
            d = pick_set[i]
            adj = DEFAULT_COSTS.spread / 2 + DEFAULT_COSTS.slippage_per_side
            fill = c.close + adj if d == Direction.LONG else c.close - adj
            q = NOTIONAL / fill
            sl_ = fill - cfg.atr_sl_mult * a if d == Direction.LONG else fill + cfg.atr_sl_mult * a
            tp_ = fill + cfg.atr_tp_mult * a if d == Direction.LONG else fill - cfg.atr_tp_mult * a
            pos = {"d": d, "e": fill, "sl": sl_, "tp": tp_, "t": c.open_time, "q": q}
        m = metrics_of(trades)
        if m:
            out.append(m["expectancy_r"])
    if not out:
        return None
    return {"mean": float(np.mean(out)), "std": float(np.std(out, ddof=1)), "n_runs": len(out)}


def bh_return(df):
    return float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)


def main():
    print("CONFIG (reproduces their published table exactly):")
    print(f"  {CFG.as_dict()}\n")

    print("=" * 78)
    print("TEST 1 — BACKWARD OUT-OF-SAMPLE: XAUUSD 2011-2018, data they never had")
    print("=" * 78)
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    windows = [("2011-2018 (unseen)", "2011-01-01", "2017-12-31"),
               ("2018-2025 (their train, our vendor)", "2018-01-01", "2025-07-31"),
               ("2025-08+ (their holdout, our vendor)", "2025-08-01", "2026-07-31")]
    rows = []
    for label, a, b in windows:
        sub = gold.loc[a:b]
        if len(sub) < 2000:
            print(f"  {label:38s} insufficient data"); continue
        m = metrics_of(run_strategy(sub))
        if not m:
            print(f"  {label:38s} too few trades"); continue
        bh = bh_return(sub)
        rows.append({"window": label, "n": m["n_trades"], "wr": m["win_rate_pct"],
                     "pf": m["profit_factor"], "sharpe": m["sharpe"],
                     "er": m["expectancy_r"], "pnl_pct": m["net_pnl_pct"], "bh": bh * 100})
        print(f"  {label:38s} n={m['n_trades']:5d} WR={m['win_rate_pct']:5.1f}% "
              f"PF={m['profit_factor'] or 0:6.3f} Sharpe={m['sharpe'] or 0:+6.2f} "
              f"E[R]={m['expectancy_r']:+.4f} | strat P&L {m['net_pnl_pct']:+6.1f}% vs gold {bh*100:+6.1f}%",
              flush=True)

    print("\n" + "=" * 78)
    print("TEST 2 — RANDOM-ENTRY PLACEBO on the full gold history")
    print("=" * 78)
    full = gold.loc["2011-01-01":]
    tr = run_strategy(full)
    m = metrics_of(tr)
    if m:
        lr = sum(1 for t in tr if t.direction == Direction.LONG) / len(tr)
        plc = run_placebo(full, m["n_trades"], lr)
        if plc and plc["std"] > 0:
            z = (m["expectancy_r"] - plc["mean"]) / plc["std"]
            print(f"  strategy  E[R] {m['expectancy_r']:+.4f}  (n={m['n_trades']}, "
                  f"{lr*100:.0f}% long)")
            print(f"  placebo   E[R] {plc['mean']:+.4f} +/- {plc['std']:.4f} "
                  f"({plc['n_runs']} runs)")
            print(f"  z = {z:+.2f}   {'BEATS placebo' if z > 1.645 else 'does NOT beat placebo'}")

    print("\n" + "=" * 78)
    print("TEST 3 — CROSS-SECTIONAL REPLICATION (identical rules, other markets)")
    print("=" * 78)
    universe = sorted({p.name for p in (ROOT / "data_cache").glob("*")
                       if (p / "H1" / f"{p.name}_H1.parquet").exists()
                       and p.name != "XAUUSD"})
    ers = []
    for sym in universe:
        try:
            d = load_parquet(sym, "H1", root=ROOT / "data_cache")
            m2 = metrics_of(run_strategy(d))
        except Exception:
            continue
        if m2:
            ers.append({"symbol": sym, "n": m2["n_trades"], "pf": m2["profit_factor"],
                        "er": m2["expectancy_r"]})
    if ers:
        d = pd.DataFrame(ers)
        gold_er = rows[0]["er"] if rows else np.nan
        full_er = m["expectancy_r"]
        pos = int((d["er"] > 0).sum())
        print(f"  markets tested : {len(d)}")
        print(f"  positive E[R]  : {pos}/{len(d)} ({pos/len(d):.0%})")
        print(f"  median E[R]    : {d['er'].median():+.4f}")
        print(f"  XAUUSD E[R]    : {full_er:+.4f} -> "
              f"{stats.percentileofscore(d['er'], full_er):.0f}th percentile")
        print("  best 5:", ", ".join(f"{r.symbol} {r.er:+.3f}" for r in d.nlargest(5, "er").itertuples()))
        d.to_json(ROOT / "el_scotto_crosssection.json", orient="records", indent=2)

    json.dump({"windows": rows}, open(ROOT / "el_scotto_harness_results.json", "w"),
              indent=2, default=str)
    print("\nSaved el_scotto_harness_results.json")


if __name__ == "__main__":
    main()
