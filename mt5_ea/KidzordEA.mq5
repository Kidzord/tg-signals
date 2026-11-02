//+------------------------------------------------------------------+
//|                                                     KidzordEA.mq5|
//|  Перенос Pine-стратегии KidzordFx V1.6 (SMC / EMA200 чек-лист)   |
//|  на MQL5 для Strategy Tester. Зеркалит гейты r1..r12 индикатора: |
//|    - контекст: EMA200 H4 (закрытый бар), наклон, дневной EMA200; |
//|    - касание/тест EMA200 на H1, уровни PDH/PDL/PWH/PWL;          |
//|    - свип по фракталам (2/2), стоп «за манипуляцией» или свинг;  |
//|    - триггеры: пин / поглощение / микро-BOS / IFVG;              |
//|    - SMC-факторы: свип, BOS, FVG, CHoCH (нужно needFac из 4);    |
//|    - сессии Лдн 08-17 / НЙ 09:30-16:00 с DST, ролловер, новости; |
//|    - мягкий режим: softNeed из (наклон, ER/диапазон, факторы,    |
//|      спред-фильтр); цель/RR и риск-коридор обязательны;          |
//|    - TP1/TP2/TP3 = 1R/2R/3R, доли 40/30/30, БУ после TP1,        |
//|      трейлинг UT Bot (2*ATR) после TP1, выход по времени 120 бар;|
//|    - дневные лимиты: 3 сделки / -2R.                             |
//|  Исполнение честное: вход по рынку на открытии следующего бара   |
//|  (ask/bid), все выходы рыночные по касанию bid/ask на тиках      |
//|  (в модели M1-OHLC), без серверных SL/TP. Отличия от Pine:       |
//|  трейлинг применяется с закрытия бара (в Pine стоп двигается     |
//|  close'ом того же бара и проверяется его же low — нереалистично).|
//+------------------------------------------------------------------+
#property copyright "tg-signals"
#property version   "1.00"

#include <Trade\Trade.mqh>

//--- ТФ и контекст
input int    InpSlopeLen    = 20;    // окно наклона, баров H4
input double InpMinSlope    = 0.01;  // мин. наклон EMA200, ATR/бар
input bool   InpUseDayCtx   = true;  // дневной контекст (цена vs EMA200 D)
input double InpTolHtf      = 0.6;   // допуск касания, ATR (H1)
input int    InpMemHours    = 24;    // память касания, часов
input int    InpChopLen     = 100;   // окно ER, баров
input double InpErMin       = 0.20;  // мин. эффективность (ER 0-1)
input double InpTolLtf      = 0.15;  // зона теста EMA200, ATR
input int    InpTestLook    = 20;    // тест актуален, баров
input int    InpTrigLook    = 5;     // триггер актуален, баров
//--- триггеры
input double InpPinMult     = 1.5;   // пин: тень/тело
input bool   InpUsePin      = true;
input bool   InpUseEngulf   = true;
input bool   InpUseMicro    = true;
input int    InpTrigBosLen  = 5;     // микро-BOS: длина свинга
input bool   InpUseIfvg     = true;
input int    InpIfvgLook    = 60;    // IFVG актуален, баров
input double InpIfvgTol     = 0.10;  // IFVG: допуск теста, ATR
input int    InpMinTrigPow  = 1;     // мин. сила триггера (0=любой)
input double InpMinRangeK   = 0.40;  // мин. размер свечи, ATR
//--- стоп/риск/цель
input int    InpSwingLen    = 10;    // стоп: за N баров
input double InpStopBuf     = 0.20;  // отступ стопа, ATR
input int    InpManipLook   = 20;    // манипуляция актуальна, баров
input double InpRiskMaxAtr  = 4.0;   // риск макс, ATR
input int    InpLiqLen      = 60;    // свинг для цели, баров
input double InpTgtMin      = 0.15;  // цель мин, %
input double InpTgtMax      = 5.00;  // цель макс, %
input double InpMinRR       = 1.5;   // мин. R:R
input bool   InpUseDayBud   = true;  // цель не дальше дневного хода
input double InpDayBudgetK  = 1.0;   // дневной ход = ATR(D) x
//--- сделка
input double InpTp1R        = 1.0;
input double InpTp2R        = 2.0;
input double InpTp3R        = 3.0;
input int    InpP1Pct       = 40;    // закрыть на TP1, %
input int    InpP2Pct       = 30;    // закрыть на TP2, %
input int    InpBeAfter     = 1;     // БУ после TP № (0=нет)
input int    InpMaxBars     = 120;   // выход по времени, баров
input int    InpReEntryBars = 10;    // кулдаун после выхода, баров
input bool   InpUseTrail    = true;
input int    InpTrailAfter  = 1;     // трейлинг после TP №
input double InpTrailK      = 2.0;   // трейлинг: отступ, ATR x
input double InpRiskPct     = 1.0;   // риск на сделку, % баланса
input int    InpMaxDayTrades= 3;     // макс. сделок за день (0=без)
input double InpDayStopR    = 2.0;   // стоп дня, R (0=без)
//--- уровни
input int    InpLvPersist   = 6;     // закрепление за уровнем, баров
input double InpLvTol       = 0.15;  // допуск теста уровня, ATR
input int    InpLvLook      = 60;    // тест уровня актуален, баров
//--- SMC
input int    InpSweepLen    = 20;    // свип: экстремум за N баров
input int    InpFracL       = 2;     // фрактал: слева
input int    InpFracR       = 2;     // фрактал: справа
input int    InpBosLen      = 20;    // BOS: структура за N баров
input int    InpFvgLook     = 30;    // FVG актуален, баров
input int    InpRangeLen    = 30;    // диапазон: окно
input double InpRangeMinAtr = 2.0;
input double InpRangeMaxAtr = 20.0;
input int    InpFacLook     = 30;    // фактор актуален, баров
input int    InpNeedFac     = 2;     // нужно факторов (из 4)
//--- сессии и фильтры
input bool   InpUseSess     = true;  // только внутри сессий Лдн/НЙ
input bool   InpSessLdn     = true;  // сессия Лондон включена
input bool   InpSessNy      = true;  // сессия Нью-Йорк включена
input bool   InpNewsOn      = true;  // блокировать окна новостей
input bool   InpRollOn      = true;  // исключать ролловер 16:55-17:05 НЙ
input bool   InpUseSprd     = true;  // спред: бар >= N x спред
input double InpSprTicks    = 5.0;
input int    InpSprK        = 5;
input int    InpSweepDelay  = 1;     // вход не раньше N баров после свипа
input bool   InpSoftMode    = true;  // мягкий режим
input int    InpSoftNeed    = 2;     // мягкий: минимум из 4
input bool   InpLimitTP     = false; // TP лимитками (на неттинге тестера конфликтует со SL)

CTrade   g_trade;

#define BIG_AGE 1000000
#define M15CNT  130            // глубина копии M15 (покрывает chopLen=100 + запас)

//--- реестр уровней (PDH/PDL/PWH/PWL; ручные и ON/IB выключены как в Pine)
struct SLvl
  {
   double px;
   bool   act;
   int    kind;      // 1 сопротивление, -1 поддержка
   int    touchS, rejectS, touchL, rejectL;   // номера баров событий, -1 = нет
  };
SLvl g_lv[4];

