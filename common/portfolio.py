"""Risk-managed exposure: volatility targeting, trend gating, and the
volatility-matched control that decides whether either is real.

Why this module exists: fourteen rounds of testing showed these markets do
not give up alpha to price-pattern signals. What DID work was an
unoptimised trend filter cutting BTCUSD's max drawdown from -93% to -70%.
So the goal moved from forecasting returns to managing exposure.

THE TRAP THIS MODULE IS BUILT AROUND: a trend filter sits in cash ~30% of
the time, and holding less trivially reduces drawdown. That is not skill.
The honest benchmark is therefore `vol_matched_benchmark` -- static
buy-and-hold levered to the SAME realised volatility. If dynamic sizing
cannot beat static sizing at matched risk, it adds nothing, and the
drawdown reduction was just reduced exposure wearing a costume.

No-look-ahead contract: every signal and every volatility estimate is
`.shift(1)`-ed, so the weight applied to day t is computed only from data
through day t-1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def realized_vol(returns: pd.Series, window: int = 20, periods_per_year: int = TRADING_DAYS) -> pd.Series:
    """Annualised trailing realised volatility, known as of the PREVIOUS bar."""
    return returns.rolling(window).std(ddof=1).shift(1) * np.sqrt(periods_per_year)


def vol_target_weights(returns: pd.Series, target_vol: float = 0.15, window: int = 20,
                       max_leverage: float = 1.0) -> pd.Series:
    """Scale exposure by target_vol / realised_vol, capped at max_leverage.

    Volatility is strongly autocorrelated while returns are not, so scaling
    by recent volatility raises risk-adjusted return without forecasting
    direction at all (Moreira & Muir, Volatility-Managed Portfolios, JF 2017).
    """
    rv = realized_vol(returns, window)
    w = (target_vol / rv).replace([np.inf, -np.inf], np.nan)
    return w.clip(upper=max_leverage).fillna(0.0)


def trend_gate(prices: pd.Series, window: int = 200) -> pd.Series:
    """1.0 when price closed above its moving average on the PREVIOUS bar, else 0."""
    ma = prices.rolling(window).mean()
    return (prices > ma).shift(1).fillna(False).astype(float)


def inverse_vol_weights(returns: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Risk-parity style allocation: weight each asset by 1/volatility, normalised."""
    rv = returns.rolling(window).std(ddof=1).shift(1)
    inv = (1.0 / rv).replace([np.inf, -np.inf], np.nan)
    return inv.div(inv.sum(axis=1), axis=0).fillna(0.0)


def apply_weights(returns: pd.Series | pd.DataFrame, weights: pd.Series | pd.DataFrame,
                  cost_per_unit_turnover: float | pd.Series = 0.0) -> pd.Series:
    """Portfolio returns from weights, charging real cost on turnover only.

    `weights` are the exposures intended for each bar (already shifted by the
    functions above). Turnover cost is charged on |Δweight| — a low-turnover
    design pays the spread a handful of times a year rather than per trade,
    which is exactly why instruments disqualified on per-trade cost grounds
    (e.g. silver at 53% spread/ATR) become viable as portfolio holdings.
    """
    if isinstance(returns, pd.DataFrame):
        gross = (returns * weights).sum(axis=1)
        turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    else:
        gross = returns * weights
        turnover = weights.diff().abs().fillna(weights.abs())
    return gross - turnover * cost_per_unit_turnover


def vol_matched_benchmark(bh_returns: pd.Series, strategy_returns: pd.Series) -> pd.Series:
    """THE decisive control: static buy-and-hold levered to the strategy's own
    realised volatility.

    Any dynamic exposure rule reduces drawdown simply by being smaller on
    average. Comparing it to raw buy-and-hold flatters it. This scales
    buy-and-hold by a single constant so both have identical realised
    volatility over the whole sample, isolating the question that matters:
    does *timing* the exposure beat merely *sizing* it?
    """
    sv, bv = strategy_returns.std(ddof=1), bh_returns.std(ddof=1)
    if bv <= 0:
        return bh_returns * 0.0
    return bh_returns * (sv / bv)


