//+------------------------------------------------------------------+
//|                                                    RangeBotEA.mq5|
//|  Перенос живой стратегии бота (range_v2 / MSNR) на MQL5.         |
//|  Зеркалит Python-логику:                                         |
//|    - зоны по телам свечей H4 (sonar.find_levels + flips);        |
//|    - вход: M15-бар проколол зону и закрылся обратно;             |
//|    - фильтры: Kaufman ER<=0.20, SMT Gold/Silver, лимит стопов;   |
//|    - стоп = 1.0*ATR(14,M15), цель = 3R или зона в [3R..5R];      |
//|    - выход по времени после 120 баров;                           |
//|    - риск 0.5% баланса на сделку.                                |
//|  Отличие от Python-бэктеста: вход по рынку на открытии следующего|
//|  бара (реалистичное исполнение в тестере), а не лимиткой по цене |
//|  уровня на баре сигнала.                                         |
//+------------------------------------------------------------------+
#property copyright "tg-signals"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//--- параметры = живые из .env бота
input group "--- Зоны H4 ---"
input int    InpH4Bars     = 1500;   // глубина истории H4 (баров)
input int    InpFracL      = 3;      // пивот: баров слева
input int    InpFracR      = 3;      // пивот: баров справа
input double InpTolMult    = 0.20;   // толщина кластера, доли ATR(H4)
input int    InpTouches    = 2;      // минимум касаний
input int    InpMinAge     = 6;      // минимальный возраст уровня (баров H4)
input double InpBreakMult  = 2.0;    // пробой: во сколько раз толще кластера
input int    InpFlipsKeep  = 60;     // сколько баров живёт перевёрнутый уровень
input double InpFlipTol    = 0.10;   // порог переворота, доли ATR(H4)

input group "--- Фильтры ---"
input int    InpErLen      = 50;     // период Kaufman ER
input double InpErMax      = 0.20;   // максимальный ER (выше — тренд, входим во флэте)
input bool   InpUseSMT     = true;   // фильтр SMT Gold/Silver
input string InpSmtSymbol  = "XAGUSD";
input string InpSmtFor     = "XAUUSD"; // для какого символа требуем подтверждение
input int    InpSmtLook    = 30;     // окно SMT (баров M15)
input int    InpMaxDailyStops = 3;   // стопов за день — день закрыт (0 = выкл)

input group "--- Сделка ---"
input double InpStopATR    = 1.0;    // стоп, ATR(14,M15)
input double InpTpR        = 3.0;    // базовая цель, R
input double InpTargetMinR = 1.0;    // зона-цель: ближе этого не берём
input double InpTargetMaxR = 5.0;    // зона-цель: дальше этого не тянем
input int    InpMaxBars    = 120;    // выход по времени (баров M15)
input double InpRiskPct    = 0.5;    // риск на сделку, % баланса

//---kind уровня: 0 = поддержка, 1 = сопротивление
#define SUP 0
#define RES 1
#define MAX_ACTIVE 256
#define MAX_FLIPS 256

struct SLevel
  {
   int               kind;
   double            price;
   int               touches;
   int               born;
   long              key;        // округлённая цена для учёта переворотов
   int               flipped_at; // бар H4 переворота, -1 если не перевёрнут
  };

struct SFlip
  {
   int               kind;
   double            price;
   int               until;      // последний бар H4, на котором уровень жив
  };

struct SRow
  {
   int               n;
   int               kind[];
   double            price[];
  };

CTrade   g_trade;
SLevel   g_active[];
SFlip    g_flips[];
SRow     g_rows[];
datetime g_rowTime[];
int      g_rowCount = 0;
datetime g_lastH4   = 0;
datetime g_lastBar  = 0;
datetime g_entryBar = 0;
double   g_entryRisk = 0.0;      // ценовое расстояние стопа сделки
int      g_smtHandleErrLogged = 0;

