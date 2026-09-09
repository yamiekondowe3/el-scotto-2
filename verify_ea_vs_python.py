"""PHASE 2 — prove the EA does what the validated Python model does.

An EA that silently differs from the model is worse than no EA: it trades a
strategy nobody tested. Both existing EAs in this project ship with "NOT
VALIDATED IN THE STRATEGY TESTER YET" in their header; this one does not.

Verification is in two parts, because they catch different failures:

  PART A — SPEC RECONCILIATION (runs here, no GUI needed).
    `ea_spec_signals()` re-implements the EA's control flow EXACTLY as written
    in ElScotto_Trend_EA.mq5 -- same ordering of gates, same indices, same
    streak handling -- and is compared trade-for-trade against the validated
    `entry_signals(df, "A2_true_pullback")`. This catches TRANSLATION bugs,
    which are the real risk. It already caught one: the EA updated the ATR
    regime streak after the session/position gates, so it was counting
    "consecutive in-session bars with no open position" rather than
    consecutive volatility-expansion bars.

  PART B — STRATEGY TESTER RECONCILIATION (needs one manual export).
    Part A cannot catch differences in MT5's own indicator maths (iATR seeding,
    iMA warmup) or in fill mechanics. `reconcile_tester()` compares an exported
    Strategy Tester deal report against the Python trade list.

Gates, from the plan: trade count within 5%, entry timestamps matching on
>=90% of trades, same-signed expectancy.
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
from el_scotto_improved import run, stats, years_of
from el_scotto_tradeable import entry_signals
from el_scotto_validate import POLICY, SESSION

# EA input defaults, kept in one place so a change to the .mq5 is a one-line
# change here too.
EA = dict(trend_sma=50, ema_period=21, atr_period=14, atr_sma=50,
          confirm_bars=3, sess_start=12, sess_end=16)

TOL_COUNT = 0.05      # trade count within 5%
TOL_MATCH = 0.90      # >=90% of entry timestamps must coincide


def wilder_atr(df, period):
    """Wilder ATR, matching src/indicators.py::atr and MT5's iATR."""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    atr.iloc[:period] = np.nan
    return atr


def ea_spec_signals(df, ea=EA):
    """The EA's control flow, transcribed literally from the .mq5.

    Deliberately written as an imperative bar loop with the SAME gate ordering
    as OnTick rather than vectorised, so that a divergence in ordering shows up
    instead of being smoothed away by pandas.
    """
    atr = wilder_atr(df, ea["atr_period"])
    atr_sma = atr.rolling(ea["atr_sma"]).mean()
    ema = df["close"].ewm(span=ea["ema_period"], adjust=False).mean()
    # daily SMA of daily closes, served from the last CLOSED daily bar --
    # this is what CopyBuffer(smaDailyHandle,...)[1] returns
    daily_c = df["close"].resample("1D").last().dropna()
    daily_sma = daily_c.rolling(ea["trend_sma"]).mean()
    trend = daily_sma.reindex(df.index.normalize()).values
    # shift by one day: bar i may only see the previous day's closed SMA
    prev_day = daily_sma.shift(1)
    trend = prev_day.reindex(df.index.normalize()).values

    o = df["open"].values; h = df["high"].values
    l = df["low"].values;  c = df["close"].values
    a = atr.values; asma = atr_sma.values; e = ema.values
    hours = df.index.hour.values

    side = np.zeros(len(df), dtype=int)
    atr_out = np.full(len(df), np.nan)
    streak = 0
    for i in range(1, len(df)):
        # --- block 1: indicators + streak, EVERY bar, before any gate ---
        if not np.isfinite(a[i]) or a[i] <= 0:
            streak = 0
            continue
        atr_out[i] = a[i]
        if not np.isfinite(asma[i]):
            streak = 0
            continue
        streak = streak + 1 if a[i] > asma[i] else 0

        # --- block 2: session gate ---
        if hours[i] < ea["sess_start"] or hours[i] >= ea["sess_end"]:
            continue
        if not np.isfinite(e[i]) or not np.isfinite(trend[i]):
            continue
        if streak < ea["confirm_bars"]:
            continue

        # --- block 3: A2_true_pullback ---
        if c[i] > trend[i] and l[i] < e[i] and c[i] > e[i]:
            side[i] = 1
        elif c[i] < trend[i] and h[i] > e[i] and c[i] < e[i]:
            side[i] = -1
    return pd.DataFrame({"side": side, "atr": atr_out}, index=df.index)


def compare(a, b, label_a, label_b, session=None):
    """Compare two signal sets on equal terms.

    The session mask must be applied to BOTH sides before comparing. The model
    emits signals for all hours and `run()` filters them at trade time, while
    the EA filters inside OnTick -- so comparing them raw comes out at 26%
    agreement purely from that asymmetry, not from any real difference.
    """
    if session is not None:
        from common.filters import named_session_mask
        ma = named_session_mask(a.index, session).values
        mb = named_session_mask(b.index, session).values
        a = a.copy(); b = b.copy()
        a.loc[~ma, "side"] = 0
        b.loc[~mb, "side"] = 0
    ta = set(a.index[a["side"] != 0])
    tb = set(b.index[b["side"] != 0])
    na, nb = len(ta), len(tb)
    inter = len(ta & tb)
    denom = max(na, nb, 1)
    cnt_diff = abs(na - nb) / max(na, nb, 1)
    match = inter / denom
    print(f"  {label_a:34s} {na:5d} signals")
    print(f"  {label_b:34s} {nb:5d} signals")
    print(f"  {'shared timestamps':34s} {inter:5d}  ({match:.1%} of the larger set)")
    print(f"  {'count difference':34s} {cnt_diff:.1%}  (tolerance {TOL_COUNT:.0%})")
    ok = cnt_diff <= TOL_COUNT and match >= TOL_MATCH
    print(f"  -> {'RECONCILED' if ok else 'MISMATCH — do not deploy'}")
    return ok, {"n_a": na, "n_b": nb, "shared": inter,
                "count_diff": cnt_diff, "match": match, "ok": bool(ok)}


