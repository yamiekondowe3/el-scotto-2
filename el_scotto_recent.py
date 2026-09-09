"""The locked config on 2025-07 .. 2026-09: data that chose nothing.

The configuration was frozen using 2018-01-01..2025-07-31 (selection) and
2011-2018 (validation). Everything from 2025-08-01 onward postdates that
freeze, so this is a genuine out-of-sample read -- the closest thing to a
forward test available without waiting.

It is also SMALL. At ~40 trades/year, 13 months carries a standard error on
E[R] near +/-0.25R, so this window cannot confirm or refute an edge. It is
reported with confidence intervals and a random-entry placebo so the noise is
visible rather than implied.
"""
import sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from common.data_fetch import load_parquet
from el_scotto_tradeable import entry_signals
from el_scotto_improved import run, stats, years_of, RISK_DOLLARS
from el_scotto_validate import POLICY, SESSION, placebo

A, B = "2025-07-01", "2026-09-30"

def main():
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    print(f"data available through {gold.index[-1]}")
    # warm the daily SMA50 from well before the window
    warm = gold.loc["2024-06-01":B]
    sig = entry_signals(warm, "A2_true_pullback")
    tr_all = run(warm, sig, POLICY, session=SESSION)
    tr = tr_all[pd.to_datetime(tr_all["entry_ts"]) >= A].reset_index(drop=True)
    win = gold.loc[A:B]
    yrs = years_of(win)
    s = stats(tr, yrs)
    if s is None:
        print("too few trades"); return

    r = tr["r"].values
    n = len(r)
    se = r.std(ddof=1) / np.sqrt(n)
    t, p = sps.ttest_1samp(r, 0)
    lo, hi = r.mean() - 1.96 * se, r.mean() + 1.96 * se

    print("=" * 88)
    print(f"LOCKED CONFIG on {win.index[0].date()} .. {win.index[-1].date()}  ({yrs:.2f} years)")
    print("=" * 88)
    print(f"  trades            {n}   ({s['tpy']:.0f}/yr)")
    print(f"  win rate          {s['wr']:.1f}%")
    print(f"  profit factor     {s['pf']:.3f}")
    print(f"  expectancy        {s['er']:+.4f}R   95% CI [{lo:+.4f}, {hi:+.4f}]")
    print(f"  t vs zero         t={t:+.2f}  p={p:.3f}")
    print(f"  total             {s['total_r']:+.1f}R")
    print(f"  max drawdown      {s['maxdd_r']:+.1f}R")
    print(f"  Sharpe            {s['sharpe']:+.2f}")
    for rp in (0.5, 0.25):
        print(f"  at {rp:.2f}% risk    return {s['total_r']*rp/100*100:+.2f}%   "
              f"drawdown {s['maxdd_r']*rp/100*100:+.2f}%")

    print("\n" + "=" * 88)
    print("BENCHMARKS ON THE SAME WINDOW")
    print("=" * 88)
    bh = float(win["close"].iloc[-1] / win["close"].iloc[0] - 1)
    strat_pct = s["total_r"] * 0.5 / 100 * 100
    print(f"  gold buy & hold   {bh*100:+.1f}%   ({win['close'].iloc[0]:.0f} -> {win['close'].iloc[-1]:.0f})")
    print(f"  strategy @0.5%    {strat_pct:+.1f}%")
    print(f"  -> {'strategy ahead' if strat_pct > bh*100 else 'buy & hold ahead'}")

    print("\n" + "=" * 88)
    print("RANDOM-ENTRY PLACEBO on this window (same n, direction mix, exits, session)")
    print("=" * 88)
    lr = float((tr["side"] > 0).mean())
    sub = gold.loc["2024-06-01":B]
    pl = placebo(sub, sig, n, lr, n_runs=200)
    if pl and pl["std"] > 0:
        z = (s["er"] - pl["mean"]) / pl["std"]
        print(f"  strategy   E[R] {s['er']:+.4f}  ({lr:.0%} long)")
        print(f"  random     E[R] {pl['mean']:+.4f} +/- {pl['std']:.4f}  ({pl['n_runs']} runs)")
        print(f"  z = {z:+.2f}   -> {'beats random' if z > 1.645 else 'does NOT beat random entry'}")

    print("\n" + "=" * 88)
    print("PRIOR WINDOWS, for context")
    print("=" * 88)
    for lbl, a2, b2 in [("2011-2018 unseen", "2011-01-01", "2017-12-31"),
                        ("2018-2025 selection", "2018-01-01", "2025-07-31")]:
        w2 = gold.loc[str(pd.Timestamp(a2) - pd.Timedelta(days=400))[:10]:b2]
        t2 = run(w2, entry_signals(w2, "A2_true_pullback"), POLICY, session=SESSION)
        t2 = t2[pd.to_datetime(t2["entry_ts"]) >= a2]
        s2 = stats(t2, years_of(gold.loc[a2:b2]))
        print(f"  {lbl:22s} n={s2['n']:4d} WR={s2['wr']:5.1f}% PF={s2['pf']:6.3f} E[R]={s2['er']:+.4f}")
    print(f"  {'2025-07..2026-09 NEW':22s} n={n:4d} WR={s['wr']:5.1f}% PF={s['pf']:6.3f} E[R]={s['er']:+.4f}")

    print("\n  Monthly R:")
    tr["m"] = pd.to_datetime(tr["entry_ts"]).dt.to_period("M")
    mo = tr.groupby("m")["r"].agg(["size", "sum"])
    print("   " + "  ".join(f"{str(i)[2:]}:{v:+.1f}({int(c)})" for i, (c, v) in mo.iterrows()))


if __name__ == "__main__":
    main()
