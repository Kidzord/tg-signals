//+------------------------------------------------------------------+
//|                                                    Ict2022EA.mq5 |
//|  Перенос ict2022.py (свип ликвидности -> FVG -> возврат в гэп)   |
//|  на MQL5 для проверки в тестере стратегий.                       |
//|  Вход — по рынку на открытии бара после сигнального (как в       |
//|  Python-бэктесте с entry_model="next_open").                     |
//+------------------------------------------------------------------+
#property copyright "tg-signals"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

input group "--- Модель ---"
input int    InpSwingLen   = 20;     // окно свипа (баров M15)
input int    InpFvgWindow  = 20;     // сколько баров ждём FVG после свипа
input int    InpRetestLook = 40;     // сколько баров ждём возврат в гэп
input double InpStopBufATR = 0.25;   // буфер стопа за свип, ATR
input double InpTpR        = 4.0;    // цель, R
input int    InpMaxBars    = 120;    // выход по времени (баров M15)

input group "--- Риск ---"
input double InpRiskPct    = 0.5;    // риск на сделку, % баланса

CTrade   g_trade;

//--- состояние сетапов (как в ict2022.py)
datetime g_lSweep = 0;   double g_lExt = 0.0;   datetime g_lFvg = 0;   double g_lTop = 0.0, g_lBot = 0.0;
datetime g_sSweep = 0;  double g_sExt = 0.0;   datetime g_sFvg = 0;   double g_sTop = 0.0, g_sBot = 0.0;
bool     g_hasL = false, g_hasS = false;

datetime g_lastBar  = 0;
datetime g_entryBar = 0;
int      g_atrFailLogged = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   g_trade.SetExpertMagicNumber(20261027);
   g_trade.SetDeviationInPoints(50);
   ResetState();
   return(INIT_SUCCEEDED);
  }

void ResetState()
  {
   g_hasL = g_hasS = false;
   g_lFvg = g_sFvg = 0;
   g_lastBar = 0;
   g_entryBar = 0;
  }

//+------------------------------------------------------------------+
//| Wilder ATR по реальным high/low/close (как ta.atr)               |
//+------------------------------------------------------------------+
double AtrAtShift(int shift, int period = 14)
  {
   int need = period + 400;
   double h[], l[], c[];
   if(CopyHigh(_Symbol, PERIOD_M15, shift, need, h) != need) return(0.0);
   if(CopyLow(_Symbol, PERIOD_M15, shift, need, l)  != need) return(0.0);
   if(CopyClose(_Symbol, PERIOD_M15, shift, need, c) != need) return(0.0);
   double tr2[];
   ArrayResize(tr2, need);
   tr2[0] = h[0] - l[0];
   for(int i = 1; i < need; i++)
      tr2[i] = MathMax(h[i] - l[i], MathMax(MathAbs(h[i] - c[i-1]), MathAbs(l[i] - c[i-1])));
   double atr = 0.0;
   double sum = 0.0;
   for(int i = 0; i < period; i++)
      sum += tr2[i];
   atr = sum / period;
   for(int i = period; i < need; i++)
      atr = (atr * (period - 1) + tr2[i]) / period;
   return(atr);
  }

//+------------------------------------------------------------------+
//| Минимум лоу / максимум хай по сдвигам [from..to] (to >= from)    |
//+------------------------------------------------------------------+
double MinLowShifts(int from, int to)
  {
   int count = to - from + 1;
   double l[];
   if(CopyLow(_Symbol, PERIOD_M15, from, count, l) != count)
      return(0.0);
   double m = l[0];
   for(int i = 1; i < count; i++)
      m = MathMin(m, l[i]);
   return(m);
  }

double MaxHighShifts(int from, int to)
  {
   int count = to - from + 1;
   double h[];
   if(CopyHigh(_Symbol, PERIOD_M15, from, count, h) != count)
      return(0.0);
   double m = h[0];
   for(int i = 1; i < count; i++)
      m = MathMax(m, h[i]);
   return(m);
  }

//+------------------------------------------------------------------+
double LotsForRisk(double risk_price)
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double money = balance * InpRiskPct / 100.0;
   if(risk_price <= 0)
      return(0.0);
   double per_unit = 0.0;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double p1 = 0.0;
   if(ask > 0 && OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, ask, ask + 1.0, p1) && p1 > 0)
      per_unit = p1;
   else
     {
      double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      if(tick_size <= 0 || tick_value <= 0)
         return(0.0);
      per_unit = tick_value / tick_size;
     }
   double lots = money / (risk_price * per_unit);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step > 0)
      lots = MathFloor(lots / step) * step;
   lots = MathMax(vmin, MathMin(vmax, lots));
   return(NormalizeDouble(lots, 2));
  }

