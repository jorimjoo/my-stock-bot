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
REPORT_INTERVAL = 3600        # 브리핑 간격 (3600초 = 1시간)

# 통계 및 상태 관리 변수
bot_state = {
    "daily_stats": {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()},
    "total_stats": {"trades": 0, "wins": 0, "profit": 0.0, "start_time": datetime.datetime.now()},
    "last_report_time": 0
}

# ==========================================
# 3. 🌐 Flask 가짜 웹 서버
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스윙 봇 V9.3 정상 구동 중! 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 유틸리티 함수
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg}, timeout=5)
    except: pass

def get_server_status():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    return cpu, ram

def send_system_briefing():
    """스크린샷 스타일의 시스템 브리핑 전송"""
    now = datetime.datetime.now()
    uptime = now - bot_state["total_stats"]["start_time"]
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    cpu, ram = get_server_status()
    krw_balance = upbit.get_balance("KRW")
    
    t_stats = bot_state["total_stats"]
    d_stats = bot_state["daily_stats"]
    
    total_win_rate = (t_stats["wins"] / t_stats["trades"] * 100) if t_stats["trades"] > 0 else 0
    daily_win_rate = (d_stats["wins"] / d_stats["trades"] * 100) if d_stats["trades"] > 0 else 0
    
    msg = f"🤖 [쿠퍼춘봉 시스템 브리핑]\n"
    msg += f"⌚ 봇 가동: {t_stats['start_time'].strftime('%m/%d %H:%M')}\n"
    msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
    msg += f"💻 서버 상태: CPU {cpu}% / RAM {ram}%\n"
    msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
    
    msg += f"📈 [전체 누적 통계]\n"
    msg += f"- 누적 수익: {t_stats['profit']:,.0f}원\n"
    msg += f"- 누적 거래: {t_stats['trades']}회 (승률 {total_win_rate:.1f}%)\n\n"
    
    msg += f"📅 [오늘 하루 통계]\n"
    msg += f"- 오늘 수익: {d_stats['profit']:,.0f}원\n"
    msg += f"- 오늘 거래: {d_stats['trades']}회 (승률 {daily_win_rate:.1f}%)"
    
    send_message(msg)
    print(f"[{now}] 시스템 브리핑 전송 완료.")

def update_statistics(profit_amount, is_win):
    now_date = datetime.datetime.now().date()
    if bot_state["daily_stats"]["date"] != now_date:
        bot_state["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}
    
    for key in ["daily_stats", "total_stats"]:
        bot_state[key]["trades"] += 1
        bot_state[key]["profit"] += profit_amount
        if is_win: bot_state[key]["wins"] += 1

# ==========================================
# 5. 매매 핵심 로직 (수정 완료)
# ==========================================
def get_elite_tickers(count=MAX_TICKERS):
    """거래대금 상위 종목 추출 로직 수정"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        # 한 번에 모든 종목 정보 가져오기
        data = pyupbit.get_current_price(tickers, verbose=True)
        
        # 데이터가 딕셔너리 형태로 오므로 items()를 사용하여 정렬
        sorted_tickers = sorted(data.items(), key=lambda x: x[1]['acc_trade_price_24h'], reverse=True)
        
        top_list = []
        for ticker, info in sorted_tickers:
            if ticker != "KRW-BTC":
                top_list.append(ticker)
            if len(top_list) >= count:
                break
        return top_list
    except Exception as e:
        print(f"종목 추출 중 오류: {e}")
        return ["KRW-ETH", "KRW-XRP", "KRW-SOL"]

def check_btc_volatility():
    """비트코인 휩쏘 방어 필터"""
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=3)
        if df is None: return False
        vol = ((df['high'].max() - df['low'].min()) / df['low'].min()) * 100
        if vol >= 2.0:
            print(f"!! 비트코인 변동성 과다 ({vol:.2f}%) - 매매 일시 중단")
            return False
        return True
    except: return False

def check_buy_condition(ticker):
    """매수 타점 상세 로그 추가"""
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
        if df is None or len(df) < 20: return False
        
        df.ta.sma(length=5, append=True)
        df.ta.rsi(length=14, append=True)
        adx = df.ta.adx(length=14)
        if adx is None: return False
        df = pd.concat([df, adx], axis=1)
        
        c = df['close'].iloc[-1]
        ma = df['SMA_5'].iloc[-1]
        rsi = df['RSI_14'].iloc[-1]
        adx_v = df['ADX_14'].iloc[-1]
        p_di = df['DMP_14'].iloc[-1]
        m_di = df['DMN_14'].iloc[-1]
        v_spike = df['volume'].iloc[-1] > df['volume'].iloc[-2] * 1.5 # 1.5배 거래량 폭증
        
        # 조건 검사
        cond1 = c > ma
        cond2 = adx_v > 25
        cond3 = p_di > m_di
        cond4 = rsi < 70
        cond5 = v_spike
        
        if cond1 and cond2 and cond3 and cond4 and cond5:
            return True
        return False
    except: return False

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
# 6. 메인 루프 (로그 강화)
# ==========================================
def main_loop():
    send_message("🚀 쿠퍼춘봉 시스템 V9.3 정상 가동 시작!\n(로직 수정 및 실시간 로그 강화)")
    print("쿠퍼춘봉 봇 가동 중... (Ctrl+C로 중단)")
    
    while True:
        try:
            now_ts = time.time()
            # 1. 브리핑 타이머 체크
            if now_ts - bot_state["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_state["last_report_time"] = now_ts

            # 2. 시장 상태 확인
            if check_btc_volatility():
                target_tickers = get_elite_tickers()
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 탐색 중: {', '.join(target_tickers[:5])}...")
                
                for ticker in target_tickers:
                    balance = upbit.get_balance(ticker)
                    curr_p = pyupbit.get_current_price(ticker)
                    
                    if balance == 0 or balance is None:
                        # 매수 탐색
                        if check_buy_condition(ticker):
                            krw = upbit.get_balance("KRW")
                            buy_amt = krw * BUY_RATIO
                            if buy_amt > 5000:
                                upbit.buy_market_order(ticker, buy_amt)
                                send_message(f"✅ [매수] {ticker}\n금액: {buy_amt:,.0f}원")
                    else:
                        # 매도 탐색
                        avg_p = upbit.get_avg_buy_price(ticker)
                        should_sell, reason = check_sell_condition(ticker, avg_p, curr_p)
                        if should_sell:
                            upbit.sell_market_order(ticker, balance)
                            profit_amt = (curr_p - avg_p) * balance
                            update_statistics(profit_amt, (curr_p > avg_p))
                            send_message(f"💰 [매도] {ticker}\n사유: {reason}\n수익: {profit_amt:,.0f}원")
                    time.sleep(0.1) # 종목간 딜레이
            
            time.sleep(1)
        except Exception as e:
            print(f"루프 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    # 웹 서버 시작
    Thread(target=run_server, daemon=True).start()
    # 봇 시작
    main_loop()
