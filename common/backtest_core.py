"""Shared bar-loop backtester driven by a signal frame plus an ExitPolicy.

Both strategies (and the random-entry placebo) produce a signal frame and
run it through this identical core, which is what makes the placebo
comparison valid: strategy and control differ ONLY in which bars they enter
on, never in how positions are managed or costed.

Signal frame must carry: open, high, low, close, atr, spread (optional),
long_signal, short_signal. Fills execute at the NEXT bar's open, exits at
the level reached, both crossing the spread against the trader.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .costs import FrictionModel
from .exits import ExitPolicy, Position, open_position, process_bar


def run(sig: pd.DataFrame, policy: ExitPolicy, symbol: str,
        starting_equity: float = 10_000.0, risk_pct: float = 0.005,
        friction: FrictionModel | None = None,
        max_trades_per_day: int | None = None,
        max_cost_ratio: float = 0.20,
        max_leverage: float = 50.0,
        entry_mode: str = "market",
        limit_offset_atr: float = 0.0,
        limit_max_wait: int = 3,
        direction: str = "both") -> dict:
    """`max_cost_ratio` skips setups whose round-trip execution cost exceeds
    that fraction of the intended risk; `max_leverage` caps notional exposure
    at a multiple of equity. Both guard the ATR-collapse blow-up described in
    the entry block below."""
    if friction is None:
        friction = FrictionModel(symbol=symbol)

    equity = starting_equity
    pos: Position | None = None
    trades: list[dict] = []
    trades_today, current_day = 0, None
    # Resting limit order: {side, price, expires_at_bar}. A mean-reversion
    # entry is a natural limit order -- you are buying a dip, so you can rest
    # a bid instead of crossing the spread. That converts the half-spread
    # from a cost into a saving. The catch is adverse selection: you only get
    # filled when price keeps coming toward you, so unfilled setups are the
    # ones that ran away without you. Both effects are modelled here --
    # unfilled orders simply expire and are counted as trades not taken.
    pending: dict | None = None
    n_signals = n_filled = 0

    idx = sig.index
    has_spread = "spread" in sig.columns
    # Column-wise numpy access: the loop runs millions of iterations and
    # DataFrame.iloc per bar is the dominant cost.
    o = sig["open"].to_numpy(); h = sig["high"].to_numpy()
    lo = sig["low"].to_numpy(); c = sig["close"].to_numpy()
    a = sig["atr"].to_numpy()
    spr = sig["spread"].to_numpy() if has_spread else np.full(len(sig), np.nan)
    ls = sig["long_signal"].to_numpy(); ss = sig["short_signal"].to_numpy()

    def close_leg(level, fraction, ts, bar_spread, reason):
        nonlocal equity
        exit_price = level - pos.side * friction.half_spread(ts, level, bar_spread)
        qty = pos.size * fraction
        pnl = pos.side * (exit_price - pos.entry_price) * qty
        pnl -= friction.commission(abs(qty * exit_price))
        pos.realized_pnl += pnl
        equity += pnl
        return reason

    for i in range(len(idx) - 1):
        ts_next = idx[i + 1]
        day = idx[i].normalize()
        if day != current_day:
            current_day, trades_today = day, 0

        if pos is not None:
            bar = {"high": h[i + 1], "low": lo[i + 1], "close": c[i + 1]}
            legs = process_bar(pos, bar, ts_next, policy)
            last_reason = None
            for leg in legs:
                last_reason = close_leg(leg["level"], leg["fraction"], ts_next,
                                        spr[i + 1], leg["reason"])
            if pos.remaining <= 1e-9:
                trades.append({
                    "entry_ts": pos.entry_ts, "exit_ts": ts_next, "side": pos.side,
                    "entry_price": pos.entry_price, "size": pos.size,
                    "bars_held": pos.bars_held, "exit_reason": last_reason,
                    "pnl": pos.realized_pnl,
                    "return": pos.realized_pnl / pos.equity_at_entry,
                    "risk_amount": pos.risk_amount,
                    "r_multiple": (pos.realized_pnl / pos.risk_amount
                                   if pos.risk_amount > 0 else np.nan),
                })
                pos = None
            continue

        # --- resting limit order: check for a fill on the next bar ---
        if pending is not None:
            nb_low, nb_high = lo[i + 1], h[i + 1]
            hit = (pending["side"] == 1 and nb_low <= pending["price"]) or \
                  (pending["side"] == -1 and nb_high >= pending["price"])
            if hit:
                side = pending["side"]
                atr_e = pending["atr"]
                # Filled AT the limit: no spread crossed. Slippage is still
                # charged (a limit can be filled and then keep going), but the
                # half-spread that a market order pays is saved.
                entry_price = pending["price"] + friction.slippage(
                    pending["price"], atr_e, side)
                risk_per_unit = policy.stop_atr * atr_e
                risk_amount = risk_pct * equity
                size = risk_amount / risk_per_unit
                if entry_price > 0 and size * entry_price > max_leverage * equity:
                    size = (max_leverage * equity) / entry_price
                    risk_amount = size * risk_per_unit
                pos = open_position(side, entry_price, atr_e, policy, size,
                                    ts_next, equity, risk_amount)
                trades_today += 1
                n_filled += 1
                pending = None
                continue
            if i + 1 >= pending["expires"]:
                pending = None      # unfilled: the setup ran away without us
            else:
                continue

        if max_trades_per_day is not None and trades_today >= max_trades_per_day:
            continue
        atr_now = a[i]
        if not np.isfinite(atr_now) or atr_now <= 0:
            continue

        side = 1 if ls[i] else (-1 if ss[i] else 0)
        if side == 0:
            continue
        if (direction == "long_only" and side != 1) or \
           (direction == "short_only" and side != -1):
            continue

        # --- COST GUARD (added after a real defect) ---------------------
        # Position size is risk_amount / (stop_atr * ATR). When ATR collapses
        # relative to the spread -- e.g. FX majors on H1 during quiet Asian
        # hours, where ATR approaches zero while the broker's spread sits at
        # its floor -- size explodes, and the fixed spread on that oversized
        # position costs many R despite a nominal 1R stop. Cross-sectional
        # testing surfaced losses of -18R to -21R per trade from exactly this.
        # Skip any setup where round-trip execution cost is a large fraction
        # of the intended risk; such a trade cannot win even if the signal is
        # right.
        risk_per_unit = policy.stop_atr * atr_now
        bar_spread = spr[i + 1] if has_spread else np.nan
        est_spread = (bar_spread if np.isfinite(bar_spread) and bar_spread > 0
                      else 2 * friction.half_spread(ts_next, o[i + 1], None))
        est_cost = est_spread + 2 * friction.slippage_atr_frac * atr_now
        if risk_per_unit <= 0 or est_cost / risk_per_unit > max_cost_ratio:
            continue

        n_signals += 1
        if entry_mode == "limit":
            # Rest the order at the signal bar's close, optionally offset
            # further in our favour. Resting AT the close means we trade only
            # if price comes back to us -- the essence of a patient
            # mean-reversion entry, and the reason the half-spread becomes a
            # saving rather than a cost.
            pending = {"side": side, "atr": atr_now,
                       "price": c[i] - side * limit_offset_atr * atr_now,
                       "expires": i + 1 + limit_max_wait}
            continue

        entry_price = friction.apply_fill(ts_next, o[i + 1], atr_now, side,
                                          bar_spread=spr[i + 1] if has_spread else None)
        n_filled += 1
        risk_amount = risk_pct * equity
        size = risk_amount / risk_per_unit
        # Second guard: cap notional exposure. Even with an acceptable cost
        # ratio, a small ATR can imply a position far larger than the account
        # could actually carry at the broker's margin requirement.
        if entry_price > 0 and size * entry_price > max_leverage * equity:
            size = (max_leverage * equity) / entry_price
            risk_amount = size * risk_per_unit

        pos = open_position(side, entry_price, atr_now, policy, size, ts_next,
                            equity, risk_amount)
        trades_today += 1

    return {"trades": pd.DataFrame(trades), "final_equity": equity,
            "n_signals": n_signals, "n_filled": n_filled,
            "fill_rate": (n_filled / n_signals) if n_signals else np.nan}