//+------------------------------------------------------------------+
bool NewBar()
  {
   datetime t = iTime(_Symbol, PERIOD_M15, 0);
   if(t != g_lastBar)
     {
      g_lastBar = t;
      return(true);
     }
   return(false);
  }

//+------------------------------------------------------------------+
//| Основной обработчик: сигнальный бар = shift 1, вход сейчас       |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!NewBar())
      return;

   datetime t1 = iTime(_Symbol, PERIOD_M15, 1);
   if(t1 == 0)
      return;
   double h1 = iHigh(_Symbol, PERIOD_M15, 1);
   double l1 = iLow(_Symbol, PERIOD_M15, 1);
   double c1 = iClose(_Symbol, PERIOD_M15, 1);
   double a = AtrAtShift(1);
   if(a <= 0)
      return;
   int sec = PeriodSeconds(PERIOD_M15);

   //--- выход по времени
   if(PositionSelect(_Symbol) && g_entryBar > 0)
     {
      if((t1 - g_entryBar) / sec > InpMaxBars)
        {
         g_trade.PositionClose(_Symbol);
         g_entryBar = 0;
        }
     }

   //--- окно свипа: InpSwingLen баров ПЕРЕД сигнальным (сдвиги 2..21)
   double min20 = MinLowShifts(2, 2 + InpSwingLen - 1);
   double max20 = MaxHighShifts(2, 2 + InpSwingLen - 1);
   if(min20 <= 0 || max20 <= 0)
      return;
   bool swept_dn = (l1 < min20);
   bool swept_up = (h1 > max20);

   //--- машина состояний лонга
   if(g_hasL && swept_dn)
      g_lExt = MathMin(g_lExt, l1);
   if(!g_hasL && swept_dn)
     {
      g_hasL = true;
      g_lSweep = t1;
      g_lExt = l1;
      g_lFvg = 0;
     }
   bool sig_long = false;
   if(g_hasL)
     {
      if(g_lFvg == 0)
        {
         if(t1 - g_lSweep > (datetime)(InpFvgWindow * sec))
            g_hasL = false;
         else if(l1 > iHigh(_Symbol, PERIOD_M15, 3))
           {
            g_lFvg = t1;
            g_lTop = l1;
            g_lBot = iHigh(_Symbol, PERIOD_M15, 3);
           }
        }
      else if(t1 > g_lFvg)
        {
         if(t1 - g_lFvg > (datetime)(InpRetestLook * sec))
            g_hasL = false;
         else if(l1 <= g_lTop && c1 > g_lBot)
           {
            sig_long = true;
            g_hasL = false;
           }
         else if(c1 < g_lBot - a)
            g_hasL = false;
        }
     }

   //--- машина состояний шорта
   if(g_hasS && swept_up)
      g_sExt = MathMax(g_sExt, h1);
   if(!g_hasS && swept_up)
     {
      g_hasS = true;
      g_sSweep = t1;
      g_sExt = h1;
      g_sFvg = 0;
     }
   bool sig_short = false;
   if(g_hasS)
     {
      if(g_sFvg == 0)
        {
         if(t1 - g_sSweep > (datetime)(InpFvgWindow * sec))
            g_hasS = false;
         else if(h1 < iLow(_Symbol, PERIOD_M15, 3))
           {
            g_sFvg = t1;
            g_sTop = h1;
            g_sBot = iLow(_Symbol, PERIOD_M15, 3);
           }
        }
      else if(t1 > g_sFvg)
        {
         if(t1 - g_sFvg > (datetime)(InpRetestLook * sec))
            g_hasS = false;
         else if(h1 >= g_sBot && c1 < g_sTop)
           {
            sig_short = true;
            g_hasS = false;
           }
         else if(c1 > g_sTop + a)
            g_hasS = false;
        }
     }

   if(!sig_long && !sig_short)
      return;
   if(PositionSelect(_Symbol))
      return;                                  // одна сделка за раз

   //--- стоп за экстремум окна 60 баров до сигнального (как в бэктесте)
   int look = InpRetestLook + InpFvgWindow;
   double stop_raw;
   if(sig_long)
      stop_raw = MinLowShifts(2, 1 + look) - a * InpStopBufATR;
   else
      stop_raw = MaxHighShifts(2, 1 + look) + a * InpStopBufATR;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = sig_long ? ask : bid;
   double risk = MathAbs(entry - stop_raw);
   if(risk <= 0)
      return;
   double sl = entry - (sig_long ? 1.0 : -1.0) * risk;
   double tp = entry + (sig_long ? 1.0 : -1.0) * risk * InpTpR;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   double lots = LotsForRisk(risk);
   if(lots <= 0)
      return;

   bool ok = sig_long ? g_trade.Buy(lots, _Symbol, 0.0, sl, tp, "ict-long")
                      : g_trade.Sell(lots, _Symbol, 0.0, sl, tp, "ict-short");
   if(ok)
      g_entryBar = t1;
  }

