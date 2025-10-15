# ==========================
# main.py — XT-ScalperPro FULL
# ==========================

# ==========================
# 📦 ІМПОРТИ — XT-ScalperPro
# ==========================

import asyncio
import ccxt
import pandas as pd
from decimal import Decimal, ROUND_DOWN
import logging
import math
import pandas_ta as ta   # ✅ використовуємо pandas_ta замість TA-Lib
from datetime import datetime
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.clock import Clock

# ==========================
# 📌 GLOBALS
# ==========================
API_KEY = "76d396d3-2f77-4bb7-a3df-03c386928b73"
API_SECRET = "d415d2938101d1d172213549c8d1e3ad177acb05"

POSITION_SIZE_USDT_MIN = 5
POSITION_SIZE_USDT_MAX = 10

SIGNAL_MODE = "ULTRA"  # ULTRA / SAFE / HYBRID
SL_METHOD = 1           # 1–8
GUARD_MODE = 1          # 1–4

bot_running = False
current_positions = {}
_all_symbols = []
live_messages = []

# Таймфрейми
TIMEFRAME_1M = "1m"
TIMEFRAME_3M = "3m"

ATR_MULT = 1.5
TRAILING_BUFFER = 0.002
TREND_CONFIRM_BARS = 5
EMA_FAST_3M = 10
EMA_SLOW_3M = 50

# ==========================
# 📌 EXCHANGE INIT
# ==========================
exchange = ccxt.xt({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
    "options": {"defaultType": "future"},
})

# ==========================
# 📌 LOGGER
# ==========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("XT-ScalperPro") 

# ==========================
# 📌 UTILS
# ==========================
async def fetch_ohlcv(symbol, timeframe='15m', limit=100):
    """Отримати історичні дані OHLCV"""
    try:
        data = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(data, columns=['timestamp','open','high','low','close','volume'])
        return df
    except Exception as e:
        logger.warning(f"fetch_ohlcv error {symbol}: {e}")
        return pd.DataFrame(columns=['timestamp','open','high','low','close','volume'])

async def get_last_price(symbol):
    """Отримати останню ціну"""
    try:
        ticker = await exchange.fetch_ticker(symbol)
        return float(ticker["last"])
    except Exception as e:
        logger.warning(f"get_last_price error {symbol}: {e}")
        return None

def calc_order_qty(usdt_amount, price, leverage=None):
    """Розрахунок кількості контрактів із авто-плечем"""
    if price == 0: return 0
    leverage = leverage or 20  # дефолт 20x
    qty = (usdt_amount * leverage) / price
    # округляємо на 4 знаки для XT
    return float(Decimal(qty).quantize(Decimal("0.0001"), rounding=ROUND_DOWN))

def adjust_qty_to_step(symbol, qty):
    """Корекція кількості під біржовий крок"""
    return round(qty, 4)

async def calculate_atr(symbol, timeframe='1m', period=14):
    """Обчислення ATR без TA-Lib (через pandas_ta)"""
    df = await fetch_ohlcv(symbol, timeframe, limit=period + 2)
    if df.empty or len(df) < 2:
        return 0
    try:
        atr = ta.atr(high=df["high"], low=df["low"], close=df["close"], length=period)
        return float(atr.iloc[-1])
    except Exception as e:
        logger.warning(f"ATR error {symbol}: {e}")
        return 0

# ==========================
# 📌 SIGNALS
# ==========================
async def check_signal(symbol):
    """Режими сигналів ULTRA / SAFE / HYBRID"""
    try:
        df = await fetch_ohlcv(symbol, '15m', limit=120)
        if df.empty or len(df) < 50:
            return "none"

        df['ema_fast'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema_trend'] = df['close'].ewm(span=100, adjust=False).mean()

        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14, min_periods=1).mean()
        loss = -delta.where(delta < 0, 0).rolling(14, min_periods=1).mean()
        rs = gain / loss.replace(0,1e-10)
        df['rsi'] = 100 - (100 / (1+rs))

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close, ema_f, ema_s, ema_tr, rsi = float(last['close']), float(last['ema_fast']), float(last['ema_slow']), float(last['ema_trend']), float(last['rsi'])
        prev_close = float(prev['close'])

        if SIGNAL_MODE=="ULTRA":
            if rsi<32 and close>ema_f and ema_f>ema_s and close>prev_close: return "buy"
            if rsi>68 and close<ema_f and ema_f<ema_s and close<prev_close: return "sell"
        elif SIGNAL_MODE=="SAFE":
            if rsi<28 and close>ema_f>ema_s and close>ema_tr and (close-prev_close)/prev_close>0.002: return "buy"
            if rsi>72 and close<ema_f<ema_s and close<ema_tr and (prev_close-close)/prev_close>0.002: return "sell"
        elif SIGNAL_MODE=="HYBRID":
            if rsi<35 and ema_f>ema_s and close>ema_f: return "buy"
            if rsi>65 and ema_f<ema_s and close<ema_f: return "sell"
        return "none"
    except Exception as e:
        logger.warning(f"check_signal error {symbol}: {e}")
        live_messages.append(f"⚠️ Signal check failed {symbol}: {e}")
        return "none"

