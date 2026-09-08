"""Reproduce el-scotto's reported figures using THEIR code and THEIR data.

Reproduce before critiquing: if our understanding of the rules is wrong, any
assessment built on it is worthless. This runs their own simulate() and
compute_metrics() on their bundled Dukascopy cache over their own train and
holdout windows, and compares against the README table.

Also reports the comparison their README does not include: what gold itself
did over each window. A long-biased trend system in a rising market earns
part of that drift for free, so "profitable" only means something measured
against holding the asset.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent / "el-scotto-review"
sys.path.insert(0, str(REPO))

from src import data_dukascopy
from src.entries_v2 import LowfreqV2Config, simulate, compute_metrics

NOTIONAL = 10_000.0
TRAIN = (datetime(2018, 1, 1, tzinfo=timezone.utc), datetime(2025, 7, 31, 23, 59, 59, tzinfo=timezone.utc))
TEST = (datetime(2025, 8, 1, tzinfo=timezone.utc), datetime(2026, 7, 31, 23, 59, 59, tzinfo=timezone.utc))

# README: "gated by an ATR-expansion Can_Trade filter that must hold for 3
# consecutive bars". Dataclass defaults otherwise.
CONFIG = LowfreqV2Config(use_regime_filter=True, regime_confirm_bars=3)

REPORTED = {
    "train": {"n": 1073, "wr": 45.1, "pf": 1.018, "sharpe": -0.519, "pnl_pct": 6.00},
    "test": {"n": 132, "wr": 56.8, "pf": 1.544, "sharpe": 1.843, "pnl_pct": 31.17},
}


def slice_c(candles, a, b):
    lo, hi = a.timestamp() * 1000, b.timestamp() * 1000
    return [c for c in candles if lo <= c.open_time <= hi]


def main():
    print("Loading their bundled Dukascopy M5 cache...")
    m5 = data_dukascopy.load_m5_candles("2018-01-01")
    h1 = data_dukascopy.resample(m5, 60)
    daily = data_dukascopy.resample(m5, 1440)
    print(f"  M5 {len(m5)}, H1 {len(h1)}, daily {len(daily)}")
    print(f"  span {datetime.fromtimestamp(h1[0].open_time/1000, timezone.utc).date()}"
          f" .. {datetime.fromtimestamp(h1[-1].open_time/1000, timezone.utc).date()}\n")
    print(f"  config: {CONFIG.as_dict()}\n")

    for label, (a, b) in [("train", TRAIN), ("test", TEST)]:
        # pad the daily series back so the SMA(100) is warm at window start,
        # mirroring their run_window()
        h1_s = slice_c(h1, a, b)
        daily_s = [c for c in daily if c.open_time <= b.timestamp() * 1000]
        if len(h1_s) < 100:
            print(f"{label}: insufficient bars ({len(h1_s)})")
            continue
        trades = simulate(h1_s, daily_s, CONFIG, NOTIONAL)
        m = compute_metrics(trades, NOTIONAL)
        r = REPORTED[label]
        print(f"--- {label.upper()} ({a.date()} .. {b.date()}) ---")
        print(f"  {'metric':12s} {'reproduced':>12s} {'reported':>12s}")
        for k, got, want in [
            ("trades", m["n_trades"], r["n"]),
            ("win rate %", round(m["win_rate_pct"], 1), r["wr"]),
            ("profit factor", round(m["profit_factor"], 3) if m["profit_factor"] else None, r["pf"]),
            ("sharpe", round(m["sharpe"], 3) if m["sharpe"] else None, r["sharpe"]),
            ("net P&L %", round(m["net_pnl_pct"], 2), r["pnl_pct"]),
        ]:
            print(f"  {k:12s} {str(got):>12s} {str(want):>12s}")
        # gold's own move over the same window, the missing benchmark
        px0, px1 = h1_s[0].close, h1_s[-1].close
        print(f"  gold buy&hold over window: {(px1/px0-1)*100:+.1f}%  "
              f"({px0:.0f} -> {px1:.0f})")
        print()


if __name__ == "__main__":
    main()