//+------------------------------------------------------------------+
//| Инициализация                                                    |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_trade.SetExpertMagicNumber(20261026);
   g_trade.SetDeviationInPoints(50);
   g_lastBar  = 0;
   g_lastH4   = 0;
   g_rowCount = 0;
   ArrayResize(g_rows, 0);
   ArrayResize(g_rowTime, 0);
   if(InpUseSMT && SymbolSelect(InpSmtSymbol, true))
      Print("SMT-пара: ", InpSmtSymbol);
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| TR по телам свечей (как в Python: h'=max(o,c), l'=min(o,c))      |
//+------------------------------------------------------------------+
void BodyTrueRange(const double &o[], const double &bh[], const double &bl[],
                   const double &c[], double &tr[])
  {
   int n = ArraySize(c);
   ArrayResize(tr, n);
   if(n == 0)
      return;
   tr[0] = bh[0] - bl[0];
   for(int i = 1; i < n; i++)
      tr[i] = MathMax(bh[i] - bl[i], MathMax(MathAbs(bh[i] - c[i-1]), MathAbs(bl[i] - c[i-1])));
  }

//+------------------------------------------------------------------+
//| Wilder ATR (rma, посев SMA) — как ta.atr в Python                |
//+------------------------------------------------------------------+
void WilderAtr(const double &tr[], int period, double &atr[])
  {
   int n = ArraySize(tr);
   ArrayResize(atr, n);
   for(int i = 0; i < n; i++)
      atr[i] = (i < period - 1) ? 0.0 : EMPTY_VALUE;
   if(n < period)
      return;
   double sum = 0.0;
   for(int i = 0; i < period; i++)
      sum += tr[i];
   atr[period - 1] = sum / period;
   for(int i = period; i < n; i++)
      atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period;
  }

//+------------------------------------------------------------------+
//| Пересборка таблицы зон H4 (полный проход по закрытым барам)      |
//+------------------------------------------------------------------+
bool BuildRows()
  {
   int total = Bars(_Symbol, PERIOD_H4);
   int count = MathMin(InpH4Bars, total - 1);      // без текущего (незакрытого) бара
   if(count < InpFracL + InpFracR + 20)
      return(false);

   double o[], h[], l[], c[];
   datetime t[];
   if(CopyOpen(_Symbol, PERIOD_H4, 1, count, o)  != count) return(false);
   if(CopyHigh(_Symbol, PERIOD_H4, 1, count, h)  != count) return(false);
   if(CopyLow(_Symbol, PERIOD_H4, 1, count, l)   != count) return(false);
   if(CopyClose(_Symbol, PERIOD_H4, 1, count, c) != count) return(false);
   if(CopyTime(_Symbol, PERIOD_H4, 1, count, t)  != count) return(false);

   double bh[], bl[], tr[], atr[];
   ArrayResize(bh, count);
   ArrayResize(bl, count);
   for(int i = 0; i < count; i++)
     {
      bh[i] = MathMax(o[i], c[i]);
      bl[i] = MathMin(o[i], c[i]);
     }
   BodyTrueRange(o, bh, bl, c, tr);
   WilderAtr(tr, 14, atr);

   ArrayResize(g_active, 0);
   ArrayResize(g_flips, 0);
   ArrayResize(g_rows, count);
   ArrayResize(g_rowTime, count);

   for(int i = 0; i < count; i++)
     {
      double a   = (atr[i] == EMPTY_VALUE) ? 0.0 : atr[i];
      double tol = a * InpTolMult;

      //--- новые пивоты: на баре i подтверждается экстремум бара j = i - frac_r
      if(i >= InpFracL + InpFracR)
        {
         int j = i - InpFracR;
         double wh = h[j];
         double wl = l[j];
         bool ph = true, pl = true;
         for(int k = j - InpFracL; k <= j + InpFracR; k++)
           {
            if(h[k] > wh) ph = false;
            if(l[k] < wl) pl = false;
           }
         if(ph) AddPivot(RES, h[j], a, i);
         if(pl) AddPivot(SUP, l[j], a, i);
        }

      //--- снимаем пробитые уровни
      KeepUnbroken(c[i], tol);

      //--- строка таблицы: выжившие уровни с достаточными касаниями/возрастом
      int n = 0;
      int kinds[];  double prices[];
      ArrayResize(kinds, 0);
      ArrayResize(prices, 0);
      for(int k = 0; k < ArraySize(g_active); k++)
        {
         if(g_active[k].touches < InpTouches)
            continue;
         if(i - g_active[k].born < InpMinAge)
            continue;
         if(g_active[k].flipped_at >= 0)
            continue;
         Push(kinds, prices, n, g_active[k].kind, g_active[k].price);
        }

      //--- перевороты: закрытие H4 прошло сквозь уровень на flip_tol*ATR
      for(int k = 0; k < n; k++)
        {
         double px = prices[k];
         if(kinds[k] == RES && c[i] > px + a * InpFlipTol)
           {
            AddFlip(SUP, px, i + InpFlipsKeep);
            MarkFlipped(px, i);
           }
         else if(kinds[k] == SUP && c[i] < px - a * InpFlipTol)
           {
            AddFlip(RES, px, i + InpFlipsKeep);
            MarkFlipped(px, i);
           }
        }
      // (после MarkFlipped строка могла измениться — пересобираем видимые)
      n = 0;
      ArrayResize(kinds, 0);
      ArrayResize(prices, 0);
      for(int k = 0; k < ArraySize(g_active); k++)
        {
         if(g_active[k].touches < InpTouches)
            continue;
         if(i - g_active[k].born < InpMinAge)
            continue;
         if(g_active[k].flipped_at >= 0)
            continue;
         Push(kinds, prices, n, g_active[k].kind, g_active[k].price);
        }

      //--- снимаем истёкшие копии ДО формирования строки (until >= i — живёт)
      PruneFlips(i);

      //--- живые перевёрнутые копии
      for(int k = 0; k < ArraySize(g_flips); k++)
         Push(kinds, prices, n, g_flips[k].kind, g_flips[k].price);

      g_rows[i].n = n;
      ArrayResize(g_rows[i].kind, n);
      ArrayResize(g_rows[i].price, n);
      for(int k = 0; k < n; k++)
        {
         g_rows[i].kind[k]  = kinds[k];
         g_rows[i].price[k] = prices[k];
        }
      g_rowTime[i] = t[i];
     }

   g_rowCount = count;
   return(true);
  }

