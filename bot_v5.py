import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys
import psutil 
from flask import Flask
from threading import Thread

# ==========================================
# 1. API 키 및 텔레그램 설정
# ==========================================
access = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
secret = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
telegram_chat_id = "8403406400"

# ==========================================
# 2. 시스템 및 매매 설정
# ==========================================
TRAILING_ACTIVATE_ROI = 1.5   # 1.5% 달성 시 트레일링(5분봉 5일선 추종) 가동
STOP_LOSS_ROI = -1.5          # 기본 손절률 타이트하게 조정 (-1.5%)
BUY_RATIO = 0.2               # 매수 비중 상향 (보유 원화의 20%)
MAX_TICKERS = 15              # 최대 탐색 종목 수

bot_state = {"loss_counts": {}, "blacklist_times": {}, "max_prices": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}
total_stats = {"trades": 0, "wins": 0, "profit": 0.0, "start_time": datetime.datetime.now()}

# ==========================================
# 3. 🌐 Flask 가짜 웹 서버 (24시간 가동용)
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스윙 봇 V9.2 (20% 비중 & 5MA 트레일링 가동 중!) 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 봇 핵심 유틸리티 함수
# ==========================================
def send_message(msg):
    """텔레그램 메시지 전송"""
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except Exception as e: 
        print(f"텔레그램 전송 오류: {e}")

def get_elite_tickers(count=MAX_TICKERS):
    """거래대금 상위 종목 동적 추출 (BTC 제외)"""
    try:
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        tickers = pyupbit.get_tickers(fiat="KRW")
        
        # URL 길이가 길어질 수 있으므로 나누어 요청 후 합침
        querystring = {"markets": ",".join(tickers)}
        response = requests.get(url, headers=headers, params=querystring)
        
        if response.status_code == 200:
            data = response.json()
            # 거래대금(acc_trade_price_24h) 기준 내림차순 정렬
            sorted_data = sorted(data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
            top_tickers = [item['market'] for item in sorted_data if item['market'] != "KRW-BTC"][:count]
            return top_tickers
        else:
            return pyupbit.get_tickers(fiat="KRW")[:count] # API 오류 시 기본 티커 반환
    except Exception as e:
        print(f"티커 로드 에러: {e}")
        return ["KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE"]

def check_btc_volatility(threshold=2.0):
    """대장주(비트코인) 급변동 휩쏘 필터"""
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=3)
        if df is None or len(df) < 3: return False 
        
        max_price = df['high'].max()
        min_price = df['low'].min()
        volatility = ((max_price - min_price) / min_price) * 100
        
        if volatility >= threshold:
            msg = f"🚨 [방어] 비트코인 급변동 감지 ({volatility:.2f}%). 알트코인 신규 진입을 일시 중단합니다."
            print(msg)
            # send_message(msg) # 너무 잦은 알림 방지를 위해 주석 처리
            return False
        return True
    except Exception:
        return False

# ==========================================
# 5. 매수 / 매도 타점 로직 (pandas_ta 활용)
# ==========================================
def check_buy_condition(ticker):
    """ADX, DMI, MA5, RSI, 거래량 기반 매수 로직"""
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute15", count=100)
        if df is None or len(df) < 20: return False

        # pandas_ta를 활용한 깔끔한 지표 계산
        df.ta.sma(length=5, append=True)
        df.ta.rsi(length=14, append=True)
        
        # ADX 및 DMI 계산 (ADX_14, DMP_14, DMN_14 컬럼 생성)
        adx_df = df.ta.adx(length=14)
        if adx_df is not None:
            df = pd.concat([df, adx_df], axis=1)
        else:
            return False

        current_price = df['close'].iloc[-1]
        ma5 = df['SMA_5'].iloc[-1]
        rsi = df['RSI_14'].iloc[-1]
        adx = df['ADX_14'].iloc[-1]
        plus_di = df['DMP_14'].iloc[-1]
        minus_di = df['DMN_14'].iloc[-1]
        
        current_volume = df['volume'].iloc[-1]
        prev_volume = df['volume'].iloc[-2]

        # 1. 상승 추세 확인 (5일선 위, 강한 트렌드, 매수세 우위)
        is_trend_up = (current_price > ma5) and (adx > 25) and (plus_di > minus_di)
        # 2. RSI 과열 방지
        is_not_overbought = (rsi < 70)
        # 3. 거래량 터진 모멘텀 확인
        is_volume_spike = (current_volume > prev_volume * 1.5)

        if is_trend_up and is_not_overbought and is_volume_spike:
            return True
        return False
    except Exception as e:
        print(f"[{ticker}] 매수 조건 확인 에러: {e}")
        return False

