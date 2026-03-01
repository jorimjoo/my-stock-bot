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
# 1. Render/Cloud 환경용 웹 서버 설정 (Port 10000)
# ==================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "UPBIT High-WinRate Bot is Running Alive!"

def run_flask():
    # Render 등 클라우드 서비스는 특정 포트가 열려있어야 서비스를 유지함
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# 웹 서버 백그라운드 실행
t = threading.Thread(target=run_flask)
t.daemon = True
t.start()

# ==================================================
# 2. 사용자 정보 입력 (키 유출 주의)
# ==================================================
ACCESS_KEY = "voMLtW0LzLkMVY0gwbRQmvASYoPC1eOExxAm8G64"
SECRET_KEY = "1GzX0hFxrc8YMhlPyhx8wnYNqNJlQ5Rzc2Xv2b2e"
TOKEN = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"  # 텔레그램 토큰
CHAT_ID = "8403406400"                                  # 텔레그램 채팅 ID

try:
    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
except Exception as e:
    print(f"API 연결 초기 실패: {e}")

# ==================================================
# 3. 전략 설정값 (승률 최적화 튜닝)
# ==================================================
MAX_SLOTS = 15          # 최대 보유 종목 수
INVEST_FIXED = 10000    # 1회 매수 금액 (10,000원)
MIN_ORDER_AMOUNT = 5000 # 업비트 최소 주문 금액
TARGET_PROFIT = 2.2     # 익절 목표 (%) - 회전율을 위해 소폭 하향
STOP_LOSS = -1.5        # 손절 제한 (%) - 리스크 관리를 위해 타이트하게 설정
K_VALUE = 0.5           # 변동성 돌파 계수
DISPARITY_LIMIT = 3.5   # 이격도 제한 (%) - 20일선 대비 너무 높으면 매수 금지
VOL_RATIO = 2.0         # 거래량 돌파 확인 - 최근 평균 대비 2배 이상 수급 확인
TICKERS_COUNT = 30      # 스캔할 상위 거래 대금 종목 수
BB_WINDOW = 20         
BB_STD = 2.0           
HEARTBEAT_HOURS = 6    

# ==================================================
# 4. 보조 지표 및 데이터 함수
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
    """현재 5분봉 거래량이 이전 10봉 평균보다 높은지 확인 (수급 필터)"""
    df = get_safe_ohlcv(ticker, "minute5", 11)
    if df is None or len(df) < 11: return False
    avg_vol = df['volume'].iloc[:-1].mean()
    curr_vol = df['volume'].iloc[-1]
    return curr_vol > (avg_vol * VOL_RATIO)

def get_disparity(ticker):
    """현재가가 20일 이동평균선과 얼마나 떨어져 있는지 확인 (고점 판별)"""
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
    upper = df['ma20'] + (df['std'] * BB_STD)
    lower = df['ma20'] - (df['std'] * BB_STD)
    return upper.iloc[-1], df['ma20'].iloc[-1], lower.iloc[-1]

def check_bearish_engulfing(ticker):
    df = get_safe_ohlcv(ticker, interval="minute1", count=2)
    if df is None or len(df) < 2: return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    if prev['close'] > prev['open'] and curr['close'] < curr['open']:
        if curr['close'] < prev['open']: return True
    return False

# ==================================================
# 5. 메인 루프 (무한 실행)
# ==================================================
print(f"▶ 승률 최적화 전략 가동 시작")
send_telegram("🛡️ [시스템 가동] 승률 최적화 모드 시작")
last_heartbeat = datetime.datetime.now()

