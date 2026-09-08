"""Realistic friction models: commissions, spread, slippage, latency.

CALIBRATION NOTE (important -- this module was badly wrong once):
The first version of this file invented plausible-sounding basis-point
constants for commission and spread. On XAUUSD those fabricated numbers
charged ~$34 per round trip against a $50 risk budget -- 68% of the
risked amount -- which is roughly 7x the broker's real cost and would
bury ANY strategy regardless of its merit. Every "no edge" conclusion
produced under that model was an artifact of the model, not the strategy.

The rule now: costs come from the BROKER'S OWN reported terms wherever
possible --
  * spread   -> the real per-bar spread MT5 records in its rate data
                (`spread` column, already converted to price units by
                common.data_fetch), falling back to a symbol default only
                when that column is absent.
  * commission-> per-asset-class, defaulting to ZERO for spread-only CFD
                brokers like Deriv. Set it explicitly if your broker
                actually charges one; do not guess.
  * slippage -> still a model (nobody publishes realized slippage), kept
                deliberately modest and ATR-scaled, and exposed so it can
                be stress-tested upward.
If you change brokers, re-derive these from that broker's contract
specs before trusting a single backtest number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
import numpy as np
import pandas as pd

ASSET_CLASS = {
    "XAUUSD": "metals", "XAGUSD": "metals",
    "USOIL": "energy",
    "BTCUSD": "crypto", "ETHUSD": "crypto",
}

# Commission per side, in basis points of notional, by asset class.
# ZERO by default: the connected broker (Deriv) prices its CFDs
# spread-only and charges no separate commission on these symbols
# (account_info reports commission_blocked = 0.0). Override per broker.
COMMISSION_BPS = {
    "metals": 0.0,
    "energy": 0.0,
    "crypto": 0.0,
}

# Fallback HALF-spread in price terms, used ONLY when the bar carries no
# real `spread` value. Derived from the broker's own quoted spreads
# (XAUUSD ~18 points = $0.18 full spread -> $0.09 half).
FALLBACK_HALF_SPREAD = {
    "metals": 0.09,
    "energy": 0.02,
    "crypto": 2.0,
}

# Multiplier applied to the recorded spread during known-illiquid windows.
# The recorded per-bar spread already widens naturally in real data, so
# this is a mild stress factor rather than the 4x guess used before.
WIDE_SPREAD_WINDOWS = [
    (time(21, 55), time(22, 10)),  # FX/CFD daily rollover ~22:00 UTC
]
WIDE_SPREAD_MULT = 1.5


# Overnight financing (swap) as an ANNUAL FRACTION OF NOTIONAL, per direction,
# measured from the connected broker via MT5 symbol_info on 2026-09-08.
#
# Two swap_mode conventions are in play and they must not be confused:
#   mode 1 (SYMBOL_SWAP_MODE_POINTS) -- metals. Money per lot per night is
#       swap_points * point * contract_size; divide by contract_size * price
#       for the rate. XAUUSD: -45 * 0.01 * 100 = -$45/night on $439,592
#       notional = -3.74%/yr.
#   mode 5 (SYMBOL_SWAP_MODE_INTEREST_CURRENT) -- crypto. The value IS the
#       annual percent already.
#
# The mode-5 reading was confirmed by elimination rather than assumed, because
# the entire venue conclusion rests on it: read as points instead, BTCUSD swap
# would be -0.00928%/yr, i.e. borrowing bitcoin for under one basis point a
# year, which no venue offers. The mode-1 metals rates computed independently
# land at 3.74% and 5.76% -- USD rates plus a spread, exactly where financing
# should sit -- which corroborates the arithmetic on both paths.
#
# Note crypto is charged in BOTH directions: -20%/yr long AND short. That is a
# borrow charge, not a rate differential, and it cannot be escaped by flipping
# direction. Gold, by contrast, PAYS +1.66%/yr to shorts.
FINANCING_ANNUAL = {
    "XAUUSD": {"long": -0.0374, "short": +0.0166},
    "XAGUSD": {"long": -0.0576, "short": +0.0175},
    "BTCUSD": {"long": -0.2000, "short": -0.2000},
    "ETHUSD": {"long": -0.1500, "short": -0.1500},
}


def financing_cost(symbol: str, notional: float, days: float = 1.0,
                   direction: str = "long") -> float:
    """Overnight financing on `notional` held for `days`, in account currency.

    Returns a SIGNED amount to add to P&L: negative is a cost. Unknown symbols
    return 0.0 rather than guessing -- an invented financing rate would repeat
    the fabricated-cost mistake documented at the top of this module.
    """
    rate = FINANCING_ANNUAL.get(symbol, {}).get(direction)
    if rate is None or notional == 0 or days == 0:
        return 0.0
    return float(rate) * abs(notional) * (days / 365.0)


@dataclass
class FrictionModel:
    symbol: str
    asset_class: str = field(init=False)
    slippage_atr_frac: float = 0.02   # mean slippage as a fraction of ATR, per side
    latency_ms_range: tuple[int, int] = (50, 250)
    commission_bps_override: float | None = None
    # Seeded by default for REPRODUCIBILITY. This was an unseeded
    # `default_rng()` and it mattered: on a 21-trade M15 sample the same
    # configuration produced +0.018R on one run and -0.077R on the next,
    # purely from different slippage draws. Backtests must be deterministic
    # or results cannot be compared at all; vary the seed deliberately when
    # you want a slippage sensitivity distribution, never by accident.
    seed: int = 20260904
    rng: np.random.Generator = field(default=None)
    # Diagnostic mode: charge NOTHING. Used to measure a signal's ceiling --
    # if a strategy cannot clear the bar even with execution costs removed
    # entirely, no execution or venue improvement can ever get it there, and
    # further work on costs is pointless. Never use for a tradable result.
    frictionless: bool = False

    def __post_init__(self):
        self.asset_class = ASSET_CLASS.get(self.symbol, "metals")
        if self.rng is None:
            self.rng = np.random.default_rng(self.seed)

    def commission(self, notional: float) -> float:
        """Per-side commission in account currency. Zero for spread-only brokers."""
        if self.frictionless:
            return 0.0
        bps = self.commission_bps_override
        if bps is None:
            bps = COMMISSION_BPS[self.asset_class]
        return notional * bps / 1e4

    def half_spread(self, ts: pd.Timestamp, price: float, bar_spread: float | None = None) -> float:
        """Half-spread in price terms.

        Prefers the broker's REAL recorded spread for that bar; falls back
        to a symbol default only when unavailable.
        """
        if self.frictionless:
            return 0.0
        if bar_spread is not None and np.isfinite(bar_spread) and bar_spread > 0:
            hs = bar_spread / 2.0
        else:
            hs = FALLBACK_HALF_SPREAD[self.asset_class]
        t = ts.time()
        for start, end in WIDE_SPREAD_WINDOWS:
            if start <= t <= end:
                hs *= WIDE_SPREAD_MULT
                break
        return hs

    def slippage(self, price: float, atr: float, side: int) -> float:
        """Variable slippage scaled to ATR (volatility), not a fixed pip amount.

        side: +1 for buys (slippage worsens the fill upward), -1 for sells.
        Magnitude is a volatility-scaled half-normal draw, so spikes
        occasionally produce much worse fills -- matching real behavior
        around breakouts and news.
        """
        if self.frictionless or atr <= 0 or not np.isfinite(atr):
            return 0.0
        magnitude = abs(self.rng.normal(loc=self.slippage_atr_frac, scale=self.slippage_atr_frac)) * atr
        return side * magnitude

    def latency_bars(self, bar_seconds: int) -> int:
        """Execution delay expressed in whole bars, given a 50-250ms latency draw."""
        ms = self.rng.uniform(*self.latency_ms_range)
        return int(np.ceil((ms / 1000.0) / bar_seconds)) if bar_seconds else 0

    def apply_fill(self, ts: pd.Timestamp, signal_price: float, atr: float, side: int,
                   bar_spread: float | None = None) -> float:
        """Full fill-price model: signal price -> spread -> slippage.

        side: +1 buy, -1 sell. Commission (if any) is charged separately in
        account currency via commission().
        """
        hs = self.half_spread(ts, signal_price, bar_spread)
        slip = self.slippage(signal_price, atr, side)
        return signal_price + side * hs + slip
