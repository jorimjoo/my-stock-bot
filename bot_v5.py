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
            with open(PERF_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"학습 파일 로드 에러: {e}")
    return {}

def save_performance(data):
    try:
        with open(PERF_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"학습 파일 저장 에러: {e}")

performance = load_performance()

# =========================
# 3. 시간 및 시스템 설정
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

MAX_POSITIONS = 5
TOP_N = 15

TRAILING_START = 3.0        # 3.0% 수익 시 트레일링
TRAILING_DROP = 1.2         # 고점 대비 1.2% 하락 시 익절
STOP_LOSS = -1.2            # 기본 손절 (-1.2%)

STAGNANT_PROFIT = 1.0       # 횡보 익절 기준 (수익 1% 이상)
STAGNANT_VOLATILITY = 0.7   # 횡보 판단 변동성 (5분봉 3개 0.7% 이하)

BLACKLIST_HOURS = 1
REPORT_INTERVAL = 3600      # 1시간 주기로 브리핑

# 상태 및 통계 관리
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
def home():
    return "🚀 V14 AI 트레이딩 봇 가동중"

def run_server():
    app.run(host='0.0.0.0', port=10000)

def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        res = requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
        if res.status_code != 200:
            print(f"텔레그램 발송 실패 (코드: {res.status_code})")
    except Exception as e:
        print(f"텔레그램 발송 네트워크 에러: {e}")

def telegram_polling():
    """'/상태' 명령어 응답 스레드 (강력한 예외 처리 적용)"""
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 10}
            if last_update_id:
                params["offset"] = last_update_id
            res = requests.get(url, params=params, timeout=15)
            
            if res.status_code == 200:
                for item in res.json().get("result", []):
                    last_update_id = item["update_id"] + 1
                    msg_text = str(item.get("message", {}).get("text", "")).strip()
                    
                    if "/상태" in msg_text:
                        print(f"[{get_kst().strftime('%H:%M:%S')}] '/상태' 명령어 수신. 브리핑 전송합니다.")
                        send_system_briefing()
        except Exception as e: 
            print(f"텔레그램 수신 에러: {e}")
        time.sleep(2)

def check_daily_reset():
    now_date = get_kst().date()
    if bot["daily_stats"]["date"] != now_date:
        bot["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}

def send_system_briefing():
    """1시간 주기 / 명령어 브리핑 보고 (에러 방지 3중 처리)"""
    try:
        check_daily_reset()
        now = get_kst()
        uptime = now - bot["total_stats"]["start_time"]
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600; minutes = (seconds % 3600) // 60
        
        try: cpu = psutil.cpu_percent(interval=None); ram = psutil.virtual_memory().percent
        except: cpu, ram = 0.0, 0.0
            
        try: 
            krw_balance = float(upbit.get_balance("KRW") or 0.0)
        except: 
            krw_balance = 0.0
            
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
        print(f"[{get_kst().strftime('%H:%M:%S')}] 시스템 브리핑 발송 완료")
    except Exception as e:
        print(f"브리핑 발송 중 치명적 에러 발생 (봇 멈춤 방지): {e}")

def update_statistics(profit_krw, is_win):
    check_daily_reset()
    for key in ["daily_stats", "total_stats"]:
        bot[key]["trades"] += 1; bot[key]["profit"] += profit_krw
        if is_win: bot[key]["wins"] += 1

def update_performance(ticker, profit):
    if ticker not in performance:
        performance[ticker] = {"trades": 0, "wins": 0}
    performance[ticker]["trades"] += 1
    if profit > 0:
        performance[ticker]["wins"] += 1
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

def btc_ok():
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=50)
        ma20 = df['close'].rolling(20).mean()
        slope = ma20.iloc[-1] - ma20.iloc[-5]
        r = ta_rsi(df['close']).iloc[-1]
        return df['close'].iloc[-1] > ma20.iloc[-1] and slope > 0 and r > 50
    except: return False

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
    except Exception as e:
        print(f"스크리닝 에러: {e}"); return []

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
            t = performance[ticker]["trades"]
            w = performance[ticker]["wins"]
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
# 7. 매도 로직 (분할 익절 + 트레일링 + 횡보 탈출)
# =========================
def sell_logic(ticker, avg_price, current_price, balance):
    profit = (current_price - avg_price) / avg_price * 100

    if ticker not in bot["max_price"]: bot["max_price"][ticker] = avg_price
    if current_price > bot["max_price"][ticker]: bot["max_price"][ticker] = current_price
    max_p = bot["max_price"][ticker]; drop = (max_p - current_price) / max_p * 100

    # 0. 횡보 탈출
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

    # 1. 절반 익절 (2.0% 도달 시)
    if profit >= 2.0 and not bot["positions"].get(ticker, {}).get("half_sold", True):
        sell_amount = balance * 0.5
        upbit.sell_market_order(ticker, sell_amount)
        bot["positions"][ticker]["half_sold"] = True
        krw_profit = (current_price - avg_price) * sell_amount
        update_statistics(krw_profit, True)
        send_msg(f"✅ 1차(절반) 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    # 2. 최종 익절 (트레일링)
    if profit >= TRAILING_START and drop >= TRAILING_DROP:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, True)
        send_msg(f"🚀 최종 트레일링 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    # 3. 손절
    if profit <= STOP_LOSS:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        bot["blacklist"][ticker] = time.time() + 3600 * BLACKLIST_HOURS
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, False)
        send_msg(f"😭 손절 {ticker} ({profit:.2f}%) / 손실: {krw_profit:,.0f}원")