//--- события (номера обработанных закрытых баров, BIG_AGE = не было)
int g_touchLBar=BIG_AGE, g_touchSBar=BIG_AGE;          // касание EMA200 H1
int g_emaTestLBar=BIG_AGE, g_emaTestSBar=BIG_AGE;      // тест EMA200 H1
int g_fracHiBar=BIG_AGE, g_fracLoBar=BIG_AGE;
double g_fracHi=0, g_fracLo=0;
int g_sweepLBar=BIG_AGE, g_sweepSBar=BIG_AGE;
double g_manipLo=0, g_manipHi=0;
int g_bosLBar=BIG_AGE, g_bosSBar=BIG_AGE;
int g_trend=0;                                         // 1/0/-1 по последовательности BOS
int g_chochUpBar=BIG_AGE, g_chochDnBar=BIG_AGE;
int g_fvgLBar=BIG_AGE, g_fvgSBar=BIG_AGE;
double g_fvgBotS=0, g_fvgTopBull=0, g_fvgTopL=0, g_fvgBotBear=0;
int g_invBarS=-1, g_invBarL=-1;
int g_patLBar=BIG_AGE, g_patSBar=BIG_AGE;
int g_bestTouchLBar=BIG_AGE, g_bestRejLBar=BIG_AGE;
int g_bestTouchSBar=BIG_AGE, g_bestRejSBar=BIG_AGE;
int g_setupLvAgoL=BIG_AGE, g_setupLvAgoS=BIG_AGE;
double g_setupLvPxL=0, g_setupLvPxS=0;

//--- состояние сетапа (автомат Pine)
int g_setupState=0, g_setupSide=0;
int g_n=0;                       // счётчик обработанных закрытых M15-баров
int g_lastExitN=-1;
datetime g_day=0;
int g_dayTrades=0;
double g_dayR=0.0;

//--- сделка
int    g_dir=0;
double g_entry=0, g_stop=0, g_tp1=0, g_tp2=0, g_tp3=0, g_rDist=0;
int    g_entryN=-1;
datetime g_entryTime=0;
double g_lots0=0, g_partDone=0, g_pineR=0, g_riskMoney=0;
bool   g_hit1=false, g_hit2=false, g_hit3=false, g_beOn=false;
bool   g_tp3Placed=false;
int    g_sessId=0;
double g_perUnit=1.0;            // $ на 1.0 движения цены на 1.0 лота
double g_stopLCur=0, g_stopSCur=0;  // кандидаты стопа текущего закрытого бара
double g_atrLast=0;                 // ATR последнего закрытого бара (для трейлинга)

//--- статистика
int    g_nTrades=0, g_nWins=0;
double g_sumR=0, g_sumWin=0, g_sumLoss=0;
double g_eqR=0, g_peakR=0, g_ddR=0;
int    g_cntSl=0, g_cntTp1=0, g_cntTp2=0, g_cntTp3=0, g_cntTime=0;
int    g_cntR[13];
string g_gatesCsv = "time;isL;r1;r2;r3;r4;r5;r6;r7;r8;r9;r10;r11;r12;entry\n";
int    g_cntEntry=0, g_cntSetup=0;
string g_tradesCsv="";

//--- кэш старших ТФ
datetime g_lastH4=0, g_lastH1=0, g_lastD1=0;
double g_ema4h=0, g_ema4hPast=0, g_atr4h=0, g_htfClose=0;
double g_emaTouch=0, g_atrTouch=0;
double g_atrD=0, g_dEma=0;

//+------------------------------------------------------------------+
//| Дата/время и DST                                                  |
//+------------------------------------------------------------------+
datetime DateAt(int y, int m, int d)
  {
   MqlDateTime s;
   s.year=y; s.mon=m; s.day=d; s.hour=0; s.min=0; s.sec=0;
   return StructToTime(s);
  }
int Dow(datetime t) { MqlDateTime s; TimeToStruct(t, s); return s.day_of_week; }
datetime LastSunday(int y, int m)
  {
   int y2 = (m==12) ? y+1 : y;
   int m2 = (m==12) ? 1  : m+1;
   datetime t = DateAt(y2, m2, 1) - 86400;
   while(Dow(t) != 0) t -= 86400;
   return t;
  }
datetime NthSunday(int y, int m, int n)
  {
   datetime t = DateAt(y, m, 1);
   while(Dow(t) != 0) t += 86400;
   return t + (n-1)*7*86400;
  }
//--- ЕС: летнее время с последнего вс марта 01:00 UTC по последнее вс октября
bool EuDst(datetime utc)
  {
   MqlDateTime s; TimeToStruct(utc, s);
   return utc >= LastSunday(s.year, 3) && utc < LastSunday(s.year, 10);
  }
//--- США: со второго вс марта по первое вс ноября (граница дня, точность ~сутки)
bool UsDst(datetime utc)
  {
   MqlDateTime s; TimeToStruct(utc, s);
   return utc >= NthSunday(s.year, 3, 2) && utc < NthSunday(s.year, 11, 1);
  }
//--- сервер MetaQuotes-Demo = EET (UTC+2/лето +3)
datetime ServerToUtc(datetime srv)
  {
   datetime u3 = srv - 3*3600;
   if(EuDst(u3)) return u3;
   return srv - 2*3600;
  }
int LdnOffMin(datetime utc) { return EuDst(utc) ? 60 : 0; }
int NyOffMin(datetime utc)  { return UsDst(utc) ? -240 : -300; }
//--- окно сессии [startMin, endMin) по местному времени зоны
bool InWin(datetime srv, int startMin, int endMin, int offMin)
  {
   long secs = (long)ServerToUtc(srv) + (long)offMin*60;
   int m = (int)((secs % 86400) / 60);
   if(m < 0) m += 1440;
   return m >= startMin && m < endMin;
  }
bool InLdn(datetime srv)
  {
   datetime utc = ServerToUtc(srv);
   return InWin(srv, 8*60, 17*60, LdnOffMin(utc));
  }
bool InNy(datetime srv)
  {
   datetime utc = ServerToUtc(srv);
   return InWin(srv, 9*60+30, 16*60, NyOffMin(utc));
  }
bool InRollover(datetime srv)  // 16:55-17:05 НЙ
  {
   datetime utc = ServerToUtc(srv);
   return InWin(srv, 16*60+55, 17*60+5, NyOffMin(utc));
  }
bool InNews(datetime srv)      // 08:28-08:34 и 13:58-14:04 НЙ
  {
   datetime utc = ServerToUtc(srv);
   return InWin(srv, 8*60+28, 8*60+34, NyOffMin(utc)) ||
          InWin(srv, 13*60+58, 14*60+4, NyOffMin(utc));
  }

//+------------------------------------------------------------------+
//| Серия: EMA на произвольном ТФ по закрытым барам (shift>=1)        |
//+------------------------------------------------------------------+
double EmaAt(ENUM_TIMEFRAMES tf, int shift, int period)
  {
   int avail = (int)MathMin(3000, Bars(_Symbol, tf) - shift - 1);
   int count = MathMin(period*6, avail);
   if(count < period) return EMPTY_VALUE;
   double c[];
   if(CopyClose(_Symbol, tf, shift, count, c) != count) return EMPTY_VALUE;
   double k = 2.0/(period+1.0);
   double e = c[0];
   for(int i=1; i<count; i++) e = c[i]*k + e*(1.0-k);
   return e;
  }
//--- Wilder ATR на произвольном ТФ по закрытым барам
double AtrAt(ENUM_TIMEFRAMES tf, int shift, int period)
  {
   int avail = (int)MathMin(3000, Bars(_Symbol, tf) - shift - 1);
   int count = MathMin(period*60, avail);
   if(count < period + 1) return EMPTY_VALUE;
   double h[], l[], c[];
   if(CopyHigh(_Symbol, tf, shift, count, h)  != count) return EMPTY_VALUE;
   if(CopyLow(_Symbol, tf, shift, count, l)   != count) return EMPTY_VALUE;
   if(CopyClose(_Symbol, tf, shift, count, c) != count) return EMPTY_VALUE;
   double atr = h[0]-l[0];
   for(int i=1; i<count; i++)
     {
      double tr = MathMax(h[i]-l[i], MathMax(MathAbs(h[i]-c[i-1]), MathAbs(l[i]-c[i-1])));
      atr = (atr*(period-1) + tr)/period;
     }
   return atr;
  }