def random_gate_placebo(returns: pd.Series, time_in_market: float, n_runs: int = 200,
                        cost_per_unit_turnover: float = 0.0, seed: int = 7) -> dict:
    """Control for gating: same fraction of days in the market, randomly chosen.

    Distinguishes "this rule picks good days" from "being out of the market
    sometimes is good".
    """
    rng = np.random.default_rng(seed)
    n = len(returns)
    k = int(round(time_in_market * n))
    sharpes, dds = [], []
    for _ in range(n_runs):
        w = pd.Series(0.0, index=returns.index)
        if k > 0:
            w.iloc[rng.choice(n, size=min(k, n), replace=False)] = 1.0
        r = apply_weights(returns, w, cost_per_unit_turnover)
        sd = r.std(ddof=1)
        sharpes.append(np.sqrt(TRADING_DAYS) * r.mean() / sd if sd > 0 else 0.0)
        eq = (1 + r).cumprod()
        dds.append(float((eq / eq.cummax() - 1).min()))
    return {"n_runs": n_runs, "sharpe_mean": float(np.mean(sharpes)),
            "sharpe_std": float(np.std(sharpes, ddof=1)),
            "max_dd_mean": float(np.mean(dds))}


def effective_sample_size(n: int, avg_correlation: float) -> float:
    """How many INDEPENDENT observations a set of n correlated ones is worth.

    Testing a rule on 30 crypto pairs is not 30 independent tests: they share
    a dominant BTC factor. For n estimates with average pairwise correlation
    rho, the variance of their mean behaves like that of
    `n / (1 + (n-1)*rho)` independent estimates. At rho=0.7 and n=30 that is
    under 2 — which is why a "25 of 30 assets improved" headline must be
    reported next to this number rather than on its own.
    """
    if n <= 1:
        return float(n)
    rho = float(np.clip(avg_correlation, 0.0, 0.999))
    return float(n / (1.0 + (n - 1) * rho))


def average_pairwise_correlation(returns: pd.DataFrame) -> float:
    c = returns.corr().to_numpy()
    iu = np.triu_indices_from(c, k=1)
    vals = c[iu]
    vals = vals[np.isfinite(vals)]
    return float(vals.mean()) if len(vals) else 0.0


def block_bootstrap_ci(returns: pd.Series, stat_fn, block_size: int = 63,
                       n_boot: int = 1000, alpha: float = 0.05,
                       seed: int = 17) -> tuple[float, float]:
    """Confidence interval that respects serial dependence.

    Financial returns are not i.i.d. — volatility clusters — so an ordinary
    bootstrap that resamples single days destroys exactly the structure this
    strategy trades and returns intervals that are far too narrow. Resampling
    contiguous blocks (default ~one quarter) preserves it. Time blocks are
    the genuinely independent dimension here, which is why this is the right
    CI to quote rather than one taken across correlated assets.
    """
    rng = np.random.default_rng(seed)
    r = returns.dropna().to_numpy()
    n = len(r)
    if n < block_size * 2:
        return (np.nan, np.nan)
    n_blocks = int(np.ceil(n / block_size))
    starts_pool = np.arange(0, n - block_size + 1)
    stats = []
    for _ in range(n_boot):
        starts = rng.choice(starts_pool, size=n_blocks, replace=True)
        sample = np.concatenate([r[s:s + block_size] for s in starts])[:n]
        stats.append(stat_fn(pd.Series(sample)))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def ulcer_index(equity: pd.Series) -> float:
    """RMS drawdown — penalises deep AND long drawdowns, unlike max DD alone."""
    dd = equity / equity.cummax() - 1.0
    return float(np.sqrt((dd ** 2).mean()))


def summarize(returns: pd.Series, weights: pd.Series | pd.DataFrame | None = None,
              periods_per_year: int = TRADING_DAYS) -> dict:
    r = returns.dropna()
    if len(r) < 2:
        return {}
    eq = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    sd = r.std(ddof=1)
    dd = float((eq / eq.cummax() - 1).min())
    cagr = float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 and eq.iloc[-1] > 0 else np.nan
    sharpe = float(np.sqrt(periods_per_year) * r.mean() / sd) if sd > 0 else 0.0
    out = {
        "cagr": cagr, "sharpe": sharpe, "vol": float(sd * np.sqrt(periods_per_year)),
        "max_drawdown": dd, "calmar": float(cagr / abs(dd)) if dd else np.nan,
        "ulcer": ulcer_index(eq), "sharpe_per_dd": float(sharpe / abs(dd)) if dd else np.nan,
        "total_return": float(eq.iloc[-1] - 1),
    }
    if weights is not None:
        w = weights.sum(axis=1) if isinstance(weights, pd.DataFrame) else weights
        out["time_in_market"] = float((w > 0).mean())
        out["avg_exposure"] = float(w.mean())
        turn = (weights.diff().abs().sum(axis=1) if isinstance(weights, pd.DataFrame)
                else weights.diff().abs())
        out["annual_turnover"] = float(turn.sum() / years) if years > 0 else np.nan
    return out
