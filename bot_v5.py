import pyupbit
import pandas as pd
import numpy as np
import time
import datetime
import requests
import os
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer 

# =========================
# 1. API 및 시스템 설정
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

# 고정된 ImageMagick 경로 
IMAGEMAGICK_BINARY = r"C:\Program Files\ImageMagick-7.1.2-Q16"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. V16 상승장 파도타기 설정값
# =========================
TOP_N = 20              
MAX_POSITIONS = 7       

# 짧고 빠른 손익비 
TAKE_PROFIT = 0.02       # +2.0% 익절 (상승장이니 수익은 조금 더 길게)
HARD_STOP_LOSS = -0.015  # -1.5% 절대 방어선 (하지만 추세 꺾이면 이전에 먼저 팝니다)

# ⏰ 타임 컷 설정
STAGNANT_SEC = 2700      # 45분 횡보 시 무조건 탈출

MIN_ORDER = 6000        
FEE_RATE = 1.0005       
REPORT_INTERVAL = 3600  

blacklist = {}
positions = {} 

bot_stats = {
    "start_time": datetime.datetime.utcnow() + datetime.timedelta(hours=9),
    "last_report_time": 0,
    "trades": 0,
    "wins": 0,
    "profit_krw": 0.0
}

def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

# =========================
# 3. 🌐 웹 서버
# =========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("🚀 춘봉봇 V16 상승장 파도타기 가동중!".encode('utf-8'))

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# =========================
# 4. 텔레그램 통신
# =========================
def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except: pass

def send_system_briefing():
    try:
        now = get_kst()
        uptime = now - bot_stats["start_time"]
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600; minutes = (seconds % 3600) // 60
        
        krw_balance = float(upbit.get_balance("KRW") or 0.0)
        win_rate = (bot_stats["wins"] / bot_stats["trades"] * 100) if bot_stats["trades"] > 0 else 0.0
        
        msg = f"🐶 [쿠퍼춘봉 V16 상승장 파도타기 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n"
        msg += f"🎯 보유 종목: {len(positions)}/{MAX_POSITIONS}개\n\n"
        msg += f"📈 [누적 통계]\n- 수익: {bot_stats['profit_krw']:,.0f}원\n- 거래: {bot_stats['trades']}회 (승률 {win_rate:.1f}%)"
        
        send_msg(msg)
    except: pass

def telegram_polling():
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
                    
                    if "/상태" in msg_text or "상태" == msg_text:
                        send_system_briefing()

                    elif "매도" in msg_text:
                        ticker_raw = msg_text.replace("매도", "").replace("/", "").strip()
                        if ticker_raw:
                            ticker = ticker_raw if ticker_raw.startswith("KRW-") else f"KRW-{ticker_raw}"
                            balance = upbit.get_balance(ticker)
                            if balance and balance > 0:
                                try:
                                    upbit.sell_market_order(ticker, balance)
                                    positions.pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 익절!")
                                except Exception as e:
                                    send_msg(f"❌ [실패]: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

# =========================
# 5. 종목 스캔 (거래대금 순)
# =========================
def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        all_data = []
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            res = requests.get(url, headers={"accept": "application/json"}, params={"markets": ",".join(batch)}, timeout=10)
            if res.status_code == 200: all_data.extend(res.json())
            time.sleep(0.1)
        
        data = []
        for item in all_data:
            if item['market'] == "KRW-BTC": continue
            if item['acc_trade_price_24h'] >= 100000000: 
                data.append({
                    'market': item['market'], 
                    'volume': item['acc_trade_price_24h']
                })

        data.sort(key=lambda x: x['volume'], reverse=True)
        return [x['market'] for x in data[:TOP_N]]
    except: return []

def ta_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# =========================
# 6. 매수 신호 (💡 상승장 유연한 탑승 로직)
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=80)
        time.sleep(0.05) 
        
        if df is None or len(df) < 65: return False, "NONE"
        
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma60'] = df['close'].rolling(60).mean()
        df['rsi'] = ta_rsi(df['close'])
        
        ma5 = df['ma5'].iloc[-1]
        ma20 = df['ma20'].iloc[-1]
        ma60 = df['ma60'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        
        cur_close = df['close'].iloc[-1]
        cur_open = df['open'].iloc[-1]
        cur_low = df['low'].iloc[-1]
        
        # 💡 전제조건: 상승장 판단 (5분봉 기준 20일선이 60일선 위에 있어야 함)
        is_uptrend = ma20 > ma60
        if not is_uptrend: return False, "NONE"
        
        # 🚀 [패턴 1] 추세 탑승: 정배열(5일선>20일선) 속에서 양봉이며 과열(RSI 70)이 아닐 때 쉽게 탑승!
        cond_riding = (ma5 > ma20) and (cur_close > cur_open) and (cur_close > ma5) and (rsi < 70)
        
        if cond_riding:
            return True, "🚀추세탑승"
            
        # 🍀 [패턴 2] 상승 눌림목: 상승장 속에서 20일선 근처로 얕게 떨어졌다 반등할 때!
        cond_dip = (cur_low <= ma20 * 1.005) and (cur_close > cur_open) and (rsi < 60)
        
        if cond_dip:
            return True, "🍀상승눌림"

        return False, "NONE"
    except: return False, "NONE"

# =========================
# 7. 장부 및 매매 실행
# =========================
def sync_balances():
    balances = upbit.get_balances()
    if not isinstance(balances, list): return 0
    
    krw_bal = 0.0
    active_tickers = []
    
    for b in balances:
        if b['currency'] == 'KRW': 
            krw_bal = float(b['balance'])
            continue
            
        ticker = f"KRW-{b['currency']}"
        avg_price = float(b['avg_buy_price'])
        amt = float(b['balance'])
        
        if amt * avg_price >= 5000:
            active_tickers.append(ticker)
            if ticker not in positions:
                positions[ticker] = {'buy_price': avg_price, 'buy_type': "수동/외부", 'buy_time': time.time()}
                
    for t in list(positions.keys()):
        if t not in active_tickers:
            positions.pop(t, None)
            
    return krw_bal

def buy_coin(ticker, krw_unit, buy_type):
    try:
        res = upbit.buy_market_order(ticker, krw_unit)
        if res is None or 'error' in res: return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'buy_type': buy_type,
            'buy_time': time.time()
        }
        
        send_msg(f"🔥 [{buy_type} 포착] {ticker} 즉시 탑승!")
    except Exception as e: pass

