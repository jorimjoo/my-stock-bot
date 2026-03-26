import time
import datetime
import requests
import pandas as pd
import pyupbit
import psutil
import json
import os

from flask import Flask
from threading import Thread

# =========================
# 1. API 설정
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 🧠 AI 성과 학습 (JSON 파일)
# =========================
PERF_FILE = "performance.json"

def load_performance():
    try:
        if os.path.exists(PERF_FILE):
            with open(PERF_FILE, "r") as f: return json.load(f)
    except Exception: pass
    return {}

def save_performance(data):
    try:
        with open(PERF_FILE, "w") as f: json.dump(data, f)
    except Exception: pass

performance = load_performance()

# =========================
# 3. 시간 및 시스템 설정
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

MAX_POSITIONS = 8           
TOP_N = 20                  

TRAILING_START = 3.0        
TRAILING_DROP = 1.2         
STOP_LOSS = -1.2            

STAGNANT_PROFIT = 1.0       
STAGNANT_VOLATILITY = 0.7   

BLACKLIST_HOURS = 1
REPORT_INTERVAL = 3600      

bot = {
    "positions": {},   
    "blacklist": {},
    "max_price": {},
    "last_report_time": 0,
    "daily_stats": {"trades": 0, "wins": 0, "profit": 0.0, "date": get_kst().date()},
    "total_stats": {"trades": 0, "wins": 0, "profit": 0.0, "start_time": get_kst()}
}

# =========================
# 4. 🌐 Flask 서버 & 텔레그램
# =========================
app = Flask(__name__)

@app.route('/')
def home(): return "🚀 V14 AI 트레이딩 봇 (텔레그램 리모컨 탑재) 가동중"

def run_server(): app.run(host='0.0.0.0', port=10000)

def send_msg(msg):
    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                     params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except: pass

def telegram_polling():
    """텔레그램 명령어 수신 스레드 (수동 매도 기능 추가)"""
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 10}
            if last_update_id: params["offset"] = last_update_id
            
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                for item in res.json().get("result", []):
                    last_update_id = item["update_id"] + 1
                    msg_text = str(item.get("message", {}).get("text", "")).strip().upper()
                    
                    # 1. 상태 점검 명령어
                    if "/상태" in msg_text or "상태" == msg_text:
                        print(f"[{get_kst().strftime('%H:%M:%S')}] 텔레그램 '/상태' 명령어 수신!")
                        send_system_briefing()
                        
                    # 2. 수동 매도 명령어 (예: "XRP 매도" 또는 "KRW-XRP 매도")
                    elif "매도" in msg_text:
                        ticker_raw = msg_text.replace("매도", "").strip()
                        if ticker_raw:
                            # 사용자가 "KRW-"를 안 붙이고 "XRP"만 입력해도 자동으로 변환
                            ticker = ticker_raw if ticker_raw.startswith("KRW-") else f"KRW-{ticker_raw}"
                            
                            balance = upbit.get_balance(ticker)
                            if balance is not None and balance > 0:
                                try:
                                    upbit.sell_market_order(ticker, balance)
                                    bot["positions"].pop(ticker, None)
                                    bot["max_price"].pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] 주인이 직접 {ticker} 전량을 시장가로 매도했습니다!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패] {ticker} 매도 중 에러 발생: {e}")
                            else:
                                send_msg(f"⚠ [매도 실패] 보유 중인 {ticker} 코인이 없습니다.")
                                
        except: pass
        time.sleep(2)

