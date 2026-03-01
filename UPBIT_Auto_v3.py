from flask import Flask
import threading
import time
import datetime
import pyupbit
import requests
import pandas as pd
import os
import traceback

# ==================================================
# 1. Render용 가짜 웹 서버 설정 (Port 10000)
# ==================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "UPBIT Bot is Running Alive! (10,000 KRW Mode)"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

t = threading.Thread(target=run_flask)
t.daemon = True
t.start()

# ==================================================
# 2. 정보 입력 (본인의 정보를 직접 입력하세요)
# ==================================================
ACCESS_KEY = "voMLtW0LzLkMVY0gwbRQmvASYoPC1eOExxAm8G64"
SECRET_KEY = "1GzX0hFxrc8YMhlPyhx8wnYNqNJlQ5Rzc2Xv2b2e"
TOKEN = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
CHAT_ID = "8403406400"

try:
    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
except Exception as e:
    print(f"API 연결 초기 실패: {e}")

# --- [설정값] 전략 및 리스크 관리 ---
K_VALUE = 0.45          # 변동성 돌파 계수
MAX_SLOTS = 3          # 최대 보유 종목 수
INVEST_FIXED = 10000    # [변경] 1회 매수 고정 금액 (10,000원)
MIN_ORDER_AMOUNT = 5000 # 업비트 최소 주문 금액
TARGET_PROFIT = 2.5    # 익절 목표 (%)
STOP_LOSS = -2.0       # 손절 제한 (%)
TICKERS_COUNT = 30      # 스캔 종목 수
BB_WINDOW = 20         
BB_STD = 2.0           
HEARTBEAT_HOURS = 6    

# ==================================================
# 3. 보조 함수들
# ==================================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {'chat_id': CHAT_ID, 'text': message}
    try: requests.post(url, data=params, timeout=10)
    except: pass

def get_rsi(ticker, period=14):
    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=period + 20)
    if df is None: return 50
    delta = df['close'].diff()
    up, down = delta.copy(), delta.copy()
    up[up < 0] = 0; down[down > 0] = 0
    _gain = up.ewm(com=period - 1, min_periods=period).mean()
    _loss = down.abs().ewm(com=period - 1, min_periods=period).mean()
    RS = _gain / _loss
    return float(100 - (100 / (1 + RS)).iloc[-1])

def get_safe_balances():
    for _ in range(3):
        try:
            balances = upbit.get_balances()
            if balances is not None: return balances
        except: time.sleep(1)
    return None

def get_safe_ohlcv(ticker, interval="day", count=20):
    for _ in range(3):
        try:
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
            if df is not None and not df.empty: return df
        except: time.sleep(1)
    return None

def get_ma(ticker, window):
    df = get_safe_ohlcv(ticker, interval="day", count=window+1)
    if df is None: return 0
    return df['close'].rolling(window=window).mean().iloc[-2]

def get_bb(ticker):
    df = get_safe_ohlcv(ticker, interval="minute5", count=BB_WINDOW + 2)
    if df is None or len(df) < BB_WINDOW: return None, None, None
    df['ma20'] = df['close'].rolling(window=BB_WINDOW).mean()
    df['std'] = df['close'].rolling(window=BB_WINDOW).std()
    df['upper'] = df['ma20'] + (df['std'] * BB_STD)
    df['lower'] = df['ma20'] - (df['std'] * BB_STD)
    return df['upper'].iloc[-1], df['ma20'].iloc[-1], df['lower'].iloc[-1]

def get_fractal_signal(ticker):
    df = get_safe_ohlcv(ticker, interval="minute5", count=10)
    if df is None or len(df) < 5: return False
    lows = df['low'].iloc[-5:].values
    if lows[2] < lows[0] and lows[2] < lows[1] and lows[2] < lows[3] and lows[2] < lows[4]:
        return True
    return False

def check_bearish_engulfing(ticker):
    df = get_safe_ohlcv(ticker, interval="minute1", count=2)
    if df is None or len(df) < 2: return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    if prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr['close'] < prev['open']: return True
    return False

# ==================================================
# 4. 메인 루프
# ==================================================
print(f"▶ 급등주 & 눌림목 공격형 모드 (10,000원 매수 설정)")
send_telegram("🛡️ [시스템 가동] 스캘핑 봇 시작 (1회 10,000원 매수)")
last_heartbeat = datetime.datetime.now()