def sell_coin(ticker, reason, current_price, buy_price):
    try:
        actual_balance = upbit.get_balance(ticker)
        profit_rate = (current_price - buy_price) / buy_price * 100
        krw_profit = (current_price - buy_price) * actual_balance
        
        res = upbit.sell_market_order(ticker, actual_balance)
        if res is None or 'error' in res: return

        positions.pop(ticker, None)
        bot_stats["trades"] += 1
        bot_stats["profit_krw"] += krw_profit
        if krw_profit > 0: bot_stats["wins"] += 1
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 손익: {krw_profit:,.0f}원")
    except Exception as e: pass

# =========================
# 8. 메인 루프
# =========================
def main():
    start_msg = "== 업비트 봇 가동 시작 =="
    send_msg(start_msg)
    send_msg("🚀 V16 상승장 파도타기 에디션! 잦은 매매와 하락 전환 시 즉각 컷 아웃 로직을 가동합니다.")
    
    bot_stats["last_report_time"] = time.time()
    last_top_coins_time = 0
    top_coins = []
    
    global blacklist

    while True:
        try:
            now = time.time()
            if now - bot_stats["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing(); bot_stats["last_report_time"] = now

            krw = sync_balances()
            blacklist = {k: v for k, v in blacklist.items() if now - v < 1800} 

            if now - last_top_coins_time > 60: 
                top_coins = get_top_coins(); last_top_coins_time = now

            holding_tickers = list(positions.keys())
            
            # ======================
            # ⚡ 스마트 매도 및 하락장 방어 로직
            # ======================
            for ticker in holding_tickers:
                current_price = pyupbit.get_current_price(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                held_time = now - pos.get('buy_time', now)
                profit = (current_price - buy_price) / buy_price

                # 1. 상승장 목표 익절 (+2.0%)
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "🎯 [상승장 파도타기 익절]", current_price, buy_price); continue

                # 2. 💡 [핵심 방어] 하락장 전환 감시 (5분봉 20일선 이탈 시 미련없이 컷!)
                df_5m = pyupbit.get_ohlcv(ticker, interval="minute5", count=25)
                if df_5m is not None and len(df_5m) >= 20:
                    ma20_cur = df_5m['close'].rolling(20).mean().iloc[-1]
                    if profit < 0 and current_price < ma20_cur:
                        sell_coin(ticker, "✂️ [추세 꺾임 빠른 컷! 손실최소화]", current_price, buy_price)
                        blacklist[ticker] = now
                        continue
                
                # 3. 절대 방어선 칼손절 (-1.5%) - 급락 시 최후의 보루
                if profit <= HARD_STOP_LOSS:
                    sell_coin(ticker, "☠️ [-1.5% 급락 절대 손절]", current_price, buy_price); continue
                    blacklist[ticker] = now

                # 4. 45분 타임 컷
                if held_time >= STAGNANT_SEC:
                    if profit > 0:
                        sell_coin(ticker, "🥱 [45분 횡보 약수익 탈출]", current_price, buy_price); continue
                    else:
                        sell_coin(ticker, "⏰ [45분 횡보 강제 컷]", current_price, buy_price); continue

                time.sleep(0.1)

            # ⚡ 빠른 매수
            for ticker in top_coins:
                if ticker in positions or ticker in blacklist: continue
                if len(positions) >= MAX_POSITIONS: break

                is_buy, buy_type = check_buy_signal(ticker)
                if is_buy:
                    buy_unit = max(krw * 0.14, MIN_ORDER) 
                    if krw >= (buy_unit * FEE_RATE) and buy_unit >= MIN_ORDER:
                        buy_coin(ticker, buy_unit, buy_type)
                        krw -= (buy_unit * FEE_RATE)
                        time.sleep(0.5)
                time.sleep(0.1) 

            time.sleep(2)
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
