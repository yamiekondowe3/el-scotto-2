"""Exit management -- the lever that was never tested.

Until now both engines had exactly one exit style: a fixed ATR stop and a
fixed ATR target. That is a 1:1-ish payoff by construction, which is why
every previous analysis concluded a ~56.5% win rate was required. That
threshold is payoff-conditional: PF > 1.3 needs only 46.4% at a 1.5:1
payoff, or 39.4% at 2:1. Asymmetric exits change the arithmetic, and the
ORB research document explicitly recommended "scale out 50% at T1, trail
the remainder" -- which was never implemented.

NO-LOOK-AHEAD CONTRACT: the stop in force during bar t is computed only
from information through bar t-1. `process_bar` therefore evaluates exits
against the CURRENT stop first, and only afterwards folds bar t's extreme
into the trailing calculation for use on bar t+1.

Conservative fill convention (unchanged from the original engines): if both
the stop and the target are touched inside one bar, the STOP is assumed to
have filled first.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ExitPolicy:
    """One pre-registered exit mechanism. `mode` selects the behaviour."""
    mode: str = "fixed"          # fixed | trail | scale_trail | be_target | time
    stop_atr: float = 2.0
    target_atr: float | None = 2.0
    trail_atr: float | None = None      # chandelier distance from the run-up extreme
    scale_frac: float = 0.5             # fraction closed at the scale-out level
    scale_at_r: float = 1.0             # scale out once this many R of profit is reached
    be_at_r: float | None = None        # move stop to entry after this many R
    max_bars: int | None = None         # force close after this many bars held
    session_end_hour: int | None = None  # force close at this UTC hour

    def describe(self) -> str:
        if self.mode == "fixed":
            return f"fixed {self.stop_atr}xATR stop / {self.target_atr}xATR target"
        if self.mode == "trail":
            return f"{self.stop_atr}xATR stop, no target, {self.trail_atr}xATR trail"
        if self.mode == "scale_trail":
            return (f"{self.stop_atr}xATR stop, {self.scale_frac:.0%} out at "
                    f"{self.scale_at_r}R, trail remainder at {self.trail_atr}xATR")
        if self.mode == "be_target":
            return (f"{self.stop_atr}xATR stop, breakeven at {self.be_at_r}R, "
                    f"{self.target_atr}xATR target")
        if self.mode == "time":
            return f"{self.stop_atr}xATR stop / {self.target_atr}xATR target, timed exit"
        return self.mode


@dataclass
class Position:
    side: int                 # +1 long, -1 short
    entry_price: float
    entry_atr: float
    stop: float
    target: float | None
    size: float
    entry_ts: object
    equity_at_entry: float
    risk_amount: float
    remaining: float = 1.0    # fraction of original size still open
    extreme: float = field(default=np.nan)   # best price seen, through the PREVIOUS bar
    bars_held: int = 0
    scaled_out: bool = False
    moved_to_be: bool = False
    realized_pnl: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.initial_stop)

    initial_stop: float = field(default=np.nan)


def open_position(side, entry_price, atr, policy: ExitPolicy, size, entry_ts,
                  equity, risk_amount) -> Position:
    stop = entry_price - side * policy.stop_atr * atr
    target = None
    if policy.target_atr is not None:
        target = entry_price + side * policy.target_atr * atr
    return Position(side=side, entry_price=entry_price, entry_atr=atr, stop=stop,
                    target=target, size=size, entry_ts=entry_ts,
                    equity_at_entry=equity, risk_amount=risk_amount,
                    extreme=entry_price, initial_stop=stop)


def process_bar(pos: Position, bar, ts, policy: ExitPolicy) -> list[dict]:
    """Evaluate exits for one bar. Returns a list of {level, fraction, reason}.

    Levels are raw price levels; the caller applies spread/slippage. Position
    state (stop, extreme, remaining) is mutated in place.
    """
    exits: list[dict] = []
    pos.bars_held += 1
    risk_unit = abs(pos.entry_price - pos.initial_stop)
    if risk_unit <= 0:
        return exits

    high, low = bar["high"], bar["low"]

    # --- 1. Stop first (conservative when both stop and target are touched) ---
    hit_stop = (pos.side == 1 and low <= pos.stop) or (pos.side == -1 and high >= pos.stop)
    if hit_stop:
        exits.append({"level": pos.stop, "fraction": pos.remaining, "reason": "stop"})
        pos.remaining = 0.0
        return exits

    # --- 2. Scale-out leg ---
    if policy.mode == "scale_trail" and not pos.scaled_out:
        scale_level = pos.entry_price + pos.side * policy.scale_at_r * risk_unit
        reached = (pos.side == 1 and high >= scale_level) or (pos.side == -1 and low <= scale_level)
        if reached:
            frac = min(policy.scale_frac, pos.remaining)
            exits.append({"level": scale_level, "fraction": frac, "reason": "scale_out"})
            pos.remaining -= frac
            pos.scaled_out = True
            if pos.remaining <= 1e-9:
                return exits

    # --- 3. Fixed target ---
    if pos.target is not None:
        hit_target = (pos.side == 1 and high >= pos.target) or (pos.side == -1 and low <= pos.target)
        if hit_target:
            exits.append({"level": pos.target, "fraction": pos.remaining, "reason": "target"})
            pos.remaining = 0.0
            return exits

    # --- 4. Time-based exit ---
    if policy.mode == "time":
        expired = policy.max_bars is not None and pos.bars_held >= policy.max_bars
        at_session_end = (policy.session_end_hour is not None
                          and getattr(ts, "hour", None) == policy.session_end_hour)
        if expired or at_session_end:
            exits.append({"level": bar["close"], "fraction": pos.remaining, "reason": "time"})
            pos.remaining = 0.0
            return exits

    # --- 5. Breakeven move (checked against THIS bar's excursion, applied from next bar) ---
    if policy.be_at_r is not None and not pos.moved_to_be:
        trigger = pos.entry_price + pos.side * policy.be_at_r * risk_unit
        reached = (pos.side == 1 and high >= trigger) or (pos.side == -1 and low <= trigger)
        if reached:
            pos.stop = (max(pos.stop, pos.entry_price) if pos.side == 1
                        else min(pos.stop, pos.entry_price))
            pos.moved_to_be = True

    # --- 6. Trailing stop: fold THIS bar's extreme in, for use on the NEXT bar ---
    if policy.trail_atr is not None and (policy.mode in ("trail", "scale_trail")):
        if policy.mode == "scale_trail" and not pos.scaled_out:
            pass  # only trail the remainder after the scale-out leg is taken
        else:
            if pos.side == 1:
                pos.extreme = max(pos.extreme, high)
                pos.stop = max(pos.stop, pos.extreme - policy.trail_atr * pos.entry_atr)
            else:
                pos.extreme = min(pos.extreme, low)
                pos.stop = min(pos.stop, pos.extreme + policy.trail_atr * pos.entry_atr)

    return exits


# The five pre-registered mechanisms tested. Fixed set, not a grid to search.
POLICIES = {
    "E0_fixed": ExitPolicy(mode="fixed", stop_atr=2.0, target_atr=2.0),
    "E1_trail": ExitPolicy(mode="trail", stop_atr=2.0, target_atr=None, trail_atr=3.0),
    "E2_scale_trail": ExitPolicy(mode="scale_trail", stop_atr=2.0, target_atr=None,
                                 trail_atr=2.0, scale_frac=0.5, scale_at_r=1.0),
    "E3_be_target": ExitPolicy(mode="be_target", stop_atr=2.0, target_atr=4.0, be_at_r=1.0),
    "E4_time": ExitPolicy(mode="time", stop_atr=2.0, target_atr=2.0, max_bars=24),
}