def check_daily_reset():
    now_date = get_kst().date()
    if bot["daily_stats"]["date"] != now_date:
        bot["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}

def send_system_briefing():
    try:
        check_daily_reset()
        now = get_kst()
        uptime = now - bot["total_stats"]["start_time"]
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600; minutes = (seconds % 3600) // 60
        
        try: cpu = psutil.cpu_percent(interval=None); ram = psutil.virtual_memory().percent
        except: cpu, ram = 0.0, 0.0
            
        try: krw_balance = float(upbit.get_balance("KRW") or 0.0)
        except: krw_balance = 0.0
            
        t_stats, d_stats = bot["total_stats"], bot["daily_stats"]
        total_win_rate = (t_stats["wins"] / t_stats["trades"] * 100) if t_stats["trades"] > 0 else 0
        daily_win_rate = (d_stats["wins"] / d_stats["trades"] * 100) if d_stats["trades"] > 0 else 0
        
        msg = f"🐶 [쿠퍼춘봉 V14 AI 브리핑]\n"
        msg += f"⌚ 봇 가동: {t_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💻 서버 상태: CPU {cpu}% / RAM {ram}%\n"
        msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
        msg += f"📈 [전체 누적 통계]\n- 수익: {t_stats['profit']:,.0f}원\n- 거래: {t_stats['trades']}회 (승률 {total_win_rate:.1f}%)\n\n"
        msg += f"📅 [오늘 하루 통계]\n- 수익: {d_stats['profit']:,.0f}원\n- 거래: {d_stats['trades']}회 (승률 {daily_win_rate:.1f}%)"
        
        send_msg(msg)
    except Exception as e: print(f"브리핑 발송 에러: {e}")

def update_statistics(profit_krw, is_win):
    check_daily_reset()
    for key in ["daily_stats", "total_stats"]:
        bot[key]["trades"] += 1; bot[key]["profit"] += profit_krw
        if is_win: bot[key]["wins"] += 1

def update_performance(ticker, profit):
    if ticker not in performance: performance[ticker] = {"trades": 0, "wins": 0}
    performance[ticker]["trades"] += 1
    if profit > 0: performance[ticker]["wins"] += 1
    save_performance(performance)

# =========================
# 5. 핵심 지표 계산
# =========================
def ta_rsi(series, period=14):
    delta = series.diff()
    up, down = delta.copy(), delta.copy()
    up[up < 0] = 0; down[down > 0] = 0
    _gain = up.ewm(com=(period - 1), min_periods=period).mean()
    _loss = down.abs().ewm(com=(period - 1), min_periods=period).mean()
    RS = _gain / _loss
    return 100 - (100 / (1 + RS))

def is_safe_volatility(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute60", count=2)
        vol = (df['high'].iloc[-1] - df['low'].iloc[-1]) / df['low'].iloc[-1] * 100
        return vol < 6.0
    except: return False

def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        all_data = []
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            res = requests.get(url, headers={"accept": "application/json"}, params={"markets": ",".join(batch)})
            if res.status_code == 200: all_data.extend(res.json())
            time.sleep(0.1) 
        
        scored_data = []
        for item in all_data:
            if item['market'] == "KRW-BTC": continue
            val = item['acc_trade_price_24h']
            change = item['signed_change_rate']
            score = (val * 0.7) + (change * 100000000 * 0.3)
            scored_data.append((item['market'], score))
        scored_data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in scored_data[:TOP_N]]
    except: return []

# =========================
# 6. 🧠 AI 타점 스코어링 및 비중 결정
# =========================
def get_score(ticker):
    score = 0
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        r = ta_rsi(df['close']).iloc[-1]

        if df['close'].iloc[-1] > df['ma5'].iloc[-1] > df['ma20'].iloc[-1]: score += 2
        if 40 < r < 65: score += 2
        if df['volume'].iloc[-2] > df['volume'].iloc[-3] * 1.5: score += 2

        if ticker in performance:
            t = performance[ticker]["trades"]; w = performance[ticker]["wins"]
            if t >= 5:
                winrate = w / t
                if winrate > 0.6: score += 3
                elif winrate < 0.4: score -= 2
    except: pass
    return score

def get_position_size(ticker, krw):
    base = krw * 0.1
    if ticker in performance:
        t = performance[ticker]["trades"]; w = performance[ticker]["wins"]
        if t >= 5:
            winrate = w / t
            if winrate > 0.6: return krw * 0.15   
            elif winrate < 0.4: return krw * 0.05 
    return base

# =========================
# 7. 매도 로직
# =========================
def sell_logic(ticker, avg_price, current_price, balance):
    profit = (current_price - avg_price) / avg_price * 100

    if ticker not in bot["max_price"]: bot["max_price"][ticker] = avg_price
    if current_price > bot["max_price"][ticker]: bot["max_price"][ticker] = current_price
    max_p = bot["max_price"][ticker]; drop = (max_p - current_price) / max_p * 100

    if profit >= STAGNANT_PROFIT:
        try:
            df5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=3)
            if df5 is not None and len(df5) >= 3:
                volatility = (df5['high'].max() - df5['low'].min()) / df5['low'].min() * 100
                if volatility <= STAGNANT_VOLATILITY:
                    upbit.sell_market_order(ticker, balance)
                    bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
                    krw_profit = (current_price - avg_price) * balance
                    update_performance(ticker, profit); update_statistics(krw_profit, True)
                    send_msg(f"🥱 횡보 탈출 전량 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
                    return
        except: pass

    if profit >= 2.0 and not bot["positions"].get(ticker, {}).get("half_sold", True):
        sell_amount = balance * 0.5
        upbit.sell_market_order(ticker, sell_amount)
        bot["positions"][ticker]["half_sold"] = True
        krw_profit = (current_price - avg_price) * sell_amount
        update_statistics(krw_profit, True)
        send_msg(f"✅ 1차(절반) 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    if profit >= TRAILING_START and drop >= TRAILING_DROP:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, True)
        send_msg(f"🚀 최종 트레일링 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    if profit <= STOP_LOSS:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        bot["blacklist"][ticker] = time.time() + 3600 * BLACKLIST_HOURS
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, False)
        send_msg(f"😭 손절 {ticker} ({profit:.2f}%) / 손실: {krw_profit:,.0f}원")

# =========================
# 8. 메인 루프 
# =========================
def main():
    send_msg(f"🚀 V14 AI 봇 (수동 조종 가능) 가동 시작 ({get_kst().strftime('%m/%d %H:%M')})")
    
    try: send_system_briefing()
    except: pass
    bot["last_report_time"] = time.time()
    
    loop_count = 0

    while True:
        try:
            now_ts = time.time()
            if now_ts - bot["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot["last_report_time"] = now_ts

            my_balances = upbit.get_balances()
            if not isinstance(my_balances, list):
                time.sleep(5)
                continue
                
            krw_balance = 0.0
            holding_dict = {}
            
            for b in my_balances:
                if b['currency'] == 'KRW':
                    krw_balance = float(b['balance'])
                else:
                    bal = float(b['balance']); avg = float(b['avg_buy_price'])
                    if bal > 0 and avg > 0:
                        holding_dict[f"KRW-{b['currency']}"] = {'balance': bal, 'avg_buy_price': avg}
                        
            loop_count += 1
            if loop_count % 10 == 0:
                print(f"[{get_kst().strftime('%H:%M:%S')}] 봇 정상 스캔 중... (현재 보유: {len(holding_dict)}/{MAX_POSITIONS}종목)")

            # [파트 A] 보유 종목 관리
            holding_tickers = list(holding_dict.keys())
            if holding_tickers:
                prices = pyupbit.get_current_price(holding_tickers)
                if prices:
                    for ticker in holding_tickers:
                        data = holding_dict[ticker]
                        balance = data['balance']; avg_price = data['avg_buy_price']
                        current_price = prices.get(ticker)
                        
                        if not current_price: continue
                            
                        if ticker not in bot["positions"]:
                            bot["positions"][ticker] = {"stage": 2, "half_sold": False, "buy_price": avg_price}
                            
                        pos = bot["positions"][ticker]
                        
                        if pos["stage"] == 1:
                            drop = (current_price - pos["buy_price"]) / pos["buy_price"] * 100
                            if drop <= -1.0:
                                amount = pos.get("target_amt", krw_balance * 0.1) * 0.5
                                if amount >= 5000 and krw_balance >= amount:
                                    upbit.buy_market_order(ticker, amount)
                                    pos["stage"] = 2
                                    krw_balance -= amount 
                                    send_msg(f"🛡️ 2차 매수(물타기) {ticker}")
                                    time.sleep(0.2)
                                    continue 
                                    
                        sell_logic(ticker, avg_price, current_price, balance)

            tracked_tickers = list(bot["positions"].keys())
            for t in tracked_tickers:
                if t not in holding_dict: del bot["positions"][t]

            # [파트 B] 신규 매수 탐색
            if len(holding_dict) < MAX_POSITIONS:
                buy_tickers = get_top_coins()
                target_tickers = [t for t in buy_tickers if t not in holding_dict and (t not in bot["blacklist"] or time.time() > bot["blacklist"][t])]
                
                if target_tickers:
                    prices = pyupbit.get_current_price(target_tickers)
                    if prices:
                        for ticker in target_tickers:
                            if len(holding_dict) >= MAX_POSITIONS: break 
                            
                            if get_score(ticker) >= 6 and is_safe_volatility(ticker):
                                total_target_amount = get_position_size(ticker, krw_balance)
                                amount = total_target_amount * 0.5
                                
                                if amount >= 5000 and krw_balance >= amount:
                                    upbit.buy_market_order(ticker, amount)
                                    bot["positions"][ticker] = {
                                        "stage": 1, 
                                        "half_sold": False, 
                                        "buy_price": prices[ticker], 
                                        "target_amt": total_target_amount
                                    }
                                    send_msg(f"🔥 AI 1차 매수 {ticker} (스코어 달성)")
                                    krw_balance -= amount
                                    holding_dict[ticker] = True 
                                    time.sleep(0.2)

            time.sleep(1) 
            
        except Exception as e:
            print(f"[{get_kst().strftime('%H:%M:%S')}] 메인 루프 에러: {e}")
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
