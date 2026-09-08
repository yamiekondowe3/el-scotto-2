"""Data ingestion: MT5 export (whatever depth the broker provides) plus
external backfill adapters, all normalized to a common OHLCV schema and
written to partitioned Parquet.

Honesty requirement (per project plan): every fetch logs and returns the
REAL achieved date range for that symbol. Never assume 16 years is
available -- report what actually came back.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).resolve().parent.parent / "data_cache"

# `spread` is the broker's REAL recorded spread for that bar, converted from
# MT5's integer points into price units. Carrying it means the backtest can
# charge the actual historical spread instead of a guessed bps constant --
# this matters enormously: a fabricated 1.2bps spread on XAUUSD is ~5x the
# broker's real ~18-point ($0.18) spread.
SCHEMA_COLUMNS = ["open", "high", "low", "close", "volume", "spread"]

# Canonical asset name -> actual broker symbol name, confirmed against the
# connected Deriv-Demo account (798 symbols enumerated). Broker symbol
# spelling is NOT standardized (e.g. "US Oil" with a space, not "USOIL") --
# always resolve through this map rather than assuming the canonical name
# is tradable as-is. Confirmed real D1 history depth as of this check:
#   XAUUSD  -> XAUUSD   2011-01-02..2026-09-03 (~15.7y)
#   XAGUSD  -> XAGUSD   2011-01-02..2026-09-03 (~15.7y)
#   USOIL   -> US Oil   2024-01-22..2026-09-03 (~2.6y only -- far short of 16y)
#   BTCUSD  -> BTCUSD   2011-03-23..2026-09-03 (~15.4y)
#   ETHUSD  -> ETHUSD   2015-08-07..2026-09-03 (~11.1y)
BROKER_SYMBOL_MAP = {
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "USOIL": "US Oil",
    "BTCUSD": "BTCUSD",
    "ETHUSD": "ETHUSD",
}

# MT5 timeframe constants are looked up lazily (import MetaTrader5 only
# inside functions that need it) so this module stays importable in
# environments/tests without the MT5 terminal installed.
MT5_TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1", "D1": "TIMEFRAME_D1",
}


def _normalize(df: pd.DataFrame, ts_col: str, unit: str | None, venue: str) -> pd.DataFrame:
    out = df.rename(columns={c: c.lower() for c in df.columns})
    if unit:
        out["timestamp"] = pd.to_datetime(out[ts_col], unit=unit, utc=True)
    else:
        out["timestamp"] = pd.to_datetime(out[ts_col], utc=True)
    out = out.set_index("timestamp").sort_index()
    for col in SCHEMA_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[SCHEMA_COLUMNS]
    out.attrs["venue"] = venue
    return out


def fetch_mt5(symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Pull whatever history the connected MT5 terminal/broker has for
    `symbol` (a canonical name, e.g. "USOIL" -- resolved through
    BROKER_SYMBOL_MAP to this broker's actual spelling, e.g. "US Oil").
    Returns an empty frame (with a logged warning) if the symbol isn't
    found or MT5 isn't reachable -- callers must handle that, not assume
    success.
    """
    import MetaTrader5 as mt5

    broker_symbol = BROKER_SYMBOL_MAP.get(symbol, symbol)

    if not mt5.initialize():
        logger.warning("MT5 initialize() failed: %s", mt5.last_error())
        return _normalize(pd.DataFrame(), "timestamp", None, venue=f"mt5:unknown")

    try:
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            logger.warning("Symbol %s (broker name %r) not found on this broker", symbol, broker_symbol)
            return _normalize(pd.DataFrame(), "timestamp", None, venue="mt5:unknown")
        if not info.visible:
            mt5.symbol_select(broker_symbol, True)

        tf_const = getattr(mt5, MT5_TIMEFRAMES[timeframe])
        rates = mt5.copy_rates_range(broker_symbol, tf_const, start.to_pydatetime(), end.to_pydatetime())
        if rates is None or len(rates) == 0:
            logger.warning("No rates returned for %s (broker name %r) %s in requested range", symbol, broker_symbol, timeframe)
            return _normalize(pd.DataFrame(), "timestamp", None, venue="mt5:unknown")

        df = pd.DataFrame(rates)
        df = df.rename(columns={"tick_volume": "volume"})
        # MT5 reports spread in integer POINTS -- convert to price units using
        # the symbol's own point size so the backtest can charge the real
        # historical spread rather than a guessed constant.
        if "spread" in df.columns:
            df["spread"] = df["spread"] * info.point
        acc = mt5.account_info()
        venue = f"mt5:{acc.server if acc else 'unknown'}"
        normalized = _normalize(df, "time", "s", venue=venue)
        achieved_start, achieved_end = normalized.index.min(), normalized.index.max()
        logger.info(
            "MT5 %s %s: requested %s..%s, achieved %s..%s (%d bars)",
            symbol, timeframe, start, end, achieved_start, achieved_end, len(normalized),
        )
        return normalized
    finally:
        mt5.shutdown()


def save_parquet(df: pd.DataFrame, symbol: str, timeframe: str, root: Path | None = None) -> Path:
    root = root or DATA_ROOT
    out_dir = root / symbol / timeframe
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{symbol}_{timeframe}.parquet"
    df.to_parquet(out_path)
    return out_path


def load_parquet(symbol: str, timeframe: str, root: Path | None = None) -> pd.DataFrame:
    root = root or DATA_ROOT
    path = root / symbol / timeframe / f"{symbol}_{timeframe}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No cached data at {path} -- run a fetch first")
    return pd.read_parquet(path)


def report_coverage(df: pd.DataFrame, symbol: str) -> dict:
    """The honest-coverage deliverable: what date range did we actually get."""
    if df.empty:
        return {"symbol": symbol, "achieved_start": None, "achieved_end": None,
                "n_bars": 0, "years_covered": 0.0}
    start, end = df.index.min(), df.index.max()
    years = (end - start).days / 365.25
    return {
        "symbol": symbol, "achieved_start": str(start), "achieved_end": str(end),
        "n_bars": len(df), "years_covered": round(years, 2),
        "venue": df.attrs.get("venue", "unknown"),
    }