//+------------------------------------------------------------------+
void Push(int &kinds[], double &prices[], int &n, int kind, double price)
  {
   ArrayResize(kinds, n + 1);
   ArrayResize(prices, n + 1);
   kinds[n] = kind;
   prices[n] = price;
   n++;
  }

//+------------------------------------------------------------------+
void AddPivot(int kind, double piv, double a, int i)
  {
   double tol = a * InpTolMult;
   for(int k = 0; k < ArraySize(g_active); k++)
     {
      if(g_active[k].kind != kind)
         continue;
      if(MathAbs(g_active[k].price - piv) <= tol)
        {
         g_active[k].touches += 1;
         g_active[k].price = (g_active[k].price * (g_active[k].touches - 1) + piv) / g_active[k].touches;
         return;
        }
     }
   int n = ArraySize(g_active);
   if(n >= MAX_ACTIVE)
      return;
   ArrayResize(g_active, n + 1);
   g_active[n].kind = kind;
   g_active[n].price = piv;
   g_active[n].touches = 1;
   g_active[n].born = i;
   g_active[n].key = PriceKey(piv);
   g_active[n].flipped_at = -1;
  }

//+------------------------------------------------------------------+
long PriceKey(double p)
  {
   return((long)MathRound(p * 100.0));
  }

//+------------------------------------------------------------------+
void KeepUnbroken(double close_px, double tol)
  {
   double limit = tol * InpBreakMult;
   int n = ArraySize(g_active);
   int w = 0;
   for(int k = 0; k < n; k++)
     {
      bool broken = (g_active[k].kind == RES && close_px > g_active[k].price + limit) ||
                    (g_active[k].kind == SUP && close_px < g_active[k].price - limit);
      if(!broken)
        {
         if(w != k)
            g_active[w] = g_active[k];
         w++;
        }
     }
   ArrayResize(g_active, w);
  }