# ==========================
# 📌 PLACE ORDER
# ==========================
async def place_order(symbol, side, amount, price=None, sl_method=None):
    """Place Order з усіма методами SL 1–8"""
    global SL_METHOD
    if sl_method is None:
        sl_method = SL_METHOD

    order_type = "MARKET"
    params = {}
    if sl_method==1: order_type="STOP_MARKET"; params["stopPrice"]=price
    elif sl_method==2: order_type="STOP"; params["stopPrice"]=price
    elif sl_method==3: order_type="STOP_LIMIT"; params["stopPrice"]=price; params["limitPrice"]=float(price*(0.999 if side=="buy" else 1.001))
    elif sl_method==4: order_type="LIMIT"; params["price"]=price
    elif sl_method==5: order_type="OCO"; params["price"]=price; params["stopPrice"]=float(price*(0.995 if side=="buy" else 1.005))
    elif sl_method==6: order_type="LIMIT"; params["price"]=price; params["reduceOnly"]=True
    elif sl_method==7: order_type="MARKET"
    elif sl_method==8: order_type="GUARD"; guard_buffer=Decimal("0.001"); params["stopPrice"]=float(price*(1-guard_buffer) if side=="buy" else price*(1+guard_buffer))

    try:
        if order_type=="MARKET":
            await exchange.create_market_order(symbol, side, float(amount))
        else:
            await exchange.create_order(symbol, order_type, side, float(amount), float(price or 0), params)
        live_messages.append(f"✅ Placed {order_type} {side.upper()} {symbol} @ {price} (SL {sl_method})")
        logger.info(f"Placed {order_type} {side.upper()} {symbol} @ {price} (SL {sl_method})")
    except Exception as e:
        live_messages.append(f"⚠️ Failed {order_type} {side.upper()} {symbol}: {e}")
        logger.warning(f"Failed {order_type} {side.upper()} {symbol}: {e}")

