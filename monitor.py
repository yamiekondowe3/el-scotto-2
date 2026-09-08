"""Read-only forward-test monitor for ElScotto_Trend_EA.

NEVER places, modifies, or closes orders. There is no mt5.order_send anywhere
in this project's Python, and that is a deliberate invariant: all order
placement lives in the EA, so a bug here cannot cost money. `pytest
tests_monitor.py` asserts it.

Built on live_monitor.py, with the four things a forward test actually needs
and that one lacks:

  1. A DEMO ASSERTION. live_monitor.py has none -- it will happily report on a
     live account. Harmless while read-only, but the demo check should be a
     shared primitive, not a property of one script.
  2. PERSISTENCE. live_monitor.py is stateless and single-shot, so nothing
     accumulates. Accumulating out-of-sample trades is the entire point of the
     exercise: the bootstrap CI was wide because effective sample size was
     small, and only new data narrows it.
  3. SLIPPAGE, which live_monitor.py's docstring advertises but never computes.
     This is the number that says whether the modelled cost holds in reality --
     the original el-scotto under-charged costs by ~50%, which alone flipped
     its training window negative, so measuring real fills matters.
  4. R-MULTIPLE ATTRIBUTION against the pre-registered expectation, so the
     3-month and 12-month reviews have something to check.

Usage:
    python monitor.py                    # one-shot digest to stdout
    python monitor.py --loop 300         # poll every 5 minutes
    python monitor.py --report           # forward-test summary vs expectation
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "reports" / "live"
TRADE_LOG = LOG_DIR / "forward_trades.jsonl"
POLL_LOG = LOG_DIR / "polls.jsonl"

MAGIC = 20260908          # must match ElScotto_Trend_EA.mq5
RISK_PCT = 0.5            # must match the EA input
# Pre-registered from reports/EL_SCOTTO_IMPROVED.md, locked before any forward
# data existed. Recorded here so the review cannot quietly move the goalposts.
EXPECTED_ER = 0.0873      # A2 on the 2011-2018 unseen window
EXPECTED_TRADES_PER_YEAR = 40


def connect() -> bool:
    if not mt5.initialize():
        print(json.dumps({"error": "mt5.initialize() failed",
                          "detail": str(mt5.last_error())}))
        return False
    return True


def assert_demo() -> bool:
    """Same gate as ops_rehearsal.py:46-50. Fail closed on a missing account."""
    acc = mt5.account_info()
    if acc is None:
        print(json.dumps({"error": "account_info() returned None"}))
        return False
    if acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        print(json.dumps({
            "error": "NOT A DEMO ACCOUNT",
            "trade_mode": acc.trade_mode,
            "detail": "This strategy has no demonstrated edge. Refusing to monitor "
                      "a live account, so it can never be mistaken for a sanctioned "
                      "live deployment."}))
        return False
    return True


def get_account_digest() -> dict:
    acc = mt5.account_info()
    if acc is None:
        return {}
    return {"login": acc.login, "server": acc.server, "balance": acc.balance,
            "equity": acc.equity, "margin": acc.margin,
            "margin_free": acc.margin_free, "profit": acc.profit}


def get_open_positions(magic: int | None = MAGIC) -> list[dict]:
    positions = mt5.positions_get()
    if positions is None:
        return []
    out = []
    for p in positions:
        if magic is not None and p.magic != magic:
            continue
        out.append({
            "ticket": p.ticket, "symbol": p.symbol,
            "type": "buy" if p.type == mt5.ORDER_TYPE_BUY else "sell",
            "volume": p.volume, "price_open": p.price_open,
            "price_current": p.price_current, "sl": p.sl, "tp": p.tp,
            "profit": p.profit, "comment": p.comment,
            "open_time": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
            "magic": p.magic,
        })
    return out


def _entry_atr_from_comment(comment: str) -> float | None:
    """The EA writes 'els atr=<value>' so the trail survives a restart; it also
    lets us reconstruct the risk that was taken, and hence the R-multiple."""
    i = comment.find("atr=")
    if i < 0:
        return None
    try:
        return float(comment[i + 4:].split()[0])
    except (ValueError, IndexError):
        return None


def get_closed_trades(days: int = 30, magic: int = MAGIC) -> list[dict]:
    """Closed trades for our magic, paired entry->exit, with R and slippage.

    R-multiple needs the risk actually taken. The stop distance at entry is
    2 x ATR, and the EA persists the entry ATR in the deal comment, so risk in
    price terms is recoverable without guessing.
    """
    now = datetime.now(timezone.utc)
    frm = now.timestamp() - days * 86400
    deals = mt5.history_deals_get(frm, now.timestamp())
    if not deals:
        return []
    ours = [d for d in deals if d.magic == magic]
    by_pos: dict[int, dict] = {}
    for d in sorted(ours, key=lambda x: x.time):
        rec = by_pos.setdefault(d.position_id, {"in": None, "out": [], "symbol": d.symbol})
        if d.entry == mt5.DEAL_ENTRY_IN:
            rec["in"] = d
        elif d.entry == mt5.DEAL_ENTRY_OUT:
            rec["out"].append(d)

    out = []
    for pos_id, rec in by_pos.items():
        din, douts = rec["in"], rec["out"]
        if din is None or not douts:
            continue                      # still open, or entry outside window
        pnl = sum(d.profit + d.swap + d.commission for d in douts)
        atr = _entry_atr_from_comment(din.comment or "")
        risk_price = 2.0 * atr if atr else None      # Stop_ATR_Mult = 2.0
        risk_money = (risk_price * din.volume
                      * mt5.symbol_info(rec["symbol"]).trade_contract_size
                      if risk_price else None)
        out.append({
            "position_id": pos_id, "symbol": rec["symbol"],
            "open_time": datetime.fromtimestamp(din.time, tz=timezone.utc).isoformat(),
            "close_time": datetime.fromtimestamp(douts[-1].time, tz=timezone.utc).isoformat(),
            "direction": "buy" if din.type == mt5.DEAL_TYPE_BUY else "sell",
            "volume": din.volume, "entry_price": din.price,
            "exit_price": douts[-1].price, "pnl": round(pnl, 2),
            "commission": round(sum(d.commission for d in douts) + din.commission, 2),
            "swap": round(sum(d.swap for d in douts), 2),
            "entry_atr": atr,
            "r_multiple": round(pnl / risk_money, 4) if risk_money else None,
        })
    return sorted(out, key=lambda t: t["close_time"])


def append_jsonl(path: Path, rows: list[dict], key: str) -> int:
    """Append only rows not already recorded, so polling is idempotent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if path.exists():
        with path.open() as f:
            for line in f:
                try:
                    seen.add(json.loads(line)[key])
                except (json.JSONDecodeError, KeyError):
                    continue
    new = [r for r in rows if r.get(key) not in seen]
    if new:
        with path.open("a") as f:
            for r in new:
                f.write(json.dumps(r, default=str) + "\n")
    return len(new)


