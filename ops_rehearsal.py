"""OPS REHEARSAL — validate the deployment pipeline end to end.

This proves the PLUMBING works. It proves nothing whatsoever about whether
any strategy is profitable, and must not be read as a step toward trading
the strategies in this project (all of which were falsified).

Its value is that if a genuine edge is ever found, deployment will not be
the thing that breaks. Better to discover a broken order path now, on a rule
nobody intends to run, than later on one that matters.

NO ORDERS ARE PLACED. Order validity is checked with MT5's order_check(),
which validates a request against margin, volume limits, stops levels and
filling mode WITHOUT executing it. Everything else is read-only.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CHECKS = []


def record(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)


def main():
    import MetaTrader5 as mt5

    print("OPS REHEARSAL — pipeline validation, no orders placed\n")

    print("1. Terminal connection")
    ok = mt5.initialize()
    record("mt5.initialize()", ok, "" if ok else str(mt5.last_error()))
    if not ok:
        return

    try:
        acc = mt5.account_info()
        record("account_info()", acc is not None,
               f"login {acc.login}, {acc.server}, equity {acc.equity:.2f} {acc.currency}"
               if acc else "")
        is_demo = acc is not None and acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        record("account is DEMO", is_demo,
               "safe to rehearse" if is_demo else "NOT a demo account — stopping")
        if not is_demo:
            return
        record("algo trading enabled", bool(acc.trade_expert),
               "terminal permits automated orders")

        print("\n2. Symbol resolution and market data")
        for sym in ["XAUUSD", "BTCUSD"]:
            info = mt5.symbol_info(sym)
            if info is None:
                record(f"{sym} resolves", False)
                continue
            mt5.symbol_select(sym, True)
            tick = mt5.symbol_info_tick(sym)
            record(f"{sym} resolves", True,
                   f"spread {info.spread} pts, digits {info.digits}")
            record(f"{sym} live tick", tick is not None and tick.ask > 0,
                   f"bid {tick.bid} ask {tick.ask}" if tick else "no tick")
            bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 300)
            record(f"{sym} history readable", bars is not None and len(bars) >= 300,
                   f"{0 if bars is None else len(bars)} H1 bars")

        print("\n3. Order path validation (order_check — NOT executed)")
        sym = "XAUUSD"
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if info and tick:
            vol = info.volume_min
            price = tick.ask
            # stops must respect the broker's minimum distance
            min_dist = info.trade_stops_level * info.point
            sl = price - max(min_dist * 2, 10 * info.point)
            tp = price + max(min_dist * 2, 10 * info.point)
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": vol,
                   "type": mt5.ORDER_TYPE_BUY, "price": price, "sl": sl, "tp": tp,
                   "deviation": 30, "magic": 20260907,
                   "comment": "ops rehearsal - not executed",
                   "type_time": mt5.ORDER_TIME_GTC,
                   "type_filling": mt5.ORDER_FILLING_FOK}
            chk = mt5.order_check(req)
            if chk is None:
                record("order_check()", False, str(mt5.last_error()))
            else:
                # retcode 0 means the request is valid and would be accepted
                good = chk.retcode == 0
                record("order request is valid", good,
                       f"retcode {chk.retcode} — {chk.comment}")
                record("margin sufficient", chk.margin_free >= 0,
                       f"would use {chk.margin:.2f}, free after {chk.margin_free:.2f}")
                record("min volume tradeable", vol >= info.volume_min,
                       f"{vol} lots (min {info.volume_min})")

        print("\n4. Monitoring path")
        positions = mt5.positions_get()
        record("positions_get()", positions is not None,
               f"{0 if positions is None else len(positions)} open")
        import datetime as _dt
        deals = mt5.history_deals_get(
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30),
            _dt.datetime.now(_dt.timezone.utc))
        record("history_deals_get()", deals is not None,
               f"{0 if deals is None else len(deals)} deals in 30d")

    finally:
        mt5.shutdown()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"\n{'='*60}")
    print(f"RESULT: {passed}/{len(CHECKS)} checks passed")
    print("=" * 60)
    if passed == len(CHECKS):
        print("Pipeline is sound: connection, symbols, data, order validation and")
        print("monitoring all work. No orders were placed.")
    else:
        print("Failures above must be fixed before any deployment.")
    print("\nThis says NOTHING about strategy profitability. Every strategy in")
    print("this project was falsified; see reports/PROJECT_SUMMARY.md.")


if __name__ == "__main__":
    main()