def check_sell_condition(ticker, buy_price, current_price):
    """다이나믹 트레일링 스탑 및 손절매 로직"""
    try:
        profit_rate = ((current_price - buy_price) / buy_price) * 100

        # 1. 트레일링 스탑 가동 (수익률이 1.5% 이상일 때)
        if profit_rate >= TRAILING_ACTIVATE_ROI:
            df_5m = pyupbit.get_ohlcv(ticker, interval="minute5", count=10)
            if df_5m is not None:
                df_5m.ta.sma(length=5, append=True)
                ma5_5m = df_5m['SMA_5'].iloc[-1]
                
                # 현재가가 5분봉의 5일선 아래로 꺾이면 익절 실행
                if current_price < ma5_5m:
                    return True, f"수익 극대화 트레일링 익절 (+{profit_rate:.2f}%)"
            
        # 2. 기본 손절매 (-1.5%)
        elif profit_rate <= STOP_LOSS_ROI:
            return True, f"손절매 방어 ({profit_rate:.2f}%)"
            
        return False, ""
    except Exception as e:
        print(f"[{ticker}] 매도 조건 확인 에러: {e}")
        return False, ""

# ==========================================
# 6. 메인 실행 루프
# ==========================================
def main_loop():
    send_message("🚀 쿠퍼춘봉 시스템 V9.2 정상 가동 시작!\n- 비중: 20%\n- 1.5% 트레일링 스탑 적용")
    print("=======================================")
    print("봇 가동 시작...")
    print("=======================================")

    while True:
        try:
            target_tickers = get_elite_tickers(count=MAX_TICKERS)
            is_btc_stable = check_btc_volatility(threshold=2.0)
            
            if is_btc_stable:
                for ticker in target_tickers:
                    balance = upbit.get_balance(ticker)
                    current_price = pyupbit.get_current_price(ticker)
                    
                    # [매수 포지션 진입]
                    if balance == 0 or balance is None:
                        if check_buy_condition(ticker):
                            krw = upbit.get_balance("KRW")
                            buy_amount = krw * BUY_RATIO # 20% 비중
                            
                            if buy_amount > 5000:
                                upbit.buy_market_order(ticker, buy_amount)
                                msg = f"✅ [매수] {ticker}\n- 매수가: {current_price:,.0f}원\n- 투입금액: {buy_amount:,.0f}원"
                                print(msg)
                                send_message(msg)
                                time.sleep(0.5)
                                
                    # [매도 포지션 관리]
                    else:
                        avg_buy_price = upbit.get_avg_buy_price(ticker)
                        if avg_buy_price > 0 and current_price is not None:
                            should_sell, reason = check_sell_condition(ticker, avg_buy_price, current_price)
                            
                            if should_sell:
                                upbit.sell_market_order(ticker, balance)
                                profit = ((current_price - avg_buy_price) / avg_buy_price) * 100
                                msg = f"💰 [매도] {ticker}\n- 매도사유: {reason}\n- 수익률: {profit:.2f}%"
                                print(msg)
                                send_message(msg)
                                time.sleep(0.5)
            
            time.sleep(1) # API 과부하 방지
            
        except Exception as e:
            msg = f"⚠️ [시스템 에러] {e}"
            print(msg)
            # send_message(msg)
            time.sleep(10)

if __name__ == "__main__":
    # Flask 서버 스레드 실행
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # 봇 메인 루프 실행
    main_loop()
