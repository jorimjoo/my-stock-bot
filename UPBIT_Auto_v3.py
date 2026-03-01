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
# 1. 클라우드 환경용 웹 서버 설정 (Port 10000)
# ==================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "UPBIT Aggressive-Balance Bot is Running!"

def run_flask():
    # Render/GCP 등에서 'Live' 상태 유지를 위해 10000번 포트를 엽니다.
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

t = threading.Thread(target=run_flask)
t.daemon = True
t.start()

# ==================================================
# 2. 사용자 정보 입력 (직접 입력 방식)
# ==================================================
ACCESS_KEY = "voMLtW0LzLkMVY0gwbRQmvASYoPC1eOExxAm8G64"
SECRET_KEY = "1GzX0hFxrc8YMhlPyhx8wnYNqNJlQ5Rzc2Xv2b2e"
TOKEN = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
CHAT_ID = "8403406400"

try:
    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
except Exception as e:
    print(f"API 연결 초기 실패: {e}")

# ==================================================
# 3. 전략 설정값 (공격형 밸런스 튜닝)
# ==================================================
MAX_SLOTS = 15          # 최대 보유 종목 수
INVEST_FIXED = 10000    # 1회 매수 금액 (10,000원)
MIN_ORDER_AMOUNT = 5000 # 업비트 최소 주문 금액
TARGET_PROFIT = 2.5     # 익절 목표 (%)
STOP_LOSS = -1.8        # 손절 제한 (%)
K_VALUE = 0.45          # 변동성 돌파 계수 (하향 조정으로 진입 속도 향상)
DISPARITY_LIMIT = 5.0   # 이격도 제한 (%) - 20일선 대비 5%까지 허용 (공격성 상향)
VOL_RATIO = 1.5         # 거래량 돌파 확인 - 평균 대비 1.5배 수급 확인 (공격성 상향)
TICKERS_COUNT = 30      # 실시간 상승률 상위 30개 종목 감시
BB_WINDOW = 20         
BB_STD = 2.0           
HEARTBEAT_HOURS = 6    

# ==================================================
# 4. 기술적 지표 및 보조 함수
# ==================================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {'chat_id': CHAT_ID, 'text': message}
    try: requests.post(url, data=params, timeout=10)
    except: pass

def get_safe_ohlcv(ticker, interval, count):
    for _ in range(3):
        try:
            df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
            if df is not None and not df.empty: return df
        except: time.sleep(0.5)
    return None

def get_rsi(ticker, period=14):
    df = get_safe_ohlcv(ticker, interval="minute5", count=period + 20)
    if df is None: return 50
    delta = df['close'].diff()
    up, down = delta.copy(), delta.copy()
    up[up < 0] = 0; down[down > 0] = 0
    _gain = up.ewm(com=period - 1, min_periods=period).mean()
    _loss = down.abs().ewm(com=period - 1, min_periods=period).mean()
    RS = _gain / _loss
    return float(100 - (100 / (1 + RS)).iloc[-1])

def get_ma(ticker, window):
    df = get_safe_ohlcv(ticker, interval="day", count=window+1)
    if df is None: return 0
    return df['close'].rolling(window=window).mean().iloc[-2]

def get_volume_status(ticker):
    df = get_safe_ohlcv(ticker, "minute5", 11)
    if df is None or len(df) < 11: return False
    avg_vol = df['volume'].iloc[:-1].mean()
    curr_vol = df['volume'].iloc[-1]
    return curr_vol > (avg_vol * VOL_RATIO)

def get_disparity(ticker):
    curr_p = pyupbit.get_current_price(ticker)
    df = get_safe_ohlcv(ticker, "day", 21)
    if df is None or curr_p is None: return 100
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    return (curr_p / ma20) * 100

def get_bb(ticker):
    df = get_safe_ohlcv(ticker, interval="minute5", count=BB_WINDOW + 2)
    if df is None or len(df) < BB_WINDOW: return None, None, None
    df['ma20'] = df['close'].rolling(window=BB_WINDOW).mean()
    df['std'] = df['close'].rolling(window=BB_WINDOW).std()
    lower = df['ma20'] - (df['std'] * BB_STD)
    return lower.iloc[-1]

def check_bearish_engulfing(ticker):
    df = get_safe_ohlcv(ticker, interval="minute1", count=2)
    if df is None or len(df) < 2: return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    if prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr['close'] < prev['open']: return True
    return False

def get_fractal_signal(ticker):
    df = get_safe_ohlcv(ticker, interval="minute5", count=10)
    if df is None or len(df) < 5: return False
    lows = df['low'].iloc[-5:].values
    if lows[2] < lows[0] and lows[2] < lows[1] and lows[2] < lows[3] and lows[2] < lows[4]:
        return True
    return False

# ==================================================
# 5. 메인 루프 (시장 감시 및 매매 실행)
# ==================================================
print(f"▶ 공격형 밸런스 시스템 가동 시작")
send_telegram("🛡️ [시스템 가동] ")
last_heartbeat = datetime.datetime.now()

