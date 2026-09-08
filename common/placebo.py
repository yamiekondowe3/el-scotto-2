"""Random-entry placebo control — the decisive test this project never ran.

The question an exit-engineering result must answer: did the SIGNAL earn
the improvement, or did the EXITS harvest drift that any entry would have
caught? Gold and BTC both trended hard across 2011-2026, so a trailing-stop
system can look profitable while carrying no information whatsoever.

The control: same number of entries, same long/short ratio, same eligible
bars (so the same session/regime filters apply), drawn at RANDOM times, run
through the IDENTICAL exit logic and cost model. If the real signal cannot
beat this, it is not a signal.

Brusco's replication of the ORB literature used exactly this device; the
research documents flagged it, and it was never built until now.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest_core import run as run_core
from .costs import FrictionModel
from .exits import ExitPolicy


def make_random_signals(sig: pd.DataFrame, n_entries: int, long_ratio: float,
                        eligible: pd.Series | None, rng: np.random.Generator) -> pd.DataFrame:
    """Random entries matched to the real strategy's count, direction mix and
    eligible bars — everything except *when* it chose to trade."""
    out = sig.copy()
    out["long_signal"] = False
    out["short_signal"] = False
    if n_entries <= 0:
        return out

    pool = np.flatnonzero(eligible.to_numpy() if eligible is not None
                          else np.isfinite(sig["atr"].to_numpy()))
    pool = pool[pool < len(sig) - 1]
    if len(pool) == 0:
        return out
    take = min(n_entries, len(pool))
    picks = rng.choice(pool, size=take, replace=False)
    n_long = int(round(take * long_ratio))
    longs, shorts = picks[:n_long], picks[n_long:]

    li = out.columns.get_loc("long_signal")
    si = out.columns.get_loc("short_signal")
    out.iloc[longs, li] = True
    out.iloc[shorts, si] = True
    return out


def run_placebo(sig: pd.DataFrame, policy: ExitPolicy, symbol: str,
                n_entries: int, long_ratio: float, eligible: pd.Series | None = None,
                n_runs: int = 200, seed: int = 4242, **core_kwargs) -> dict:
    """Distribution of placebo outcomes, not a single point estimate."""
    rng = np.random.default_rng(seed)
    exp_r, win_rates, pfs = [], [], []

    for _ in range(n_runs):
        rand_sig = make_random_signals(sig, n_entries, long_ratio, eligible, rng)
        # Each placebo run gets its own friction seed so slippage draws vary
        # like the real world, while staying reproducible overall.
        fm = FrictionModel(symbol=symbol, seed=int(rng.integers(1, 2**31 - 1)))
        t = run_core(rand_sig, policy, symbol, friction=fm, **core_kwargs)["trades"]
        if len(t) < 5:
            continue
        r = t["r_multiple"].dropna()
        if len(r) == 0:
            continue
        exp_r.append(float(r.mean()))
        win_rates.append(float((t["pnl"] > 0).mean()))
        gains = t.loc[t["pnl"] > 0, "pnl"].sum()
        losses = -t.loc[t["pnl"] < 0, "pnl"].sum()
        pfs.append(float(gains / losses) if losses > 0 else np.nan)

    if not exp_r:
        return {"n_runs": 0}
    exp_r = np.array(exp_r)
    return {
        "n_runs": len(exp_r),
        "expectancy_r_mean": float(exp_r.mean()),
        "expectancy_r_std": float(exp_r.std(ddof=1)),
        "expectancy_r_p05": float(np.percentile(exp_r, 5)),
        "expectancy_r_p95": float(np.percentile(exp_r, 95)),
        "win_rate_mean": float(np.mean(win_rates)),
        "profit_factor_mean": float(np.nanmean(pfs)),
    }


def percentile_of(value: float, placebo: dict, samples: np.ndarray | None = None) -> float:
    """Where the real result falls in the placebo distribution.

    >0.95 means the strategy beat 95% of random-entry runs using the same
    exits — the only framing in which an exit-driven improvement counts as
    evidence of signal.
    """
    if placebo.get("n_runs", 0) == 0:
        return float("nan")
    mu, sd = placebo["expectancy_r_mean"], placebo["expectancy_r_std"]
    if sd <= 0:
        return float("nan")
    from scipy import stats
    return float(stats.norm.cdf((value - mu) / sd))


def buy_and_hold(df: pd.DataFrame, periods_per_year: float) -> dict:
    """Benchmark: simply holding the instrument over the same window."""
    px = df["close"].dropna()
    if len(px) < 2:
        return {}
    rets = px.pct_change().dropna()
    years = (px.index[-1] - px.index[0]).days / 365.25
    total = float(px.iloc[-1] / px.iloc[0] - 1)
    cagr = float((px.iloc[-1] / px.iloc[0]) ** (1 / years) - 1) if years > 0 else np.nan
    sharpe = float(np.sqrt(periods_per_year) * rets.mean() / rets.std(ddof=1)) if rets.std(ddof=1) > 0 else 0.0
    dd = float((px / px.cummax() - 1).min())
    return {"total_return_pct": total, "cagr": cagr, "sharpe": sharpe, "max_drawdown_pct": dd}
