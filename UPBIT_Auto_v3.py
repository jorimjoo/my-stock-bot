from flask import Flask
import threading
import time
import datetime
import pyupbit
import requests
import pandas as pd
import os
import traceback
import numpy as np

# ==================================================
# 1. 클라우드 및 서버 설정 (Render 유지용)
# ==================================================
app = Flask(__name__)

@app.route('/')
def home(): 
    # 웹 브라우저 접속 시 출력될 문구
    return "UPBIT Testing Bot (No Dust Sell) is Running!"

def run_flask():
    # Render는 PORT 환경 변수를 사용합니다.
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# 별도 스레드에서 Flask 서버 실행
t = threading.Thread(target=run_flask)
t.daemon = True
t.start()

# ==================================================
# 2. 사용자 정보 및 환경 설정
# ==================================================
# ⚠️ 주의: 공개된 장소에 키를 노출하지 마세요!
ACCESS_KEY = "voMLtW0LzLkMVY0gwbRQmvASYoPC1eOExxAm8G64"
SECRET_KEY = "1GzX0hFxrc8YMhlPyhx8wnYNqNJlQ5Rzc2Xv2b2e"
TOKEN = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# 전략 파라미터
MAX_SLOTS = 15             # 최대 보유 종목 수
BASE_INVEST = 20000        # 종목당 투자 금액 (원)
MIN_ORDER_AMOUNT = 5000    # 업비트 최소 주문 금액 (5,000원)
HEARTBEAT_HOURS = 6        # 생존 신고 주기

# 상태 저장 변수 (분할 매수/매도 단계 추적)
trade_state = {} 

# ==================================================
# 3. 유틸리티 및 기술 지표 함수
# ==================================================
def send_telegram(message):
    """텔레그램 메시지 전송"""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        params = {'chat_id': CHAT_ID, 'text': message}
        requests.post(url, data=params, timeout=10)
    except: 
        pass

def get_indicators(ticker, interval, count=200):
    """기술 지표 계산 (EMA, RSI, ATR, Volume MA)"""
    df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
    if df is None or df.empty: return None
    
    # EMA (추세 필터)
    df['ema60'] = df['close'].ewm(span=60, adjust=False).mean()
    df['ema120'] = df['close'].ewm(span=120, adjust=False).mean()
    
    # RSI (눌림목 판단)
    delta = df['close'].diff()
    up, down = delta.copy(), delta.copy()
    up[up < 0] = 0; down[down > 0] = 0
    gain = up.ewm(com=13, min_periods=14).mean()
    loss = down.abs().ewm(com=13, min_periods=14).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / loss)))
    
    # ATR (변동성 손절 기준)
    high_low = df['high'] - df['low']
    high_pc = (df['high'] - df['close'].shift()).abs()
    low_pc = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=14).mean()
    
    # 거래량 이평선
    df['volume_ma20'] = df['volume'].rolling(window=20).mean()
    return df

# ==================================================
# 4. 봇 시작 및 IP 확인 로그 (요청하신 부분)
# ==================================================
try:
    # 외부 아이피 확인
    current_ip = requests.get("https://api.ipify.org").text
    log_msg = f"✅ --- Currently running on IP: {current_ip} ---"
    print(f"\n{log_msg}")
    send_telegram(f"🤖 봇 시스템 시작\nIP: {current_ip}")
except Exception as e:
    print(f"❌ IP 확인 실패: {e}")

print("▶ [테스트 모드] 소액 자산 보호 시스템 가동")

