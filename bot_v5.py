import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys
from flask import Flask
from threading import Thread

# ==========================================
# 1. API 키 및 텔레그램 설정
# 🚨 주의: 반드시 '새로 발급받은' 업비트 키를 넣으세요!
# ==========================================
access = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
secret = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"  # 봇파더에서 새로 발급(revoke)받은 토큰
telegram_chat_id = "8403406400" # 챗 아이디는 그대로 사용

# ==========================================
# 2. 핵심 설정 (초단타 스캘핑)
# ==========================================
TARGET_ROI = 0.5          # 목표 익절률 (%)
STOP_LOSS_ROI = -1.0      # 기계적 칼손절률 (%)
COOL_DOWN_MINUTES = 30    # 손절 후 해당 종목 재진입 금지 시간 (분)

# 통계 및 상태 관리
bot_state = {"last_loss_times": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}

# ==========================================
# 3. 🌐 가짜 웹 서버 방어막 (Render Timed out 방지)
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스캘핑 봇 V4.1이 정상적으로 24시간 감시 중입니다! 🐶"

def run_server():
    # Render가 요구하는 포트(기본 10000)를 열어서 서버가 꺼지지 않게 속입니다.
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 봇 핵심 로직 (매도 / 매수 / 알림)
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """당일 거래대금 상위 30개 추출"""
    try:
        return pyupbit.get_tickers(fiat="KRW")[:30]
    except: 
        return pyupbit.get_tickers(fiat="KRW")[:30]

def sell_manager():
    """[최우선 실행] 내 잔고 확인 및 0.5% 도달 시 즉각 전량 매도"""
    global daily_stats, bot_state
    try:
        balances = upbit.get_balances()
        if not balances: return "조회실패"
        
        holdings = []
        for b in balances:
            if b['currency'] != 'KRW' and float(b['balance']) + float(b['locked']) > 0:
                holdings.append({
                    'ticker': f"KRW-{b['currency']}",
                    'balance': float(b['balance']) + float(b['locked']),
                    'avg_buy_price': float(b['avg_buy_price'])
                })
                
        if not holdings: return "없음"

        tickers_to_check = [h['ticker'] for h in holdings]
        current_prices = pyupbit.get_current_price(tickers_to_check)
        if not current_prices: return "가격로딩중"

        display_info = []
        for hold in holdings:
            ticker = hold['ticker']
            buy_price = hold['avg_buy_price']
            balance = hold['balance']
            
            if buy_price == 0: continue 
            
            curr_price = current_prices[ticker] if isinstance(current_prices, dict) else current_prices
            roi = ((curr_price - buy_price) / buy_price) * 100

            # 🚨 0.5% 익절 또는 -1.0% 손절 터치 시 즉각 전량 매도
            if roi >= TARGET_ROI or roi <= STOP_LOSS_ROI:
                if balance * curr_price > 5000:
                    upbit.sell_market_order(ticker, balance)
                    reason = f"✅ 목표 익절 (+{roi:.2f}%)" if roi > 0 else f"❌ 칼손절 ({roi:.2f}%)"
                    
                    daily_stats["trades"] += 1
                    if roi > 0: daily_stats["wins"] += 1
                    daily_stats["profit"] += (curr_price - buy_price) * balance
                    
                    send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n- 매도가: {curr_price:,.0f}원")
                    
                    if roi < 0: bot_state["last_loss_times"][ticker] = datetime.datetime.now()
                    else: bot_state["last_loss_times"].pop(ticker, None)
                    continue 

            display_info.append(f"[{ticker} {roi:+.2f}%]")
            
        return " ".join(display_info) if display_info else "없음"
    except: return "에러"

def buy_manager(ticker, krw_balance):
    """5분봉 스캘핑 타점 스캔"""
    global bot_state
    now = datetime.datetime.now()

    # 손절 쿨다운 확인
    last_loss = bot_state["last_loss_times"].get(ticker)
    if last_loss and (now - last_loss).total_seconds() / 60 < COOL_DOWN_MINUTES: return
    # 이미 보유 중이면 추가 매수 안 함
    if upbit.get_balance(ticker) > 0: return

    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=20)
    if df is None or len(df) < 20: return
        
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    curr, prev = df.iloc[-1], df.iloc[-2]
    
    # 5분봉 정배열 + 거래량 돌파 로직
    trend_ok = curr['close'] > curr['ma20']
    cross_ok = (curr['ma5'] > curr['ma20']) and (prev['ma5'] <= prev['ma20'])
    avg_vol = df['volume'].iloc[-6:-1].mean()
    vol_ok = curr['volume'] > (avg_vol * 2.0)
    
    if trend_ok and cross_ok and vol_ok:
        invest_ratio = 0.3 if curr['volume'] > (avg_vol * 3.0) else 0.1
        buy_amt = krw_balance * invest_ratio
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            send_message(f"🚀 [{ticker}] 5분봉 스캘핑 진입\n- 매수가: {curr['close']:,.0f}원\n- 비중: {invest_ratio*100}%")

# ==========================================
# 5. 메인 실행부
# ==========================================
if __name__ == "__main__":
    # 가짜 웹 서버를 백그라운드(스레드)에서 실행
    Thread(target=run_server, daemon=True).start()
    
    send_message("== 쿠퍼춘봉 스캘핑 봇 V4.1 가동 ==\n(Render Timed out 방지 & 초단타 모드)")
    target_list = get_target_tickers()

    while True:
        try:
            now = datetime.datetime.now()
            
            # 자정 통계 전송
            if daily_stats["date"] != now.date():
                wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
                send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
                daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
                target_list = get_target_tickers()

            for ticker in target_list:
                krw = upbit.get_balance("KRW")
                
                # 매도 최우선 검사 & 화면 표시
                holdings_str = sell_manager()
                
                sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 스캔: {ticker} | 잔고: {krw:,.0f}원   ")
                sys.stdout.flush()
                
                # 매수 검사
                if krw > 5000: 
                    buy_manager(ticker, krw)
                
                time.sleep(0.3) # API 호출 제한(Rate Limit) 방지용 필수 휴식

        except Exception as e:
            time.sleep(5)