//+------------------------------------------------------------------+
void AddFlip(int kind, double price, int until)
  {
   int n = ArraySize(g_flips);
   if(n >= MAX_FLIPS)
      return;
   ArrayResize(g_flips, n + 1);
   g_flips[n].kind = kind;
   g_flips[n].price = price;
   g_flips[n].until = until;
  }

//+------------------------------------------------------------------+
void MarkFlipped(double price, int i)
  {
   long key = PriceKey(price);
   for(int k = 0; k < ArraySize(g_active); k++)
      if(g_active[k].key == key)
        {
         g_active[k].flipped_at = i;
         return;
        }
  }

//+------------------------------------------------------------------+
void PruneFlips(int i)
  {
   int n = ArraySize(g_flips);
   int w = 0;
   for(int k = 0; k < n; k++)
     {
      if(g_flips[k].until >= i)
        {
         if(w != k)
            g_flips[w] = g_flips[k];
         w++;
        }
     }
   ArrayResize(g_flips, w);
  }

//+------------------------------------------------------------------+
//| Строка зон для M15-бара с временем t: уровни ЗАКРЫТОГО H4-бара   |
//+------------------------------------------------------------------+
bool RowForTime(datetime t, int &kinds[], double &prices[])
  {
   int j = iBarShift(_Symbol, PERIOD_H4, t, false);
   if(j < 0)
      return(false);
   int idx = -1;
   for(int i = g_rowCount - 1; i >= 0; i--)
      if(g_rowTime[i] <= t)
        {
         idx = i;
         break;
        }
   // idx — бар H4, содержащий t; берём предыдущий (закрытый относительно t)
   int row = idx - 1;
   if(row < 0 || row >= g_rowCount)
      return(false);
   ArrayResize(kinds, g_rows[row].n);
   ArrayResize(prices, g_rows[row].n);
   for(int k = 0; k < g_rows[row].n; k++)
     {
      kinds[k] = g_rows[row].kind[k];
      prices[k] = g_rows[row].price[k];
     }
   return(true);
  }

//+------------------------------------------------------------------+
//| Kaufman ER на закрытом M15-баре (shift 1)                        |
//+------------------------------------------------------------------+
double EfficiencyRatio()
  {
   int need = InpErLen + 2;
   double c[];
   if(CopyClose(_Symbol, PERIOD_M15, 1, need, c) != need)
      return(EMPTY_VALUE);
   double net = MathAbs(c[need - 1] - c[0]);
   double path = 0.0;
   for(int i = 1; i < need; i++)
      path += MathAbs(c[i] - c[i-1]);
   if(path <= 0.0)
      return(EMPTY_VALUE);
   return(net / path);
  }

//+------------------------------------------------------------------+
//| SMT: наш инструмент снял экстремум, парный — нет                 |
//+------------------------------------------------------------------+
bool SmtOk(int shift, bool is_long)
  {
   if(!InpUseSMT || InpSmtLook <= 0)
      return(true);
   if(_Symbol != InpSmtFor)
      return(true);                      // как в боте: SMT только для SMT_FOR
   datetime t = iTime(_Symbol, PERIOD_M15, shift);
   int a = iBarShift(InpSmtSymbol, PERIOD_M15, t, false);
   if(a < InpSmtLook + 1)
      return(true);                      // нет истории пары — не блокируем (как smt=None)
   //--- окно: InpSmtLook баров ПЕРЕД сигнальным (как chart.l[i-look:i] в Python)
   double my[];
   if(CopyLow(_Symbol, PERIOD_M15, shift - InpSmtLook, InpSmtLook, my) != InpSmtLook)
      return(true);
   if(is_long)
     {
      double ref_bar[], ref_win[];
      if(CopyLow(InpSmtSymbol, PERIOD_M15, a, 1, ref_bar) != 1) return(true);
      if(CopyLow(InpSmtSymbol, PERIOD_M15, a - InpSmtLook, InpSmtLook, ref_win) != InpSmtLook)
         return(true);
      double my_min = my[0], ref_min = ref_win[0];
      for(int k = 1; k < InpSmtLook; k++)
        {
         my_min = MathMin(my_min, my[k]);
         ref_min = MathMin(ref_min, ref_win[k]);
        }
      double my_bar = iLow(_Symbol, PERIOD_M15, shift);
      return(my_bar <= my_min && ref_bar[0] > ref_min);
     }
   double ref_bar[], ref_win[];
   if(CopyHigh(InpSmtSymbol, PERIOD_M15, a, 1, ref_bar) != 1) return(true);
   if(CopyHigh(InpSmtSymbol, PERIOD_M15, a - InpSmtLook, InpSmtLook, ref_win) != InpSmtLook)
      return(true);
   double my_max = my[0], ref_max = ref_win[0];
   for(int k = 1; k < InpSmtLook; k++)
     {
      my_max = MathMax(my_max, my[k]);
      ref_max = MathMax(ref_max, ref_win[k]);
     }
   double my_bar = iHigh(_Symbol, PERIOD_M15, shift);
   return(my_bar >= my_max && ref_bar[0] < ref_max);
  }