# ==================================================
# 5. 메인 매매 루프
# ==================================================
while True:
    try:
        now = datetime.datetime.now()
        
        # 1. 포트폴리오 현황 파악
        balances = upbit.get_balances()
        portfolio = []
        all_tickers = pyupbit.get_tickers(fiat="KRW")
        
        for b in balances:
            ticker = f"KRW-{b['currency']}"
            if b['currency'] != 'KRW' and float(b['balance']) > 0 and ticker in all_tickers:
                portfolio.append({
                    'ticker': ticker, 
                    'balance': float(b['balance']), 
                    'avg_p': float(b['avg_buy_price'])
                })

        print(f"\r[{now.strftime('%H:%M:%S')}] 감시 중 (보유: {len(portfolio)}/{MAX_SLOTS})", end="")

        # 2. 매도 감시 루프
        for item in portfolio:
            ticker, bal, avg_b = item['ticker'], item['balance'], item['avg_p']
            curr_p = pyupbit.get_current_price(ticker)
            if curr_p is None: continue
            
            # [자투리 매도 방지] 보유 총 금액이 5,000원 미만이면 매도 시도 안 함
            total_value = bal * curr_p
            if total_value <= MIN_ORDER_AMOUNT:
                continue 

            df_m5 = get_indicators(ticker, "minute5", 20)
            if df_m5 is None: continue
            
            profit_rate = ((curr_p - avg_b) / avg_b) * 100
            atr = df_m5['atr'].iloc[-1]
            stop_price = avg_b - (atr * 1.5) # ATR 기반 가변 손절선
            
            # [매도 1] ATR 손절
            if curr_p <= stop_price:
                upbit.sell_market_order(ticker, bal)
                send_telegram(f"📉 [손절] {ticker}\n수익률: {profit_rate:.2f}%\n사유: ATR 이탈")
                if ticker in trade_state: del trade_state[ticker]
                continue

            # [매도 2] 3단계 분할 익절 로직
            if ticker not in trade_state: trade_state[ticker] = {'stage': 0}
            
            # 1단계: 2.0% 수익 시 30% 익절
            if profit_rate >= 2.0 and trade_state[ticker]['stage'] == 0:
                sell_amt = bal * 0.3
                if (sell_amt * curr_p) >= MIN_ORDER_AMOUNT:
                    upbit.sell_market_order(ticker, sell_amt)
                    trade_state[ticker]['stage'] = 1
                    send_telegram(f"💰 [익절 1단계] {ticker} 30% 매도")
                else:
                    trade_state[ticker]['stage'] = 1

            # 2단계: 4.0% 수익 시 추가 30% 익절
            elif profit_rate >= 4.0 and trade_state[ticker]['stage'] == 1:
                sell_amt = bal * 0.43 # 남은 수량의 약 절반
                if (sell_amt * curr_p) >= MIN_ORDER_AMOUNT:
                    upbit.sell_market_order(ticker, sell_amt)
                    trade_state[ticker]['stage'] = 2
                    send_telegram(f"💰 [익절 2단계] {ticker} 30% 매도")
                else:
                    trade_state[ticker]['stage'] = 2

            # 3단계: 6.0% 수익 시 전량 익절
            elif profit_rate >= 6.0:
                upbit.sell_market_order(ticker, bal)
                send_telegram(f"🚀 [익절 완료] {ticker} 전량 매도 완료")
                if ticker in trade_state: del trade_state[ticker]

        # 3. 매수 탐색 루프 (보유 슬롯이 남았을 때만)
        if len(portfolio) < MAX_SLOTS:
            # 전일 대비 등락률 상위 25개 종목 스캔
            prices = pyupbit.get_current_price(all_tickers, verbose=True)
            target_list = pd.DataFrame(prices).sort_values(by='signed_change_rate', ascending=False).head(25)
            
            for _, row in target_list.iterrows():
                ticker = row['market']
                # 이미 보유 중이면 패스
                if any(p['ticker'] == ticker for p in portfolio): continue
                
                # 1시간 봉 기준 추세 확인 (EMA 60 > 120)
                df_h1 = get_indicators(ticker, "minute60", 150)
                if df_h1 is None: continue
                h1_trend_up = df_h1['ema60'].iloc[-1] > df_h1['ema120'].iloc[-1]
                
                if h1_trend_up:
                    # 5분 봉 기준 눌림목/거래량 확인
                    df_m5 = get_indicators(ticker, "minute5", 30)
                    if df_m5 is None: continue
                    
                    rsi = df_m5['rsi'].iloc[-1]
                    vol_spike = df_m5['volume'].iloc[-1] > (df_m5['volume_ma20'].iloc[-1] * 1.5)
                    pullback = rsi < 45 # 과매수 이후 살짝 식은 지점
                    
                    if pullback and vol_spike:
                        krw_balance = float(upbit.get_balance("KRW"))
                        if krw_balance >= BASE_INVEST:
                            upbit.buy_market_order(ticker, BASE_INVEST)
                            send_telegram(f"✅ [매수] {ticker}\n전략: 1H추세 눌림목 돌파")
                            break # 한 루프에 하나씩만 매수
                time.sleep(0.1) # API 과부하 방지

        time.sleep(1) # 루프 간격

    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"\n🚨 오류 발생:\n{error_msg}")
        send_telegram(f"🚨 봇 오류 발생:\n{str(e)[:100]}")
        time.sleep(10)