void RefreshHTF()
  {
   datetime h4 = iTime(_Symbol, PERIOD_H4, 0);
   if(h4 != g_lastH4)
     {
      g_ema4h     = EmaAt(PERIOD_H4, 1, 200);
      g_ema4hPast = EmaAt(PERIOD_H4, 1+InpSlopeLen, 200);
      g_atr4h     = AtrAt(PERIOD_H4, 1, 14);
      g_htfClose  = iClose(_Symbol, PERIOD_H4, 1);
      g_lastH4    = h4;
     }
   datetime h1 = iTime(_Symbol, PERIOD_H1, 0);
   if(h1 != g_lastH1)
     {
      g_emaTouch = EmaAt(PERIOD_H1, 1, 200);
      g_atrTouch = AtrAt(PERIOD_H1, 1, 14);
      g_lastH1   = h1;
     }
   datetime d1 = iTime(_Symbol, PERIOD_D1, 0);
   if(d1 != g_lastD1)
     {
      g_atrD = AtrAt(PERIOD_D1, 1, 14);
      g_dEma = EmaAt(PERIOD_D1, 1, 200);
      g_lastD1 = d1;
     }
  }

//+------------------------------------------------------------------+
//| Максимум/минимум по копии M15 (индекс = count - shift)            |
//+------------------------------------------------------------------+
double HighestArr(const double &a[], int count, int endShift, int len)
  {
   double v = -DBL_MAX;
   for(int s=endShift; s<endShift+len; s++)
     {
      int i = count - s;
      if(i >= 0 && i < count && a[i] > v) v = a[i];
     }
   return v;
  }
double LowestArr(const double &a[], int count, int endShift, int len)
  {
   double v = DBL_MAX;
   for(int s=endShift; s<endShift+len; s++)
     {
      int i = count - s;
      if(i >= 0 && i < count && a[i] < v) v = a[i];
     }
   return v;
  }

//+------------------------------------------------------------------+
//| Инициализация                                                     |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_trade.SetExpertMagicNumber(20261101);
   g_trade.SetDeviationInPoints(50);
   for(int i=0; i<4; i++)
     {
      g_lv[i].px=0; g_lv[i].act=false; g_lv[i].kind=0;
      g_lv[i].touchS=-1; g_lv[i].rejectS=-1; g_lv[i].touchL=-1; g_lv[i].rejectL=-1;
     }
   for(int i=0; i<13; i++) g_cntR[i]=0;
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Обновление уровня: смена цены сбрасывает касания                  |
//+------------------------------------------------------------------+
void UpdLvl(int idx, string nm, double px, bool act, int kind)
  {
   SLvl l = g_lv[idx];
   if(act && (!l.act || px != l.px))
     {
      l.touchS=-1; l.rejectS=-1; l.touchL=-1; l.rejectL=-1;
     }
   l.px=px; l.act=act; l.kind=kind;
   g_lv[idx]=l;
  }

//+------------------------------------------------------------------+
//| Закрыть часть позиции                                             |
//+------------------------------------------------------------------+
bool ClosePartial(double vol)
  {
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(vol < vmin - 1e-9) return false;
   return g_trade.PositionClosePartial(_Symbol, vol);
  }

//+------------------------------------------------------------------+
//| Удалить наши отложенные TP-лимитки                                |
//+------------------------------------------------------------------+
void DeletePendings()
  {
   for(int i=OrdersTotal()-1; i>=0; i--)
     {
      ulong tk = OrderGetTicket(i);
      if(tk == 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) != 20261101) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol)  continue;
      g_trade.OrderDelete(tk);
     }
  }
double NormLots(double v)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(step > 0) v = MathFloor(v/step + 1e-9)*step;
   return (v >= vmin - 1e-9) ? v : 0.0;
  }

//+------------------------------------------------------------------+
//| Завершение сделки: книжка, статистика, CSV                        |
//+------------------------------------------------------------------+
void FinishTrade(string reason)
  {
   //--- фактический результат по сделкам истории
   double realized = 0.0;
   if(HistorySelect(g_entryTime - 120, TimeCurrent() + 60))
     {
      int deals = HistoryDealsTotal();
      for(int i=0; i<deals; i++)
        {
         ulong tk = HistoryDealGetTicket(i);
         if(HistoryDealGetInteger(tk, DEAL_MAGIC) != 20261101) continue;
         if(HistoryDealGetString(tk, DEAL_SYMBOL) != _Symbol)  continue;
         realized += HistoryDealGetDouble(tk, DEAL_PROFIT)
                   + HistoryDealGetDouble(tk, DEAL_SWAP)
                   + HistoryDealGetDouble(tk, DEAL_COMMISSION);
        }
     }
   double actualR = (g_riskMoney > 0) ? realized/g_riskMoney : 0.0;
   string exitTime = TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES);

   //--- стоп-аут (серверный SL): R остатка по фактической цене стопа
   if(reason == "sl" || reason == "be" || reason == "trail")
     {
      double stopR = (g_dir==1) ? (g_stop-g_entry)/g_rDist : (g_entry-g_stop)/g_rDist;
      g_pineR += (1.0-g_partDone)*stopR;
     }

   g_nTrades++;
   g_sumR += g_pineR;
   if(realized > 0) { g_nWins++; g_sumWin += realized; }
   else if(realized < 0) g_sumLoss += -realized;
   g_eqR += g_pineR;
   if(g_eqR > g_peakR) g_peakR = g_eqR;
   if(g_eqR - g_peakR < g_ddR) g_ddR = g_eqR - g_peakR;
   g_dayR += g_pineR;

   //--- исходы как в Pine: TP3 > TP2 > TP1 > время > SL
   string outcome;
   if(g_hit3)                { g_cntTp3++;  outcome="tp3"; }
   else if(g_hit2)           { g_cntTp2++;  outcome="tp2"; }
   else if(g_hit1)           { g_cntTp1++;  outcome="tp1"; }
   else if(reason=="time")   { g_cntTime++; outcome="time"; }
   else                      { g_cntSl++;   outcome="sl"; }

   string sess = (g_sessId==3) ? "NY" : (g_sessId==2) ? "LDN" : "off";
   g_tradesCsv += StringFormat("%s;%s;%.2f;%.2f;%.2f;%.2f;%.2f;%s;%s;%s;%.2f;%.2f;%.2f\n",
                   TimeToString(g_entryTime, TIME_DATE|TIME_MINUTES),
                   g_dir==1 ? "LONG" : "SHORT",
                   g_entry, g_stop, g_tp1, g_tp2, g_tp3,
                   exitTime, reason, sess, g_pineR, realized, actualR);

   PrintFormat("Сделка %s %s: вход %.2f, причина %s, pineR %.2f, факт %.2f$ (%.2fR)",
               g_dir==1?"LONG":"SHORT", sess, g_entry, reason, g_pineR, realized, actualR);

   g_dir=0;
   g_lastExitN = g_n;
   g_partDone=0; g_pineR=0; g_hit1=false; g_hit2=false; g_hit3=false; g_beOn=false;
  }

//+------------------------------------------------------------------+
//| Перенос стопа (позиции): серверный SL — филл по цене стопа        |
//+------------------------------------------------------------------+
void ModifyStop(double newStop)
  {
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_stop = NormalizeDouble(newStop, digits);
   g_trade.PositionModify(_Symbol, g_stop, 0.0);
  }