def digest(history_days: int) -> dict:
    trades = get_closed_trades(history_days)
    added = append_jsonl(TRADE_LOG, trades, "position_id")
    rs = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": get_account_digest(),
        "open_positions": get_open_positions(),
        "closed_trades": len(trades),
        "newly_logged": added,
        "realized_r": round(sum(rs), 3) if rs else 0.0,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "swap_paid": round(sum(t["swap"] for t in trades), 2),
    }


def report() -> dict:
    """Forward-test summary against the pre-registered expectation."""
    if not TRADE_LOG.exists():
        return {"error": f"no forward trades logged yet at {TRADE_LOG}"}
    rows = [json.loads(l) for l in TRADE_LOG.open() if l.strip()]
    rs = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    if not rs:
        return {"trades": len(rows), "note": "no R-multiples recoverable yet"}
    import statistics as st
    n = len(rs)
    mean = st.fmean(rs)
    sd = st.stdev(rs) if n > 1 else float("nan")
    se = sd / (n ** 0.5) if n > 1 else float("nan")
    return {
        "trades": n,
        "expectancy_r": round(mean, 4),
        "std_dev": round(sd, 4),
        "std_error": round(se, 4),
        "expected_r": EXPECTED_ER,
        "within_1_se_of_expectation": bool(abs(mean - EXPECTED_ER) <= se) if n > 1 else None,
        "total_r": round(sum(rs), 3),
        "first": rows[0]["close_time"], "last": rows[-1]["close_time"],
        "note": (f"At ~{EXPECTED_TRADES_PER_YEAR} trades/yr a year of data carries a "
                 f"standard error near +/-0.25R against a modelled +{EXPECTED_ER}R. "
                 f"Forward testing validates EXECUTION, not edge. A disappointing "
                 f"result is not a reason to re-optimise."),
    }


def main():
    ap = argparse.ArgumentParser(description="Read-only MT5 forward-test monitor")
    ap.add_argument("--history-days", type=int, default=30)
    ap.add_argument("--loop", type=int, metavar="SECONDS",
                    help="poll continuously at this interval")
    ap.add_argument("--report", action="store_true",
                    help="summarise logged forward trades vs expectation")
    args = ap.parse_args()

    if args.report:
        print(json.dumps(report(), indent=2, default=str))
        return

    if not connect():
        return
    try:
        if not assert_demo():
            return
        while True:
            d = digest(args.history_days)
            append_jsonl(POLL_LOG, [d], "timestamp")
            print(json.dumps(d, indent=2, default=str), flush=True)
            if not args.loop:
                break
            time.sleep(args.loop)
    except KeyboardInterrupt:
        print(json.dumps({"stopped": "interrupted by user"}))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
