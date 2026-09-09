//+------------------------------------------------------------------+
//|                                          ElScotto_Trend_EA.mq5   |
//|   el-scotto XAUUSD_LOWFREQ, corrected and re-validated.          |
//|   Mirrors el_scotto_tradeable.py::entry_signals(.,"A2_true_pull- |
//|   back") + common/exits.py POLICIES["E1_trail"] + the ny_open    |
//|   window from common/filters.py, so the EA and the Python        |
//|   backtest stay in lockstep.                                     |
//|                                                                  |
//|   PARAMETERS ARE LOCKED. Do NOT optimise them. Every optimisation |
//|   pass in this project produced a result that vanished out of     |
//|   sample; the entry signal is already known to be statistically   |
//|   indistinguishable from random timing (z=+0.75, p=0.334 on the   |
//|   2011-2018 window). Re-tuning fits noise harder.                 |
//|                                                                  |
//|   DEMO ONLY. OnInit refuses to run on a live account.             |
//+------------------------------------------------------------------+
#property copyright "trading-systems"
#property version   "1.00"
#property strict

//--- Strategy (LOCKED - see reports/EL_SCOTTO_IMPROVED.md)
input int    Trend_SMA_Period     = 50;    // daily SMA regime filter (theirs)
input int    Pullback_EMA_Period  = 21;    // H1 EMA the pullback is measured against
input int    ATR_Period           = 14;
input int    ATR_SMA_Period       = 50;    // ATR-expansion gate baseline (theirs)
input int    Regime_Confirm_Bars  = 3;     // gate must hold this many consecutive bars
input double Stop_ATR_Mult        = 2.0;
input double Trail_ATR_Mult       = 3.0;   // chandelier distance; NO fixed target
//--- Session: ny_open from common/filters.py, half-open [start, end)
input int    Session_Start_Hour_UTC = 12;
input int    Session_End_Hour_UTC   = 16;
input int    Server_UTC_Offset_Hours = 0;  // measured UTC+0 on Deriv-Demo; assert, never assume
//--- Risk
input double Risk_Pct             = 0.5;   // percent of equity risked per trade
input int    Max_Trades_Per_Day   = 3;
input double Max_Daily_Loss_Pct   = 2.0;   // kill switch: stop trading for the day
//--- Ops
input double Max_Cost_Ratio       = 0.20;  // skip setups where spread exceeds this share of stop distance
input int    Slippage_Points      = 30;
input double Max_Leverage         = 50.0;  // cap notional at this multiple of equity
input bool   Require_Demo_Account = true;  // hard rail; leave true
//--- Diagnostics. Writes one CSV row per bar (time, indicators, gates, signal)
//--- to MQL5/Files so a Strategy Tester run can be reconciled against the
//--- Python model VALUE BY VALUE. Inference from trade lists alone took this
//--- project through four wrong hypotheses; a trace settles it in one run.
input bool   Debug_Log_Signals    = false;
input int    MagicNumber          = 20260908;

//--- NOTE: no iATR handle. MT5's iATR is an SMA of True Range, not
//--- Wilder's; ATR comes from WilderATR() below. See its header.
int emaHandle, smaDailyHandle;
int dbgHandle = INVALID_HANDLE;
datetime lastBarTime = 0;
int      regimeStreak = 0;
//--- per-position trail state. entryAtr is FROZEN at entry: the trail must use
//--- the ATR at entry, not the current bar's, to match common/exits.py.
double   entryAtr = 0.0;
double   runExtreme = 0.0;
ulong    trackedTicket = 0;
//--- daily counters
datetime tradesTodayDate = 0;
int      tradesToday = 0;
double   equityAtDayStart = 0.0;