//+------------------------------------------------------------------+
//| Ведение открытой сделки: TP-партиалы и перенос стопа на тиках     |
//| Стоп исполняется сервером (филл по цене стопа, честно для гэпов)  |
//+------------------------------------------------------------------+
void ManageTrade()
  {
   if(g_dir == 0) return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(g_dir == 1)
     {
      if(!g_hit1 && bid >= g_tp1)
        {
         if(!InpLimitTP) ClosePartial(NormLots(g_lots0*InpP1Pct/100.0));
         g_hit1 = true;
         g_partDone = MathMin(1.0, g_partDone + InpP1Pct/100.0);
         g_pineR += (InpP1Pct/100.0)*InpTp1R;
         if(InpBeAfter == 1 && !g_beOn) { g_beOn = true; ModifyStop(g_entry); }
        }
      if(g_hit1 && !g_hit2 && bid >= g_tp2)
        {
         if(!InpLimitTP) ClosePartial(NormLots(g_lots0*InpP2Pct/100.0));
         g_hit2 = true;
         g_partDone = MathMin(1.0, g_partDone + InpP2Pct/100.0);
         g_pineR += (InpP2Pct/100.0)*InpTp2R;
         if(InpBeAfter == 2 && !g_beOn) { g_beOn = true; ModifyStop(g_entry); }
        }
      if(!g_tp3Placed && g_hit2 && bid >= g_tp3)
        {
         g_hit3 = true;
         g_pineR += (1.0-g_partDone)*InpTp3R;
         g_partDone = 1.0;
         DeletePendings();
         g_trade.PositionClose(_Symbol);
         FinishTrade("tp3");
        }
     }
   else
     {
      if(!g_hit1 && ask <= g_tp1)
        {
         if(!InpLimitTP) ClosePartial(NormLots(g_lots0*InpP1Pct/100.0));
         g_hit1 = true;
         g_partDone = MathMin(1.0, g_partDone + InpP1Pct/100.0);
         g_pineR += (InpP1Pct/100.0)*InpTp1R;
         if(InpBeAfter == 1 && !g_beOn) { g_beOn = true; ModifyStop(g_entry); }
        }
      if(g_hit1 && !g_hit2 && ask <= g_tp2)
        {
         if(!InpLimitTP) ClosePartial(NormLots(g_lots0*InpP2Pct/100.0));
         g_hit2 = true;
         g_partDone = MathMin(1.0, g_partDone + InpP2Pct/100.0);
         g_pineR += (InpP2Pct/100.0)*InpTp2R;
         if(InpBeAfter == 2 && !g_beOn) { g_beOn = true; ModifyStop(g_entry); }
        }
      if(!g_tp3Placed && g_hit2 && ask <= g_tp3)
        {
         g_hit3 = true;
         g_pineR += (1.0-g_partDone)*InpTp3R;
         g_partDone = 1.0;
         DeletePendings();
         g_trade.PositionClose(_Symbol);
         FinishTrade("tp3");
        }
     }
  }

//+------------------------------------------------------------------+
//| Открытие сделки по рынку                                          |
//+------------------------------------------------------------------+
void OpenTrade(bool isLong)
  {
   double stp = isLong ? g_stopLCur : g_stopSCur;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = isLong ? ask : bid;
   double dist = MathAbs(entry - stp);
   if(dist <= SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE)) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double money = balance * InpRiskPct/100.0;
   double p1 = 0.0;
   if(!(OrderCalcProfit(ORDER_TYPE_BUY, _Symbol, 1.0, ask, ask+1.0, p1) && p1 > 0))
     {
      double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      if(ts <= 0 || tv <= 0) return;
      p1 = tv/ts;
     }
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lots = MathFloor(money/(dist*p1)/step)*step;
   if(lots < vmin) lots = vmin;
   if(lots > vmax) lots = vmax;
   if(lots <= 0) return;

   double sgn = isLong ? 1.0 : -1.0;
   g_dir       = isLong ? 1 : -1;
   g_entry     = entry;
   g_stop      = stp;
   g_rDist     = dist;
   g_tp1       = entry + sgn*InpTp1R*dist;
   g_tp2       = entry + sgn*InpTp2R*dist;
   g_tp3       = entry + sgn*InpTp3R*dist;
   g_entryN    = g_n;
   g_entryTime = TimeCurrent();
   g_lots0     = lots;
   g_partDone  = 0; g_pineR = 0;
   g_hit1=false; g_hit2=false; g_hit3=false; g_beOn=false;
   g_riskMoney = lots*dist*p1;            // фактический риск выбранным лотом
   g_perUnit   = p1;
   g_sessId    = InNy(iTime(_Symbol, PERIOD_M15, 1)) ? 3 : (InLdn(iTime(_Symbol, PERIOD_M15, 1)) ? 2 : 0);
   g_dayTrades++;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   bool ok = isLong ? g_trade.Buy(lots, _Symbol, 0.0, NormalizeDouble(stp, digits), 0.0, "kidzord-long")
                    : g_trade.Sell(lots, _Symbol, 0.0, NormalizeDouble(stp, digits), 0.0, "kidzord-short");
   if(!ok) { g_dir=0; g_dayTrades--; return; }
   //--- TP1/TP2/TP3 серверными лимитками: филл точно по цели.
   //--- На неттинге тестера лимитки конфликтуют со стопом входа (сталый SL-ордер
   //--- исполняется на полный объём после закрытия лимитками) — по умолчанию выкл.
   g_tp3Placed = false;
   if(!InpLimitTP) return;
   double vol1 = NormLots(g_lots0*InpP1Pct/100.0);
   double vol2 = NormLots(g_lots0*InpP2Pct/100.0);
   double vol3 = NormLots(g_lots0 - vol1 - vol2);
   if(vol1 > 0)
      if(isLong) g_trade.SellLimit(vol1, NormalizeDouble(g_tp1, digits), _Symbol, 0, 0, ORDER_TIME_GTC, 0, "kidzord-tp1");
      else       g_trade.BuyLimit(vol1,  NormalizeDouble(g_tp1, digits), _Symbol, 0, 0, ORDER_TIME_GTC, 0, "kidzord-tp1");
   if(vol2 > 0)
      if(isLong) g_trade.SellLimit(vol2, NormalizeDouble(g_tp2, digits), _Symbol, 0, 0, ORDER_TIME_GTC, 0, "kidzord-tp2");
      else       g_trade.BuyLimit(vol2,  NormalizeDouble(g_tp2, digits), _Symbol, 0, 0, ORDER_TIME_GTC, 0, "kidzord-tp2");
   if(vol3 > 0)
     {
      if(isLong) g_trade.SellLimit(vol3, NormalizeDouble(g_tp3, digits), _Symbol, 0, 0, ORDER_TIME_GTC, 0, "kidzord-tp3");
      else       g_trade.BuyLimit(vol3,  NormalizeDouble(g_tp3, digits), _Symbol, 0, 0, ORDER_TIME_GTC, 0, "kidzord-tp3");
      g_tp3Placed = true;
     }
  }