# =========================
# 8. 기존 보유 잔고 동기화 (봇 가동 시 단 1회)
# =========================
def sync_existing_positions():
    try:
        my_balances = upbit.get_balances()
        count = 0
        if isinstance(my_balances, list):
            for b in my_balances:
                if b['currency'] == 'KRW': continue
                
                balance = float(b['balance'])
                avg_buy_price = float(b['avg_buy_price'])
                
                if balance > 0 and avg_buy_price > 0:
                    ticker = f"KRW-{b['currency']}"
                    bot["positions"][ticker] = {
                        "stage": 2, # 무리한 물타기 방지
                        "half_sold": False,
                        "buy_price": avg_buy_price
                    }
                    count += 1
        return count
    except Exception as e:
        print(f"기존 잔고 연동 에러: {e}")
        return 0

# =========================
# 9. 메인 루프
# =========================
def main():
    synced_count = sync_existing_positions()
    
    start_msg = f"🚀 V14 AI 트레이딩 봇 시작 ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 기존 보유 종목 {synced_count}개 연동 완료!"
    send_msg(start_msg)
    
    # 여기서 에러가 나도 메인 루프가 죽지 않도록 내부에서 예외처리를 강화함
    send_system_briefing()
    bot["last_report_time"] = time.time()

    while True:
        try:
            now_ts = time.time()
            if now_ts - bot["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing(); bot["last_report_time"] = now_ts

            if not btc_ok():
                time.sleep(5)
                continue

            tickers = get_top_coins()
            all_prices = pyupbit.get_current_price(tickers) 
            my_balances = upbit.get_balances()              
            
            balance_dict = {}
            krw_balance = 0.0
            
            if isinstance(my_balances, list):
                for b in my_balances:
                    if b['currency'] == 'KRW':
                        krw_balance = float(b['balance'])
                    else:
                        balance_dict[f"KRW-{b['currency']}"] = {
                            "balance": float(b['balance']),
                            "avg_buy_price": float(b['avg_buy_price'])
                        }

            for ticker in tickers:
                if ticker in bot["blacklist"] and time.time() < bot["blacklist"][ticker]: continue
                
                price = all_prices.get(ticker, 0.0) if all_prices else 0.0
                coin_data = balance_dict.get(ticker, {"balance": 0.0, "avg_buy_price": 0.0})
                balance = coin_data["balance"]
                avg = coin_data["avg_buy_price"]

                # --- 신규 1차 진입 ---
                if balance == 0:
                    if len(bot["positions"]) >= MAX_POSITIONS: continue

                    if get_score(ticker) >= 7 and is_safe_volatility(ticker):
                        total_target_amount = get_position_size(ticker, krw_balance)
                        amount = total_target_amount * 0.5 
                        
                        if amount > 5000:
                            upbit.buy_market_order(ticker, amount)
                            bot["positions"][ticker] = {"stage": 1, "half_sold": False, "buy_price": price, "target_amt": total_target_amount}
                            send_msg(f"🔥 AI 1차 매수 {ticker} (스코어 달성)")
                            time.sleep(0.2) 

                # --- 보유 종목 관리 (물타기 & 매도) ---
                else:
                    if avg > 0 and price > 0:
                        if ticker in bot["positions"]:
                            pos = bot["positions"][ticker]
                            if pos["stage"] == 1:
                                drop = (price - pos["buy_price"]) / pos["buy_price"] * 100
                                if drop <= -1.0: 
                                    amount = pos.get("target_amt", krw_balance * 0.1) * 0.5
                                    if amount > 5000:
                                        upbit.buy_market_order(ticker, amount)
                                        pos["stage"] = 2  
                                        send_msg(f"🛡️ 2차 매수(물타기) {ticker}")
                                        time.sleep(0.2)
                                        continue 

                        sell_logic(ticker, avg, price, balance)

                time.sleep(0.2) 
                
        except Exception as e:
            print(f"메인 루프 에러: {e}")
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