while True:
    try:
        now = datetime.datetime.now()
        if now - last_heartbeat > datetime.timedelta(hours=HEARTBEAT_HOURS):
            send_telegram(f"💓 [정상 작동] {now.strftime('%H:%M')} 현재 시장 감시 중")
            last_heartbeat = now
        print(f"\r[{now.strftime('%H:%M:%S')}] 종목 스캔 및 잔고 확인 중...", end="")

        all_krw_tickers = pyupbit.get_tickers(fiat="KRW")
        balances = get_safe_balances()
        if balances is None: continue

        portfolio = []
        for b in balances:
            ticker = f"KRW-{b['currency']}"
            if b['currency'] != 'KRW' and float(b['balance']) > 0 and ticker in all_krw_tickers:
                portfolio.append({'ticker': ticker, 'balance': float(b['balance']), 'avg_p': float(b['avg_buy_price'])})

        # --- 매도 감시 ---
        for item in portfolio:
            ticker, balance, avg_p = item['ticker'], item['balance'], item['avg_p']
            curr_p = pyupbit.get_current_price(ticker)
            if curr_p is None: continue
            
            sell_amount = balance * curr_p
            rev_rate = ((curr_p - avg_p) / avg_p) * 100
            
            reason = ""
            if rev_rate >= TARGET_PROFIT: reason = "🎯 목표 익절"
            elif rev_rate <= STOP_LOSS: reason = "⚠️ 손절선 이탈"
            elif check_bearish_engulfing(ticker) and rev_rate < -0.5: reason = "📉 위험 캔들 회피"

            if reason and sell_amount >= MIN_ORDER_AMOUNT:
                upbit.sell_market_order(ticker, balance)
                send_telegram(f"💰 [매도 완료]\n종목: {ticker}\n수익률: {rev_rate:.2f}%\n사유: {reason}")
                time.sleep(0.5)

        # --- 매수 탐색 ---
        if len(portfolio) < MAX_SLOTS:
            prices = pyupbit.get_current_price(all_krw_tickers, verbose=True)
            df_gainers = pd.DataFrame(prices)
            df_gainers['rate'] = df_gainers['signed_change_rate'] * 100
            target_tickers_df = df_gainers.sort_values(by='rate', ascending=False).head(TICKERS_COUNT)
            
            for index, row in target_tickers_df.iterrows():
                ticker = row['market']
                change_rate = row['rate']
                if any(p['ticker'] == ticker for p in portfolio): continue
                
                curr_p = pyupbit.get_current_price(ticker)
                upper_bb, mid_bb, lower_bb = get_bb(ticker)
                ma7 = get_ma(ticker, 7)
                rsi = get_rsi(ticker)
                if not curr_p or not lower_bb or not ma7: continue

                df_day = get_safe_ohlcv(ticker, interval="day", count=2)
                target_p = df_day.iloc[0]['close'] + (df_day.iloc[0]['high'] - df_day.iloc[0]['low']) * K_VALUE
                
                # 매수 조건 (공격형 눌림목 + 신중한 돌파 + 급등주 모멘텀)
                cond_break = (curr_p > target_p) and (curr_p > ma7) and (45 < rsi < 75)
                cond_momentum = (change_rate > 5.0) and (curr_p > ma7) and (60 < rsi < 78)
                cond_pullback = (curr_p <= lower_bb * 1.05) and (rsi < 60) and (get_fractal_signal(ticker) or rsi < 40)

                if cond_break or cond_momentum or cond_pullback:
                    krw_bal = upbit.get_balance("KRW")
                    if krw_bal >= INVEST_FIXED:
                        upbit.buy_market_order(ticker, INVEST_FIXED)
                        
                        if cond_momentum: strat = "급등주모멘텀"
                        elif cond_break: strat = "변동성돌파"
                        else: strat = "눌림목"
                        
                        send_telegram(f"🚀 [매수 완료]\n종목: {ticker}\n전략: {strat}\n전일대비: {change_rate:.1f}%\n금액: 10,000원")
                        break
                time.sleep(0.05)
        time.sleep(1)
    except Exception as e:
        print(f"\n🚨 시스템 오류: {e}")
        time.sleep(10)
