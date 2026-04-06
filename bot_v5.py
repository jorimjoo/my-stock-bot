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
# 1. 시스템 및 환경 변수
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

# 시스템 경로 유지
IMAGEMAGICK_BINARY = r"C:\Program Files\ImageMagick-7.1.2-Q16"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 스윙/장기 매매 설정값 (💡 조건 완화)
# =========================
TOP_N = 15              # 💡 10개에서 15개로 후보군 확장!
MAX_POSITIONS = 3       # 3종목에 30%씩 비중을 크게 실어서 진입!

# 동적 매도 설정 (유지)
TRAILING_ACTIVATE = 0.05 # 수익이 +5% 이상일 때부터 고점 추적
TRAILING_DROP = 0.02     # 최고점 대비 -2% 꺾이면 하락 징후로 보고 익절
HARD_STOP_LOSS = -0.03   # 최후의 보루: -3% 도달 시 어떤 이유든 칼손절

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
# 3. 🌐 웹 서버 (기절 방지)
# =========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("🚀 춘봉봇 V12.1 스윙 완화판 가동중!".encode('utf-8'))

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# =========================
# 4. 텔레그램 통신 & 브리핑
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
        
        msg = f"🐶 [쿠퍼춘봉 V12.1 스윙/조건완화 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n"
        msg += f"🎯 보유 종목 (최대 3개 집중): {len(positions)}/{MAX_POSITIONS}개\n\n"
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
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량 익절!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패]: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

# =========================
# 5. 종목 스캔 & 💡 매수 신호 (조건 완화 적용)
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
            time.sleep(0.2)
        
        data = []
        for item in all_data:
            if item['market'] == "KRW-BTC": continue
            data.append((item['market'], item['acc_trade_price_24h']))

        data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in data[:TOP_N]]
    except: return []

def check_buy_signal(ticker):
    """
    반환값: (진입 여부, 일봉 20일선 가격)
    """
    try:
        # 1. 일봉 분석 (큰 추세 확인 - 유지)
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=30)
        if df_day is None or len(df_day) < 25: return False, 0
        
        day_ma20 = df_day['close'].rolling(20).mean().iloc[-1]
        day_close = df_day['close'].iloc[-1]
        
        if day_close < day_ma20:
            return False, 0
            
        time.sleep(0.1) 
        
        # 💡 2. 15분봉 분석 (눌림목 기준 완화!)
        df_15m = pyupbit.get_ohlcv(ticker, interval="minute15", count=30)
        if df_15m is None or len(df_15m) < 25: return False, 0
        
        df_15m['ma20'] = df_15m['close'].rolling(20).mean()
        
        cur_close = df_15m['close'].iloc[-1]
        cur_open = df_15m['open'].iloc[-1]
        cur_low = df_15m['low'].iloc[-1]
        ma20_15m = df_15m['ma20'].iloc[-1]
        
        # 💡 극단적 하단 터치 대신, 15분봉 20일선 부근(0.5% 위아래)으로 내려오면 눌림목 인정
        is_dipped = cur_low <= (ma20_15m * 1.005) 
        is_bouncing = cur_close > cur_open
        
        if is_dipped and is_bouncing:
            return True, day_ma20
            
        return False, 0
    except: return False, 0

# =========================
# 6. 장부 관리 및 실행 
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
        
        buy_amount = amt * avg_price
        
        if buy_amount >= 5000:
            active_tickers.append(ticker)
            if ticker not in positions:
                positions[ticker] = {
                    'buy_price': avg_price,
                    'peak_price': avg_price,
                    'day_ma20': 0 
                }
                
    for t in list(positions.keys()):
        if t not in active_tickers:
            positions.pop(t, None)
            
    return krw_bal

def buy_coin(ticker, krw, day_ma20):
    try:
        res = upbit.buy_market_order(ticker, krw)
        if res is None or 'error' in res: return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'peak_price': current_price,
            'day_ma20': day_ma20
        }
        
        send_msg(f"🎯 [비중 30% 진입] {ticker}\n(일봉 상승 확인 + 15분봉 유연한 눌림목 포착!)")
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
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 익손절: {krw_profit:,.0f}원")
    except Exception as e: pass

# =========================
# 7. 메인 루프 (시나리오 기반 스윙 매매)
# =========================
def main():
    start_msg = f"🚀 V12.1 스윙 완화판 가동! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 너무 깐깐했던 15분봉 조건을 현실적인 눌림목으로 러프하게 풀었습니다."
    send_msg(start_msg)
    
    bot_stats["last_report_time"] = time.time()
    last_top_coins_time = 0
    top_coins = []
    
    global blacklist

    while True:
        try:
            now = time.time()
            
            if now - bot_stats["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_stats["last_report_time"] = now

            krw = sync_balances()
            blacklist = {k: v for k, v in blacklist.items() if now - v < 3600} 

            if now - last_top_coins_time > 300: 
                top_coins = get_top_coins()
                last_top_coins_time = now

            holding_tickers = list(positions.keys())
            
            # ======================
            # 시나리오 기반 동적 매도 로직
            # ======================
            for ticker in holding_tickers:
                current_price = pyupbit.get_current_price(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                profit = (current_price - buy_price) / buy_price
                
                if current_price > pos['peak_price']: pos['peak_price'] = current_price
                drop_from_peak = (pos['peak_price'] - current_price) / pos['peak_price']

                df_day = pyupbit.get_ohlcv(ticker, interval="day", count=25)
                if df_day is not None and len(df_day) >= 20:
                    current_day_ma20 = df_day['close'].rolling(20).mean().iloc[-1]
                else:
                    current_day_ma20 = pos['day_ma20'] 

                # [시나리오 1] 장기 상승 오버슈팅 (수익 극대화 후 하락 예측 시 매도)
                if profit >= TRAILING_ACTIVATE:
                    if drop_from_peak >= TRAILING_DROP:
                        sell_coin(ticker, "💸 [추세 꺾임 감지 최고점 익절]", current_price, buy_price)
                        continue
                
                # [시나리오 3] 분석 결과 지속 하락 예측 (일봉 추세선 붕괴)
                if profit < 0 and current_price < current_day_ma20:
                    sell_coin(ticker, "✂️ [일봉 추세 붕괴 빠른 컷]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue
                    
                # [안전장치] 시나리오 2(단기 하락 버티기) 중이라도 -3%에 닿으면 즉시 컷
                if profit <= HARD_STOP_LOSS:
                    sell_coin(ticker, "☠️ [-3% 방어선 칼손절]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue

                time.sleep(0.2)

            # ======================
            # 💡 비중 UP 스윙 매수 로직 (유연해진 그물망)
            # ======================
            for ticker in top_coins:
                if ticker in positions or ticker in blacklist: continue
                if len(positions) >= MAX_POSITIONS: break

                is_buy, day_ma20 = check_buy_signal(ticker)
                
                if is_buy:
                    buy_amount = max(krw * 0.30, MIN_ORDER)
                    
                    if krw >= (buy_amount * FEE_RATE) and buy_amount >= MIN_ORDER:
                        buy_coin(ticker, buy_amount, day_ma20)
                        krw -= (buy_amount * FEE_RATE)
                        time.sleep(0.5)

                time.sleep(0.2) 

            time.sleep(3)

        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