# ==========================
# 📌 MONITOR POSITIONS OPTIMIZED
# ==========================
def round_price(price, tick_size=0.0001):
    """Округлення ціни до кроку біржі"""
    return (Decimal(price) // Decimal(str(tick_size))) * Decimal(str(tick_size))

async def monitor_position_mode(symbol, pos, tick_size=0.0001):
    """TP1 / TP2 / SL з Guard Modes 1–4, оптимізований з округленням"""
    entry = Decimal(str(pos.get("entryPrice", 20)))
    side = pos.get("side", "buy").lower()
    amount = Decimal(str(pos.get("contracts", POSITION_SIZE_USDT_MAX)))
    last_price = await get_last_price(symbol)
    if last_price is None: 
        return

    sl_price = tp1_price = tp2_price = entry

    # --- Guard Modes ---
    if GUARD_MODE == 1:
        df = await fetch_ohlcv(symbol, TIMEFRAME_1M, limit=5)
        if not df.empty:
            sl_price = min(df["low"]) if side == "buy" else max(df["high"])
            tp1_price = entry * (1.015 if side == "buy" else 0.985)
            tp2_price = entry * (1.025 if side == "buy" else 0.975)

    elif GUARD_MODE == 2:
        atr = await calculate_atr(symbol, TIMEFRAME_1M)
        sl_price = entry - Decimal(str(atr * ATR_MULT)) if side == "buy" else entry + Decimal(str(atr * ATR_MULT))
        tp1_price = entry + Decimal(str(atr * ATR_MULT)) if side == "buy" else entry - Decimal(str(atr * ATR_MULT))
        tp2_price = entry + Decimal(str(atr * ATR_MULT * 2)) if side == "buy" else entry - Decimal(str(atr * ATR_MULT * 2))

    elif GUARD_MODE == 3:
        sl_price = entry * (0.99 if side == "buy" else 1.01)
        tp1_price = entry * (1.015 if side == "buy" else 0.985)
        tp2_price = entry * (1.025 if side == "buy" else 0.975)
        if side == "buy" and last_price > tp2_price:
            tp2_price = last_price * (1 - TRAILING_BUFFER)
        elif side == "sell" and last_price < tp2_price:
            tp2_price = last_price * (1 + TRAILING_BUFFER)

    elif GUARD_MODE == 4:
        df = await fetch_ohlcv(symbol, TIMEFRAME_3M, limit=TREND_CONFIRM_BARS + 1)
        if not df.empty:
            ema_fast = ta.ema(df["close"], length=EMA_FAST_3M).iloc[-1]
            ema_slow = ta.ema(df["close"], length=EMA_SLOW_3M).iloc[-1]
            gap = abs(ema_fast - ema_slow) / last_price
            sl_price = entry - Decimal(str(gap * last_price)) if side == "buy" else entry + Decimal(str(gap * last_price))
            tp1_price = entry + Decimal(str(gap * last_price)) if side == "buy" else entry - Decimal(str(gap * last_price))
            tp2_price = entry + Decimal(str(2 * gap * last_price)) if side == "buy" else entry - Decimal(str(2 * gap * last_price))

    # --- Округляємо всі ціни під крок біржі ---
    sl_price = round_price(sl_price, tick_size)
    tp1_price = round_price(tp1_price, tick_size)
    tp2_price = round_price(tp2_price, tick_size)

    # --- TP/SL Execution ---
    if not pos.get("tp1_closed", False):
        if (side == "buy" and last_price >= tp1_price) or (side == "sell" and last_price <= tp1_price):
            await place_order(symbol, "sell" if side == "buy" else "buy", amount * Decimal("0.5"), tp1_price)
            pos["tp1_closed"] = True
            live_messages.append(f"🏆 TP1 hit for {symbol}")

    if not pos.get("tp2_closed", False):
        if (side == "buy" and last_price >= tp2_price) or (side == "sell" and last_price <= tp2_price):
            await place_order(symbol, "sell" if side == "buy" else "buy", amount * Decimal("0.5"), tp2_price)
            pos["tp2_closed"] = True
            live_messages.append(f"🏆 TP2 hit for {symbol}")

    if not pos.get("sl_hit", False):
        if (side == "buy" and last_price <= sl_price) or (side == "sell" and last_price >= sl_price):
            await place_order(symbol, "sell" if side == "buy" else "buy", amount, sl_price)
            pos["sl_hit"] = True
            live_messages.append(f"⚡ SL hit for {symbol}")

# ==========================
# 📌 UPDATE SYMBOLS
# ==========================
async def update_symbols():
    global _all_symbols
    markets = await exchange.load_markets()
    _all_symbols = [s for s in markets if s.endswith("USDT:USDT")]

# ==========================
# 📌 BOT LOOP
# ==========================
async def start_bot():
    global bot_running
    bot_running=True
    await update_symbols()
    while bot_running:
        for sym in _all_symbols[:10]:
            if sym not in current_positions:
                sig = await check_signal(sym)
                if sig in ["buy","sell"]:
                    price = await get_last_price(sym)
                    qty_usdt = POSITION_SIZE_USDT_MIN + (POSITION_SIZE_USDT_MAX-POSITION_SIZE_USDT_MIN)/2
                    qty = adjust_qty_to_step(sym, calc_order_qty(qty_usdt, price))
                    if qty>0:
                        await place_order(sym, sig, qty, price)
        # Моніторинг відкритих позицій
        for sym,pos in current_positions.items():
            await monitor_position_mode(sym,pos)
        await asyncio.sleep(3)

# ==========================
# 📌 KIVY GUI
# ==========================
class ScalperGUI(BoxLayout):
    def update_positions(self, text):
        self.ids.positions_output.text = text

    def update_signals(self, text):
        self.ids.signals_output.text = text

class ScalperApp(App):
    def build(self):
        self.bot_running = False
        self.gui = ScalperGUI()
        Clock.schedule_interval(lambda dt: asyncio.ensure_future(self.gui_loop()), 2)
        return self.gui

    async def gui_loop(self):
        pos_text=""
        for sym,pos in current_positions.items():
            pos_text+=f"{sym} | {pos['side']} | кількість: {pos['contracts']} | вхід: {pos['entryPrice']}\n"
        self.gui.update_positions(pos_text or "Немає позицій")

        sig_text=""
        for sym in _all_symbols[:20]:
            sig = await check_signal(sym)
            if sig!="none":
                sig_text+=f"{sym}: {sig}\n"
        self.gui.update_signals(sig_text or "Немає сигналів")

    def start_bot(self):
        self.bot_running = True
        asyncio.ensure_future(start_bot())

    def stop_bot(self):
        self.bot_running = False

    def change_signal_mode(self):
        global SIGNAL_MODE
        SIGNAL_MODE = {"ULTRA":"SAFE","SAFE":"HYBRID","HYBRID":"ULTRA"}[SIGNAL_MODE]

    def change_sl_method(self):
        global SL_METHOD
        SL_METHOD = SL_METHOD + 1 if SL_METHOD < 8 else 1

    def change_guard_mode(self):
        global GUARD_MODE
        GUARD_MODE = GUARD_MODE + 1 if GUARD_MODE < 4 else 1

if __name__=="__main__":
    ScalperApp().run()