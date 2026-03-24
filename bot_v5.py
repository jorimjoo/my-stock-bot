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
# 2. 시스템 및 매매 설정 (오리지널 로직 원복)
# ==========================================
TRAILING_ACTIVATE_ROI = 1.5   # 1.5% 이상 수익 시 트레일링 가동
TRAILING_DROP_RATE = 1.0      # 최고점 대비 1.0% 하락 시 익절
STOP_LOSS_ROI = -1.5          # 기본 손절률
BLACKLIST_HOURS = 1           # 손절 시 1시간 쿨다운 (재진입 방지)
BUY_RATIO = 0.2               # 매수 비중 (20%)
MAX_TICKERS = 15              # 최대 탐색 종목 수
REPORT_INTERVAL = 3600        # 브리핑 간격 (1시간)

# 한국 시간(KST) 강제 적용을 위한 타임존 설정
KST = datetime.timezone(datetime.timedelta(hours=9))

# 오리지널 상태 관리 변수 원복
bot_state = {
    "loss_counts": {}, 
    "blacklist_times": {}, 
    "max_prices": {},
    "last_report_time": 0
}

daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now(KST).date()}
total_stats = {"trades": 0, "wins": 0, "profit": 0.0, "start_time": datetime.datetime.now(KST)}

# ==========================================
# 3. 🌐 Flask 가짜 웹 서버
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스윙 봇 V9.4 (오류 수정 및 오리지널 로직 복구 완료!) 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 유틸리티 함수 (KST 시간 적용)
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg}, timeout=5)
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")

def get_server_status():
    return psutil.cpu_percent(), psutil.virtual_memory().percent

def send_system_briefing():
    now = datetime.datetime.now(KST)
    uptime = now - total_stats["start_time"]
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    cpu, ram = get_server_status()
    krw_balance = upbit.get_balance("KRW")
    
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
    global daily_stats, total_stats
    now_date = datetime.datetime.now(KST).date()
    
    if daily_stats["date"] != now_date:
        daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}
    
    for stats in [daily_stats, total_stats]:
        stats["trades"] += 1
        stats["profit"] += profit_amount
        if is_win: stats["wins"] += 1

# ==========================================
# 5. 핵심 매매 로직 (API 원복)
# ==========================================
def get_elite_tickers(count=MAX_TICKERS):
    """오리지널 REST API 방식 복구 (정상 작동)"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        
        # URL 길이 제한을 피해 50개씩 끊어서 요청
        all_data = []
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            querystring = {"markets": ",".join(batch)}
            response = requests.get(url, headers=headers, params=querystring)
            if response.status_code == 200:
                all_data.extend(response.json())
            time.sleep(0.1)
            
        sorted_data = sorted(all_data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        return [item['market'] for item in sorted_data if item['market'] != "KRW-BTC"][:count]
    except Exception as e:
        print(f"종목 조회 에러: {e}")
        return []

def check_btc_volatility():
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=3)
        if df is None: return False
        vol = ((df['high'].max() - df['low'].min()) / df['low'].min()) * 100
        if vol >= 2.0: return False
        return True
    except: return False

def check_buy_condition(ticker):
    # 블랙리스트(쿨다운) 종목 필터링
    if ticker in bot_state["blacklist_times"]:
        if time.time() < bot_state["blacklist_times"][ticker]: return False
        else: del bot_state["blacklist_times"][ticker]

    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
        if df is None or len(df) < 20: return False
        
        df.ta.sma(length=5, append=True); df.ta.rsi(length=14, append=True)
        adx = df.ta.adx(length=14)
        if adx is None: return False
        df = pd.concat([df, adx], axis=1)
        
        c = df['close'].iloc[-1]
        ma = df['SMA_5'].iloc[-1]
        rsi = df['RSI_14'].iloc[-1]
        adx_v = df['ADX_14'].iloc[-1]
        p_di = df['DMP_14'].iloc[-1]
        m_di = df['DMN_14'].iloc[-1]
        
        # 거래량 필터를 1.5배 -> 1.2배로 소폭 완화 (너무 잦은 매수 패스 방지)
        v_spike = df['volume'].iloc[-1] > df['volume'].iloc[-2] * 1.2 
        
        if (c > ma) and (adx_v > 25) and (p_di > m_di) and (rsi < 70) and v_spike:
            return True
        return False
    except: return False

def check_sell_condition(ticker, buy_price, current_price):
    """고점 추적 다이나믹 트레일링 스탑 (오리지널 복구)"""
    profit_rate = ((current_price - buy_price) / buy_price) * 100

    # 1. 최고점 갱신 로직
    if ticker not in bot_state["max_prices"]:
        bot_state["max_prices"][ticker] = buy_price
        
    if current_price > bot_state["max_prices"][ticker]:
        bot_state["max_prices"][ticker] = current_price
        
    max_price = bot_state["max_prices"][ticker]
    drop_from_max = ((max_price - current_price) / max_price) * 100

    # 2. 익절 조건 (1.5% 이상 도달 후 고점 대비 1.0% 하락 시)
    if profit_rate >= TRAILING_ACTIVATE_ROI:
        if drop_from_max >= TRAILING_DROP_RATE:
            del bot_state["max_prices"][ticker]
            return True, f"고점 트레일링 익절 (+{profit_rate:.2f}%)"
            
    # 3. 손절 조건 (-1.5%)
    elif profit_rate <= STOP_LOSS_ROI:
        if ticker in bot_state["max_prices"]: del bot_state["max_prices"][ticker]
        # 손절 시 블랙리스트 추가 (1시간 동안 재진입 금지)
        bot_state["blacklist_times"][ticker] = time.time() + (3600 * BLACKLIST_HOURS)
        return True, f"손절 방어 ({profit_rate:.2f}%)"
        
    return False, ""

# ==========================================
# 6. 메인 실행 루프
# ==========================================
def main_loop():
    send_message("🚀 쿠퍼춘봉 시스템 V9.4 가동!\n- 시간 동기화(KST) 및 오리지널 로직 원복 완료")
    print("=======================================")
    print("안정화 버전 가동 시작...")
    print("=======================================")
    
    bot_state["last_report_time"] = time.time()

    while True:
        try:
            now_ts = time.time()
            if now_ts - bot_state["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_state["last_report_time"] = now_ts

            if check_btc_volatility():
                target_tickers = get_elite_tickers()
                
                # 로그 출력 (한국 시간 적용)
                current_time_str = datetime.datetime.now(KST).strftime('%H:%M:%S')
                if target_tickers:
                    print(f"[{current_time_str}] 정상 탐색 중... 상위: {', '.join(target_tickers[:3])}")
                
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
                        if avg_p > 0 and curr_p is not None:
                            should_sell, reason = check_sell_condition(ticker, avg_p, curr_p)
                            if should_sell:
                                upbit.sell_market_order(ticker, balance)
                                profit_amt = (curr_p - avg_p) * balance
                                update_statistics(profit_amt, (curr_p > avg_p))
                                send_message(f"💰 [매도] {ticker}\n사유: {reason}\n수익: {profit_amt:,.0f}원")
                    time.sleep(0.1)
            time.sleep(1)
        except Exception as e:
            print(f"시스템 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    main_loop()
