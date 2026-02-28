import time
import datetime
import pyupbit
import requests
import pandas as pd
import traceback

# ==================================================
# --- 정보 입력 (본인의 정보를 직접 입력하세요) ---
# ==================================================
ACCESS_KEY = "voMLtW0LzLkMVY0gwbRQmvASYoPC1eOExxAm8G64"
SECRET_KEY = "1GzX0hFxrc8YMhlPyhx8wnYNqNJlQ5Rzc2Xv2b2e"
TOKEN = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
CHAT_ID = "8403406400"
# ==================================================

# 업비트 객체 초기화
try:
    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
except Exception as e:
    print(f"API 연결 초기 실패: {e}")

# --- [설정값] 전략 및 리스크 관리 ---
K_VALUE = 0.55          
MAX_SLOTS = 3          
INVEST_FIXED = 5000    
MIN_ORDER_AMOUNT = 5000 
TARGET_PROFIT = 2.5    
STOP_LOSS = -1.8       
TICKERS_COUNT = 15      
BB_WINDOW = 20         
BB_STD = 2.0           
HEARTBEAT_HOURS = 6    

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {'chat_id': CHAT_ID, 'text': message}
    try:
        response = requests.post(url, data=params, timeout=10)
        if response.status_code != 200:
            print(f"❌ 텔레그램 실패")
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

# --- 메인 루프 ---
print(f"▶ 눌림목 공격형 모드 가동 중...")
send_telegram("🛡️ [시스템 가동] 스캘핑 봇 시작 ")
last_heartbeat = datetime.datetime.now()

while True:
    try:
        now = datetime.datetime.now()
        if now - last_heartbeat > datetime.timedelta(hours=HEARTBEAT_HOURS):
            send_telegram(f"💓 [정상 가동 중] {now.strftime('%H:%M')}")
            last_heartbeat = now
        print(f"\r[{now.strftime('%H:%M:%S')}] 감시 중...", end="")

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
            rev_rate = ((curr_p - avg_p) / avg_p) * 100
            reason = ""
            if rev_rate >= TARGET_PROFIT: reason = "🎯 목표 익절"
            elif rev_rate <= STOP_LOSS: reason = "⚠️ 손절선 이탈"
            elif check_bearish_engulfing(ticker) and rev_rate < -0.5: reason = "📉 위험 캔들 회피"

            if reason and (balance * curr_p >= MIN_ORDER_AMOUNT):
                upbit.sell_market_order(ticker, balance)
                send_telegram(f"💰 [매도]\n종목: {ticker}\n수익률: {rev_rate:.2f}%\n사유: {reason}")
                time.sleep(0.5)

        # --- 매수 탐색 ---
        day_info = get_safe_ohlcv("KRW-BTC", interval="day", count=1)
        if day_info is not None:
            start_time, end_time = day_info.index[0], day_info.index[0] + datetime.timedelta(days=1)

            if start_time + datetime.timedelta(seconds=10) < now < end_time - datetime.timedelta(minutes=5):
                if len(portfolio) < MAX_SLOTS:
                    prices = pyupbit.get_current_price(all_krw_tickers, verbose=True)
                    df_gainers = pd.DataFrame(prices)
                    df_gainers['rate'] = df_gainers['signed_change_rate'] * 100
                    target_tickers = df_gainers.sort_values(by='rate', ascending=False).head(TICKERS_COUNT)['market'].tolist()
                    
                    for ticker in target_tickers:
                        if any(p['ticker'] == ticker for p in portfolio): continue
                        curr_p = pyupbit.get_current_price(ticker)
                        upper_bb, mid_bb, lower_bb = get_bb(ticker)
                        ma7 = get_ma(ticker, 7)
                        ma20 = get_ma(ticker, 20)
                        rsi = get_rsi(ticker)
                        if not curr_p or not lower_bb or not ma7: continue

                        df_day = get_safe_ohlcv(ticker, interval="day", count=2)
                        target_p = df_day.iloc[0]['close'] + (df_day.iloc[0]['high'] - df_day.iloc[0]['low']) * K_VALUE
                        
                        # [전략 1] 신중한 돌파 (일봉 추세 유지)
                        cond_break = (curr_p > target_p) and (curr_p > ma7) and (curr_p > ma20) and (45 < rsi < 75)
                        
                        # [전략 2] 완화된 눌림목 (일봉 추세 무관, BB 하단 + RSI 위주)
                        # 하단 밴드 5% 이내 접근 + RSI 60 미만 + (프랙탈 신호 OR RSI 40 미만 과매도)
                        cond_pullback = (curr_p <= lower_bb * 1.05) and (rsi < 60) and (get_fractal_signal(ticker) or rsi < 40)

                        if cond_break or cond_pullback:
                            krw_bal = upbit.get_balance("KRW")
                            if krw_bal >= INVEST_FIXED:
                                upbit.buy_market_order(ticker, INVEST_FIXED)
                                strat = "돌파" if cond_break else "공격형눌림"
                                send_telegram(f"🚀 [매수]\n종목: {ticker}\n전략: {strat}\nRSI: {rsi:.1f}\n금액: 5,000원")
                                break
                        time.sleep(0.1)
        time.sleep(1)
    except Exception as e:
        print(f"\n🚨 오류: {e}"); time.sleep(10)