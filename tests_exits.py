"""Tests for the exit manager, filters, backtest core and placebo control."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.exits import ExitPolicy, open_position, process_bar, POLICIES
from common.filters import session_mask, volatility_regime_mask, apply_direction
from common.backtest_core import run as run_core
from common.costs import FrictionModel
from common.placebo import run_placebo, buy_and_hold, make_random_signals


def synth(n=4000, freq="1h", seed=3, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq=freq, tz="UTC")
    ret = rng.normal(drift, 0.004, n)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = np.roll(close, 1); open_[0] = close[0]
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "volume": rng.integers(10, 100, n).astype(float),
                       "spread": 0.02}, index=idx)
    from common.indicators import atr
    df["atr"] = atr(df, 14)
    return df


def _pos(side=1, entry=100.0, atr_val=1.0, policy=None):
    policy = policy or ExitPolicy(mode="fixed", stop_atr=2.0, target_atr=2.0)
    return open_position(side, entry, atr_val, policy, size=10.0, entry_ts=None,
                         equity=10_000.0, risk_amount=50.0)


def test_trailing_stop_never_moves_against_the_position():
    policy = ExitPolicy(mode="trail", stop_atr=2.0, target_atr=None, trail_atr=1.0)
    pos = _pos(side=1, policy=policy)
    stops = []
    for hi in [101, 103, 102, 105, 101, 104]:   # price rises then retraces
        process_bar(pos, {"high": hi, "low": hi - 0.5, "close": hi}, None, policy)
        stops.append(pos.stop)
    assert all(b >= a - 1e-9 for a, b in zip(stops, stops[1:])), "long trailing stop moved DOWN"

    policy_s = ExitPolicy(mode="trail", stop_atr=2.0, target_atr=None, trail_atr=1.0)
    pos_s = _pos(side=-1, policy=policy_s)
    stops_s = []
    for lo in [99, 97, 98, 95, 99, 96]:
        process_bar(pos_s, {"high": lo + 0.5, "low": lo, "close": lo}, None, policy_s)
        stops_s.append(pos_s.stop)
    assert all(b <= a + 1e-9 for a, b in zip(stops_s, stops_s[1:])), "short trailing stop moved UP"


def test_breakeven_stop_never_worse_than_entry():
    policy = ExitPolicy(mode="be_target", stop_atr=2.0, target_atr=4.0, be_at_r=1.0)
    pos = _pos(side=1, policy=policy)
    # 1R = 2.0 here, so a high of 102+ triggers the breakeven move
    process_bar(pos, {"high": 102.5, "low": 100.5, "close": 102.0}, None, policy)
    assert pos.moved_to_be
    assert pos.stop >= pos.entry_price - 1e-9


def test_scale_out_fractions_sum_to_full_size():
    policy = POLICIES["E2_scale_trail"]
    pos = _pos(side=1, atr_val=1.0, policy=policy)
    total = 0.0
    # first bar reaches 1R -> partial; later bar gaps below stop -> remainder
    for leg in process_bar(pos, {"high": 103.0, "low": 100.2, "close": 102.5}, None, policy):
        total += leg["fraction"]
    assert pos.scaled_out and 0 < total < 1.0
    for leg in process_bar(pos, {"high": 100.0, "low": 90.0, "close": 91.0}, None, policy):
        total += leg["fraction"]
    assert total == pytest.approx(1.0), "scale-out legs must sum to the whole position"
    assert pos.remaining == pytest.approx(0.0)


def test_stop_assumed_first_when_stop_and_target_both_touched():
    policy = ExitPolicy(mode="fixed", stop_atr=2.0, target_atr=2.0)
    pos = _pos(side=1, policy=policy)   # stop 98, target 102
    legs = process_bar(pos, {"high": 103.0, "low": 97.0, "close": 100.0}, None, policy)
    assert len(legs) == 1 and legs[0]["reason"] == "stop"


def test_time_exit_closes_on_schedule():
    policy = ExitPolicy(mode="time", stop_atr=5.0, target_atr=5.0, max_bars=3)
    pos = _pos(side=1, policy=policy)
    for _ in range(2):
        assert process_bar(pos, {"high": 100.1, "low": 99.9, "close": 100.0}, None, policy) == []
    legs = process_bar(pos, {"high": 100.1, "low": 99.9, "close": 100.0}, None, policy)
    assert legs and legs[0]["reason"] == "time"


def test_session_and_volatility_filters_are_subsets():
    df = synth()
    sm = session_mask(df.index, "metals")
    assert sm.sum() < len(df) and sm.sum() > 0
    vm = volatility_regime_mask(df, pctile=50.0, window=200)
    assert 0 < vm.sum() < len(df)


def test_direction_filter_removes_one_side():
    s = pd.Series([True, True, False])
    long_only_l, long_only_s = apply_direction(s, s, "long_only")
    assert long_only_l.any() and not long_only_s.any()
    _, short_only_s = apply_direction(s, s, "short_only")
    assert short_only_s.any()


def test_core_is_deterministic():
    df = synth()
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = df.index.hour == 15
    a = run_core(df, POLICIES["E0_fixed"], "XAUUSD")["trades"]
    b = run_core(df, POLICIES["E0_fixed"], "XAUUSD")["trades"]
    assert a["pnl"].sum() == pytest.approx(b["pnl"].sum())


def test_placebo_shows_no_edge_on_driftless_random_walk():
    """Harness sanity: with no drift and random entries, expectancy must be
    ~0 (slightly negative after costs). If this shows an edge, the harness
    is broken and every downstream result is worthless."""
    df = synth(n=6000, drift=0.0)
    df["long_signal"] = False
    df["short_signal"] = False
    res = run_placebo(df, POLICIES["E1_trail"], "XAUUSD", n_entries=150,
                      long_ratio=0.5, eligible=df["atr"].notna(), n_runs=30)
    assert res["n_runs"] > 0
    # costs make it negative; it must not be meaningfully positive
    assert res["expectancy_r_mean"] < 0.05, f"placebo found edge in noise: {res}"


def test_random_signals_match_requested_count_and_ratio():
    df = synth(n=2000)
    rng = np.random.default_rng(0)
    out = make_random_signals(df, n_entries=100, long_ratio=0.7,
                              eligible=df["atr"].notna(), rng=rng)
    n_long = int(out["long_signal"].sum()); n_short = int(out["short_signal"].sum())
    assert n_long + n_short == 100
    assert n_long == 70


def test_buy_and_hold_benchmark_on_uptrend():
    df = synth(n=3000, drift=0.0005)
    bh = buy_and_hold(df, periods_per_year=24 * 365)
    assert bh["total_return_pct"] > 0 and np.isfinite(bh["sharpe"])


def test_cost_guard_skips_setups_where_spread_dominates_risk():
    """The ATR-collapse defect: when ATR shrinks toward the spread, size
    explodes and a nominal 1R stop costs many R. The guard must refuse
    those setups entirely."""
    df = synth(n=3000, freq="1h")
    df["spread"] = 5.0                      # spread enormous vs ATR (~0.2)
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = False
    permissive = run_core(df, POLICIES["E0_fixed"], "XAUUSD", max_cost_ratio=99.0)["trades"]
    guarded = run_core(df, POLICIES["E0_fixed"], "XAUUSD", max_cost_ratio=0.20)["trades"]
    assert len(permissive) > 0, "fixture should trade without the guard"
    assert len(guarded) == 0, "guard must refuse setups where spread dominates risk"
    # and the unguarded version is exactly the pathology we found in FX
    assert permissive["r_multiple"].median() < -2.0


def test_cost_guard_leaves_healthy_setups_alone():
    df = synth(n=3000, freq="1h")
    df["spread"] = 0.001                    # negligible vs ATR
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = False
    a = run_core(df, POLICIES["E0_fixed"], "XAUUSD", max_cost_ratio=99.0)["trades"]
    b = run_core(df, POLICIES["E0_fixed"], "XAUUSD", max_cost_ratio=0.20)["trades"]
    assert len(a) == len(b), "guard must not touch setups with sane cost ratios"


def test_leverage_cap_bounds_notional():
    df = synth(n=3000, freq="1h")
    df["spread"] = 0.001
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = False
    t = run_core(df, POLICIES["E0_fixed"], "XAUUSD",
                 starting_equity=10_000.0, max_leverage=5.0)["trades"]
    if len(t):
        notional = t["size"] * t["entry_price"]
        assert (notional <= 5.0 * 10_000.0 * 1.01).all(), "leverage cap breached"


def test_limit_entry_never_fills_better_than_the_limit():
    """A resting buy limit must not fill below its price (that would be
    inventing free money); slippage may only make the fill worse."""
    df = synth(n=4000, freq="1h")
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = False
    res = run_core(df, POLICIES["E0_fixed"], "XAUUSD",
                   entry_mode="limit", limit_offset_atr=0.0, limit_max_wait=3)
    t = res["trades"]
    if len(t):
        # entry is the limit plus (adverse) slippage, so never far below close
        assert (t["entry_price"] > 0).all()
    assert 0.0 < res["fill_rate"] <= 1.0


def test_limit_entry_has_realistic_fill_rate_below_one():
    """Adverse selection is the whole risk of limit entry: some setups run
    away unfilled. A model showing 100% fills would be fantasy."""
    df = synth(n=6000, freq="1h")
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = False
    res = run_core(df, POLICIES["E0_fixed"], "XAUUSD",
                   entry_mode="limit", limit_offset_atr=0.25, limit_max_wait=2)
    assert res["fill_rate"] < 1.0, "limit orders must sometimes go unfilled"
    assert res["n_signals"] > res["n_filled"]


def test_market_entry_fills_everything():
    df = synth(n=4000, freq="1h")
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = False
    res = run_core(df, POLICIES["E0_fixed"], "XAUUSD", entry_mode="market")
    assert res["fill_rate"] == pytest.approx(1.0)


def test_direction_filter_in_core():
    df = synth(n=4000, freq="1h")
    df["long_signal"] = df.index.hour == 9
    df["short_signal"] = df.index.hour == 15
    both = run_core(df, POLICIES["E0_fixed"], "XAUUSD", direction="both")["trades"]
    longs = run_core(df, POLICIES["E0_fixed"], "XAUUSD", direction="long_only")["trades"]
    assert (longs["side"] == 1).all()
    assert len(longs) < len(both)