//+------------------------------------------------------------------+
//| Обработка закрытого M15-бара: гейты и вход                        |
//+------------------------------------------------------------------+
void ProcessBar()
  {
   g_n++;
   int N = g_n;
   int count = M15CNT;
   double o[], h[], l[], c[];
   if(CopyOpen(_Symbol, PERIOD_M15, 1, count, o)  != count) return;
   if(CopyHigh(_Symbol, PERIOD_M15, 1, count, h)  != count) return;
   if(CopyLow(_Symbol,  PERIOD_M15, 1, count, l)  != count) return;
   if(CopyClose(_Symbol,PERIOD_M15, 1, count, c)  != count) return;
   datetime barTime = iTime(_Symbol, PERIOD_M15, 1);
   // индекс закрытого бара в массивах: iN = count-1 (shift 1)
   int iN = count-1;
   double oN=o[iN], hN=h[iN], lN=l[iN], cN=c[iN];
   double o2=o[iN-1], h2=h[iN-1], l2=l[iN-1], c2=c[iN-1];   // shift 2
   double h3=h[iN-2], l3=l[iN-2];                            // shift 3

   //--- дневные счётчики
   datetime day = barTime - (barTime % 86400);
   if(day != g_day) { g_day = day; g_dayTrades = 0; g_dayR = 0.0; }

   RefreshHTF();

   //--- ATR рабочего ТФ (Wilder 14, по закрытым барам до shift 1)
   double atr = 0.0;
     {
      int need = 100;
      double ah[], al[], ac[];
      if(CopyHigh(_Symbol, PERIOD_M15, 1, need, ah)  != need) return;
      if(CopyLow(_Symbol,  PERIOD_M15, 1, need, al)  != need) return;
      if(CopyClose(_Symbol,PERIOD_M15, 1, need, ac)  != need) return;
      atr = ah[0]-al[0];
      for(int i=1; i<need; i++)
        {
         double tr = MathMax(ah[i]-al[i], MathMax(MathAbs(ah[i]-ac[i-1]), MathAbs(al[i]-ac[i-1])));
         atr = (atr*13 + tr)/14;
        }
     }
   if(atr <= 0) return;
   g_atrLast = atr;

   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   int    touchBars = (int)MathMax(1.0, MathRound(InpMemHours*3600.0/900.0));

   //=== УРОВНИ: PDH/PDL/PWH/PWL + события касаний/отбоёв ===
   UpdLvl(0, "PDH", iHigh(_Symbol, PERIOD_D1, 1), true,  1);
   UpdLvl(1, "PDL", iLow(_Symbol,  PERIOD_D1, 1), true, -1);
   UpdLvl(2, "PWH", iHigh(_Symbol, PERIOD_W1, 1), true,  1);
   UpdLvl(3, "PWL", iLow(_Symbol,  PERIOD_W1, 1), true, -1);
   double lvTolAbs = atr*InpLvTol;
   for(int i=0; i<4; i++)
     {
      SLvl lv = g_lv[i];
      if(!lv.act) continue;
      bool tS = lv.kind >= 0 && hN >= lv.px - lvTolAbs;
      bool rS = tS && cN < lv.px;
      bool tL = lv.kind <= 0 && lN <= lv.px + lvTolAbs;
      bool rL = tL && cN > lv.px;
      if(tS) lv.touchS  = N;
      if(rS) lv.rejectS = N;
      if(tL) lv.touchL  = N;
      if(rL) lv.rejectL = N;
      g_lv[i] = lv;
     }
   double hiCl = HighestArr(c, count, 1, InpLvPersist);
   double loCl = LowestArr(c, count, 1, InpLvPersist);
   //--- лучший сетап уровня (касание+отбой одного уровня, свежий отбой)
   g_setupLvAgoL=BIG_AGE; g_setupLvAgoS=BIG_AGE; g_setupLvPxL=0; g_setupLvPxS=0;
   for(int i=0; i<4; i++)
     {
      SLvl lv = g_lv[i];
      if(!lv.act) continue;
      if(lv.kind >= 0 && hiCl < lv.px)
        {
         bool tOkS = lv.touchS >= 0 && N-lv.touchS <= InpLvLook;
         bool rOkS = lv.rejectS >= 0 && N-lv.rejectS <= InpLvLook;
         if(tOkS && rOkS && N-lv.rejectS < g_setupLvAgoS)
           { g_setupLvAgoS = N-lv.rejectS; g_setupLvPxS = lv.px; }
         if(tOkS && N-lv.touchS < N-g_bestTouchSBar) g_bestTouchSBar = lv.touchS;
         if(rOkS && N-lv.rejectS  < N-g_bestRejSBar)  g_bestRejSBar  = lv.rejectS;
        }
      if(lv.kind <= 0 && loCl > lv.px)
        {
         bool tOkL = lv.touchL >= 0 && N-lv.touchL <= InpLvLook;
         bool rOkL = lv.rejectL >= 0 && N-lv.rejectL <= InpLvLook;
         if(tOkL && rOkL && N-lv.rejectL < g_setupLvAgoL)
           { g_setupLvAgoL = N-lv.rejectL; g_setupLvPxL = lv.px; }
         if(tOkL && N-lv.touchL < N-g_bestTouchLBar) g_bestTouchLBar = lv.touchL;
         if(rOkL && N-lv.rejectL  < N-g_bestRejLBar)  g_bestRejLBar  = lv.rejectL;
        }
     }
   bool lvSetupOkL = g_setupLvAgoL < BIG_AGE;
   bool lvSetupOkS = g_setupLvAgoS < BIG_AGE;

   //=== КАСАНИЕ/ТЕСТ EMA200 H1 ===
   double zoneHalf = g_atrTouch*InpTolHtf;
   double testZone = g_atrTouch*InpTolLtf;
   if(lN <= g_emaTouch + zoneHalf && cN >= g_emaTouch - zoneHalf) g_touchLBar = N;
   if(hN >= g_emaTouch - zoneHalf && cN <= g_emaTouch + zoneHalf) g_touchSBar = N;
   if(lN <= g_emaTouch + testZone && cN > g_emaTouch) g_emaTestLBar = N;
   if(hN >= g_emaTouch - testZone && cN < g_emaTouch) g_emaTestSBar = N;
   bool touchLRecent = N-g_touchLBar <= touchBars;
   bool touchSRecent = N-g_touchSBar <= touchBars;
   bool emaTestRecentL = N-g_emaTestLBar <= InpTestLook;
   bool emaTestRecentS = N-g_emaTestSBar <= InpTestLook;

   //=== ВЫБОР СЕТАПА: уровень vs EMA (по свежести) ===
   bool emaSetupL = touchLRecent && emaTestRecentL;
   bool emaSetupS = touchSRecent && emaTestRecentS;
   int  barsEmaTestL = N-g_emaTestLBar;
   int  barsEmaTestS = N-g_emaTestSBar;
   bool useLvL = lvSetupOkL && (!emaSetupL || g_setupLvAgoL <= barsEmaTestL);
   bool useLvS = lvSetupOkS && (!emaSetupS || g_setupLvAgoS <= barsEmaTestS);
   bool lvTouchL = N-g_bestTouchLBar <= InpLvLook;
   bool lvTestL  = N-g_bestRejLBar  <= InpLvLook;
   bool lvTouchS = N-g_bestTouchSBar <= InpLvLook;
   bool lvTestS  = N-g_bestRejSBar  <= InpLvLook;
   bool touchOkL = useLvL ? lvTouchL : (emaSetupL ? touchLRecent : false);
   bool touchOkS = useLvS ? lvTouchS : (emaSetupS ? touchSRecent : false);
   bool testOkL  = useLvL ? lvTestL  : (emaSetupL ? emaTestRecentL : false);
   bool testOkS  = useLvS ? lvTestS  : (emaSetupS ? emaTestRecentS : false);
   double refL = useLvL ? g_setupLvPxL : g_emaTouch;
   double refS = useLvS ? g_setupLvPxS : g_emaTouch;

   //=== БОЛТАНКА: ER + диапазон ===
   double erNum = MathAbs(cN - c[iN-InpChopLen]);
   double erDen = 0.0;
   for(int i=iN-InpChopLen+1; i<=iN; i++) erDen += MathAbs(c[i]-c[i-1]);
   double erVal = erDen > 0 ? erNum/erDen : 0.0;
   bool noChop = erVal >= InpErMin;
   double rngHi = HighestArr(h, count, 1, InpRangeLen);
   double rngLo = LowestArr(l, count, 1, InpRangeLen);
   double rngAtr = (rngHi-rngLo)/atr;
   bool fRange = rngAtr >= InpRangeMinAtr && rngAtr <= InpRangeMaxAtr;

   //=== ФРАКТАЛЫ И СВИП ===
   // пивот подтверждается на баре N для бара N-2
   int j = iN - InpFracR;
   if(j-InpFracL >= 0 && j+InpFracR <= iN)
     {
      bool ph = true, pl = true;
      for(int k=j-InpFracL; k<=j+InpFracR; k++)
        {
         if(k == j) continue;
         if(h[k] >= h[j]) ph = false;
         if(l[k] <= l[j]) pl = false;
        }
      if(ph) { g_fracHi = h[j]; g_fracHiBar = N-InpFracR; }
      if(pl) { g_fracLo = l[j]; g_fracLoBar = N-InpFracR; }
     }
   bool fracFreshL = g_fracLoBar < BIG_AGE && N-g_fracLoBar <= InpSweepLen;
   bool fracFreshS = g_fracHiBar < BIG_AGE && N-g_fracHiBar <= InpSweepLen;
   double sweepRefL = LowestArr(l, count, 2, InpSweepLen);
   double sweepRefS = HighestArr(h, count, 2, InpSweepLen);
   bool sweepLraw = lN < sweepRefL && cN > sweepRefL;
   bool sweepSraw = hN > sweepRefS && cN < sweepRefS;
   bool sweepLfr = g_fracLoBar < BIG_AGE && lN < g_fracLo && cN > g_fracLo;
   bool sweepSfr = g_fracHiBar < BIG_AGE && hN > g_fracHi && cN < g_fracHi;
   bool sweepL = fracFreshL ? sweepLfr : sweepLraw;
   bool sweepS = fracFreshS ? sweepSfr : sweepSraw;
   if(sweepL) { g_sweepLBar = N; g_manipLo = lN; }
   if(sweepS) { g_sweepSBar = N; g_manipHi = hN; }

   //=== BOS + CHoCH ===
   double bosRefL = (g_fracHiBar < BIG_AGE) ? MathMax(g_fracHi, HighestArr(h, count, 2, InpBosLen))
                                            : HighestArr(h, count, 2, InpBosLen);
   double bosRefS = (g_fracLoBar < BIG_AGE) ? MathMin(g_fracLo, LowestArr(l, count, 2, InpBosLen))
                                            : LowestArr(l, count, 2, InpBosLen);
   bool bosL = cN > bosRefL;
   bool bosS = cN < bosRefS;
   bool chochUpEvt = false, chochDnEvt = false;
   if(bosL) { chochUpEvt = (g_trend == -1); g_trend = 1;  g_bosLBar = N; }
   if(bosS) { chochDnEvt = (g_trend == 1);  g_trend = -1; g_bosSBar = N; }
   if(chochUpEvt) g_chochUpBar = N;
   if(chochDnEvt) g_chochDnBar = N;

   //=== FVG / IFVG ===
   bool bullFvg = lN > h3;
   bool bearFvg = hN < l3;
   if(bullFvg) { g_fvgBotS = h3; g_fvgTopBull = lN; g_fvgLBar = N; g_invBarS = -1; }
   if(bearFvg) { g_fvgTopL = l3; g_fvgBotBear = hN; g_fvgSBar = N; g_invBarL = -1; }
   if(g_fvgTopBull > 0 && cN > g_fvgTopBull) g_invBarS = -1;
   if(g_fvgBotBear > 0 && cN < g_fvgBotBear) g_invBarL = -1;
   bool invCrossS = g_fvgBotS > 0 && cN < g_fvgBotS && c2 >= g_fvgBotS;
   bool invCrossL = g_fvgTopL > 0 && cN > g_fvgTopL && c2 <= g_fvgTopL;
   if(invCrossS) g_invBarS = N;
   if(invCrossL) g_invBarL = N;
   bool invS = InpUseIfvg && g_fvgBotS > 0 && g_invBarS > 0 && N-g_invBarS <= InpIfvgLook &&
               cN < g_fvgBotS && hN >= g_fvgBotS - atr*InpIfvgTol;
   bool invL = InpUseIfvg && g_fvgTopL > 0 && g_invBarL > 0 && N-g_invBarL <= InpIfvgLook &&
               cN > g_fvgTopL && lN <= g_fvgTopL + atr*InpIfvgTol;

   //=== СТОП/РИСК ===
   bool useManipL = (N-g_sweepLBar <= InpManipLook) && g_manipLo > 0 && g_manipLo < cN;
   bool useManipS = (N-g_sweepSBar <= InpManipLook) && g_manipHi > 0 && g_manipHi > cN;
   double swingBaseL = LowestArr(l, count, 1, InpSwingLen);
   double swingBaseS = HighestArr(h, count, 1, InpSwingLen);
   g_stopLCur = (useManipL ? g_manipLo : swingBaseL) - atr*InpStopBuf;
   g_stopSCur = (useManipS ? g_manipHi : swingBaseS) + atr*InpStopBuf;
   double riskAtrL = (cN-g_stopLCur)/atr;
   double riskAtrS = (g_stopSCur-cN)/atr;
   bool riskOkL = riskAtrL >= 0 && riskAtrL <= InpRiskMaxAtr;
   bool riskOkS = riskAtrS >= 0 && riskAtrS <= InpRiskMaxAtr;
   double riskLpct = (cN-g_stopLCur)/cN*100.0;
   double riskSpct = (g_stopSCur-cN)/cN*100.0;

   //=== ТРИГГЕРЫ ===
   double rngN = hN-lN;
   double bodyAbs = MathAbs(cN-oN);
   double refBody = MathMax(bodyAbs, tickSize);
   double lowerW = MathMin(oN, cN) - lN;
   double upperW = hN - MathMax(oN, cN);
   bool bigEnough = rngN >= atr*InpMinRangeK;
   bool pinL = InpUsePin && lowerW >= InpPinMult*refBody && cN > (hN+lN)/2.0;
   bool pinS = InpUsePin && upperW >= InpPinMult*refBody && cN < (hN+lN)/2.0;
   bool engL = InpUseEngulf && cN > oN && c2 < o2 && cN >= o2 && oN <= c2;
   bool engS = InpUseEngulf && cN < oN && c2 > o2 && cN <= o2 && oN >= c2;
   bool micL = InpUseMicro && cN > HighestArr(h, count, 2, InpTrigBosLen);
   bool micS = InpUseMicro && cN < LowestArr(l, count, 2, InpTrigBosLen);
   bool reclaimL = N-g_sweepLBar <= InpTrigLook && g_manipLo > 0 && cN > g_manipLo;
   bool reclaimS = N-g_sweepSBar <= InpTrigLook && g_manipHi > 0 && cN < g_manipHi;
   int powTrigL = micL ? 4 : invL ? 3 : (engL && bigEnough) ? 2 : (pinL && bigEnough) ? 1 : 0;
   int powTrigS = micS ? 4 : invS ? 3 : (engS && bigEnough) ? 2 : (pinS && bigEnough) ? 1 : 0;
   bool patL = powTrigL > 0 && powTrigL >= InpMinTrigPow && (cN > refL || reclaimL);
   bool patS = powTrigS > 0 && powTrigS >= InpMinTrigPow && (cN < refS || reclaimS);
   if(patL) g_patLBar = N;
   if(patS) g_patSBar = N;

   //=== ЦЕЛЬ ===
   double dayCap = (g_atrD > 0 && cN > 0) ? g_atrD*InpDayBudgetK/cN*100.0 : BIG_AGE;
   double tgtPxL = 0, tgtPxS = 0;
   for(int i=0; i<4; i++)
     {
      SLvl lv = g_lv[i];
      if(!lv.act) continue;
      if(lv.kind >= 0 && lv.px > cN)
        {
         double t = (lv.px-cN)/cN*100.0;
         bool ok = t >= InpTgtMin && t <= InpTgtMax && riskLpct > 0 &&
                   t/riskLpct >= InpMinRR && (!InpUseDayBud || t <= dayCap);
         if(ok && (tgtPxL == 0 || lv.px < tgtPxL)) tgtPxL = lv.px;
        }
      if(lv.kind <= 0 && lv.px < cN)
        {
         double t = (cN-lv.px)/cN*100.0;
         bool ok = t >= InpTgtMin && t <= InpTgtMax && riskSpct > 0 &&
                   t/riskSpct >= InpMinRR && (!InpUseDayBud || t <= dayCap);
         if(ok && (tgtPxS == 0 || lv.px > tgtPxS)) tgtPxS = lv.px;
        }
     }
   double swingHi = HighestArr(h, count, 2, InpLiqLen);
   double swingLo = LowestArr(l, count, 2, InpLiqLen);
   if(swingHi > cN)
     {
      double t = (swingHi-cN)/cN*100.0;
      bool ok = t >= InpTgtMin && t <= InpTgtMax && riskLpct > 0 &&
                t/riskLpct >= InpMinRR && (!InpUseDayBud || t <= dayCap);
      if(ok && (tgtPxL == 0 || swingHi < tgtPxL)) tgtPxL = swingHi;
     }
   if(swingLo > 0 && swingLo < cN)
     {
      double t = (cN-swingLo)/cN*100.0;
      bool ok = t >= InpTgtMin && t <= InpTgtMax && riskSpct > 0 &&
                t/riskSpct >= InpMinRR && (!InpUseDayBud || t <= dayCap);
      if(ok && (tgtPxS == 0 || swingLo > tgtPxS)) tgtPxS = swingLo;
     }
   double tgtL = tgtPxL > 0 ? (tgtPxL-cN)/cN*100.0 : 0;
   double tgtS = tgtPxS > 0 ? (cN-tgtPxS)/cN*100.0 : 0;
   bool tgtOkL = tgtPxL > 0 && tgtL >= InpTgtMin && tgtL <= InpTgtMax && tgtL/riskLpct >= InpMinRR;
   bool tgtOkS = tgtPxS > 0 && tgtS >= InpTgtMin && tgtS <= InpTgtMax && tgtS/riskSpct >= InpMinRR;

   //=== SMC-ФАКТОРЫ ===
   bool fSweepL = N-g_sweepLBar <= InpFacLook;
   bool fSweepS = N-g_sweepSBar <= InpFacLook;
   bool fBosL = N-g_bosLBar <= InpFacLook;
   bool fBosS = N-g_bosSBar <= InpFacLook;
   bool fChokL = N-g_chochUpBar <= InpFacLook;
   bool fChokS = N-g_chochDnBar <= InpFacLook;
   double fvgProx = atr*0.5;
   bool nearBull = g_fvgBotS > 0 && g_fvgTopBull > 0 && cN >= g_fvgBotS-fvgProx && cN <= g_fvgTopBull+fvgProx;
   bool nearBear = g_fvgTopL > 0 && g_fvgBotBear > 0 && cN <= g_fvgTopL+fvgProx && cN >= g_fvgBotBear-fvgProx;
   bool fFvgL = N-g_fvgLBar <= InpFvgLook && nearBull;
   bool fFvgS = N-g_fvgSBar <= InpFvgLook && nearBear;
   int scoreL = (fSweepL?1:0)+(fBosL?1:0)+(fFvgL?1:0)+(fChokL?1:0);
   int scoreS = (fSweepS?1:0)+(fBosS?1:0)+(fFvgS?1:0)+(fChokS?1:0);

   //=== СТОРОНА И ГЕЙТЫ ===
   bool ctxKnown = g_htfClose > 0 && g_ema4h != EMPTY_VALUE && g_ema4h > 0;
   bool isL = ctxKnown ? g_htfClose >= g_ema4h : true;
   bool ctxL = ctxKnown && g_htfClose > g_ema4h;
   bool ctxS = ctxKnown && g_htfClose < g_ema4h;
   double slopeVal = (g_ema4h != EMPTY_VALUE && g_ema4hPast != EMPTY_VALUE &&
                      g_atr4h != EMPTY_VALUE && g_atr4h > 0 && InpSlopeLen > 0)
                     ? (g_ema4h-g_ema4hPast)/g_atr4h/InpSlopeLen : 0.0;
   bool dayUp = g_dEma != EMPTY_VALUE && cN > g_dEma;
   bool dayDn = g_dEma != EMPTY_VALUE && cN < g_dEma;
   bool dayOkL = !InpUseDayCtx || dayUp;
   bool dayOkS = !InpUseDayCtx || dayDn;

   bool r1 = true;
   bool r2 = isL ? (ctxL && dayOkL) : (ctxS && dayOkS);
   bool r3 = isL ? touchOkL : touchOkS;
   bool r4 = isL ? (slopeVal >= InpMinSlope) : (slopeVal <= -InpMinSlope);
   bool r5 = isL ? testOkL : testOkS;
   bool r6 = isL ? (N-g_patLBar <= InpTrigLook) : (N-g_patSBar <= InpTrigLook);
   bool r7 = isL ? riskOkL : riskOkS;
   bool r8 = (isL ? tgtOkL : tgtOkS) && (!InpUseDayBud || (isL ? tgtL : tgtS) <= dayCap);
   bool r9 = noChop && fRange;
   bool r10 = (isL ? scoreL : scoreS) >= MathMin(InpNeedFac, 4);
   bool okSprd = !InpUseSprd || rngN >= InpSprTicks*tickSize*InpSprK;
   bool r11 = okSprd;
   bool dayLimOk = (InpMaxDayTrades <= 0 || g_dayTrades < InpMaxDayTrades) &&
                   (InpDayStopR <= 0 || g_dayR > -InpDayStopR);
   bool sessOk = (!InpUseSess || (InpSessLdn && InLdn(barTime)) || (InpSessNy && InNy(barTime))) &&
                 (!InpRollOn || !InRollover(barTime));
   bool r12 = sessOk && (!InpNewsOn || !InNews(barTime)) && dayLimOk;

   for(int i=1; i<=12; i++)
     {
      bool v = (i==1)?r1:(i==2)?r2:(i==3)?r3:(i==4)?r4:(i==5)?r5:(i==6)?r6:
               (i==7)?r7:(i==8)?r8:(i==9)?r9:(i==10)?r10:(i==11)?r11:r12;
      if(v) g_cntR[i]++;
     }
   //=== АВТОМАТ СЕТАПА И ВХОД ===
   int sideNow = isL ? 1 : -1;
   if(sideNow != g_setupSide) { g_setupState = 0; g_setupSide = sideNow; }
   bool setupNow = r1 && r2 && r3 && r5 && r12;
   bool trigNow = r6 && r7 && r8;
   if(!setupNow) g_setupState = 0;
   else if(g_setupState == 0) g_setupState = 1;
   bool inPos = PositionSelect(_Symbol);
   if(inPos) g_setupState = 2;
   if(g_setupState == 1) g_cntSetup++;
   bool coolOk = g_lastExitN < 0 || (N-g_lastExitN) >= InpReEntryBars;
   bool sweepOk = isL ? (N-g_sweepLBar >= InpSweepDelay) : (N-g_sweepSBar >= InpSweepDelay);
   bool entryNow = setupNow && trigNow && g_setupState == 1 && !inPos && coolOk && sweepOk;
   string row = TimeToString(barTime, TIME_DATE|TIME_MINUTES) + ";" + (isL ? "1" : "0");
   for(int i=1; i<=12; i++)
     {
      bool v = (i==1)?r1:(i==2)?r2:(i==3)?r3:(i==4)?r4:(i==5)?r5:(i==6)?r6:
               (i==7)?r7:(i==8)?r8:(i==9)?r9:(i==10)?r10:(i==11)?r11:r12;
      row += ";" + (v ? "1" : "0");
     }
   row += ";" + (entryNow ? "1" : "0") + "\n";
   g_gatesCsv += row;

   if(entryNow)
     {
      g_setupState = 2;
      g_cntEntry++;
      OpenTrade(isL);
     }
  }

