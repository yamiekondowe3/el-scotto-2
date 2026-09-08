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
input int    MagicNumber          = 20260908;

int atrHandle, atrDailyDummy, emaHandle, smaDailyHandle;
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

   atrHandle      = iATR(_Symbol, PERIOD_CURRENT, ATR_Period);
   emaHandle      = iMA(_Symbol, PERIOD_CURRENT, Pullback_EMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   smaDailyHandle = iMA(_Symbol, PERIOD_D1, Trend_SMA_Period, 0, MODE_SMA, PRICE_CLOSE);
   if(atrHandle == INVALID_HANDLE || emaHandle == INVALID_HANDLE ||
      smaDailyHandle == INVALID_HANDLE)
   {
      Print("Failed to create indicator handles");
      return INIT_FAILED;
   }
   equityAtDayStart = AccountInfoDouble(ACCOUNT_EQUITY);
   RecoverPositionState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(atrHandle);
   IndicatorRelease(emaHandle);
   IndicatorRelease(smaDailyHandle);
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

   //--- session gate, half-open [start, end) in UTC. ny_open from filters.py.
   int utcHour = (int)(dt.hour - Server_UTC_Offset_Hours);
   if(utcHour < 0)  utcHour += 24;
   if(utcHour > 23) utcHour -= 24;
   if(utcHour < Session_Start_Hour_UTC || utcHour >= Session_End_Hour_UTC) return;

   //--- indicators, all read from the LAST CLOSED bar (index 1)
   double atrVals[], emaVals[], smaVals[];
   ArraySetAsSeries(atrVals, true);
   ArraySetAsSeries(emaVals, true);
   ArraySetAsSeries(smaVals, true);
   int need = ATR_SMA_Period + 2;
   if(CopyBuffer(atrHandle, 0, 0, need, atrVals) < need) return;
   if(CopyBuffer(emaHandle, 0, 0, 2, emaVals) < 2) return;
   if(CopyBuffer(smaDailyHandle, 0, 0, 2, smaVals) < 2) return;

   double atrNow = atrVals[1];
   double ema    = emaVals[1];
   double trend  = smaVals[1];              // last CLOSED daily bar's SMA
   if(atrNow <= 0 || ema <= 0 || trend <= 0) return;

   //--- ATR-expansion gate: ATR(14) > SMA50(ATR14), theirs, KEPT.
   //--- Dropping it made results worse (+0.0307 vs +0.0605), contradicting
   //--- our prior from the volatility-regime work.
   double atrSum = 0.0;
   for(int i = 1; i <= ATR_SMA_Period; i++) atrSum += atrVals[i];
   double atrSma = atrSum / ATR_SMA_Period;
   if(atrNow > atrSma) regimeStreak++; else regimeStreak = 0;
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

   double entryPrice, stop;
   if(longSignal)
   {
      entryPrice = ask;
      stop = NormalizeDouble(entryPrice - Stop_ATR_Mult * atrNow, digits);
      if((entryPrice - stop) < minDist) return;   // would be rejected 10016
      req.type = ORDER_TYPE_BUY;
   }
   else
   {
      entryPrice = bid;
      stop = NormalizeDouble(entryPrice + Stop_ATR_Mult * atrNow, digits);
      if((stop - entryPrice) < minDist) return;
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