//+------------------------------------------------------------------+
int OnInit()
{
   //--- HARD RAIL: this project has no validated edge. Demo only.
   if(Require_Demo_Account &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
   {
      Print("REFUSING TO START: account is not a demo account. ",
            "This strategy has no demonstrated edge and must not trade real capital.");
      return INIT_FAILED;
   }
   //--- Assert the server clock matches the assumed offset rather than trusting
   //--- it. Both existing EAs use TimeCurrent() (server time) while naming every
   //--- input _UTC; on a UTC+2/+3 broker that silently shifts the session window
   //--- and would destroy the one filter that doubled expectancy.
   long drift = (long)TimeCurrent() - (long)TimeGMT()
                - (long)Server_UTC_Offset_Hours * 3600;
   if(MathAbs((double)drift) > 300)
   {
      PrintFormat("REFUSING TO START: server clock is %.1f h from the configured "
                  "Server_UTC_Offset_Hours=%d. Set it correctly or the session "
                  "filter trades the wrong hours.",
                  (double)drift / 3600.0, Server_UTC_Offset_Hours);
      return INIT_FAILED;
   }

   emaHandle      = iMA(_Symbol, PERIOD_CURRENT, Pullback_EMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   smaDailyHandle = iMA(_Symbol, PERIOD_D1, Trend_SMA_Period, 0, MODE_SMA, PRICE_CLOSE);
   if(emaHandle == INVALID_HANDLE || smaDailyHandle == INVALID_HANDLE)
   {
      Print("Failed to create indicator handles");
      return INIT_FAILED;
   }
   if(Debug_Log_Signals)
   {
      string fn = StringFormat("elscotto_trace_%s.csv", _Symbol);
      //--- FILE_COMMON is essential in the Strategy Tester. Without it each
      //--- tester AGENT writes into its own sandbox
      //--- (...\Tester\<id>\Agent-127.0.0.1-300X\MQL5\Files), which is why
      //--- the first traced run produced no findable file. FILE_COMMON puts it
      //--- in one place: ...\MetaQuotes\Terminal\Common\Files.
      dbgHandle = FileOpen(fn, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
      if(dbgHandle == INVALID_HANDLE)
      {
         PrintFormat("Could not open %s for trace logging (err %d)", fn, GetLastError());
      }
      else
      {
         PrintFormat("Trace log written to Common Files: %s", fn);
         FileWrite(dbgHandle, "bar_time", "close", "high", "low", "atr",
                   "atr_sma", "streak", "ema", "trend_sma", "in_session", "side");
      }
   }
   equityAtDayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   RecoverPositionState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(emaHandle);
   IndicatorRelease(smaDailyHandle);
   if(dbgHandle != INVALID_HANDLE) { FileClose(dbgHandle); dbgHandle = INVALID_HANDLE; }
}

bool IsNewBar()
{
   datetime t = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(t != lastBarTime) { lastBarTime = t; return true; }
   return false;
}

//--- Select a filling mode the SYMBOL actually supports.
//--- Both EAs previously hardcoded ORDER_FILLING_IOC. This broker reports
//--- filling_mode=1 (FOK only) on XAUUSD and BTCUSD, so every order would
//--- have been rejected with retcode 10030 "Unsupported filling mode".
//--- Found by ops_rehearsal.py via order_check() before any live deployment.
ENUM_ORDER_TYPE_FILLING PickFillingMode()
{
   long modes = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}

//+------------------------------------------------------------------+
//| Wilder ATR, computed by hand.                                     |
//|                                                                   |
//| MT5's built-in iATR is a SIMPLE moving average of True Range, NOT |
//| Wilder's smoothed average. A per-bar trace of 44,620 bars against |
//| the Python model showed iATR running 7-16% HIGHER than Wilder on  |
//| 44,607 of them (mean +0.48 on gold); it matched SMA(TR,14) to     |
//| 2.4e-6. Close and EMA matched to six decimals, so ATR was the      |
//| entire divergence.                                                |
//|                                                                   |
//| This matters because ATR drives FOUR things at once: the stop      |
//| (2xATR), the trail (3xATR), the position size (risk/2xATR) and    |
//| the regime gate (ATR > SMA50(ATR)). Using iATR would trade a      |
//| materially different strategy from the validated one, with ~10%   |
//| wider stops and ~10% smaller positions.                           |
//|                                                                   |
//| Matches el-scotto's src/indicators.py::atr: seed with the SMA of  |
//| the first `period` true ranges, then Wilder-smooth. Seeded from    |
//| WARMUP extra bars so the seeding error has decayed to nothing by   |
//| the time it reaches the bars we act on ((13/14)^300 ~ 1e-10).     |
//+------------------------------------------------------------------+
bool WilderATR(int period, int count, double &out[])
{
   const int WARMUP = 300;
   int need = count + period + WARMUP + 2;
   int avail = Bars(_Symbol, PERIOD_CURRENT);
   if(avail < period + count + 2) return false;
   if(need > avail) need = avail;

   double hi[], lo[], cl[];
   ArraySetAsSeries(hi, false);          // chronological: index 0 is OLDEST
   ArraySetAsSeries(lo, false);
   ArraySetAsSeries(cl, false);
   if(CopyHigh (_Symbol, PERIOD_CURRENT, 0, need, hi) < need) return false;
   if(CopyLow  (_Symbol, PERIOD_CURRENT, 0, need, lo) < need) return false;
   if(CopyClose(_Symbol, PERIOD_CURRENT, 0, need, cl) < need) return false;
   if(need < period + count + 2) return false;

   double series[];
   ArrayResize(series, need);
   ArrayInitialize(series, 0.0);

   double sum = 0.0;
   for(int i = 1; i <= period; i++)
      sum += MathMax(hi[i] - lo[i],
                     MathMax(MathAbs(hi[i] - cl[i-1]), MathAbs(lo[i] - cl[i-1])));
   double atr = sum / period;
   series[period] = atr;
   for(int i = period + 1; i < need; i++)
   {
      double tr = MathMax(hi[i] - lo[i],
                          MathMax(MathAbs(hi[i] - cl[i-1]), MathAbs(lo[i] - cl[i-1])));
      atr = (atr * (period - 1) + tr) / period;
      series[i] = atr;
   }

   ArrayResize(out, count + 1);
   ArraySetAsSeries(out, true);          // out[0] = current bar, out[1] = last closed
   for(int k = 0; k <= count; k++)
   {
      int src = need - 1 - k;
      if(src < period) return false;
      out[k] = series[src];
   }
   return true;
}

double CalcLotSize(double stopDistance)
{
   //--- COST GUARD (added after a real defect found in cross-sectional testing)
   //--- Size is riskAmount/stopDistance. When ATR collapses relative to the
   //--- spread (e.g. quiet Asian-session hours on low-volatility symbols),
   //--- stopDistance shrinks, size explodes, and the fixed spread on that
   //--- oversized position costs many R despite a nominal 1R stop. Backtests
   //--- showed -18R to -21R per trade from exactly this. Refuse such setups.
   double spreadPrice = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)
                        * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(stopDistance <= 0) return 0.0;
   if(spreadPrice / stopDistance > Max_Cost_Ratio) return 0.0;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskAmount = equity * (Risk_Pct / 100.0);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0 || tickValue <= 0 || stopDistance <= 0) return 0.0;
   double valuePerUnit = tickValue / tickSize; // account currency per 1.0 price unit per lot
   double lots = riskAmount / (stopDistance * valuePerUnit);

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   //--- Second guard: cap notional so a small stop cannot imply a position
   //--- larger than the account can carry.
   double contract = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double price    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(contract > 0 && price > 0)
   {
      double maxLots = (Max_Leverage * equity) / (contract * price);
      lots = MathMin(lots, maxLots);
   }
   lots = MathFloor(lots / step) * step;
   //--- If the guards pushed size below the broker minimum, SKIP the trade.
   //--- Forcing it back up to minLot would silently violate the very cap that
   //--- was just applied -- the original bug in a different disguise.
   if(lots < minLot) return 0.0;
   return MathMin(maxLot, lots);
}

//--- Return our position's ticket, or 0. Unlike HasOpenPosition() in the other
//--- EAs this yields the ticket, because the trail must MODIFY the position.
ulong SelectOwnPosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      string sym = PositionGetSymbol(i);
      if(sym == _Symbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         return (ulong)PositionGetInteger(POSITION_TICKET);
   }
   return 0;
}

