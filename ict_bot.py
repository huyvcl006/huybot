import asyncio
import logging
import ccxt
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = "8850521535:AAFR_RkD2tB-yk3p8Vx3iTGMR-fwgFvzX-E"   # ← Thay token vào đây

# Cấu hình
EMA_LENGTH = 200
ATR_LENGTH = 14
ATR_MULTIPLIER = 2.0
RR_RATIO = 5.0
TIMEFRAME = "15m"

current_symbol = None
monitoring_task = None

exchange = ccxt.binance({'enableRateLimit': True})

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def get_signal(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        df['ema200'] = calculate_ema(df['close'], EMA_LENGTH)
        df['atr'] = calculate_atr(df, ATR_LENGTH)
        
        current = df.iloc[-1]
        previous = df.iloc[-2]
        
        longCondition = (current['close'] > current['ema200']) and (previous['close'] <= previous['ema200'])
        shortCondition = (current['close'] < current['ema200']) and (previous['close'] >= previous['ema200'])
        
        if longCondition or shortCondition:
            direction = "LONG" if longCondition else "SHORT"
            sl = current['close'] - current['atr'] * ATR_MULTIPLIER if longCondition else current['close'] + current['atr'] * ATR_MULTIPLIER
            tp = current['close'] + (current['close'] - sl) * RR_RATIO if longCondition else current['close'] - (sl - current['close']) * RR_RATIO
            
            return {
                "direction": direction,
                "entry": current['close'],
                "sl": sl,
                "tp": tp,
                "time": current['timestamp']
            }
        return None

    except Exception as e:
        print(f"Lỗi: {e}")
        return None


async def monitor(context: ContextTypes.DEFAULT_TYPE):
    global current_symbol
    chat_id = context.job.data['chat_id']
    
    while True:
        if current_symbol:
            signal = get_signal(current_symbol)
            if signal:
                emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
                msg = f"""
{emoji} **ICT ENTRY SIGNAL**

**{signal["direction"]} {current_symbol}**

Entry : **{signal['entry']:.4f}**
SL     : **{signal['sl']:.4f}**
TP     : **{signal['tp']:.4f}**
R:R    : **1:{RR_RATIO}**

⏰ {signal['time'].strftime('%H:%M:%S')}
                """
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
        await asyncio.sleep(60)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 ICT Bot đã sẵn sàng!\nDùng lệnh: /set BTC/USDT")

async def set_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_symbol, monitoring_task
    if not context.args:
        await update.message.reply_text("Cách dùng: /set BTC/USDT")
        return
    
    symbol = context.args[0].upper().strip()
    if '/' not in symbol:
        symbol += "/USDT"
    
    current_symbol = symbol
    chat_id = update.effective_chat.id
    
    if monitoring_task:
        monitoring_task.schedule_removal()
    
    monitoring_task = context.job_queue.run_repeating(
        monitor, interval=60, first=5, data={'chat_id': chat_id}
    )
    
    await update.message.reply_text(f"✅ **Đang theo dõi {symbol}**\nBot sẽ báo Entry khi có tín hiệu.")

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_symbol, monitoring_task
    current_symbol = None
    if monitoring_task:
        monitoring_task.schedule_removal()
    await update.message.reply_text("⛔ Đã dừng theo dõi.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_pair))
    app.add_handler(CommandHandler("stop", stop_bot))
    
    print("🚀 ICT Bot đang chạy...")
    app.run_polling()

if __name__ == '__main__':
    main()