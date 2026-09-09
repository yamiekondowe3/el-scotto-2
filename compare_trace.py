"""Reconcile the EA's per-bar trace against the Python model, value by value.

Four hypotheses were tested by inference from trade lists alone -- min-lot
skipping, holding time, the daily cap, data volume -- and all four were wrong
or only partial. 124 of 130 model-only entries occurred while the EA was FLAT,
which proves the divergence is at the SIGNAL level, not in trade management.
Inference cannot localise that; a per-bar trace can.

Run the EA in the Strategy Tester with Debug_Log_Signals=true, then:
    python compare_trace.py <path to elscotto_trace_XAUUSD.csv>
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from common.data_fetch import load_parquet
from el_scotto_tradeable import entry_signals
from verify_ea_vs_python import wilder_atr


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    ea = pd.read_csv(sys.argv[1])
    ea["bar_time"] = pd.to_datetime(ea["bar_time"], format="%Y.%m.%d %H:%M",
                                    errors="coerce")
    ea = ea.dropna(subset=["bar_time"]).set_index("bar_time").sort_index()
    print(f"EA trace rows: {len(ea)}  {ea.index[0]} .. {ea.index[-1]}")

    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    df = gold.loc["2017-06-01":"2025-07-31"]
    py = pd.DataFrame(index=df.index.tz_localize(None))
    py["close"] = df["close"].values
    py["atr"] = wilder_atr(df, 14).values
    py["atr_sma"] = wilder_atr(df, 14).rolling(50).mean().values
    py["ema"] = df["close"].ewm(span=21, adjust=False).mean().values
    d = df["close"].resample("1D").last().dropna().rolling(50).mean().shift(1)
    py["trend_sma"] = d.reindex(df.index.normalize()).values
    py["side"] = entry_signals(df, "A2_true_pullback")["side"].values

    j = ea.join(py, how="inner", rsuffix="_py")
    print(f"overlapping bars: {len(j)}\n")
    print(f"{'field':12s} {'mean abs diff':>14s} {'max abs diff':>13s} {'bars >0.1% apart':>17s}")
    print("-" * 60)
    for f in ["close", "atr", "atr_sma", "ema", "trend_sma"]:
        a, b = j[f].astype(float), j[f + "_py"].astype(float)
        dd = (a - b).abs()
        rel = dd / b.abs().replace(0, np.nan)
        print(f"{f:12s} {dd.mean():14.6f} {dd.max():13.6f} {int((rel > 0.001).sum()):17d}")

    print()
    agree = (j["side"].astype(int) == j["side_py"].astype(int)).mean()
    ea_sig = int((j["side"].astype(int) != 0).sum())
    py_sig = int((j["side_py"].astype(int) != 0).sum())
    print(f"EA signals {ea_sig}   model signals {py_sig}   per-bar agreement {agree:.2%}")
    dis = j[(j["side"].astype(int) != 0) | (j["side_py"].astype(int) != 0)]
    dis = dis[dis["side"].astype(int) != dis["side_py"].astype(int)]
    print(f"disagreeing bars: {len(dis)}")
    if len(dis):
        cols = ["close", "close_py", "ema", "ema_py", "trend_sma", "trend_sma_py",
                "streak", "in_session", "side", "side_py"]
        print(dis[cols].head(12).to_string())


if __name__ == "__main__":
    main()
