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
# 1. 클라우드 환경용 웹 서버 설정
# ==================================================
app = Flask(__name__)
@app.route('/')
def home(): return "UPBIT Super Aggressive Bot is Running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

t = threading.Thread(target=run_flask); t.daemon = True; t.start()

# ==================================================
# 2. 사용자 정보 입력
# ==================================================
ACCESS_KEY = "voMLtW0LzLkMVY0gwbRQmvASYoPC1eOExxAm8G64"
SECRET_KEY = "1GzX0hFxrc8YMhlPyhx8wnYNqNJlQ5Rzc2Xv2b2e"
TOKEN = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
CHAT_ID = "8403406400"
upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# ==================================================
# 3. 기본 설정값 (평상시)
# ==================================================
MAX_SLOTS = 15          
BASE_INVEST = 10000     
MIN_ORDER_AMOUNT = 5000 
TARGET_PROFIT = 2.5    
STOP_LOSS = -1.8       
BASE_K = 0.45           
BASE_DISPARITY = 5.0    
BASE_VOL_RATIO = 1.5    
TICKERS_COUNT = 30      
HEARTBEAT_HOURS = 6    

# ==================================================
# 4. 보조 지표 함수
# ==================================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {'chat_id': CHAT_ID, 'text': message}; requests.post(url, data=params, timeout=10)

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
    return float(100 - (100 / (1 + (_gain / _loss))).iloc[-1])

def get_ma(ticker, window):
    df = get_safe_ohlcv(ticker, interval="day", count=window+1)
    return df['close'].rolling(window=window).mean().iloc[-2] if df is not None else 0

def get_volume_status(ticker, ratio):
    df = get_safe_ohlcv(ticker, "minute5", 11)
    if df is None or len(df) < 11: return False
    return df['volume'].iloc[-1] > (df['volume'].iloc[:-1].mean() * ratio)

def get_disparity(ticker):
    curr_p = pyupbit.get_current_price(ticker)
    df = get_safe_ohlcv(ticker, "day", 21)
    if df is None or curr_p is None: return 100
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    return (curr_p / ma20) * 100

# ==================================================
# 5. 메인 루프
# ==================================================
print("▶ Super Aggressive(50% UP) 시스템 가동")
send_telegram("🚀 [시스템 가동] ")
last_heartbeat = datetime.datetime.now()

while True:
    try:
        now = datetime.datetime.now()
        
        # --- 시간대별 50% 공격성 차등 적용 ---
        is_aggressive_time = (now.hour == 9 and 0 <= now.minute < 20)
        
        if is_aggressive_time:
            # 50% 강화 설정 (금액 대폭 UP, 진입장벽 대폭 DOWN)
            current_invest = BASE_INVEST * 1.5        # 15,000원 매수
            current_k = BASE_K * 0.5                  # K값 50% 하향 (타점 초고속)
            current_disparity = BASE_DISPARITY * 1.5  # 이격도 50% 상향 (추격 매수 극대화)
            current_vol_ratio = BASE_VOL_RATIO * 0.5  # 거래량 기준 50% 하향 (선취매)
            mode_name = "💥 초공격(50% UP)"
        else:
            current_invest, current_k, current_disparity, current_vol_ratio = BASE_INVEST, BASE_K, BASE_DISPARITY, BASE_VOL_RATIO
            mode_name = "🛡️ 일반감시"

        if now - last_heartbeat > datetime.timedelta(hours=HEARTBEAT_HOURS):
            send_telegram(f"💓 [정상 작동] {mode_name}"); last_heartbeat = now

        # 잔고 및 포트폴리오 확인
        all_krw_tickers = pyupbit.get_tickers(fiat="KRW")
        balances = upbit.get_balances()
        if balances is None: time.sleep(2); continue

        portfolio = []
        for b in balances:
            ticker = f"KRW-{b['currency']}"
            if b['currency'] != 'KRW' and float(b['balance']) > 0 and ticker in all_krw_tickers:
                portfolio.append({'ticker': ticker, 'balance': float(b['balance']), 'avg_p': float(b['avg_buy_price'])})

        print(f"\r[{now.strftime('%H:%M:%S')}] {mode_name} (보유: {len(portfolio)}/15)", end="")

        # 매도 감시
        for item in portfolio:
            t_code, bal, avg_b = item['ticker'], item['balance'], item['avg_p']
            curr_p = pyupbit.get_current_price(t_code)
            if curr_p is None: continue
            rate = ((curr_p - avg_b) / avg_b) * 100
            
            if rate >= TARGET_PROFIT or rate <= STOP_LOSS:
                if (bal * curr_p >= MIN_ORDER_AMOUNT):
                    upbit.sell_market_order(t_code, bal)
                    send_telegram(f"💰 [매도] {t_code}\n수익: {rate:.2f}%\n모드: {mode_name}")

        # 매수 탐색
        if len(portfolio) < MAX_SLOTS:
            prices = pyupbit.get_current_price(all_krw_tickers, verbose=True)
            target_list = pd.DataFrame(prices).sort_values(by='signed_change_rate', ascending=False).head(TICKERS_COUNT)
            
            for _, row in target_list.iterrows():
                ticker = row['market']
                change_rate = row['signed_change_rate'] * 100
                if any(p['ticker'] == ticker for p in portfolio): continue
                
                curr_p = pyupbit.get_current_price(ticker)
                rsi = get_rsi(ticker)
                ma7 = get_ma(ticker, 7)
                disparity = get_disparity(ticker)      
                is_vol_burst = get_volume_status(ticker, current_vol_ratio) 
                
                if not curr_p or not ma7: continue

                df_d = get_safe_ohlcv(ticker, "day", 2)
                target_p = df_d.iloc[0]['close'] + (df_d.iloc[0]['high'] - df_d.iloc[0]['low']) * current_k

                # 초공격 모드 시 RSI 상한을 85까지 대폭 상향
                rsi_upper = 85 if is_aggressive_time else 76
                
                cond_momentum = (change_rate > 3.0) and is_vol_burst and (50 < rsi < rsi_upper) and (disparity < 100 + current_disparity)
                cond_break = (curr_p > target_p) and (curr_p > ma7) and (45 < rsi < 75) and (disparity < 100 + current_disparity)

                if cond_momentum or cond_break:
                    if float(upbit.get_balance("KRW")) >= current_invest:
                        upbit.buy_market_order(ticker, current_invest)
                        send_telegram(f"🚀 [매수: {mode_name}]\n종목: {ticker}\n금액: {current_invest:,.0f}원")
                        break
                time.sleep(0.05)
        time.sleep(1)
    except Exception as e:
        print(f"\n🚨 오류: {e}"); time.sleep(10)