while True:
    try:
        now = datetime.datetime.now()
        if now - last_heartbeat > datetime.timedelta(hours=HEARTBEAT_HOURS):
            send_telegram(f"💓 [정상 작동] {now.strftime('%H:%M')} 현재 시장 감시 중")
            last_heartbeat = now

        # 1. 시장 종목 및 잔고 데이터 동기화
        all_krw_tickers = pyupbit.get_tickers(fiat="KRW")
        balances = upbit.get_balances()
        if balances is None: 
            time.sleep(2); continue

        portfolio = []
        for b in balances:
            ticker = f"KRW-{b['currency']}"
            if b['currency'] != 'KRW' and float(b['balance']) > 0 and ticker in all_krw_tickers:
                portfolio.append({'ticker': ticker, 'balance': float(b['balance']), 'avg_p': float(b['avg_buy_price'])})

        print(f"\r[{now.strftime('%H:%M:%S')}] 감시 중 (보유: {len(portfolio)}/15)", end="")

        # 2. 매도 로직 (익절/손절/캔들회피)
        for item in portfolio:
            t_code, bal, avg_b = item['ticker'], item['balance'], item['avg_p']
            curr_p = pyupbit.get_current_price(t_code)
            if curr_p is None: continue
            
            profit_rate = ((curr_p - avg_b) / avg_b) * 100
            reason = ""
            
            if profit_rate >= TARGET_PROFIT: reason = "🎯 목표 익절"
            elif profit_rate <= STOP_LOSS: reason = "⚠️ 손절선 이탈"
            elif check_bearish_engulfing(t_code) and profit_rate < -0.3: reason = "📉 위험 캔들 회피"

            if reason and (bal * curr_p >= MIN_ORDER_AMOUNT):
                upbit.sell_market_order(t_code, bal)
                send_telegram(f"💰 [매도]\n종목: {t_code}\n수익률: {profit_rate:.2f}%\n사유: {reason}")
                time.sleep(0.5)

        # 3. 매수 탐색 (실시간 상승률 상위 30개 종목 갱신 검색)
        if len(portfolio) < MAX_SLOTS:
            prices = pyupbit.get_current_price(all_krw_tickers, verbose=True)
            df_mkt = pd.DataFrame(prices)
            df_mkt['rate'] = df_mkt['signed_change_rate'] * 100
            target_list = df_mkt.sort_values(by='rate', ascending=False).head(TICKERS_COUNT)
            
            for _, row in target_list.iterrows():
                ticker = row['market']
                change_rate = row['rate']
                if any(p['ticker'] == ticker for p in portfolio): continue
                
                curr_p = pyupbit.get_current_price(ticker)
                rsi = get_rsi(ticker)
                ma7 = get_ma(ticker, 7)
                disparity = get_disparity(ticker)      # 이격도 필터 (고점 방지)
                is_vol_burst = get_volume_status(ticker) # 수급 필터
                lower_bb = get_bb(ticker)
                
                if not curr_p or not ma7 or lower_bb is None: continue

                df_d = get_safe_ohlcv(ticker, "day", 2)
                target_p = df_d.iloc[0]['close'] + (df_d.iloc[0]['high'] - df_d.iloc[0]['low']) * K_VALUE

                # --- 공격형 매수 전략 로직 ---
                # A. 모멘텀: 전일대비 3% 이상 + 수급 1.5배 + RSI 76 미만 + 이격도 5% 이내
                cond_momentum = (change_rate > 3.0) and is_vol_burst and (50 < rsi < 76) and (disparity < 100 + DISPARITY_LIMIT)
                
                # B. 변동성 돌파: K=0.45 적용하여 더 빠르게 돌파 시 진입
                cond_break = (curr_p > target_p) and (curr_p > ma7) and (45 < rsi < 75) and (disparity < 100 + DISPARITY_LIMIT)
                
                # C. 눌림목: RSI 40 미만 또는 BB 하단 3% 이내 접근 시
                cond_pullback = (curr_p <= lower_bb * 1.03) and (rsi < 40)

                if cond_momentum or cond_break or cond_pullback:
                    krw_bal = float(upbit.get_balance("KRW"))
                    if krw_bal >= INVEST_FIXED:
                        upbit.buy_market_order(ticker, INVEST_FIXED)
                        s_name = "모멘텀(적극)" if cond_momentum else ("돌파(적극)" if cond_break else "눌림목(완화)")
                        send_telegram(f"🚀 [매수]\n종목: {ticker}\n전략: {s_name}\n이격도: {disparity-100:.1f}%\nRSI: {rsi:.1f}")
                        break
                time.sleep(0.05)
        
        time.sleep(1)

    except Exception as e:
        print(f"\n🚨 시스템 오류: {e}")
        time.sleep(10)