//+------------------------------------------------------------------+
//| Стопов сегодня уже было? (по дате закрытого бара)                |
//+------------------------------------------------------------------+
int StopsToday(datetime bar_time)
  {
   if(InpMaxDailyStops <= 0)
      return(0);
   MqlDateTime d;
   TimeToStruct(bar_time, d);
   int cnt = 0;
   HistorySelect(bar_time - 86400, TimeCurrent() + 60);
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; i++)
     {
      ulong ticket = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != 20261026)
         continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT)
         continue;
      long reason = HistoryDealGetInteger(ticket, DEAL_REASON);
      if(reason != DEAL_REASON_SL)
         continue;
      MqlDateTime dd;
      TimeToStruct((datetime)HistoryDealGetInteger(ticket, DEAL_TIME), dd);
      if(dd.day == d.day && dd.mon == d.mon && dd.year == d.year)
         cnt++;
     }
   return(cnt);
  }

//+------------------------------------------------------------------+
//| Лот по риску % баланса                                           |
//+------------------------------------------------------------------+
double LotsForRisk(double risk_price)
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double money = balance * InpRiskPct / 100.0;
   if(risk_price <= 0)
      return(0.0);
   //--- стоимость движения цены на 1.0 для 1 лота: спрашиваем у движка,
   //--- т.к. SYMBOL_TRADE_TICK_VALUE у части серверов врёт (XAUUSD: 0.1 вместо 1.0)
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
//| Цель: 3R или ближайшая противоположная зона в [3R..5R]           |
//+------------------------------------------------------------------+
double TargetPrice(double entry, double risk, bool is_long,
                   const int &kinds[], const double &prices[])
  {
   double target = entry + (is_long ? 1.0 : -1.0) * risk * InpTpR;
   double best = 0.0;
   bool found = false;
   for(int k = 0; k < ArraySize(prices); k++)
     {
      double p = prices[k];
      if(is_long && kinds[k] == RES && p > entry + risk * InpTargetMinR)
         if(!found || p < best) { best = p; found = true; }
      if(!is_long && kinds[k] == SUP && p < entry - risk * InpTargetMinR)
         if(!found || p > best) { best = p; found = true; }
     }
   if(found)
     {
      double dist_r = MathAbs(best - entry) / risk;
      if(dist_r >= InpTpR && dist_r <= InpTargetMaxR)
         target = best;
     }
   return(target);
  }

