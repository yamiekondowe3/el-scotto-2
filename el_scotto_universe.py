"""The locked config on seven named instruments.

The list was specified by the user, so this is NOT a selection exercise -- the
instruments were not chosen by looking at results, which is the failure mode
that killed six earlier positives here.

Costs are per instrument (each market's own median recorded spread, halved).
Using gold's $0.40 on EURUSD is hundreds of R per trade; that mistake was made
once in this project and produced a fake 3% pass rate.

Windows are reported separately because history depth differs a lot, and that
governs how much any number means:

  XAUUSD, BTCUSD          deep -- the 2011-2018 unseen window is available
  EURUSD/GBPUSD/USDJPY/GBPJPY  ~9.7y from 2016 -- no 2011-2018
  US100, US500            2.6y from 2024-01 -- overlaps the recent regime
                          entirely, so those numbers cannot be independent of it
"""
import sys, json, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats as sps

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from common.data_fetch import load_parquet
from el_scotto_tradeable import entry_signals
from el_scotto_improved import run, stats, years_of
from el_scotto_validate import POLICY, SESSION, placebo

SYMBOLS = ["XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "GBPJPY", "USDJPY", "US100", "US500"]
WINDOWS = [("2011-2018 unseen", "2011-01-01", "2017-12-31"),
           ("2018-2025 select", "2018-01-01", "2025-07-31"),
           ("2025-07+ recent",  "2025-07-01", "2026-09-30")]


def costs_for(d):
    hs = float(d["spread"].median()) / 2
    return hs, hs * (0.05 / 0.075)      # slippage scaled in gold's proportion


def evaluate(sym, a, b):
    d = load_parquet(sym, "H1", root=ROOT / "data_cache")
    warm = str(pd.Timestamp(a) - pd.Timedelta(days=400))[:10]
    sub = d.loc[warm:b]
    if len(sub) < 3000:
        return None, None
    hs, sl = costs_for(d)
    sig = entry_signals(sub, "A2_true_pullback")
    tr = run(sub, sig, POLICY, session=SESSION, half_spread=hs, slip=sl)
    tr = tr[pd.to_datetime(tr["entry_ts"]) >= a]
    if len(tr) < 15:
        return None, None
    span = d.loc[a:b]
    if len(span) < 500:
        return None, None
    return stats(tr, years_of(span)), (sub, sig, tr, span)


def main():
    print("=" * 108)
    print("LOCKED CONFIG ACROSS SEVEN NAMED INSTRUMENTS (per-instrument costs)")
    print("=" * 108)
    hdr = f"{'symbol':8s} {'window':18s} {'n':>5s} {'/yr':>5s} {'WR':>6s} {'PF':>7s} {'E[R]':>9s} {'tot R':>8s} {'B&H':>8s}"
    print(hdr); print("-" * len(hdr))
    rows = []
    for sym in SYMBOLS:
        try:
            d = load_parquet(sym, "H1", root=ROOT / "data_cache")
        except Exception:
            print(f"{sym:8s} no data"); continue
        yrs_avail = (d.index[-1] - d.index[0]).days / 365.25
        for lbl, a, b in WINDOWS:
            s, extra = evaluate(sym, a, b)
            if s is None:
                print(f"{sym:8s} {lbl:18s} {'-- insufficient history --':>40s}")
                continue
            span = extra[3]
            bh = float(span["close"].iloc[-1] / span["close"].iloc[0] - 1) * 100
            rows.append(dict(symbol=sym, window=lbl, **{k: s[k] for k in
                        ("n", "tpy", "wr", "pf", "er", "total_r")}, bh=bh))
            print(f"{sym:8s} {lbl:18s} {s['n']:5d} {s['tpy']:5.0f} {s['wr']:5.1f}% "
                  f"{s['pf']:7.3f} {s['er']:+9.4f} {s['total_r']:+8.1f} {bh:+7.1f}%", flush=True)
        print()

    df = pd.DataFrame(rows)
    print("=" * 108)
    print("SUMMARY BY WINDOW  (how many instruments make money, and is gold special?)")
    print("=" * 108)
    for lbl, _, _ in WINDOWS:
        w = df[df.window == lbl]
        if not len(w):
            continue
        pos = int((w.er > 0).sum())
        g = w[w.symbol == "XAUUSD"]
        gtxt = f"gold {g.er.iloc[0]:+.4f}" if len(g) else "gold n/a"
        print(f"  {lbl:18s} {pos}/{len(w)} positive   median E[R] {w.er.median():+.4f}   {gtxt}")

    print("\n" + "=" * 108)
    print("PLACEBO on the RECENT window — does the entry beat random timing per instrument?")
    print("=" * 108)
    for sym in SYMBOLS:
        s, extra = evaluate(sym, "2025-07-01", "2026-09-30")
        if s is None:
            continue
        sub, sig, tr, _ = extra
        lr = float((tr["side"] > 0).mean())
        d = load_parquet(sym, "H1", root=ROOT / "data_cache")
        hs, sl = costs_for(d)
        try:
            pl = placebo(sub, sig, s["n"], lr, n_runs=150, half_spread=hs, slip=sl)
        except Exception:
            pl = None
        if pl and pl["std"] > 0:
            z = (s["er"] - pl["mean"]) / pl["std"]
            mark = "BEATS" if z > 1.645 else "-"
            print(f"  {sym:8s} strat {s['er']:+.4f}  random {pl['mean']:+.4f} +/- {pl['std']:.4f}  "
                  f"z={z:+6.2f}  {mark}", flush=True)

    df.to_json(ROOT / "el_scotto_universe_results.json", orient="records", indent=2)
    print("\nSaved el_scotto_universe_results.json")


if __name__ == "__main__":
    main()
