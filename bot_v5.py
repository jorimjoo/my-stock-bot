import time
import pyupbit
import pandas as pd
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
    return "쿠퍼춘봉 스캘핑 봇 V5.3 정상 가동 중! 🐶"

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
    try:
        return pyupbit.get_tickers(fiat="KRW")[:30]
    except: 
        return pyupbit.get_tickers(fiat="KRW")[:30]

def sell_manager(valid_krw_tickers):
    """[최우선 실행] 원화 마켓 유효 코인만 필터링 후 가격 조회 및 즉각 매도"""
    global daily_stats, bot_state
    try:
        balances = upbit.get_balances()
        if not balances: return "조회실패"
        
        holdings = []
        for b in balances:
            if b['currency'] != 'KRW' and float(b['balance']) + float(b['locked']) > 0:
                ticker = f"KRW-{b['currency']}"
                # 🔥 [핵심 방어막] 현재 원화 마켓에 존재하는 코인만 담기!
                if ticker in valid_krw_tickers:
                    holdings.append({
                        'ticker': ticker,
                        'balance': float(b['balance']) + float(b['locked']),
                        'avg_buy_price': float(b['avg_buy_price'])
                    })
                
        if not holdings: return "없음"

        # 걸러진 정상 코인들만 모아서 가격 조회 (Code not found 에러 원천 차단)
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

            # 🚨 0.5% 익절 또는 -1.0% 손절 터치 시 즉각 매도
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
    global bot_state
    now = datetime.datetime.now()

    last_loss = bot_state["last_loss_times"].get(ticker)
    if last_loss and (now - last_loss).total_seconds() / 60 < COOL_DOWN_MINUTES: return
    if upbit.get_balance(ticker) > 0: return

    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=20)
    if df is None or len(df) < 20: return
        
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    curr, prev = df.iloc[-1], df.iloc[-2]
    
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
    try:
        current_ip = requests.get('https://api.ipify.org').text
        print(f"\n======================================")
        print(f"🌐 현재 외부 IP: {current_ip}")
        print(f"======================================\n")
    except: pass

    Thread(target=run_server, daemon=True).start()
    send_message("== 쿠퍼춘봉 스캘핑 봇 V5.3 가동 ==")
    
    target_list = get_target_tickers()
    # 시작할 때 원화 마켓 전체 코인 목록을 미리 가져옵니다.
    valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")

    while True:
        try:
            now = datetime.datetime.now()
            
            if daily_stats["date"] != now.date():
                wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
                send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
                daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
                
                target_list = get_target_tickers()
                # 하루에 한 번씩 원화 마켓 유효 코인 목록 업데이트
                valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")

            for ticker in target_list:
                krw = upbit.get_balance("KRW")
                
                if krw is None:
                    print(f"[{now.strftime('%H:%M:%S')}] ⚠️ 잔고 조회 실패 (일시적 오류)")
                    time.sleep(5)
                    continue

                # 걸러낼 코인 목록을 sell_manager에 전달!
                holdings_str = sell_manager(valid_krw_tickers)
                
                print(f"[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 스캔: {ticker} | 잔고: {krw:,.0f}원")
                
                if krw > 5000: 
                    buy_manager(ticker, krw)
                
                time.sleep(0.3) 

        except Exception as e:
            print(f"\n[!] 봇 작동 중 에러 발생: {e}")
            time.sleep(5)
