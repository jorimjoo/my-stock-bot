import time
import pyupbit
import pandas as pd
import requests
import datetime
import sys
from flask import Flask
from threading import Thread

# ==========================================
# 1. API 키 및 텔레그램 설정 (보안 주의!)
# ==========================================
access = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
secret = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
telegram_chat_id = "8403406400"

# ==========================================
# 2. 핵심 설정 (0.5% 초단타 스캘핑)
# ==========================================
TARGET_ROI = 0.5          # 목표 익절률 (%)
STOP_LOSS_ROI = -1.0      # 기계적 칼손절률 (%)
COOL_DOWN_MINUTES = 30    # 손절 후 해당 종목 재진입 금지 시간 (분)

bot_state = {"last_loss_times": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}

# ==========================================
# 3. 🌐 가짜 웹 서버 (클라우드 구동 대비)
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스캘핑 봇 V5.4 (주도주 추적 모드) 정상 가동 중! 🐶"

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
    """🔥 [V5.4 핵심 개선] 당일 거래대금 상위 30개 종목을 정확히 추출 (초고속 API)"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        querystring = {"markets": ",".join(tickers)}
        
        response = requests.get(url, headers=headers, params=querystring)
        data = response.json()
        
        # 24시간 거래대금(acc_trade_price_24h) 기준으로 내림차순 정렬
        sorted_data = sorted(data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        top_30 = [item['market'] for item in sorted_data][:30]
        
        return top_30
    except Exception as e:
        print(f"종목 갱신 에러: {e}")
        return pyupbit.get_tickers(fiat="KRW")[:30]

def sell_manager(valid_krw_tickers):
    """원화 마켓 유효 코인만 필터링 후 가격 조회 및 즉각 매도"""
    global daily_stats, bot_state
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
    except Exception as e: 
        return f"에러({e})"

def buy_manager(ticker, krw_balance):
    """🔥 [V5.4 핵심 개선] 정배열 상승 추세 & 완화된 수급 조건 탑승"""
    global bot_state
    now = datetime.datetime.now()

    last_loss = bot_state["last_loss_times"].get(ticker)
    if last_loss and (now - last_loss).total_seconds() / 60 < COOL_DOWN_MINUTES: return
    if upbit.get_balance(ticker) > 0: return

    # 분석 기간을 20 캔들에서 30 캔들로 늘려 평균의 정확도를 높임
    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)
    if df is None or len(df) < 30: return
        
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    curr = df.iloc[-1]
    
    # 1. 상승 추세 (정배열): 가격 > 5일선 > 20일선으로 열려있기만 하면 OK
    trend_ok = (curr['close'] > curr['ma5']) and (curr['ma5'] > curr['ma20'])
    
    # 2. 수급 (거래량): 직전 10개 캔들 평균보다 1.5배 터지면 진입 (허들 완화)
    avg_vol = df['volume'].iloc[-11:-1].mean()
    vol_ok = curr['volume'] > (avg_vol * 1.5)
    
    if trend_ok and vol_ok:
        # 거래량이 압도적(2.5배 이상)이면 30% 비중, 아니면 10%
        invest_ratio = 0.3 if curr['volume'] > (avg_vol * 2.5) else 0.1
        buy_amt = krw_balance * invest_ratio
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            send_message(f"🚀 [{ticker}] 주도주 정배열 탑승\n- 매수가: {curr['close']:,.0f}원\n- 비중: {invest_ratio*100}%")

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
    send_message("== 쿠퍼춘봉 스캘핑 봇 V5.4 가동 ==\n(🔥 거래대금 주도주 스캔 & 타점 완화)")
    
    # 시작할 때 거래대금 상위 30개 및 전체 목록 가져오기
    target_list = get_target_tickers()
    valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")

    while True:
        try:
            now = datetime.datetime.now()
            
            # 매일 자정 결산 및 주도주 리스트 대규모 갱신
            if daily_stats["date"] != now.date():
                wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
                send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
                daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
                
                target_list = get_target_tickers()
                valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")

            # 루프를 돌 때마다 거래대금 상위 30개 종목을 집중 스캔
            for ticker in target_list:
                krw = upbit.get_balance("KRW")
                
                if krw is None:
                    time.sleep(5)
                    continue

                holdings_str = sell_manager(valid_krw_tickers)
                
                sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 타겟: {ticker} | 잔고: {krw:,.0f}원    ")
                sys.stdout.flush()
                
                if krw > 5000: 
                    buy_manager(ticker, krw)
                
                time.sleep(0.3) 

            # 30개 순회가 끝나면 실시간 주도주(거래대금 상위)를 다시 뽑아냅니다 (초고속 API 활용)
            target_list = get_target_tickers()

        except Exception as e:
            time.sleep(5)
