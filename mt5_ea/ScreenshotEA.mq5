//+------------------------------------------------------------------+
//| ScreenshotEA.mq5                                                  |
//| Слушает файл-запрос от Python и сохраняет ChartScreenShot.        |
//|                                                                  |
//| Куда положить: MQL5/Experts/ScreenshotEA.ex5                      |
//| Куда прикрепить: любой график (например, XAUUSD M15), Алго-трейдинг|
//| для скриншотов не нужен. Советник сам открывает временный график  |
//| нужного символа/ТФ, рисует вход/стоп/цели/выход и делает PNG.     |
//|                                                                  |
//| Обмен файлами — MQL5/Files:                                       |
//|   kz_screenshot_request.txt  (Python -> EA)                       |
//|   kz_screenshot_done.txt     (EA -> Python)                       |
//|   kz_screenshot_heartbeat.txt (EA -> Python, раз в секунду)       |
//+------------------------------------------------------------------+
#property strict
#property version   "1.10"
#property description "KidzordFX: PNG графика по запросу Python"

input int InpTimerSec = 1;      // период опроса, секунд
input int InpWidth    = 1280;   // ширина PNG по умолчанию
input int InpHeight   = 720;    // высота PNG по умолчанию
input int InpScale    = 2;      // запасной масштаб, если нет времени входа/выхода
input int InpWaitMs   = 1500;   // сколько ждать отрисовки графика
input int InpRightMargin = 8;   // сколько баров оставить справа от выхода

string REQ  = "kz_screenshot_request.txt";
string DONE = "kz_screenshot_done.txt";
string HB   = "kz_screenshot_heartbeat.txt";

string g_last_out = "";

//+------------------------------------------------------------------+
int OnInit()
{
   EventSetTimer((int)MathMax(1, InpTimerSec));
   WriteHeartbeat();
   Print("ScreenshotEA запущен. Файлы: ", TerminalInfoString(TERMINAL_DATA_PATH), "\\MQL5\\Files");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   WriteHeartbeat();
   CheckRequest();
}