//+------------------------------------------------------------------+
//| Основной обработчик                                               |
//+------------------------------------------------------------------+
void OnTick()
  {
   //--- позицию мог снять серверный стоп
   if(g_dir != 0)
     {
      bool posExists = PositionSelect(_Symbol);
      long ptype = posExists ? PositionGetInteger(POSITION_TYPE) : -1;
      bool matches = posExists &&
            ((g_dir==1 && ptype==POSITION_TYPE_BUY) || (g_dir==-1 && ptype==POSITION_TYPE_SELL));
      if(!matches)
        {
         //--- позицию снял серверный стоп, либо TP-лимитка открыла фантом на неттинге
         DeletePendings();
         if(posExists) g_trade.PositionClose(_Symbol);
         string rs = "sl";
         if(g_hit2 || g_hit3) rs = "tp3";
         else if(g_hit1)      rs = (g_dir==1 ? g_stop > g_entry : g_stop < g_entry) ? "trail" : "be";
         FinishTrade(rs);
         return;
        }
      ManageTrade();
     }

   //--- закрытый M15-бар
   datetime t = iTime(_Symbol, PERIOD_M15, 0);
   static datetime lastBar = 0;
   if(t == lastBar) return;
   lastBar = t;

   if(g_dir != 0 && PositionSelect(_Symbol) && g_entryN >= 0)
     {
      //--- трейлинг UT Bot: стоп подтягивается к close ∓ k*ATR, не отдаляется
      bool trailOn = InpUseTrail && (InpTrailAfter==1 ? g_hit1 : InpTrailAfter==2 ? g_hit2 : g_hit3);
      if(trailOn && g_atrLast > 0)
        {
         double cB = iClose(_Symbol, PERIOD_M15, 1);
         if(g_dir == 1)
           {
            double ns = MathMax(g_stop, cB - InpTrailK*g_atrLast);
            if(ns > g_stop + 1e-9) ModifyStop(ns);
           }
         else
           {
            double ns = MathMin(g_stop, cB + InpTrailK*g_atrLast);
            if(ns < g_stop - 1e-9) ModifyStop(ns);
           }
        }
      //--- выход по времени: bar_index - stBar > maxBars
      if((g_n-g_entryN) > InpMaxBars)
        {
         DeletePendings();
         double px = g_dir==1 ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                              : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double mtm = g_dir==1 ? (px-g_entry)/g_rDist : (g_entry-px)/g_rDist;
         g_pineR += (1.0-g_partDone)*mtm;
         g_partDone = 1.0;
         g_trade.PositionClose(_Symbol);
         FinishTrade("time");
        }
     }

   ProcessBar();
  }

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(g_dir == 0) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != 20261101) return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;
   string com = HistoryDealGetString(trans.deal, DEAL_COMMENT);
   if(StringFind(com, "kidzord-tp1") >= 0 && !g_hit1)
     {
      g_hit1 = true;
      g_partDone = MathMin(1.0, g_partDone + InpP1Pct/100.0);
      g_pineR += (InpP1Pct/100.0)*InpTp1R;
      if(InpBeAfter == 1 && !g_beOn) { g_beOn = true; ModifyStop(g_entry); }
     }
   else if(StringFind(com, "kidzord-tp2") >= 0 && !g_hit2)
     {
      g_hit2 = true;
      g_partDone = MathMin(1.0, g_partDone + InpP2Pct/100.0);
      g_pineR += (InpP2Pct/100.0)*InpTp2R;
      if(InpBeAfter == 2 && !g_beOn) { g_beOn = true; ModifyStop(g_entry); }
     }
   else if(StringFind(com, "kidzord-tp3") >= 0 && !g_hit3)
     {
      g_hit3 = true;
      g_pineR += (1.0-g_partDone)*InpTp3R;
      g_partDone = 1.0;
     }
  }

