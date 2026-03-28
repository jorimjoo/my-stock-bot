import time
import datetime
import requests
import pandas as pd
import pyupbit
import json
import os
import gc
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer 

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
    except: pass
    return {}

def save_performance(data):
    try:
        with open(PERF_FILE, "w") as f:
            json.dump(data, f)
    except: pass

performance = load_performance()

# =========================
# 3. 시간 및 시스템 설정 (초단타 공격형 셋팅)
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

MAX_POSITIONS = 8           
TOP_N = 20                  

# 💣 공격형 매도 타점
TRAILING_START = 1.5        # 1.5% 수익 시 트레일링 시작
TRAILING_DROP = 0.5         # 고점 대비 0.5% 꺾이면 전량 익절
STOP_LOSS = -0.8            # -0.8% 칼손절
HALF_SELL_TARGET = 1.0      # +1.0% 도달 시 절반 익절

STAGNANT_PROFIT = 0.5       # +0.5% 수익이라도 횡보하면 던짐
STAGNANT_VOLATILITY = 0.5   

TIME_CUT_SEC = 7200         # 💣 2시간(7200초) 경과 시 무조건 청산
BLACKLIST_HOURS = 0.5       # 손절 후 30분만 대기 (빠른 재진입)
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
# 4. 🌐 내장 웹 서버
# =========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write("🚀 춘봉봇 정상 가동중!".encode('utf-8'))

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# =========================
# 5. 텔레그램 통신
# =========================
def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except: pass

def telegram_polling():
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
                    msg_text = str(item.get("message", {}).get("text", "")).strip().upper()
                    
                    if "/상태" in msg_text or "상태" == msg_text:
                        send_system_briefing()
                        
                    elif "매도" in msg_text:
                        ticker_raw = msg_text.replace("매도", "").strip()
                        if ticker_raw:
                            ticker = ticker_raw if ticker_raw.startswith("KRW-") else f"KRW-{ticker_raw}"
                            balance = upbit.get_balance(ticker)
                            if balance and balance > 0:
                                try:
                                    upbit.sell_market_order(ticker, balance)
                                    bot["positions"].pop(ticker, None)
                                    bot["max_price"].pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량을 시장가로 매도했습니다!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패] {ticker} 에러: {e}")
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
        
        try: krw_balance = float(upbit.get_balance("KRW") or 0.0)
        except: krw_balance = 0.0
            
        t_stats, d_stats = bot["total_stats"], bot["daily_stats"]
        total_win_rate = (t_stats["wins"] / t_stats["trades"] * 100) if t_stats["trades"] > 0 else 0
        daily_win_rate = (d_stats["wins"] / d_stats["trades"] * 100) if d_stats["trades"] > 0 else 0
        
        msg = f"🐶 [쿠퍼춘봉 V17 행동대장 브리핑]\n"
        msg += f"⌚ 봇 가동: {t_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
        msg += f"📈 [전체 누적 통계]\n- 수익: {t_stats['profit']:,.0f}원\n- 거래: {t_stats['trades']}회 (승률 {total_win_rate:.1f}%)\n\n"
        msg += f"📅 [오늘 하루 통계]\n- 수익: {d_stats['profit']:,.0f}원\n- 거래: {d_stats['trades']}회 (승률 {daily_win_rate:.1f}%)"
        
        send_msg(msg)
    except: pass

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
# 6. 핵심 지표 계산 
# =========================
def ta_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def is_safe_volatility(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute60", count=2)
        if df is None or len(df) < 2: return False
        vol = (df['high'].iloc[-1] - df['low'].iloc[-1]) / df['low'].iloc[-1] * 100
        return vol < 8.0 
    except: return False

def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        all_data = []
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            res = requests.get(url, headers={"accept": "application/json"}, params={"markets": ",".join(batch)}, timeout=10)
            if res.status_code == 200: all_data.extend(res.json())
            time.sleep(0.2) 
        
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
# 7. 🧠 AI 타점 스코어링 (5분봉 초단타 모드)
# =========================
def get_score(ticker):
    score = 0
    try:
        # 💣 15분봉 -> 5분봉으로 더 예민하게 스캔
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=50)
        if df is None or len(df) < 20: return 0

        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        r = ta_rsi(df['close']).iloc[-1]

        if df['close'].iloc[-1] > df['ma20'].iloc[-1]: score += 1
        if df['close'].iloc[-1] > df['ma5'].iloc[-1]: score += 1
        
        # 💣 타점 완화: 살짝 과매도이거나, 50 이상(매수세)이면 가산점
        if r <= 45: score += 1        
        elif r > 50: score += 1 

        if df['volume'].iloc[-1] > df['volume'].iloc[-2] * 1.1: score += 1 

        if ticker in performance:
            t = performance[ticker]["trades"]; w = performance[ticker]["wins"]
            if t >= 3:
                winrate = w / t
                if winrate > 0.5: score += 1
                elif winrate < 0.3: score -= 1
    except: pass
    return score