//+------------------------------------------------------------------+
void WriteHeartbeat()
{
   int h = FileOpen(HB, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;
   FileWrite(h, "ts=" + IntegerToString((long)TimeCurrent()));
   FileWrite(h, "ver=110");
   FileWrite(h, "symbol=" + _Symbol);
   FileWrite(h, "chart=" + IntegerToString(ChartID()));
   FileClose(h);
}

void WriteDone(string status, string out, string error)
{
   StringReplace(error, "\n", " ");
   StringReplace(error, "\r", " ");
   int h = FileOpen(DONE, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;
   FileWrite(h, "status=" + status);
   FileWrite(h, "out=" + out);
   FileWrite(h, "error=" + error);
   FileClose(h);
}

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StrToTF(string s)
{
   StringToUpper(s);
   if(s == "M1"  || s == "1")   return(PERIOD_M1);
   if(s == "M5"  || s == "5")   return(PERIOD_M5);
   if(s == "M15" || s == "15")  return(PERIOD_M15);
   if(s == "M30" || s == "30")  return(PERIOD_M30);
   if(s == "H1"  || s == "60")  return(PERIOD_H1);
   if(s == "H4"  || s == "240") return(PERIOD_H4);
   if(s == "D1"  || s == "1D")  return(PERIOD_D1);
   if(s == "W1"  || s == "1W")  return(PERIOD_W1);
   return(PERIOD_CURRENT);
}

void ConfigureChart(long chart, int scale)
{
   ChartSetInteger(chart, CHART_MODE, CHART_CANDLES);
   ChartSetInteger(chart, CHART_COLOR_BACKGROUND, C'14,17,22');
   ChartSetInteger(chart, CHART_COLOR_FOREGROUND, C'228,230,233');
   ChartSetInteger(chart, CHART_COLOR_GRID, C'36,43,57');
   ChartSetInteger(chart, CHART_COLOR_CANDLE_BULL, C'87,160,139');
   ChartSetInteger(chart, CHART_COLOR_CANDLE_BEAR, C'178,91,73');
   ChartSetInteger(chart, CHART_COLOR_CHART_UP, C'87,160,139');
   ChartSetInteger(chart, CHART_COLOR_CHART_DOWN, C'178,91,73');
   ChartSetInteger(chart, CHART_COLOR_CHART_LINE, C'228,230,233');
   ChartSetInteger(chart, CHART_COLOR_BID, C'141,148,158');
   ChartSetInteger(chart, CHART_COLOR_ASK, C'141,148,158');
   ChartSetInteger(chart, CHART_COLOR_LAST, C'141,148,158');
   ChartSetInteger(chart, CHART_SHOW_GRID, true);
   ChartSetInteger(chart, CHART_SHOW_VOLUMES, CHART_VOLUME_HIDE);
   ChartSetInteger(chart, CHART_SHOW_OHLC, false);
   ChartSetInteger(chart, CHART_SHOW_BID_LINE, false);
   ChartSetInteger(chart, CHART_SHOW_ASK_LINE, false);
   ChartSetInteger(chart, CHART_SHOW_LAST_LINE, false);
   ChartSetInteger(chart, CHART_SHOW_PERIOD_SEP, true);
   ChartSetInteger(chart, CHART_SCALE, (int)MathMax(0, MathMin(5, scale)));
   ChartSetInteger(chart, CHART_AUTOSCROLL, false);
}

// Сколько баров назад находится время ts (0 = текущий бар).
int ShiftOf(string symbol, ENUM_TIMEFRAMES tf, long ts)
{
   if(ts <= 0)
      return(-1);
   return(iBarShift(symbol, tf, (datetime)ts, false));
}

int BarsBetween(string symbol, ENUM_TIMEFRAMES tf, long entry_ts, long exit_ts)
{
   int a = ShiftOf(symbol, tf, entry_ts);
   int b = ShiftOf(symbol, tf, exit_ts);
   if(a < 0 || b < 0)
      return(-1);
   return(MathAbs(a - b) + 1);
}

// Масштаб под длительность сделки: видимых баров должно быть с запасом.
int PickScale(int span)
{
   if(span <= 0)
      return(-1);
   int need = span * 3 + 20;
   if(need <= 45)  return(5);
   if(need <= 90)  return(4);
   if(need <= 180) return(3);
   if(need <= 360) return(2);
   if(need <= 720) return(1);
   return(0);
}

// Прокрутка к бару выхода: он остаётся справа с небольшим отступом.
void FocusChart(long chart, string symbol, ENUM_TIMEFRAMES tf,
                long entry_ts, long exit_ts)
{
   int i_exit = ShiftOf(symbol, tf, exit_ts);
   if(i_exit < 0)
   {
      ChartSetInteger(chart, CHART_AUTOSCROLL, true);
      ChartNavigate(chart, CHART_END, 0);
      return;
   }
   int margin = (int)MathMax(0, MathMin(50, InpRightMargin));
   int shift = (int)MathMin(0, margin - i_exit);
   ChartNavigate(chart, CHART_END, shift);
}

// Фиксируем ценовую шкалу по уровням сделки и свечам её окна, чтобы
// вход/стоп/цели не сжимались в тонкую полоску.
void FixPriceRange(long chart, string symbol, ENUM_TIMEFRAMES tf,
                   long entry_ts, long exit_ts,
                   double entry, double stop, double tp1, double tp2,
                   double tp3, double exit_px)
{
   double vals[6];
   vals[0] = entry; vals[1] = stop; vals[2] = tp1;
   vals[3] = tp2;   vals[4] = tp3;  vals[5] = exit_px;
   double hi = 0, lo = 0;
   for(int i = 0; i < 6; i++)
   {
      if(vals[i] <= 0)
         continue;
      if(hi == 0 || vals[i] > hi) hi = vals[i];
      if(lo == 0 || vals[i] < lo) lo = vals[i];
   }
   if(hi <= 0 || lo <= 0)
      return;

   int i_exit = ShiftOf(symbol, tf, exit_ts);
   if(i_exit < 0)
      i_exit = 0;
   int span = BarsBetween(symbol, tf, entry_ts, exit_ts);
   if(span < 0)
      span = 40;
   int margin = (int)MathMax(0, MathMin(50, InpRightMargin));
   int right = (int)MathMax(0, i_exit - margin);
   int left  = i_exit + (int)MathMin(500, MathMax(30, span * 3));
   int count = (int)MathMax(10, left - right + 1);

   double highs[], lows[];
   int got_h = CopyHigh(symbol, tf, right, count, highs);
   int got_l = CopyLow(symbol, tf, right, count, lows);
   int n = (int)MathMin(got_h, got_l);
   for(int i = 0; i < n; i++)
   {
      if(highs[i] > hi) hi = highs[i];
      if(lows[i] > 0 && lows[i] < lo) lo = lows[i];
   }

   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double pad = MathMax((hi - lo) * 0.08, point * 20);
   ChartSetDouble(chart, CHART_FIXED_MAX, hi + pad);
   ChartSetDouble(chart, CHART_FIXED_MIN, lo - pad);
}

void HLine(long chart, string name, double price, color clr,
           ENUM_LINE_STYLE style, int width, string text)
{
   if(price <= 0.0)
      return;
   if(!ObjectCreate(chart, name, OBJ_HLINE, 0, 0, price))
      return;
   ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, name, OBJPROP_STYLE, style);
   ObjectSetInteger(chart, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(chart, name, OBJPROP_BACK, false);
   ObjectSetInteger(chart, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(chart, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetString(chart, name, OBJPROP_TEXT, text);
}

void AddLabel(long chart, string name, int x, int y, string text,
              color clr, int size, string font = "Arial")
{
   if(!ObjectCreate(chart, name, OBJ_LABEL, 0, 0, 0))
      return;
   ObjectSetInteger(chart, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(chart, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(chart, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(chart, name, OBJPROP_TEXT, text);
   ObjectSetString(chart, name, OBJPROP_FONT, font);
   ObjectSetInteger(chart, name, OBJPROP_FONTSIZE, size);
   ObjectSetInteger(chart, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(chart, name, OBJPROP_SELECTABLE, false);
}

void DrawLevels(long chart, string symbol, string side, string label,
                double entry, double stop, double tp1, double tp2,
                double tp3, double exit_px, double r)
{
   string name = symbol + "  " + side;
   if(label != "")
      name = name + "  ·  " + label;
   if(MathAbs(r) > 0.0001)
      name = name + "  ·  " + (r > 0 ? "+" : "") + DoubleToString(r, 2) + "R";
   AddLabel(chart, "kz_title", 12, 14, name, clrWhite, 12, "Arial Bold");

   int dg = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   HLine(chart, "kz_entry", entry, clrSilver, STYLE_SOLID, 1,
         "Вход " + DoubleToString(entry, dg));
   HLine(chart, "kz_stop", stop, clrTomato, STYLE_DASH, 2,
         "Стоп " + DoubleToString(stop, dg));
   HLine(chart, "kz_tp1", tp1, clrLime, STYLE_DOT, 1,
         "TP1 " + DoubleToString(tp1, dg));
   HLine(chart, "kz_tp2", tp2, clrLime, STYLE_DOT, 1,
         "TP2 " + DoubleToString(tp2, dg));
   HLine(chart, "kz_tp3", tp3, clrLime, STYLE_DOT, 1,
         "TP3 " + DoubleToString(tp3, dg));
   HLine(chart, "kz_exit", exit_px, clrGold, STYLE_SOLID, 2,
         "Выход " + DoubleToString(exit_px, dg));
}

//+------------------------------------------------------------------+
void CheckRequest()
{
   if(!FileIsExist(REQ))
      return;

   int h = FileOpen(REQ, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;

   string symbol = "", tf_str = "", out = "", side = "LONG", label = "";
   double entry = 0, stop = 0, tp1 = 0, tp2 = 0, tp3 = 0, exit_px = 0, r = 0;
   long entry_ts = 0, exit_ts = 0;
   int width = InpWidth, height = InpHeight;

   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      int pos = StringFind(line, "=");
      if(pos <= 0)
         continue;
      string key = StringSubstr(line, 0, pos);
      string val = StringSubstr(line, pos + 1);
      StringTrimLeft(key);
      StringTrimRight(key);
      StringTrimLeft(val);
      StringTrimRight(val);
      if(key == "symbol")      symbol = val;
      else if(key == "tf")     tf_str = val;
      else if(key == "out")    out = val;
      else if(key == "side")   side = val;
      else if(key == "label")  label = val;
      else if(key == "entry")  entry = StringToDouble(val);
      else if(key == "stop")   stop = StringToDouble(val);
      else if(key == "tp1")    tp1 = StringToDouble(val);
      else if(key == "tp2")    tp2 = StringToDouble(val);
      else if(key == "tp3")    tp3 = StringToDouble(val);
      else if(key == "exit")   exit_px = StringToDouble(val);
      else if(key == "r")      r = StringToDouble(val);
      else if(key == "entry_ts") entry_ts = (long)StringToInteger(val);
      else if(key == "exit_ts")  exit_ts = (long)StringToInteger(val);
      else if(key == "width")  width = (int)StringToInteger(val);
      else if(key == "height") height = (int)StringToInteger(val);
   }
   FileClose(h);
   FileDelete(REQ);

   if(out == "" || out == g_last_out)
      return;
   g_last_out = out;

   ENUM_TIMEFRAMES tf = StrToTF(tf_str);
   if(symbol == "" || tf == PERIOD_CURRENT)
   {
      WriteDone("error", out, "bad symbol/tf");
      return;
   }

   SymbolSelect(symbol, true);
   long chart = ChartOpen(symbol, tf);
   if(chart == 0)
   {
      WriteDone("error", out, "ChartOpen failed " + IntegerToString(GetLastError()));
      return;
   }

   // Ждём, пока терминал подтянет историю для нового графика.
   int wait = 0;
   while(Bars(symbol, tf) == 0 && wait < 50)
   {
      Sleep(100);
      wait++;
   }

   int span = BarsBetween(symbol, tf, entry_ts, exit_ts);
   int scale = PickScale(span);
   if(scale < 0)
      scale = (int)MathMax(0, MathMin(5, InpScale));

   ConfigureChart(chart, scale);
   FocusChart(chart, symbol, tf, entry_ts, exit_ts);
   FixPriceRange(chart, symbol, tf, entry_ts, exit_ts,
                 entry, stop, tp1, tp2, tp3, exit_px);
   DrawLevels(chart, symbol, side, label, entry, stop, tp1, tp2, tp3, exit_px, r);
   ChartRedraw(chart);
   Sleep(InpWaitMs);
   ChartRedraw(chart);

   bool ok = ChartScreenShot(chart, out, (int)MathMax(320, width),
                             (int)MathMax(240, height), ALIGN_RIGHT);
   if(!ok)
   {
      Sleep(700);
      ChartRedraw(chart);
      ok = ChartScreenShot(chart, out, (int)MathMax(320, width),
                           (int)MathMax(240, height), ALIGN_RIGHT);
   }

   ChartClose(chart);
   if(ok)
      WriteDone("ok", out, "");
   else
      WriteDone("error", out, "ChartScreenShot failed " + IntegerToString(GetLastError()));
}
//+------------------------------------------------------------------+
