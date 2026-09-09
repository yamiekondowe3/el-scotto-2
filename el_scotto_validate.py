"""PHASE 3 — validation of the locked configuration.

CONFIG LOCKED BEFORE THIS FILE WAS RUN, from the 2018-2025 selection window
only:

    entry   : theirs, unmodified (sma50/ema21/tol0.20, ATR gate ON, confirm 3)
    exit    : E1_trail -- 2.0xATR stop, NO target, 3.0xATR chandelier trail
    session : ny_open (12:00-16:00 UTC)
    sizing  : risk-based, constant $100 risk -> results are R-multiples
    costs   : half-spread + slippage on BOTH sides, extra slippage through stops

Phase 2 selection result: E[R] +0.1209, PF 1.236, Sharpe +0.56, n=460.

Two of the four pre-registered changes fired, and one failed:
  P1 exits    PASSED and is the whole story: -0.0055 -> +0.0605 E[R].
  P3 session  PASSED: +0.0605 -> +0.1209.
  P4 gate off FAILED: +0.0307, worse than keeping their gate. Their ATR
              expansion gate helps, contradicting our prior from the
              volatility-regime work. Reported as a negative result, not
              quietly dropped.

I have now inspected ~10 configurations on the selection window, so selection
risk is real and the controls below are what decide the question. 2011-2018
played NO part in any choice above.

Four gates, all of which must pass:
  V1 backward OOS 2011-2018 -- seven years the design has never seen
  V2 random-entry placebo   -- Bonferroni z >= 2.24 (4 change families)
  V3 cross-section          -- 72 markets it was not developed on
  V4 buy-and-hold + vol-matched gold
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
sys.path.insert(0, str(ROOT))

from common.exits import POLICIES
from common.data_fetch import load_parquet
from el_scotto_improved import their_signals, run, stats, fmt, years_of, RISK_DOLLARS

POLICY = POLICIES["E1_trail"]
SESSION = "ny_open"
SEED = 20260908
N_PLACEBO = 200


def placebo(df, sig, n_target, long_ratio, n_runs=N_PLACEBO, seed=SEED,
            half_spread=None, slip=None):
    """Same trade count, direction mix, exits, costs and sizing; entry TIMING
    randomised over bars that were eligible (warm indicators, in session).

    Isolates whether the pullback trigger adds anything over the trend regime,
    the trailing exit and the session filter -- the control that killed a
    PF 1.53 result earlier in this project.
    """
    from common.filters import named_session_mask
    ok = named_session_mask(df.index, SESSION).values
    atr_a = sig["atr"].values
    eligible = np.flatnonzero(np.isfinite(atr_a) & (atr_a > 0) & ok)
    if len(eligible) < n_target * 2:
        return None
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n_runs):
        picks = rng.choice(eligible, size=n_target, replace=False)
        s = pd.DataFrame({"side": 0, "atr": atr_a}, index=df.index)
        sides = np.where(rng.random(n_target) < long_ratio, 1, -1)
        s.iloc[picks, s.columns.get_loc("side")] = sides
        # Costs MUST match the strategy run being compared against. Charging
        # gold's spread to a placebo on EURUSD produced random E[R] of -114.99
        # and a meaningless z of +27; the control has to be costed like the
        # thing it is controlling for.
        r = run(df, s, POLICY, session=SESSION,
                half_spread=half_spread, slip=slip)
        st = stats(r, years_of(df))
        if st:
            out.append(st["er"])
    if len(out) < 20:
        return None
    return {"mean": float(np.mean(out)), "std": float(np.std(out, ddof=1)),
            "n_runs": len(out)}


def main():
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    print(f"LOCKED CONFIG: entry=theirs(gate ON) exit=E1_trail session={SESSION} risk-based\n")

    print("=" * 100)
    print("V1 — BACKWARD OUT-OF-SAMPLE: 2011-2018, seven years the design has never seen")
    print("=" * 100)
    res = {}
    for lbl, a, b in [("2011-2018 UNSEEN", "2011-01-01", "2017-12-31"),
                      ("2018-2025 (selection)", "2018-01-01", "2025-07-31"),
                      ("2025-08+ holdout", "2025-08-01", "2026-07-31")]:
        d = gold.loc[a:b]
        if len(d) < 2000:
            print(f"  {lbl:24s} insufficient"); continue
        sg = their_signals(d)
        st = stats(run(d, sg, POLICY, session=SESSION), years_of(d))
        res[lbl] = st
        print(fmt(lbl, st))
    v1 = res.get("2011-2018 UNSEEN")
    ok1 = bool(v1 and v1["er"] > 0)
    print(f"\n  V1 {'PASS' if ok1 else 'FAIL'} — "
          f"{'positive expectancy on data never seen' if ok1 else 'loses money on the unseen window'}")

    print("\n" + "=" * 100)
    print("V2 — RANDOM-ENTRY PLACEBO (full history; Bonferroni z >= 2.24)")
    print("=" * 100)
    full = gold.loc["2011-01-01":]
    sgf = their_signals(full)
    tr = run(full, sgf, POLICY, session=SESSION)
    stf = stats(tr, years_of(full))
    print(fmt("strategy, full history", stf))
    lr = float((tr["side"] > 0).mean())
    pl = placebo(full, sgf, stf["n"], lr)
    ok2 = False
    if pl and pl["std"] > 0:
        z = (stf["er"] - pl["mean"]) / pl["std"]
        ok2 = z >= 2.24
        print(f"  placebo E[R] {pl['mean']:+.4f} +/- {pl['std']:.4f} ({pl['n_runs']} runs, "
              f"{lr:.0%} long)")
        print(f"\n  z = {z:+.2f}   V2 {'PASS' if ok2 else 'FAIL'} "
              f"(threshold 2.24 after Bonferroni for 4 change families)")

    print("\n" + "=" * 100)
    print("V3 — CROSS-SECTIONAL REPLICATION (markets it was not developed on)")
    print("=" * 100)
    universe = sorted({p.name for p in (ROOT / "data_cache").glob("*")
                       if (p / "H1" / f"{p.name}_H1.parquet").exists()
                       and p.name != "XAUUSD"})
    # Costs MUST be per instrument. Each market's own median recorded spread is
    # used, with slippage scaled in the same proportion as gold's. Applying
    # gold's $0.40 to EURUSD is hundreds of R per trade and manufactures a fake
    # failure -- an error made once already in this project and corrected.
    gold_half = float(gold["spread"].median()) / 2
    rows = []
    for sym in universe:
        try:
            d = load_parquet(sym, "H1", root=ROOT / "data_cache")
            if "spread" not in d.columns:
                continue
            hs = float(d["spread"].median()) / 2
            if not np.isfinite(hs) or hs <= 0:
                continue
            s = stats(run(d, their_signals(d), POLICY, session=SESSION,
                          half_spread=hs, slip=hs * (0.05 / max(gold_half, 1e-12))),
                      years_of(d))
        except Exception:
            continue
        if s:
            rows.append({"symbol": sym, "n": s["n"], "er": s["er"], "pf": s["pf"]})
    ok3 = False
    if rows:
        dfx = pd.DataFrame(rows)
        pos = int((dfx["er"] > 0).sum())
        pct = sps.percentileofscore(dfx["er"], stf["er"])
        ok3 = pos / len(dfx) > 0.5
        print(f"  markets       : {len(dfx)}")
        print(f"  positive E[R] : {pos}/{len(dfx)} ({pos/len(dfx):.0%})   "
              f"(baseline before changes: 19%)")
        print(f"  median E[R]   : {dfx['er'].median():+.4f}")
        print(f"  XAUUSD        : {stf['er']:+.4f} -> {pct:.0f}th percentile")
        print(f"  best 5        : " +
              ", ".join(f"{r.symbol} {r.er:+.3f}" for r in dfx.nlargest(5, "er").itertuples()))
        print(f"\n  V3 {'PASS' if ok3 else 'FAIL'} — "
              f"{'edge generalises' if ok3 else 'concentrated in the instrument it was built on'}")
        dfx.to_json(ROOT / "el_scotto_improved_crosssection.json", orient="records", indent=2)

    print("\n" + "=" * 100)
    print("V4 — VS HOLDING GOLD (the alternative that beat everything else in this project)")
    print("=" * 100)
    for lbl, a, b in [("2011-2018 UNSEEN", "2011-01-01", "2017-12-31"),
                      ("2018-2025", "2018-01-01", "2025-07-31"),
                      ("full 2011+", "2011-01-01", None)]:
        d = gold.loc[a:b] if b else gold.loc[a:]
        sg = their_signals(d)
        st = stats(run(d, sg, POLICY, session=SESSION), years_of(d))
        if not st:
            continue
        bh = float(d["close"].iloc[-1] / d["close"].iloc[0] - 1)
        # strategy return on the same $10k, risking $100/trade
        strat_pct = st["total_r"] * RISK_DOLLARS / 10_000 * 100
        print(f"  {lbl:22s} strategy {strat_pct:+7.1f}%   gold B&H {bh*100:+7.1f}%   "
              f"{'ahead' if strat_pct > bh*100 else 'behind'}")

    print("\n" + "=" * 100)
    print(f"VERDICT: V1 {'PASS' if ok1 else 'FAIL'} | V2 {'PASS' if ok2 else 'FAIL'} | "
          f"V3 {'PASS' if ok3 else 'FAIL'}")
    print("=" * 100)

    json.dump({"windows": res, "placebo": pl, "z": (stf["er"] - pl["mean"]) / pl["std"] if pl else None,
               "v1": ok1, "v2": ok2, "v3": ok3},
              open(ROOT / "el_scotto_validate_results.json", "w"), indent=2, default=str)
    print("Saved el_scotto_validate_results.json")


if __name__ == "__main__":
    main()