//+------------------------------------------------------------------+
//| Выгрузка результатов теста в CSV (общая папка Files)             |
//+------------------------------------------------------------------+
double OnTester()
  {
   HistorySelect(0, TimeCurrent());
   int deals = HistoryDealsTotal();

   double deposit = 0.0;
   double balance = 0.0;      // накопленный P/L
   double peak = 0.0, dd_abs = 0.0, dd_pct = 0.0;
   int trades = 0, wins = 0;
   double gross_p = 0.0, gross_l = 0.0;
   double best = 0.0, worst = 0.0;
   string out_lines = "";

   for(int i = 0; i < deals; i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_IN)
        {
         if(HistoryDealGetInteger(tk, DEAL_TYPE) == DEAL_TYPE_BALANCE)
           {
            if(deposit == 0.0)
               deposit = HistoryDealGetDouble(tk, DEAL_PROFIT);
            continue;
           }
         balance += HistoryDealGetDouble(tk, DEAL_SWAP) + HistoryDealGetDouble(tk, DEAL_COMMISSION);
         continue;
        }
      if(HistoryDealGetInteger(tk, DEAL_TYPE) == DEAL_TYPE_BALANCE)
         continue;
      double p = HistoryDealGetDouble(tk, DEAL_PROFIT)
               + HistoryDealGetDouble(tk, DEAL_SWAP)
               + HistoryDealGetDouble(tk, DEAL_COMMISSION);
      balance += p;
      double bal_now = deposit + balance;
      trades++;
      if(p > 0) { wins++; gross_p += p; } else { gross_l += -p; }
      best = MathMax(best, p);
      worst = MathMin(worst, p);
      if(peak < bal_now) peak = bal_now;
      double d = peak - bal_now;
      if(d > dd_abs) dd_abs = d;
      if(peak > 0) dd_pct = MathMax(dd_pct, d / peak * 100.0);

      string com = HistoryDealGetString(tk, DEAL_COMMENT);
      out_lines += StringFormat("%s;%s;%.2f;%.2f;%.2f;%s\n",
                     TimeToString((datetime)HistoryDealGetInteger(tk, DEAL_TIME), TIME_DATE|TIME_MINUTES),
                     (HistoryDealGetInteger(tk, DEAL_TYPE) == DEAL_TYPE_BUY ? "CLOSE_BUY" : "CLOSE_SELL"),
                     p, bal_now, dd_pct, com);
     }

   balance = deposit + balance;
   double net = balance - deposit;
   double pf = (gross_l > 0) ? gross_p / gross_l : 999.0;

   string f = "ict_ea_" + _Symbol + "_result.csv";
   int fh = FileOpen(f, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ';');
   if(fh != INVALID_HANDLE)
     {
      FileWrite(fh, "symbol;deposit;balance;net;return_pct;trades;winrate_pct;profit_factor;max_dd_pct;best;worst");
      FileWrite(fh, StringFormat("%s;%.2f;%.2f;%.2f;%.1f;%d;%.1f;%.2f;%.2f;%.2f;%.2f",
                                _Symbol, deposit, balance, net,
                                deposit > 0 ? net / deposit * 100.0 : 0.0,
                                trades,
                                trades > 0 ? 100.0 * wins / trades : 0.0,
                                pf, dd_pct, best, worst));
      FileWrite(fh, "time;event;profit;balance_after;dd_pct;comment");
      FileWrite(fh, out_lines);
      FileClose(fh);
     }
   PrintFormat("ИТОГ: депозит %.2f -> баланс %.2f (нетто %+.2f, %.1f%%), сделок %d, винрейт %.0f%%, PF %.2f, максДД %.2f%%",
               deposit, balance, net, deposit > 0 ? net / deposit * 100.0 : 0.0,
               trades, trades > 0 ? 100.0 * wins / trades : 0.0, pf, dd_pct);
   return(net);
  }
//+------------------------------------------------------------------+
