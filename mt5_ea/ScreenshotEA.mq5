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
#property version   "1.00"
#property description "KidzordFX: PNG графика по запросу Python"

input int InpTimerSec = 1;      // период опроса, секунд
input int InpWidth    = 1280;   // ширина PNG по умолчанию
input int InpHeight   = 720;    // высота PNG по умолчанию
input int InpScale    = 2;      // масштаб графика 0..5
input int InpWaitMs   = 1500;   // сколько ждать отрисовки графика

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

void ConfigureChart(long chart)
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
   ChartSetInteger(chart, CHART_SCALE, (int)MathMax(0, MathMin(5, InpScale)));
   ChartSetInteger(chart, CHART_AUTOSCROLL, true);
   ChartNavigate(chart, CHART_END, 0);
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

   ConfigureChart(chart);
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
