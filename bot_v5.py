import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
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
TRAILING_ACTIVATE_ROI = 1.5   # 1.5% 달성 시 트레일링 가동
STOP_LOSS_ROI = -1.5          # 손절률 (-1.5%)
BUY_RATIO = 0.2               # 매수 비중 (20%)
MAX_TICKERS = 15              # 최대 탐색 종목 수
REPORT_INTERVAL = 3600        # 브리핑 간격 (1시간마다 보고)

# 통계 관리 변수
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}
total_stats = {"trades": 0, "wins": 0, "profit": 0.0, "start_time": datetime.datetime.now()}
last_report_time = 0 

# ==========================================
# 3. 🌐 Flask 가짜 웹 서버
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스윙 봇 V9.2 가동 중! 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 유틸리티 함수 (알림 및 상태 점검)
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_server_status():
    """서버 자원 상태 확인"""
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    return cpu, ram

def send_system_briefing():
    """스크린샷 스타일의 시스템 브리핑 전송"""
    global daily_stats, total_stats
    
    now = datetime.datetime.now()
    uptime = now - total_stats["start_time"]
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    cpu, ram = get_server_status()
    krw_balance = upbit.get_balance("KRW")
    
    # 승률 계산
    total_win_rate = (total_stats["wins"] / total_stats["trades"] * 100) if total_stats["trades"] > 0 else 0
    daily_win_rate = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
    
    msg = f"🤖 [쿠퍼춘봉 시스템 브리핑]\n"
    msg += f"⌚ 봇 가동: {total_stats['start_time'].strftime('%m/%d %H:%M')}\n"
    msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
    msg += f"💻 서버 상태: CPU {cpu}% / RAM {ram}%\n"
    msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
    
    msg += f"📈 [전체 누적 통계]\n"
    msg += f"- 누적 수익: {total_stats['profit']:,.0f}원\n"
    msg += f"- 누적 거래: {total_stats['trades']}회 (승률 {total_win_rate:.1f}%)\n\n"
    
    msg += f"📅 [오늘 하루 통계]\n"
    msg += f"- 오늘 수익: {daily_stats['profit']:,.0f}원\n"
    msg += f"- 오늘 거래: {daily_stats['trades']}회 (승률 {daily_win_rate:.1f}%)"
    
    send_message(msg)

def update_statistics(profit_amount, is_win):
    """거래 종료 시 통계 업데이트"""
    global daily_stats, total_stats
    
    # 날짜가 바뀌었으면 오늘 통계 초기화
    if daily_stats["date"] != datetime.datetime.now().date():
        daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}
    
    # 통계 합산
    for stats in [daily_stats, total_stats]:
        stats["trades"] += 1
        stats["profit"] += profit_amount
        if is_win: stats["wins"] += 1

# ==========================================
# 5. 핵심 매매 로직 (이전과 동일)
# ==========================================
def get_elite_tickers(count=MAX_TICKERS):
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        data = pyupbit.get_current_price(tickers, verbose=True)
        # 거래대금 상위 정렬 (BTC 제외)
        sorted_data = sorted(data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        top_tickers = [item['market'] for item in sorted_data if item['market'] != "KRW-BTC"][:count]
        return top_tickers
    except: return ["KRW-ETH", "KRW-SOL", "KRW-XRP"]

def check_btc_volatility():
    df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=3)
    if df is None: return False
    vol = ((df['high'].max() - df['low'].min()) / df['low'].min()) * 100
    return vol < 2.0

def check_buy_condition(ticker):
    df = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
    if df is None or len(df) < 20: return False
    df.ta.sma(length=5, append=True); df.ta.rsi(length=14, append=True)
    adx = df.ta.adx(length=14)
    if adx is None: return False
    df = pd.concat([df, adx], axis=1)
    
    c, ma, rsi = df['close'].iloc[-1], df['SMA_5'].iloc[-1], df['RSI_14'].iloc[-1]
    adx_v, p_di, m_di = df['ADX_14'].iloc[-1], df['DMP_14'].iloc[-1], df['DMN_14'].iloc[-1]
    v_spike = df['volume'].iloc[-1] > df['volume'].iloc[-2] * 1.5
    
    return (c > ma) and (adx_v > 25) and (p_di > m_di) and (rsi < 70) and v_spike

def check_sell_condition(ticker, buy_price, current_price):
    profit_rate = ((current_price - buy_price) / buy_price) * 100
    if profit_rate >= TRAILING_ACTIVATE_ROI:
        df_5m = pyupbit.get_ohlcv(ticker, interval="minute5", count=10)
        if df_5m is not None:
            df_5m.ta.sma(length=5, append=True)
            if current_price < df_5m['SMA_5'].iloc[-1]:
                return True, f"트레일링 익절 (+{profit_rate:.2f}%)"
    elif profit_rate <= STOP_LOSS_ROI:
        return True, f"손절 방어 ({profit_rate:.2f}%)"
    return False, ""

# ==========================================
# 6. 메인 루프
# ==========================================
def main_loop():
    global last_report_time
    send_message("🚀 쿠퍼춘봉 시스템 V9.2 가동 시작!\n(상태 확인 및 브리핑 로직 통합 완료)")
    
    while True:
        try:
            # 주기적 시스템 브리핑 보고
            if time.time() - last_report_time > REPORT_INTERVAL:
                send_system_briefing()
                last_report_time = time.time()

            if check_btc_volatility():
                target_tickers = get_elite_tickers()
                for ticker in target_tickers:
                    balance = upbit.get_balance(ticker)
                    curr_p = pyupbit.get_current_price(ticker)
                    
                    if balance == 0 or balance is None:
                        if check_buy_condition(ticker):
                            krw = upbit.get_balance("KRW")
                            buy_amt = krw * BUY_RATIO
                            if buy_amt > 5000:
                                upbit.buy_market_order(ticker, buy_amt)
                                send_message(f"✅ [매수] {ticker}\n금액: {buy_amt:,.0f}원")
                    else:
                        avg_p = upbit.get_avg_buy_price(ticker)
                        should_sell, reason = check_sell_condition(ticker, avg_p, curr_p)
                        if should_sell:
                            upbit.sell_market_order(ticker, balance)
                            profit_amt = (curr_p - avg_p) * balance
                            update_statistics(profit_amt, (curr_p > avg_p))
                            send_message(f"💰 [매도] {ticker}\n사유: {reason}\n수익: {profit_amt:,.0f}원")
            
            time.sleep(1)
        except Exception as e:
            print(f"Error: {e}"); time.sleep(10)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    main_loop()