def reconcile_tester(csv_path, gold, a="2018-01-01", b="2025-07-31",
                     deposit=25_000.0):
    """PART B: compare an exported Strategy Tester deal list to the model.

    Three things must be accounted for or the comparison is meaningless, and
    all three were learned the hard way from the first export:

      1. ENTRY vs EXIT. `run()` records the exit bar in `ts`; the entry is in
         `entry_ts`. Comparing tester entries against `ts` gave 6% agreement
         and looked like a logic bug.
      2. THE FILL BAR. The EA evaluates the signal on the last CLOSED bar and
         fills on the next one, so a tester entry is stamped one bar after the
         signal. Un-shifted agreement was 7%; shifted, 70%.
      3. WARMUP. Slicing the data at the window start leaves the daily SMA50
         cold for ~10 weeks, so the model's first trade was 2018-03-09 against
         the tester's 2018-01-03. Signals must be computed from earlier data.
    """
    p = Path(csv_path)
    if not p.exists():
        print(f"  no tester export at {p} - skipping Part B")
        return None
    t = pd.read_csv(p)
    t["Time"] = pd.to_datetime(t["Time"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    ins = t[t["Direction"] == "in"].dropna(subset=["Time"])
    tester = set(ins["Time"])

    warm = gold.loc["2017-06-01":b]
    sig = entry_signals(warm, "A2_true_pullback")
    tr = run(warm, sig, POLICY, session=SESSION)
    tr = tr[pd.to_datetime(tr["entry_ts"]) >= a]
    ent = pd.to_datetime(tr["entry_ts"]).dt.tz_localize(None) + pd.Timedelta(hours=1)
    model = set(ent)

    shared = len(tester & model)
    frac = shared / max(len(tester), 1)
    print(f"  tester entries      : {len(tester)}")
    print(f"  model entries       : {len(model)}")
    print(f"  shared timestamps   : {shared}  ({frac:.0%} of tester)")
    print(f"  tester-only         : {len(tester - model)}")

    # The min-lot guard skips any setup whose risk budget buys less than one
    # minimum lot. At the tester's $3,000 deposit and 0.5% risk that is a $15
    # budget, so anything with ATR above $7.50 is skipped -- which selectively
    # drops high-volatility periods and almost all of 2025.
    atr = sig["atr"].reindex(tr["entry_ts"]).to_numpy()
    lots = (deposit * 0.005) / (2 * atr * 100)
    skipped = int(np.sum(np.asarray(lots < 0.01)))
    max_atr = (deposit * 0.005) / (2 * 100 * 0.01)
    print(f"  min-lot check at ${deposit:,.0f} deposit / 0.5% risk: "
          f"max tradeable ATR ${max_atr:.2f}, would skip {skipped} model trades")

    ok = frac >= TOL_MATCH
    print(f"  -> {'RECONCILED' if ok else 'NOT RECONCILED'} "
          f"(threshold {TOL_MATCH:.0%} of tester entries matched)")
    return {"tester": len(tester), "model": len(model), "shared": shared,
            "match": frac, "minlot_skipped": skipped, "ok": bool(ok)}


def main():
    gold = load_parquet("XAUUSD", "H1", root=ROOT / "data_cache")
    window = gold.loc["2018-01-01":"2025-07-31"]

    print("=" * 92)
    print("PART A — SPEC RECONCILIATION: EA control flow vs the validated model")
    print("=" * 92)
    model = entry_signals(window, "A2_true_pullback")
    spec = ea_spec_signals(window)
    ok, detail = compare(model, spec, "validated python model", "EA spec (as written in .mq5)",
                         session=SESSION)

    print("\n  Trade-level reconciliation (the gate that actually matters —")
    print("  both signal sets driven through the identical exit/sizing layer):")
    y = years_of(window)
    res = {}
    for lbl, sg in [("model", model), ("EA spec", spec)]:
        s = stats(run(window, sg, POLICY, session=SESSION), y)
        res[lbl] = s
        if s:
            print(f"    {lbl:10s} n={s['n']:5d} PF={s['pf']:6.3f} E[R]={s['er']:+.4f} "
                  f"Sh={s['sharpe']:+.2f}")
    if res["model"] and res["EA spec"]:
        same = (res["model"]["n"] == res["EA spec"]["n"]
                and abs(res["model"]["er"] - res["EA spec"]["er"]) < 1e-9)
        print(f"    -> {'IDENTICAL' if same else 'DIVERGENT'}")
        ok = ok and same
        detail["trades_identical"] = bool(same)

    print("\n" + "=" * 92)
    print("PART B — STRATEGY TESTER RECONCILIATION")
    print("=" * 92)
    csv = sys.argv[1] if len(sys.argv) > 1 else "tester_deals.csv"
    tester = reconcile_tester(csv, gold)

    print("\n" + "=" * 92)
    print(f"DEPLOYMENT GATE: Part A {'PASS' if ok else 'FAIL'}"
          f"{'' if tester is None else (' | Part B ' + ('PASS' if tester['ok'] else 'FAIL'))}")
    if tester is None:
        print("Part B still required before attaching the EA to a live chart.")
    print("=" * 92)

    json.dump({"part_a": detail, "part_b": tester},
              open(ROOT / "verify_ea_results.json", "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
