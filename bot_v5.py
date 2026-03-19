import time
import pyupbit
import pandas as pd
import pandas_ta as ta  # 🔥 눌림목 포착을 위해 RSI 지표 추가
import requests
import datetime
import sys
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
# 2. 핵심 설정 (유튜브 고수들의 손익비 적용)
# ==========================================
TARGET_ROI = 1.0          # 목표 익절률 (+) 길게!
STOP_LOSS_ROI = -0.7      # 기계적 칼손절률 (-) 짧게!
BLACKLIST_HOURS = 24      # 2번 손절 시 블랙리스트 유배 시간

# 상태 관리 (2-아웃 제도입)
bot_state = {"loss_counts": {}, "blacklist_times": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}

# ==========================================
# 3. 🌐 가짜 웹 서버 (클라우드 구동 대비)
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스캘핑 봇 V5.6 (눌림목 & 블랙리스트 모드) 가동 중! 🐶"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 봇 핵심 로직
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """당일 거래대금 상위 30개 종목 추출"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        querystring = {"markets": ",".join(tickers)}
        
        response = requests.get(url, headers=headers, params=querystring)
        data = response.json()
        
        sorted_data = sorted(data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        return [item['market'] for item in sorted_data][:30]
    except Exception as e:
        return pyupbit.get_tickers(fiat="KRW")[:30]

def sell_manager(valid_krw_tickers):
    """손익비가 개선된 즉각 매도 및 투 스트라이크 아웃 기록"""
    global daily_stats, bot_state
    now = datetime.datetime.now()
    try:
        balances = upbit.get_balances()
        if not balances: return "조회실패"
        
        holdings = []
        for b in balances:
            if b['currency'] != 'KRW' and float(b['balance']) + float(b['locked']) > 0:
                ticker = f"KRW-{b['currency']}"
                if ticker in valid_krw_tickers:
                    holdings.append({
                        'ticker': ticker,
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

            # 🚨 익절(1.0%) 또는 손절(-0.7%) 도달 시 즉각 시장가 매도
            if roi >= TARGET_ROI or roi <= STOP_LOSS_ROI:
                if balance * curr_price > 5000:
                    upbit.sell_market_order(ticker, balance)
                    reason = f"✅ 목표 익절 (+{roi:.2f}%)" if roi > 0 else f"❌ 칼손절 ({roi:.2f}%)"
                    
                    daily_stats["trades"] += 1
                    if roi > 0: daily_stats["wins"] += 1
                    daily_stats["profit"] += (curr_price - buy_price) * balance
                    
                    # 🔥 [투 스트라이크 블랙리스트 처리]
                    if roi < 0:
                        bot_state["loss_counts"][ticker] = bot_state["loss_counts"].get(ticker, 0) + 1
                        if bot_state["loss_counts"][ticker] >= 2:
                            bot_state["blacklist_times"][ticker] = now
                            send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n- 매도가: {curr_price:,.0f}원\n🚨 2회 손절로 24시간 매수 금지!")
                        else:
                            send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n- 매도가: {curr_price:,.0f}원\n⚠️ 1회 경고 누적")
                    else:
                        # 수익을 내면 경고 초기화
                        bot_state["loss_counts"][ticker] = 0
                        send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n- 매도가: {curr_price:,.0f}원")
                    continue 

            display_info.append(f"[{ticker} {roi:+.2f}%]")
            
        return " ".join(display_info) if display_info else "없음"
    except Exception as e: 
        return f"에러({e})"

def buy_manager(ticker, krw_balance):
    """🔥 [V5.6 핵심] 불기둥 추격매수 금지 -> 안전한 상승장 '눌림목' 포착"""
    global bot_state
    now = datetime.datetime.now()

    # 1. 블랙리스트(24시간) 확인
    blacklist_time = bot_state["blacklist_times"].get(ticker)
    if blacklist_time and (now - blacklist_time).total_seconds() / 3600 < BLACKLIST_HOURS:
        return
    if upbit.get_balance(ticker) > 0: return

    # 2. 지표 계산 (안정적인 흐름을 보기 위해 60개 캔들 호출)
    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=60)
    if df is None or len(df) < 60: return
        
    df['ma20'] = ta.sma(df['close'], length=20)
    df['ma60'] = ta.sma(df['close'], length=60)
    df['rsi'] = ta.rsi(df['close'], length=14)
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 💡 [진입 조건 1] 장기 상승 추세: 20일선이 60일선 위에 있어야 함 (기본 뼈대가 튼튼한 코인)
    trend_ok = curr['ma20'] > curr['ma60']
    
    # 💡 [진입 조건 2] 눌림목(과매도): RSI가 40 이하로 떨어졌을 때 (단기적으로 가격이 훅 빠졌을 때)
    rsi_ok = curr['rsi'] < 40
    
    # 💡 [진입 조건 3] 반등 시작: 직전 캔들보다 현재 캔들의 가격이 살짝 오르며 양봉 조짐을 보일 때
    rebound_ok = curr['close'] > prev['close']
    
    if trend_ok and rsi_ok and rebound_ok:
        buy_amt = krw_balance * 0.20 # 안정성을 위해 비중은 20%로 고정
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            send_message(f"🚀 [{ticker}] 상승장 눌림목(RSI {curr['rsi']:.1f}) 포착!\n- 매수가: {curr['close']:,.0f}원")

# ==========================================
# 5. 메인 실행부
# ==========================================
if __name__ == "__main__":
    try:
        current_ip = requests.get('https://api.ipify.org').text
        print(f"\n======================================")
        print(f"🌐 현재 외부 IP: {current_ip}")
        print(f"======================================\n")
    except: pass

    Thread(target=run_server, daemon=True).start()
    send_message("== 쿠퍼춘봉 스캘핑 봇 V5.6 가동 ==\n(🔥 추격매수 금지 & 눌림목 헌터 & 2-아웃 제도)")
    
    target_list = get_target_tickers()
    valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")

    while True:
        try:
            now = datetime.datetime.now()
            
            if daily_stats["date"] != now.date():
                wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
                send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
                daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
                # 매일 자정에 블랙리스트 대사면(초기화)
                bot_state["loss_counts"] = {}
                bot_state["blacklist_times"] = {}
                
                target_list = get_target_tickers()
                valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")

            for ticker in target_list:
                krw = upbit.get_balance("KRW")
                
                if krw is None:
                    time.sleep(5)
                    continue

                holdings_str = sell_manager(valid_krw_tickers)
                
                sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 스캔: {ticker} | 잔고: {krw:,.0f}원    ")
                sys.stdout.flush()
                
                if krw > 5000: 
                    buy_manager(ticker, krw)
                
                time.sleep(0.3) 

            target_list = get_target_tickers()

        except Exception as e:
            time.sleep(5)