while True:
    try:
        now = datetime.datetime.now()
        # 생존 신고
        if now - last_heartbeat > datetime.timedelta(hours=HEARTBEAT_HOURS):
            send_telegram(f"💓 [정상 가동] {now.strftime('%H:%M')} 현재 시장 감시 중")
            last_heartbeat = now

        # 1. 종목 리스트 및 잔고 확보
        all_krw_tickers = pyupbit.get_tickers(fiat="KRW")
        balances = upbit.get_balances()
        if balances is None: 
            time.sleep(2); continue

        portfolio = []
        for b in balances:
            ticker = f"KRW-{b['currency']}"
            if b['currency'] != 'KRW' and float(b['balance']) > 0 and ticker in all_krw_tickers:
                portfolio.append({'ticker': ticker, 'balance': float(b['balance']), 'avg_p': float(b['avg_buy_price'])})

        print(f"\r[{now.strftime('%H:%M:%S')}] 감시 중 (슬롯: {len(portfolio)}/15)", end="")

        # 2. 매도 로직 (익절/손절/캔들회피)
        for item in portfolio:
            t_code, bal, avg_b = item['ticker'], item['balance'], item['avg_p']
            curr_p = pyupbit.get_current_price(t_code)
            if curr_p is None: continue
            
            profit_rate = ((curr_p - avg_b) / avg_b) * 100
            reason = ""
            
            if profit_rate >= TARGET_PROFIT: reason = "🎯 목표 익절"
            elif profit_rate <= STOP_LOSS: reason = "⚠️ 손절선 이탈"
            elif check_bearish_engulfing(t_code) and profit_rate < -0.3: reason = "📉 하락장악형 조기회피"

            if reason and (bal * curr_p >= MIN_ORDER_AMOUNT):
                upbit.sell_market_order(t_code, bal)
                send_telegram(f"💰 [매도 완료]\n종목: {t_code}\n수익률: {profit_rate:.2f}%\n사유: {reason}")
                time.sleep(0.5)

        # 3. 매수 탐색 (슬롯 여유 있을 때만)
        if len(portfolio) < MAX_SLOTS:
            prices = pyupbit.get_current_price(all_krw_tickers, verbose=True)
            df_mkt = pd.DataFrame(prices)
            df_mkt['rate'] = df_mkt['signed_change_rate'] * 100
            target_list = df_mkt.sort_values(by='rate', ascending=False).head(TICKERS_COUNT)
            
            for _, row in target_list.iterrows():
                ticker = row['market']
                change_rate = row['rate']
                if any(p['ticker'] == ticker for p in portfolio): continue
                
                # 기술적 지표 수집
                curr_p = pyupbit.get_current_price(ticker)
                rsi = get_rsi(ticker)
                ma7 = get_ma(ticker, 7)
                disparity = get_disparity(ticker)      # 이격도 필터 (고점 매수 방지)
                is_vol_burst = get_volume_status(ticker) # 수급 확증 필터
                _, _, lower_bb = get_bb(ticker)
                
                if not curr_p or not ma7 or not lower_bb: continue

                # 돌파 기준 가격 계산
                df_d = get_safe_ohlcv(ticker, "day", 2)
                target_p = df_d.iloc[0]['close'] + (df_d.iloc[0]['high'] - df_d.iloc[0]['low']) * K_VALUE

                # --- 매수 전략 필터링 ---
                
                # [전략 A] 수급 동반 모멘텀: 거래량이 터지면서 상승세인 경우 (고점 이격도 3.5% 이내만)
                cond_momentum = (change_rate > 4.0) and is_vol_burst and (50 < rsi < 72) and (disparity < 100 + DISPARITY_LIMIT)
                
                # [전략 B] 변동성 돌파: 확실한 추세 돌파 (RSI 과열 전 단계)
                cond_break = (curr_p > target_p) and (curr_p > ma7) and (45 < rsi < 70) and (disparity < 100 + DISPARITY_LIMIT)
                
                # [전략 C] 안전한 눌림목: 깊은 조정 후 반등 자리
                cond_pullback = (curr_p <= lower_bb * 1.02) and (rsi < 35)

                if cond_momentum or cond_break or cond_pullback:
                    krw_bal = float(upbit.get_balance("KRW"))
                    if krw_bal >= INVEST_FIXED:
                        upbit.buy_market_order(ticker, INVEST_FIXED)
                        s_name = "모멘텀" if cond_momentum else ("돌파" if cond_break else "눌림목")
                        send_telegram(f"🛡️ [안전 매수]\n종목: {ticker}\n전략: {s_name}\n이격도: {disparity-100:.1f}%\nRSI: {rsi:.1f}")
                        break
                time.sleep(0.05)
        
        time.sleep(1)

    except Exception as e:
        print(f"\n🚨 시스템 오류: {e}")
        time.sleep(10)
