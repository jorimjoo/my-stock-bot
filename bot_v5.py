import pyupbit
import pandas as pd
import time
import datetime

# =========================
# API KEY
# =========================
ACCESS_KEY = ""
SECRET_KEY = ""

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 설정값
# =========================
TOP_N = 15
MAX_POSITIONS = 15
STOP_LOSS = -0.03       # -3%
TAKE_PROFIT = 0.05      # +5%
TRAILING_STOP = 0.02    # 2%
BLACKLIST_HOLD = 1800   # 30분

MIN_ORDER = 6000

blacklist = {}
entry_price = {}
peak_price = {}

# =========================
# 거래대금 TOP 코인
# =========================
def get_top_coins():
    tickers = pyupbit.get_tickers(fiat="KRW")
    data = []

    for t in tickers:
        df = pyupbit.get_ohlcv(t, interval="day", count=1)
        if df is not None:
            volume = df['value'].iloc[-1]
            data.append((t, volume))

    data.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in data[:TOP_N]]

# =========================
# MA 계산
# =========================
def get_ma(df, period):
    return df['close'].rolling(period).mean().iloc[-1]

# =========================
# 매수 조건
# =========================
def check_buy_signal(ticker):
    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)

    if df is None or len(df) < 20:
        return False

    current_price = df['close'].iloc[-1]
    ma5 = get_ma(df, 5)
    ma20 = get_ma(df, 20)

    # 1. 정배열
    cond1 = current_price > ma5 > ma20

    # 2. 양봉
    cond2 = df['close'].iloc[-1] > df['open'].iloc[-1]

    # 3. 거래량 증가
    vol1 = df['volume'].iloc[-2]
    vol2 = df['volume'].iloc[-3]
    cond3 = vol1 > vol2

    return cond1 and cond2 and cond3

# =========================
# 보유 코인 조회
# =========================
def get_balances():
    balances = upbit.get_balances()
    result = {}

    for b in balances:
        if b['currency'] == 'KRW':
            result['KRW'] = float(b['balance'])
        else:
            ticker = f"KRW-{b['currency']}"
            amount = float(b['balance']) * float(b['avg_buy_price'])
            if amount > 5000:
                result[ticker] = float(b['avg_buy_price'])

    return result

# =========================
# 매수
# =========================
def buy_coin(ticker, krw):
    try:
        upbit.buy_market_order(ticker, krw)
        print(f"매수: {ticker}")
        entry_price[ticker] = pyupbit.get_current_price(ticker)
        peak_price[ticker] = entry_price[ticker]
    except Exception as e:
        print("매수 실패:", e)

# =========================
# 매도
# =========================
def sell_coin(ticker):
    try:
        balance = upbit.get_balance(ticker)
        upbit.sell_market_order(ticker, balance)
        print(f"매도: {ticker}")
    except Exception as e:
        print("매도 실패:", e)

# =========================
# 메인 루프
# =========================
while True:
    try:
        now = time.time()

        balances = get_balances()
        krw = balances.get("KRW", 0)

        # 블랙리스트 정리
        blacklist = {k: v for k, v in blacklist.items() if now - v < BLACKLIST_HOLD}

        top_coins = get_top_coins()

        # ======================
        # 매수 로직
        # ======================
        for ticker in top_coins:
            if ticker in balances:
                continue

            if ticker in blacklist:
                continue

            if len(balances) - 1 >= MAX_POSITIONS:
                break

            if check_buy_signal(ticker):
                buy_amount = max(krw * 0.2, MIN_ORDER)
                if krw > MIN_ORDER:
                    buy_coin(ticker, buy_amount)
                    time.sleep(1)

        # ======================
        # 매도 로직
        # ======================
        for ticker in list(balances.keys()):
            if ticker == "KRW":
                continue

            current_price = pyupbit.get_current_price(ticker)
            buy_price = balances[ticker]

            profit = (current_price - buy_price) / buy_price

            # 최고가 갱신
            if ticker in peak_price:
                peak_price[ticker] = max(peak_price[ticker], current_price)

            # 손절
            if profit <= STOP_LOSS:
                sell_coin(ticker)
                blacklist[ticker] = now
                continue

            # 익절
            if profit >= TAKE_PROFIT:
                sell_coin(ticker)
                continue

            # 트레일링 스탑
            if ticker in peak_price:
                drop = (peak_price[ticker] - current_price) / peak_price[ticker]
                if drop >= TRAILING_STOP:
                    sell_coin(ticker)
                    continue

        time.sleep(5)

    except Exception as e:
        print("에러:", e)
        time.sleep(5)
