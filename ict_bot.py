import asyncio
import logging
import ccxt
import pandas as pd
import pandas_ta as ta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from datetime import datetime

TELEGRAM_TOKEN = "8850521535:AAFR_RkD2tB-yk3p8Vx3iTGMR-fwgFvzX-E"

# ================== CẤU HÌNH ==================
RR_RATIO = 5.0
EMA_LEN = 200
SWING_LEN = 5
ATR_LEN = 14
ATR_MULT = 1.0

# Các khung thời gian muốn quét
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m","45m", "1h", "4h"]   # Thêm/bớt khung ở đây

watching_symbols = []
monitoring_task = None

exchange = ccxt.binance({'enableRateLimit': True})
logging.basicConfig(level=logging.INFO)

def is_killzone():
    now = datetime.utcnow()
    hour = now.hour
    return (7 <= hour <= 11) or (13 <= hour <= 17)

def detect_fvg(df):
    for i in range(2, len(df)-1):
        if df['high'].iloc[i-1] < df['low'].iloc[i+1]:
            return True
        if df['low'].iloc[i-1] > df['high'].iloc[i+1]:
            return True
    return False

def get_ict_pro_signal(symbol, timeframe):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=400)
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        
        df['ema200'] = ta.ema(df['close'], length=EMA_LEN)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=ATR_LEN)
        
        df['swing_high'] = ta.pivothigh(df['high'], length=SWING_LEN)
        df['swing_low'] = ta.pivotlow(df['low'], length=SWING_LEN)
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        bull_trend = last['close'] > last['ema200']
        bear_trend = last['close'] < last['ema200']
        
        # Liquidity Sweep
        sweep_low = False
        sweep_high = False
        if not df['swing_low'].dropna().empty:
            last_low = df['swing_low'].dropna().iloc[-1]
            sweep_low = (last['low'] < last_low) and (last['close'] > last_low)
        if not df['swing_high'].dropna().empty:
            last_high = df['swing_high'].dropna().iloc[-1]
            sweep_high = (last['high'] > last_high) and (last['close'] < last_high)
        
        # BOS
        bos_long = not df['swing_high'].dropna().empty and last['close'] > df['swing_high'].dropna().iloc[-1]
        bos_short = not df['swing_low'].dropna().empty and last['close'] < df['swing_low'].dropna().iloc[-1]
        
        has_fvg = detect_fvg(df)
        killzone_ok = is_killzone()
        
        long_condition = bull_trend and (sweep_low or bos_long) and (has_fvg or killzone_ok)
        short_condition = bear_trend and (sweep_high or bos_short) and (has_fvg or killzone_ok)
        
        if long_condition:
            sl = last['close'] - last['atr'] * ATR_MULT
            tp = last['close'] + (last['close'] - sl) * RR_RATIO
            reason = "BOS/Sweep + FVG" if has_fvg else "BOS/Sweep + Killzone"
            return {
                "type": "LONG", "symbol": symbol, "tf": timeframe,
                "entry": last['close'], "sl": sl, "tp": tp,
                "reason": reason, "time": last['ts']
            }
        
        elif short_condition:
            sl = last['close'] + last['atr'] * ATR_MULT
            tp = last['close'] - (sl - last['close']) * RR_RATIO
            reason = "BOS/Sweep + FVG" if has_fvg else "BOS/Sweep + Killzone"
            return {
                "type": "SHORT", "symbol": symbol, "tf": timeframe,
                "entry": last['close'], "sl": sl, "tp": tp,
                "reason": reason, "time": last['ts']
            }
        return None
        
    except Exception as e:
        print(f"Lỗi {symbol} {timeframe}: {e}")
        return None


async def monitor(context: ContextTypes.DEFAULT_TYPE):
    global watching_symbols
    chat_id = context.job.data['chat_id']
    
    while True:
        for symbol in watching_symbols[:]:
            for tf in TIMEFRAMES:
                signal = get_ict_pro_signal(symbol, tf)
                if signal:
                    emoji = "🟢" if signal["type"] == "LONG" else "🔴"
                    text = f"""
{emoji} **ICT PRO SIGNAL - {signal["tf"]}**

**{signal["type"]} {signal["symbol"]}**

Entry : **{signal['entry']:.4f}**
SL    : **{signal['sl']:.4f}**
TP    : **{signal['tp']:.4f}**
R:R   : **1:{RR_RATIO}**
Lý do : {signal['reason']}

⏰ {signal['time'].strftime('%d/%m %H:%M:%S')}
                    """
                    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
                    break  # Chỉ báo 1 tín hiệu mạnh nhất cho coin đó
        
        await asyncio.sleep(30)


# ====================== COMMANDS ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 **ICT Pro Multi-Timeframe Bot** đã sẵn sàng!")

async def set_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global watching_symbols, monitoring_task
    if not context.args:
        await update.message.reply_text("Cách dùng: /set BTC/USDT ETH/USDT")
        return
    for arg in context.args:
        symbol = arg.upper().strip()
        if '/' not in symbol:
            symbol += "/USDT"
        if symbol not in watching_symbols:
            watching_symbols.append(symbol)
    
    chat_id = update.effective_chat.id
    if monitoring_task is None:
        monitoring_task = context.job_queue.run_repeating(monitor, interval=30, first=5, data={'chat_id': chat_id})
    
    await update.message.reply_text(f"✅ Đang theo dõi {len(watching_symbols)} coin trên {len(TIMEFRAMES)} khung thời gian.")

async def stop_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global watching_symbols, monitoring_task
    watching_symbols.clear()
    if monitoring_task:
        monitoring_task.schedule_removal()
        monitoring_task = None
    await update.message.reply_text("⛔ Đã dừng.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_pairs))
    app.add_handler(CommandHandler("stop", stop_monitor))
    
    print("🚀 ICT Pro Multi-TF Bot đang chạy...")
    app.run_polling()

if __name__ == '__main__':
    main()
