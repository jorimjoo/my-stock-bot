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
# 2. 한국 시간 강제 변환
# ==========================================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

# ==========================================
# 3. 시스템 및 매매 설정
# ==========================================
TRAILING_ACTIVATE_ROI = 1.5   
TRAILING_DROP_RATE = 1.0      
STOP_LOSS_ROI = -1.5          
BLACKLIST_HOURS = 1           
BUY_RATIO = 0.2               
MAX_TICKERS = 15              
REPORT_INTERVAL = 3600        # 1시간 주기로 자동 브리핑

bot_state = {
    "loss_counts": {}, 
    "blacklist_times": {}, 
    "max_prices": {},
    "last_report_time": 0,
    "daily_stats": {"trades": 0, "wins": 0, "profit": 0.0, "date": get_kst().date()},
    "total_stats": {"trades": 0, "wins": 0, "profit": 0.0, "start_time": get_kst()}
}

# ==========================================
# 4. 🌐 Flask 서버 & 텔레그램 수신 스레드
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스윙 봇 V9.7 정상 구동 중! 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# 💡 [핵심 수정] 텔레그램 메세지 수신 봇 (명령어 대기)
def telegram_polling():
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{telegram_token}/getUpdates"
            params = {"timeout": 10}
            if last_update_id:
                params["offset"] = last_update_id
            
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for item in data.get("result", []):
                    last_update_id = item["update_id"] + 1
                    msg_text = item.get("message", {}).get("text", "")
                    
                    # 사용자가 '/상태' 라고 입력하면 즉시 브리핑 발송
                    if msg_text == "/상태":
                        send_system_briefing()
        except Exception:
            pass
        time.sleep(2)

# ==========================================
# 5. 알림 및 상태 브리핑 로직
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg}, timeout=5)
    except: pass

def get_server_status():
    try:
        return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    except:
        return 0.0, 0.0

def check_daily_reset():
    now_date = get_kst().date()
    if bot_state["daily_stats"]["date"] != now_date:
        bot_state["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}

def send_system_briefing():
    check_daily_reset()
    now = get_kst()
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

def update_statistics(profit_amount, is_win):
    check_daily_reset()
    for key in ["daily_stats", "total_stats"]:
        bot_state[key]["trades"] += 1
        bot_state[key]["profit"] += profit_amount
        if is_win: bot_state[key]["wins"] += 1

# ==========================================
# 6. 매매 핵심 로직
# ==========================================
def get_elite_tickers(count=MAX_TICKERS):
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        
        all_data = []
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            querystring = {"markets": ",".join(batch)}
            res = requests.get(url, headers=headers, params=querystring)
            if res.status_code == 200:
                all_data.extend(res.json())
            time.sleep(0.1)
            
        sorted_data = sorted(all_data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        return [item['market'] for item in sorted_data if item['market'] != "KRW-BTC"][:count]
    except Exception as e:
        return []

def check_btc_volatility():
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=3)
        if df is None: return False
        vol = ((df['high'].max() - df['low'].min()) / df['low'].min()) * 100
        return vol < 2.0
    except: return False

def check_buy_condition(ticker):
    if ticker in bot_state["blacklist_times"]:
        if time.time() < bot_state["blacklist_times"][ticker]: 
            return False
        else: 
            del bot_state["blacklist_times"][ticker]

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
        
        # 💡 [핵심 수정] 실시간 불완전 캔들이 아닌, '직전 완성 캔들'의 거래량 폭증 확인
        prev_vol = df['volume'].iloc[-2]  # 직전 15분 완성 캔들
        pprev_vol = df['volume'].iloc[-3] # 그 전 15분 완성 캔들
        v_spike = prev_vol > (pprev_vol * 1.2)
        
        if (c > ma) and (adx_v > 25) and (p_di > m_di) and (rsi < 70) and v_spike:
            return True
        return False
    except: return False

def check_sell_condition(ticker, buy_price, current_price):
    profit_rate = ((current_price - buy_price) / buy_price) * 100

    if ticker not in bot_state["max_prices"]:
        bot_state["max_prices"][ticker] = buy_price
    if current_price > bot_state["max_prices"][ticker]:
        bot_state["max_prices"][ticker] = current_price
        
    max_price = bot_state["max_prices"][ticker]
    drop_from_max = ((max_price - current_price) / max_price) * 100

    if profit_rate >= TRAILING_ACTIVATE_ROI:
        df_5m = pyupbit.get_ohlcv(ticker, interval="minute5", count=10)
        if df_5m is not None:
            df_5m.ta.sma(length=5, append=True)
            if current_price < df_5m['SMA_5'].iloc[-1]:
                del bot_state["max_prices"][ticker]
                return True, f"5MA 이탈 트레일링 익절 (+{profit_rate:.2f}%)"
                
        if drop_from_max >= TRAILING_DROP_RATE:
            del bot_state["max_prices"][ticker]
            return True, f"고점 대비 하락 익절 (+{profit_rate:.2f}%)"
            
    elif profit_rate <= STOP_LOSS_ROI:
        if ticker in bot_state["max_prices"]: del bot_state["max_prices"][ticker]
        bot_state["blacklist_times"][ticker] = time.time() + (3600 * BLACKLIST_HOURS)
        return True, f"손절 방어 및 1시간 제한 ({profit_rate:.2f}%)"
        
    return False, ""

# ==========================================
# 7. 메인 실행 루프
# ==========================================
def main_loop():
    send_message(f"🚀 쿠퍼춘봉 봇 V9.7 가동 완료!\n- '/상태' 명령어 수신 복구\n- 거래량 필터 오류 완벽 해결")
    
    send_system_briefing()
    bot_state["last_report_time"] = time.time()

    while True:
        try:
            now_ts = time.time()
            if now_ts - bot_state["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_state["last_report_time"] = now_ts

            if check_btc_volatility():
                target_tickers = get_elite_tickers()
                if target_tickers:
                    print(f"[{get_kst().strftime('%H:%M:%S')}] 탐색 중... (상위: {', '.join(target_tickers[:3])})")
                
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
            print(f"루프 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    # Flask 서버 스레드 시작
    Thread(target=run_server, daemon=True).start()
    
    # 💡 텔레그램 수신 봇 스레드 시작 (이걸 누락했었습니다 ㅠㅠ)
    Thread(target=telegram_polling, daemon=True).start()
    
    # 봇 가동
    main_loop()
