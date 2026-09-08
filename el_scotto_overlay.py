"""The last honest candidate: a trend OVERLAY on gold, with no entry signal.

Every entry trigger tested -- theirs, Donchian, a corrected pullback, momentum
persistence -- failed the unseen-window placebo. The control that used no
entry timing at all (A4_always_in) scored the HIGHEST z of the five. That is
not a signal result; it says the tradeable object, if one exists, is the
regime + risk layer:

    long when H1 close > daily SMA50, short when below   (their trend regime)
    only while ATR(14) > SMA50(ATR14) for 3 bars         (their gate; P4 kept it)
    only 12:00-16:00 UTC                                 (NY session)
    2xATR stop, no target, 3xATR chandelier trail        (E1_trail)
    constant risk per trade

For an always-in directional system the placebo is meaningless -- random entry
timing IS roughly the same system. The correct benchmark is the one that beat
everything else in this project: HOLDING GOLD, levered to the same realised
volatility, so a result cannot come from simply carrying more risk.

This is the smart-beta test applied to a single asset, and it is the last
question left. If the overlay beats vol-matched gold out of sample it is
tradeable as a risk-management overlay -- not as an edge, and it must be
described that way. If it does not, the answer is no and the project is done.
"""
import sys
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from common.data_fetch import load_parquet
from common.portfolio import vol_matched_benchmark, summarize
from common.exits import POLICIES
from el_scotto_improved import run, stats, years_of, RISK_DOLLARS, EQUITY
from el_scotto_tradeable import entry_signals
from el_scotto_validate import POLICY, SESSION

BARS_PER_YEAR = 24 * 252


def to_daily_returns(trades, index):
    """Trade P&L -> a daily return series on a constant $10k, so the strategy
    can be compared with a buy-and-hold price series on equal footing.

    Returns are NOT compounded: each trade risks a fixed $100, so the series is
    additive in R. That keeps the comparison immune to position-sizing path,
    the same reason R-multiples are used throughout this project.
    """
    s = pd.Series(0.0, index=index)
    if trades is None or len(trades) == 0:
        return s.resample("1D").sum()
    pnl = pd.Series(trades["pnl"].values, index=pd.DatetimeIndex(trades["ts"]))
    pnl = pnl.groupby(level=0).sum()
    s.loc[pnl.index] = pnl.values
    return (s / EQUITY).resample("1D").sum()


def gold_daily(df):
    d = df["close"].resample("1D").last().dropna()
    return d.pct_change().dropna()


def main():
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    windows = [("2011-2018 UNSEEN", "2011-01-01", "2017-12-31"),
               ("2018-2025 selection", "2018-01-01", "2025-07-31"),
               ("full 2011+", "2011-01-01", None)]

    print("=" * 100)
    print("TREND OVERLAY vs HOLDING GOLD, VOLATILITY-MATCHED")
    print("(vol-matching removes 'it just carried more risk' as an explanation)")
    print("=" * 100)
    print(f"  {'window':22s} {'':10s} {'CAGR':>8s} {'vol':>7s} {'Sharpe':>8s} {'maxDD':>9s}")
    print("  " + "-" * 74)

    out = {}
    for lbl, a, b in windows:
        d = gold.loc[a:b] if b else gold.loc[a:]
        if len(d) < 2000:
            continue
        tr = run(d, entry_signals(d, "A4_always_in"), POLICY, session=SESSION)
        sr = to_daily_returns(tr, d.index)
        gr = gold_daily(d)
        common = sr.index.intersection(gr.index)
        sr, gr = sr.loc[common], gr.loc[common]
        vm = vol_matched_benchmark(gr, sr)

        ss, sg, sv = summarize(sr), summarize(gr), summarize(vm)
        out[lbl] = {"strategy": ss, "gold": sg, "vol_matched_gold": sv,
                    "n_trades": int(len(tr))}
        print(f"\n  {lbl:22s} {'overlay':10s} {ss['cagr']*100:+7.2f}% {ss['vol']*100:6.1f}% "
              f"{ss['sharpe']:+8.2f} {ss['max_drawdown']*100:+8.1f}%   (n={len(tr)})")
        print(f"  {'':22s} {'gold B&H':10s} {sg['cagr']*100:+7.2f}% {sg['vol']*100:6.1f}% "
              f"{sg['sharpe']:+8.2f} {sg['max_drawdown']*100:+8.1f}%")
        print(f"  {'':22s} {'vm gold':10s} {'':8s} {sv['vol']*100:6.1f}% "
              f"{sv['sharpe']:+8.2f} {sv['max_drawdown']*100:+8.1f}%")
        ds = ss["sharpe"] - sv["sharpe"]
        dd = ss["max_drawdown"] - sv["max_drawdown"]
        ok = ds > 0 and dd > 0
        print(f"  {'':22s} {'-> vs vm gold: dSharpe ' + f'{ds:+.2f}' + ', dMaxDD ' + f'{dd*100:+.1f}pt':50s}"
              f"  {'BEATS' if ok else 'does not beat'}")

    print("\n" + "=" * 100)
    u = out.get("2011-2018 UNSEEN")
    if u:
        ds = u["strategy"]["sharpe"] - u["vol_matched_gold"]["sharpe"]
        dd = u["strategy"]["max_drawdown"] - u["vol_matched_gold"]["max_drawdown"]
        verdict = ds > 0 and dd > 0
        print(f"DECISION (on the UNSEEN window, the only one that counts):")
        print(f"  overlay Sharpe {u['strategy']['sharpe']:+.2f} vs vol-matched gold "
              f"{u['vol_matched_gold']['sharpe']:+.2f}   (dSharpe {ds:+.2f})")
        print(f"  overlay maxDD  {u['strategy']['max_drawdown']*100:+.1f}% vs "
              f"{u['vol_matched_gold']['max_drawdown']*100:+.1f}%   (dMaxDD {dd*100:+.1f}pt)")
        print(f"  -> {'TRADEABLE as a risk overlay' if verdict else 'NOT TRADEABLE'}")
    print("=" * 100)

    json.dump(out, open(ROOT / "el_scotto_overlay_results.json", "w"),
              indent=2, default=str)
    print("Saved el_scotto_overlay_results.json")


if __name__ == "__main__":
    main()
