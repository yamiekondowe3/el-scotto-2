"""No-look-ahead VWAP, RSI, ATR, and pivot-point math.

Every function here operates strictly on CLOSED bars. Anything computed
"as of" bar t must not use information only available after bar t closes --
this is the #1 documented risk in the fused strategies (see the research
docs' "look-ahead traps" sections).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def anchored_vwap(df: pd.DataFrame, anchor_mask: pd.Series) -> pd.Series:
    """Session/anchor-reset VWAP.

    df must have columns: high, low, close, volume, indexed by timestamp.
    anchor_mask is True on bars where the VWAP resets (e.g. first bar of a
    new UTC day). Uses typical price * volume, cumulative within each
    anchor segment -- never reaches across a reset boundary.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = typical * df["volume"]
    segment = anchor_mask.cumsum()
    cum_tpv = tpv.groupby(segment).cumsum()
    cum_vol = df["volume"].groupby(segment).cumsum().replace(0, np.nan)
    return cum_tpv / cum_vol


def vwap_bands(df: pd.DataFrame, vwap: pd.Series, anchor_mask: pd.Series, mult: float = 1.0) -> tuple[pd.Series, pd.Series]:
    """Upper/lower VWAP standard-deviation bands, computed within each anchor segment."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    segment = anchor_mask.cumsum()
    sq_dev = (typical - vwap) ** 2 * df["volume"]
    cum_sq = sq_dev.groupby(segment).cumsum()
    cum_vol = df["volume"].groupby(segment).cumsum().replace(0, np.nan)
    std = np.sqrt(cum_sq / cum_vol)
    return vwap + mult * std, vwap - mult * std


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def standard_pivots(prior_high: float, prior_low: float, prior_close: float) -> dict:
    """Floor-trader pivots from a fully-closed PRIOR period's H/L/C only."""
    p = (prior_high + prior_low + prior_close) / 3.0
    r1 = 2 * p - prior_low
    s1 = 2 * p - prior_high
    r2 = p + (prior_high - prior_low)
    s2 = p - (prior_high - prior_low)
    r3 = prior_high + 2 * (p - prior_low)
    s3 = prior_low - 2 * (prior_high - p)
    return {"P": p, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3}


def camarilla_pivots(prior_high: float, prior_low: float, prior_close: float) -> dict:
    r = prior_high - prior_low
    c = prior_close
    return {
        "R4": c + r * 1.1 / 2, "R3": c + r * 1.1 / 4, "R2": c + r * 1.1 / 6, "R1": c + r * 1.1 / 12,
        "S1": c - r * 1.1 / 12, "S2": c - r * 1.1 / 6, "S3": c - r * 1.1 / 4, "S4": c - r * 1.1 / 2,
    }


def daily_prior_hlc(df: pd.DataFrame, day_boundary_hour_utc: int = 0) -> pd.DataFrame:
    """Return a per-bar-aligned frame of the PRIOR completed day's H/L/C.

    day_boundary_hour_utc: the UTC hour the trading "day" rolls at (0 for
    crypto's 00:00 UTC convention, 17 for the FX 17:00 ET convention
    expressed in UTC as appropriate for the instrument -- pass the correct
    boundary per asset, do not assume midnight everywhere).
    """
    idx = df.index
    day_key = (idx - pd.Timedelta(hours=day_boundary_hour_utc)).floor("D")
    daily = df.groupby(day_key).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    daily.index.name = "day"
    prior = daily.shift(1)
    aligned = prior.reindex(day_key).set_index(idx)
    return aligned.rename(columns={"high": "prior_high", "low": "prior_low", "close": "prior_close"})