def get_position_size(ticker, krw):
    base = krw * 0.1
    if ticker in performance:
        t = performance[ticker]["trades"]; w = performance[ticker]["wins"]
        if t >= 3:
            winrate = w / t
            if winrate > 0.5: return krw * 0.15   
            elif winrate < 0.3: return krw * 0.05 
    return base

# =========================
# 8. 매도 로직 (pos 데이터 포함, 타임컷 강제 청산 추가)
# =========================
def sell_logic(ticker, avg_price, current_price, balance, pos):
    profit = (current_price - avg_price) / avg_price * 100

    if ticker not in bot["max_price"]: bot["max_price"][ticker] = avg_price
    if current_price > bot["max_price"][ticker]: bot["max_price"][ticker] = current_price
    max_p = bot["max_price"][ticker]; drop = (max_p - current_price) / max_p * 100

    # 💣 0. 타임컷 (2시간 이상 들고 있으면 무조건 강제 청산)
    if time.time() - pos.get("buy_time", time.time()) > TIME_CUT_SEC:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, profit > 0)
        send_msg(f"⏰ [타임컷 강제청산] 2시간 경과 {ticker} ({profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    # 1. 횡보 탈출
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

    # 2. 절반 익절 (+1.0%)
    if profit >= HALF_SELL_TARGET and not pos.get("half_sold", True):
        sell_amount = balance * 0.5
        upbit.sell_market_order(ticker, sell_amount)
        pos["half_sold"] = True
        krw_profit = (current_price - avg_price) * sell_amount
        update_statistics(krw_profit, True)
        send_msg(f"✅ 1차(절반) 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    # 3. 최종 트레일링 익절
    if profit >= TRAILING_START and drop >= TRAILING_DROP:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, True)
        send_msg(f"🚀 최종 트레일링 익절 {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    # 4. 칼 손절 (-0.8%)
    if profit <= STOP_LOSS:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None); bot["max_price"].pop(ticker, None)
        bot["blacklist"][ticker] = time.time() + 3600 * BLACKLIST_HOURS
        krw_profit = (current_price - avg_price) * balance
        update_performance(ticker, profit); update_statistics(krw_profit, False)
        send_msg(f"😭 칼손절 {ticker} ({profit:.2f}%) / 손실: {krw_profit:,.0f}원")

# =========================
# 9. 기존 보유 잔고 동기화 (시간 포함)
# =========================
def sync_existing_positions():
    try:
        my_balances = upbit.get_balances()
        count = 0
        if isinstance(my_balances, list):
            for b in my_balances:
                if b['currency'] == 'KRW': continue
                balance = float(b['balance']); avg_buy_price = float(b['avg_buy_price'])
                if balance > 0 and avg_buy_price > 0:
                    bot["positions"][f"KRW-{b['currency']}"] = {
                        "stage": 2, 
                        "half_sold": False, 
                        "buy_price": avg_buy_price,
                        "buy_time": time.time() # 💣 봇 가동 시점부터 2시간 카운트 시작
                    }
                    count += 1
        return count
    except: return 0

# =========================
# 10. 메인 루프 
# =========================
def main():
    synced_count = sync_existing_positions()
    
    start_msg = f"🚀 V17 초단타 행동대장 시작 ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"🔥 2시간 타임컷 & 매수기준 2점 하향 장전!"
    send_msg(start_msg)
    
    send_system_briefing()
    bot["last_report_time"] = time.time()
    
    loop_count = 0
    last_top_coins_time = 0
    buy_tickers = []

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
                
            balance_dict = {}
            krw_balance = 0.0
            for b in my_balances:
                if b['currency'] == 'KRW': krw_balance = float(b['balance'])
                else:
                    balance_dict[f"KRW-{b['currency']}"] = {"balance": float(b['balance']), "avg_buy_price": float(b['avg_buy_price'])}

            holding_tickers = list(balance_dict.keys())
            
            if now_ts - last_top_coins_time > 900:
                buy_tickers = get_top_coins()
                last_top_coins_time = now_ts
            
            all_target_tickers = list(set(holding_tickers + buy_tickers))
            all_prices = {}
            if all_target_tickers:
                res_prices = pyupbit.get_current_price(all_target_tickers)
                if isinstance(res_prices, float) or isinstance(res_prices, int): all_prices = {all_target_tickers[0]: float(res_prices)}
                elif isinstance(res_prices, dict): all_prices = res_prices

            loop_count += 1
            if loop_count % 10 == 0:
                holding_info = [f"{t.replace('KRW-', '')} {((all_prices[t] - balance_dict[t]['avg_buy_price']) / balance_dict[t]['avg_buy_price'] * 100):+.1f}%" for t in holding_tickers if t in all_prices and balance_dict[t]['avg_buy_price'] > 0]
                info_str = ", ".join(holding_info) if holding_info else "없음"
                print(f"\n[{get_kst().strftime('%H:%M:%S')}] 🤖 공격 모드 (보유: {len(holding_tickers)}/{MAX_POSITIONS} | {info_str}) / 현금: {krw_balance:,.0f}원")

            # 파트 A: 보유 종목 관리
            for ticker in holding_tickers:
                data = balance_dict[ticker]
                balance = data["balance"]; avg_price = data["avg_buy_price"]
                current_price = all_prices.get(ticker)
                
                if not current_price: continue
                    
                if ticker not in bot["positions"]:
                    bot["positions"][ticker] = {"stage": 2, "half_sold": False, "buy_price": avg_price, "buy_time": time.time()}
                    
                pos = bot["positions"][ticker]
                
                if pos["stage"] == 1:
                    drop = (current_price - pos["buy_price"]) / pos["buy_price"] * 100
                    # 💣 물타기 구간도 -0.8%로 타이트하게
                    if drop <= -0.8: 
                        amount = max(pos.get("target_amt", krw_balance * 0.1) * 0.5, 6000)
                        if krw_balance >= amount:
                            try:
                                upbit.buy_market_order(ticker, amount)
                                pos["stage"] = 2  
                                krw_balance -= amount 
                                send_msg(f"🛡️ 2차 매수(물타기) {ticker}")
                            except: pass
                            time.sleep(0.2)
                            continue 

                sell_logic(ticker, avg_price, current_price, balance, pos)
                time.sleep(0.1)

            tracked_tickers = list(bot["positions"].keys())
            for t in tracked_tickers:
                if t not in balance_dict: del bot["positions"][t]

            # 파트 B: 신규 매수 탐색
            if len(balance_dict) < MAX_POSITIONS:
                target_tickers = [t for t in buy_tickers if t not in balance_dict and (t not in bot["blacklist"] or time.time() > bot["blacklist"][t])]
                
                if loop_count % 10 == 0: print("--- 🔍 야수 스캔 중 ---")
                
                for ticker in target_tickers:
                    if len(balance_dict) >= MAX_POSITIONS: break 
                    
                    score = get_score(ticker)
                    if loop_count % 10 == 0 and score > 0: print(f" - {ticker.replace('KRW-', '')} : {score}점")
                    
                    # 💣 합격선 2점으로 극단적 인하
                    if score >= 2 and is_safe_volatility(ticker):
                        current_price = all_prices.get(ticker)
                        if not current_price: continue

                        total_target_amount = get_position_size(ticker, krw_balance)
                        amount = max(total_target_amount * 0.5, 6000)
                        
                        if krw_balance >= amount:
                            try:
                                upbit.buy_market_order(ticker, amount)
                                bot["positions"][ticker] = {
                                    "stage": 1, 
                                    "half_sold": False, 
                                    "buy_price": current_price, 
                                    "target_amt": total_target_amount,
                                    "buy_time": time.time() # 💣 매수 시간 기록
                                }
                                send_msg(f"🔥 AI 1차 매수 {ticker} ({score}점 합격!)")
                                krw_balance -= amount
                                balance_dict[ticker] = True 
                            except Exception as e: print(f"매수 에러 ({ticker}): {e}")
                    
                    time.sleep(0.3) 

            gc.collect()
            time.sleep(1)
            
        except Exception as e:
            print(f"[{get_kst().strftime('%H:%M:%S')}] 메인 루프 복구 중: {e}")
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
