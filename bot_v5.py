import pyupbit
import pandas as pd
import numpy as np
import time
import datetime
import requests
import os
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer 

# =========================
# 1. API 설정
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 전략 설정 (V17)
# =========================
TOP_N = 25
MAX_POSITIONS = 5

TAKE_PROFIT = 0.025     # +2.5%
STOP_LOSS = -0.02       # -2.0%

MIN_ORDER = 6000
FEE_RATE = 1.0005

positions = {}
blacklist = {}

# =========================
# 3. 유틸
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except:
        pass

# =========================
# 4. 거래량 TOP 코인
# =========================
def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        data = []

        for t in tickers:
            df = pyupbit.get_ohlcv(t, interval="minute5", count=2)
            if df is None:
                continue
            
            volume = df['volume'].iloc[-1]
            data.append((t, volume))

        data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in data[:TOP_N]]
    except:
        return []

# =========================
# 5. RSI
# =========================
def ta_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    ema_up = up.ewm(com=period-1).mean()
    ema_down = down.ewm(com=period-1).mean()

    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# =========================
# 6. 매수 로직 (핵심 개선)
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=50)
        if df is None or len(df) < 30:
            return False

        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['rsi'] = ta_rsi(df['close'])

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # ✅ 상승 조건 완화
        if cur['close'] < cur['ma20']:
            return False

        # ✅ 거래량 증가
        vol_now = cur['volume']
        vol_avg = df['volume'].rolling(20).mean().iloc[-1]

        if vol_now < vol_avg * 1.5:
            return False

        # ✅ RSI 완화
        if cur['rsi'] > 85:
            return False

        # ✅ 1분봉 상승 확인
        df1 = pyupbit.get_ohlcv(ticker, interval="minute1", count=5)
        if df1 is None:
            return False

        if df1['close'].iloc[-1] < df1['close'].iloc[-2]:
            return False

        return True

    except Exception as e:
        print("BUY ERROR:", e)
        return False

# =========================
# 7. 매수
# =========================
def buy_coin(ticker, krw):
    try:
        print(f"[매수 시도] {ticker}")

        res = upbit.buy_market_order(ticker, krw)

        if res is None or 'error' in res:
            print("❌ 매수 실패", res)
            return

        price = pyupbit.get_current_price(ticker)

        positions[ticker] = {
            "buy_price": price,
            "time": time.time()
        }

        send_msg(f"🔥 매수: {ticker}")

    except Exception as e:
        print("BUY FAIL:", e)

# =========================
# 8. 매도
# =========================
def sell_coin(ticker, reason):
    try:
        balance = upbit.get_balance(ticker)
        if balance is None or balance == 0:
            return

        price = pyupbit.get_current_price(ticker)
        buy_price = positions[ticker]['buy_price']

        profit = (price - buy_price) / buy_price * 100

        upbit.sell_market_order(ticker, balance)

        send_msg(f"{reason} {ticker} ({profit:.2f}%)")

        positions.pop(ticker, None)
        blacklist[ticker] = time.time()

    except Exception as e:
        print("SELL FAIL:", e)

# =========================
# 9. 메인 루프
# =========================
def main():
    send_msg("🚀 V17 실전 자동매매 시작")

    while True:
        try:
            krw = upbit.get_balance("KRW")
            if krw is None:
                krw = 0

            # TOP 코인
            top_coins = get_top_coins()

            # =================
            # 매도
            # =================
            for ticker in list(positions.keys()):
                price = pyupbit.get_current_price(ticker)
                buy_price = positions[ticker]['buy_price']

                profit = (price - buy_price) / buy_price

                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "🎯 익절")

                elif profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ 손절")

            # =================
            # 매수
            # =================
            for ticker in top_coins:
                if ticker in positions:
                    continue

                if ticker in blacklist and time.time() - blacklist[ticker] < 1800:
                    continue

                if len(positions) >= MAX_POSITIONS:
                    break

                if check_buy_signal(ticker):
                    print(f"[매수 신호] {ticker}")

                    if krw < MIN_ORDER:
                        continue

                    buy_amount = krw / (MAX_POSITIONS - len(positions))

                    if buy_amount < MIN_ORDER:
                        continue

                    buy_coin(ticker, buy_amount)
                    time.sleep(1)

            time.sleep(3)

        except Exception as e:
            print("MAIN ERROR:", e)
            time.sleep(5)

# =========================
# 실행
# =========================
if __name__ == "__main__":
    main()