//+------------------------------------------------------------------+
//| Новый бар M15?                                                   |
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
//| Основной обработчик                                              |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!NewBar())
      return;
   int sh = 1;                                   // закрытый M15-бар
   datetime bar_time = iTime(_Symbol, PERIOD_M15, sh);
   if(bar_time == 0)
      return;

   //--- новая свеча H4 -> пересобрать зоны
   datetime h4 = iTime(_Symbol, PERIOD_H4, 0);
   if(h4 != g_lastH4 || g_rowCount == 0)
     {
      if(!BuildRows())
         return;
      g_lastH4 = h4;
     }

   //--- выход по времени
   if(PositionSelect(_Symbol) && g_entryBar > 0)
     {
      int held = int((bar_time - g_entryBar) / PeriodSeconds(PERIOD_M15));
      if(held > InpMaxBars)
        {
         g_trade.PositionClose(_Symbol);
         g_entryBar = 0;
        }
     }

   //--- одна сделка за раз
   if(PositionSelect(_Symbol))
      return;

   //--- дневной лимит стопов
   if(InpMaxDailyStops > 0 && StopsToday(bar_time) >= InpMaxDailyStops)
      return;

   //--- фильтр режима: во флэте преимущество, в тренде его почти нет
   double er = EfficiencyRatio();
   if(er == EMPTY_VALUE || er > InpErMax)
      return;

   //--- сигнальный бар: прокол зоны и закрытие обратно
   int kinds[];
   double prices[];
   if(!RowForTime(bar_time, kinds, prices))
      return;
   double hi = iHigh(_Symbol, PERIOD_M15, sh);
   double lo = iLow(_Symbol, PERIOD_M15, sh);
   double cl = iClose(_Symbol, PERIOD_M15, sh);

   int sig_kind = -1;
   double sig_px = 0.0;
   for(int k = 0; k < ArraySize(prices); k++)
     {
      if(kinds[k] == SUP && lo <= prices[k] && cl > prices[k])
        {
         sig_kind = SUP;
         sig_px = prices[k];
         break;
        }
      if(kinds[k] == RES && hi >= prices[k] && cl < prices[k])
        {
         sig_kind = RES;
         sig_px = prices[k];
         break;
        }
     }
   if(sig_kind < 0)
      return;

   bool is_long = (sig_kind == SUP);
   if(!SmtOk(sh, is_long))
      return;

   //--- стоп и цель от фактической цены входа (рынок на открытии бара)
   double atr15[];
   double tr_buf[];
   int need = 15 + 400;
   double hb[], lb[], cb[];
   if(CopyHigh(_Symbol, PERIOD_M15, sh, need, hb) != need) return;
   if(CopyLow(_Symbol, PERIOD_M15, sh, need, lb)  != need) return;
   if(CopyClose(_Symbol, PERIOD_M15, sh, need, cb) != need) return;
   double ob[];
   if(CopyOpen(_Symbol, PERIOD_M15, sh, need, ob) != need) return;
   BodyTrueRange(ob, hb, lb, cb, tr_buf);
   WilderAtr(tr_buf, 14, atr15);
   double a = atr15[need - 1];
   if(a <= 0 || a == EMPTY_VALUE)
      return;
   double risk = a * InpStopATR;
   g_entryRisk = risk;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = is_long ? ask : bid;
   double sl = is_long ? entry - risk : entry + risk;
   double tp = TargetPrice(entry, risk, is_long, kinds, prices);

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   double lots = LotsForRisk(risk);
   if(lots <= 0)
      return;

   bool ok = is_long ? g_trade.Buy(lots, _Symbol, 0.0, sl, tp, "range-long")
                     : g_trade.Sell(lots, _Symbol, 0.0, sl, tp, "range-short");
   if(ok)
      g_entryBar = bar_time;
  }

//+------------------------------------------------------------------+
//| Выгрузка результатов теста в CSV (общая папка Files)             |
//+------------------------------------------------------------------+
double OnTester()
  {
   HistorySelect(0, TimeCurrent());
   int deals = HistoryDealsTotal();

   double deposit = 0.0;
   double balance = 0.0;
   double peak = 0.0, dd_abs = 0.0, dd_pct = 0.0;
   int trades = 0, wins = 0;
   double gross_p = 0.0, gross_l = 0.0;
   double best = 0.0, worst = 0.0;
   double rs = 0.0;                       // сумма R (прибыль / плановый риск в $)
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
      balance += p;                          // накопленный P/L
      double bal_now = deposit + balance;    // реальный баланс
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

   balance = deposit + balance;               // финальный баланс
   double net = balance - deposit;
   double pf = (gross_l > 0) ? gross_p / gross_l : 999.0;

   string f = "range_ea_" + _Symbol + "_result.csv";
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