void OnDeinit(const int reason)
  {
   DeletePendings();
  }

//+------------------------------------------------------------------+
//| Выгрузка результатов теста в CSV (общая папка Files)              |
//+------------------------------------------------------------------+
double OnTester()
  {
   HistorySelect(0, TimeCurrent());
   int deals = HistoryDealsTotal();
   double deposit=0, balance=0, peak=0, ddAbs=0, ddPct=0;
   int trades=0, wins=0;
   double grossP=0, grossL=0;

   for(int i=0; i<deals; i++)
     {
      ulong tk = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(tk, DEAL_TYPE) == DEAL_TYPE_BALANCE)
        {
         if(deposit == 0) deposit = HistoryDealGetDouble(tk, DEAL_PROFIT);
         continue;
        }
      double p = HistoryDealGetDouble(tk, DEAL_PROFIT)
               + HistoryDealGetDouble(tk, DEAL_SWAP)
               + HistoryDealGetDouble(tk, DEAL_COMMISSION);
      balance += p;
      double balNow = deposit + balance;
      if(HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_OUT ||
         HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_INOUT)
        {
         trades++;
         if(p > 0) { wins++; grossP += p; } else if(p < 0) grossL += -p;
        }
      if(peak < balNow) peak = balNow;
      double d = peak - balNow;
      if(d > ddAbs) ddAbs = d;
      if(peak > 0) ddPct = MathMax(ddPct, d/peak*100.0);
     }
   balance = deposit + balance;
   double net = balance - deposit;
   double pf = grossL > 0 ? grossP/grossL : 999.0;
   double avgR = g_nTrades > 0 ? g_sumR/g_nTrades : 0.0;
   double pfR = g_sumLoss > 0 ? g_sumWin/g_sumLoss : 999.0;

   string f = "kidzord_ea_" + _Symbol + "_result.csv";
   int fh = FileOpen(f, FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ';');
   if(fh != INVALID_HANDLE)
     {
      FileWrite(fh, "symbol;deposit;balance;net;return_pct;trades;winrate_pct;pf_money;max_dd_pct");
      FileWrite(fh, StringFormat("%s;%.2f;%.2f;%.2f;%.1f;%d;%.1f;%.2f;%.2f",
                  _Symbol, deposit, balance, net,
                  deposit > 0 ? net/deposit*100.0 : 0.0,
                  trades, trades > 0 ? 100.0*wins/trades : 0.0, pf, ddPct));
      FileWrite(fh, StringFormat("pineR: sumR=%.2f avgR=%.2f winR=%.1f%% pfR=%.2f maxDD_R=%.2f",
                  g_sumR, avgR, g_nTrades > 0 ? 100.0*g_nWins/g_nTrades : 0.0, pfR, g_ddR));
      FileWrite(fh, StringFormat("outcomes: sl=%d tp1=%d tp2=%d tp3=%d time=%d",
                  g_cntSl, g_cntTp1, g_cntTp2, g_cntTp3, g_cntTime));
      string gates = "gates%:";
      for(int i=1; i<=12; i++) gates += StringFormat(" r%d=%.1f", i, g_n > 0 ? 100.0*g_cntR[i]/g_n : 0.0);
      gates += StringFormat(" | setups=%d entries=%d bars=%d", g_cntSetup, g_cntEntry, g_n);
      FileWrite(fh, gates);
      FileWrite(fh, "entry_time;dir;entry;stop;tp1;tp2;tp3;exit_time;reason;session;pineR;usd;actualR");
      FileWrite(fh, g_tradesCsv);
      FileClose(fh);
     }
   int fg = FileOpen("kidzord_ea_" + _Symbol + "_gates.csv",
                     FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ';');
   if(fg != INVALID_HANDLE)
     {
      FileWrite(fg, g_gatesCsv);
      FileClose(fg);
     }
   PrintFormat("ИТОГ KidzordEA: депозит %.2f -> баланс %.2f (нетто %+.2f, %.1f%%), сделок %d, винрейт %.0f%%, PF %.2f, максДД %.2f%%, sumR %.2f",
               deposit, balance, net, deposit > 0 ? net/deposit*100.0 : 0.0,
               trades, trades > 0 ? 100.0*wins/trades : 0.0, pf, ddPct, g_sumR);
   return(net);
  }