//--- Recover trail state after a terminal restart. entryAtr is written into the
//--- order comment at entry precisely so it survives; guessing a trail distance
//--- would silently trade a different strategy from the validated one.
void RecoverPositionState()
{
   ulong ticket = SelectOwnPosition();
   if(ticket == 0) { trackedTicket = 0; entryAtr = 0.0; return; }
   if(!PositionSelectByTicket(ticket)) return;
   string cmt = PositionGetString(POSITION_COMMENT);
   int p = StringFind(cmt, "atr=");
   if(p >= 0)
   {
      entryAtr = StringToDouble(StringSubstr(cmt, p + 4));
      trackedTicket = ticket;
      runExtreme = PositionGetDouble(POSITION_PRICE_OPEN);
      PrintFormat("Recovered position %I64u with entryAtr=%.5f from comment.",
                  ticket, entryAtr);
   }
   else
   {
      entryAtr = 0.0;
      trackedTicket = ticket;
      Print("WARNING: open position ", ticket, " has no recoverable entry ATR. ",
            "The trail is DISABLED for it; its broker stop still protects it. ",
            "Close it manually to restore normal operation.");
   }
}

//--- Chandelier trail. Mirrors common/exits.py:153-163 exactly:
//---   * distance is Trail_ATR_Mult * entryAtr (ATR FROZEN AT ENTRY)
//---   * extreme is the run-up high/low since entry, not the close
//---   * the stop is MONOTONIC -- it can never move against the position
void UpdateTrail()
{
   ulong ticket = SelectOwnPosition();
   if(ticket == 0) { trackedTicket = 0; entryAtr = 0.0; return; }
   if(entryAtr <= 0) return;                 // unrecoverable state; leave it alone
   if(!PositionSelectByTicket(ticket)) return;

   long   type = PositionGetInteger(POSITION_TYPE);
   double curSL = PositionGetDouble(POSITION_SL);
   double high = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double low  = iLow(_Symbol, PERIOD_CURRENT, 1);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double newSL;

   if(type == POSITION_TYPE_BUY)
   {
      runExtreme = MathMax(runExtreme, high);
      newSL = MathMax(curSL, runExtreme - Trail_ATR_Mult * entryAtr);
   }
   else
   {
      runExtreme = (runExtreme <= 0) ? low : MathMin(runExtreme, low);
      newSL = MathMin(curSL, runExtreme + Trail_ATR_Mult * entryAtr);
      if(curSL <= 0) newSL = runExtreme + Trail_ATR_Mult * entryAtr;
   }
   newSL = NormalizeDouble(newSL, digits);
   if(MathAbs(newSL - curSL) < SymbolInfoDouble(_Symbol, SYMBOL_POINT)) return;

   //--- respect the broker's minimum stop distance or the modify is rejected 10016
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL)
                    * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(type == POSITION_TYPE_BUY  && (bid - newSL) < minDist) return;
   if(type == POSITION_TYPE_SELL && (newSL - ask) < minDist) return;

   MqlTradeRequest req; MqlTradeResult res;
   ZeroMemory(req); ZeroMemory(res);
   req.action   = TRADE_ACTION_SLTP;
   req.symbol   = _Symbol;
   req.position = ticket;
   req.sl       = newSL;
   req.tp       = 0.0;                      // E1_trail has NO fixed target
   if(!OrderSend(req, res) || res.retcode != TRADE_RETCODE_DONE)
      PrintFormat("Trail modify failed: retcode=%d %s", res.retcode, res.comment);
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewBar()) return;

   //--- trail BEFORE the position check, so an open trade is managed every bar
   UpdateTrail();

   //--- INDICATORS AND THE REGIME STREAK ARE UPDATED FIRST, ON EVERY BAR.
   //--- This ordering is load-bearing. The Python model computes the
   //--- ATR-expansion streak continuously across the whole series
   //--- (el_scotto_tradeable.py::entry_signals), so if the streak only
   //--- advanced on bars that survived the session/position/cap gates it would
   //--- count a completely different thing -- effectively "consecutive
   //--- in-session bars with no open position", not "consecutive expansion
   //--- bars". Every early return below must come AFTER this block.
   double atrVals[], emaVals[], smaVals[];
   ArraySetAsSeries(atrVals, true);
   ArraySetAsSeries(emaVals, true);
   ArraySetAsSeries(smaVals, true);
   if(!WilderATR(ATR_Period, ATR_SMA_Period + 1, atrVals)) return;
   if(CopyBuffer(emaHandle, 0, 0, 2, emaVals) < 2) return;
   if(CopyBuffer(smaDailyHandle, 0, 0, 2, smaVals) < 2) return;

   double atrNow = atrVals[1];
   double ema    = emaVals[1];
   double trend  = smaVals[1];              // last CLOSED daily bar's SMA

   //--- ATR-expansion gate: ATR(14) > SMA50(ATR14), theirs, KEPT.
   //--- Dropping it made results worse (+0.0307 vs +0.0605), contradicting
   //--- our prior from the volatility-regime work.
   //--- Warmup mirrors Python: a bar with no valid ATR resets the streak,
   //--- matching the `None` handling in regime_filter.atr_expansion_gate.
   if(atrNow <= 0)
   {
      regimeStreak = 0;
      return;
   }
   double atrSum = 0.0;
   for(int i = 1; i <= ATR_SMA_Period; i++) atrSum += atrVals[i];
   double atrSma = atrSum / ATR_SMA_Period;
   if(atrNow > atrSma) regimeStreak++; else regimeStreak = 0;

   //--- TRACE: written for EVERY bar, before any gate, so the Python model can
   //--- be reconciled value by value rather than inferred from the trade list.
   if(dbgHandle != INVALID_HANDLE)
   {
      MqlDateTime td;
      TimeToStruct(iTime(_Symbol, PERIOD_CURRENT, 1), td);
      int uh = (int)(td.hour - Server_UTC_Offset_Hours);
      if(uh < 0) uh += 24; if(uh > 23) uh -= 24;
      bool insess = (uh >= Session_Start_Hour_UTC && uh < Session_End_Hour_UTC);
      double dc = iClose(_Symbol, PERIOD_CURRENT, 1);
      double dh = iHigh(_Symbol, PERIOD_CURRENT, 1);
      double dl = iLow(_Symbol, PERIOD_CURRENT, 1);
      int dside = 0;
      if(ema > 0 && trend > 0 && regimeStreak >= Regime_Confirm_Bars && insess)
      {
         if(dc > trend && dl < ema && dc > ema)      dside = 1;
         else if(dc < trend && dh > ema && dc < ema) dside = -1;
      }
      FileWrite(dbgHandle,
                TimeToString(iTime(_Symbol, PERIOD_CURRENT, 1), TIME_DATE|TIME_MINUTES),
                DoubleToString(dc, 3), DoubleToString(dh, 3), DoubleToString(dl, 3),
                DoubleToString(atrNow, 5), DoubleToString(atrSma, 5),
                IntegerToString(regimeStreak), DoubleToString(ema, 5),
                DoubleToString(trend, 5), (insess ? "1" : "0"),
                IntegerToString(dside));
   }

   //--- daily rollover, counters and the loss kill switch
   MqlDateTime dt;
   datetime nowSrv = TimeCurrent();
   TimeToStruct(nowSrv, dt);
   datetime today = nowSrv - (dt.hour * 3600 + dt.min * 60 + dt.sec);
   if(today != tradesTodayDate)
   {
      tradesTodayDate = today;
      tradesToday = 0;
      equityAtDayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   }

   if(SelectOwnPosition() != 0) return;     // one position at a time
   if(tradesToday >= Max_Trades_Per_Day) return;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equityAtDayStart > 0 &&
      (equityAtDayStart - equity) / equityAtDayStart * 100.0 >= Max_Daily_Loss_Pct)
   {
      return;                               // kill switch: done for the day
   }

   //--- Session gate, half-open [start, end) in UTC. ny_open from filters.py.
   //--- Gated on the SIGNAL bar (index 1), NOT on the current bar. The signal
   //--- is evaluated on the last closed bar and the order fills on the bar
   //--- after it, so gating on TimeCurrent() shifts the traded window an hour
   //--- early: the Strategy Tester took signals from 11:00-14:59 while the
   //--- validated model uses 12:00-15:59. That off-by-one dropped agreement
   //--- with the model to 7%; correcting the comparison for it restored 80%.
   MqlDateTime sigdt;
   TimeToStruct(iTime(_Symbol, PERIOD_CURRENT, 1), sigdt);
   int utcHour = (int)(sigdt.hour - Server_UTC_Offset_Hours);
   if(utcHour < 0)  utcHour += 24;
   if(utcHour > 23) utcHour -= 24;
   if(utcHour < Session_Start_Hour_UTC || utcHour >= Session_End_Hour_UTC) return;

   if(ema <= 0 || trend <= 0) return;
   if(regimeStreak < Regime_Confirm_Bars) return;

   double closeNow = iClose(_Symbol, PERIOD_CURRENT, 1);
   double lowNow   = iLow(_Symbol, PERIOD_CURRENT, 1);
   double highNow  = iHigh(_Symbol, PERIOD_CURRENT, 1);

   //--- A2_true_pullback: their DOCUMENTED intent, correctly implemented.
   //--- Their code (entries_v2.py:218) has no lower bound on the band, so a bar
   //--- that never approached the EMA still qualified. Requiring the low to dip
   //--- THROUGH the EMA and the close to recover above it lifted PF 1.236->1.394.
   bool longSignal  = (closeNow > trend) && (lowNow  < ema) && (closeNow > ema);
   bool shortSignal = (closeNow < trend) && (highNow > ema) && (closeNow < ema);
   if(!longSignal && !shortSignal) return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL)
                    * SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   MqlTradeRequest req; MqlTradeResult res;
   ZeroMemory(req); ZeroMemory(res);
   req.action       = TRADE_ACTION_DEAL;
   req.symbol       = _Symbol;
   req.deviation    = Slippage_Points;
   req.magic        = MagicNumber;
   req.type_filling = PickFillingMode();
   req.type_time    = ORDER_TIME_GTC;
   //--- entry ATR is persisted here so the trail survives a terminal restart
   req.comment      = StringFormat("els atr=%.5f", atrNow);

   //--- Stop distance is measured by the broker from the price the position
   //--- would CLOSE at, not the price it opens at: a BUY closes at bid, a SELL
   //--- at ask. Checking against the open side instead understates the distance
   //--- by the whole spread. ops_rehearsal.py had exactly this bug and it only
   //--- surfaced when the spread widened to 39 points near rollover -- its
   //--- $0.40 stop below ask left 1 point above bid and was rejected 10016.
   double entryPrice, stop;
   if(longSignal)
   {
      entryPrice = ask;
      stop = NormalizeDouble(entryPrice - Stop_ATR_Mult * atrNow, digits);
      if((bid - stop) < minDist) return;          // would be rejected 10016
      req.type = ORDER_TYPE_BUY;
   }
   else
   {
      entryPrice = bid;
      stop = NormalizeDouble(entryPrice + Stop_ATR_Mult * atrNow, digits);
      if((stop - ask) < minDist) return;
      req.type = ORDER_TYPE_SELL;
   }

   double lots = CalcLotSize(MathAbs(entryPrice - stop));
   if(lots <= 0) return;
   req.price  = NormalizeDouble(entryPrice, digits);
   req.volume = lots;
   req.sl     = stop;
   req.tp     = 0.0;                        // E1_trail: NO fixed target

   //--- Both existing EAs ignore OrderSend's result entirely, which is how the
   //--- FOK bug could have run silently forever. Always read the retcode.
   if(!OrderSend(req, res) || res.retcode != TRADE_RETCODE_DONE)
   {
      PrintFormat("OrderSend FAILED: retcode=%d %s (vol=%.2f price=%.*f sl=%.*f)",
                  res.retcode, res.comment, lots, digits, req.price, digits, stop);
      return;
   }
   tradesToday++;
   entryAtr      = atrNow;
   runExtreme    = res.price > 0 ? res.price : entryPrice;
   trackedTicket = res.order;
   PrintFormat("ENTRY %s %.2f @ %.*f sl=%.*f atr=%.5f (trail %.1fxATR)",
               longSignal ? "BUY" : "SELL", lots, digits, res.price, digits, stop,
               atrNow, Trail_ATR_Mult);
}
//+------------------------------------------------------------------+
