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

IMAGEMAGICK_BINARY = r"C:\Program Files\ImageMagick-7.1.2-Q16"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. V18 실전 수익 창출 설정값
# =========================
TOP_N = 25
MAX_POSITIONS = 5

# 💡 스마트 수익 & 손실 관리
TRAILING_ACTIVATE = 0.015  # +1.5% 도달 시 트레일링 익절 시작 (이익 보존)
TRAILING_DROP = 0.005      # 고점 대비 -0.5% 하락 시 익절
STOP_LOSS = -0.015         # -1.5% 피도 눈물도 없는 칼손절

# ⏰ 타임 컷 설정
STAGNANT_SEC = 2700        # 45분 횡보 시 무조건 탈출하여 자금 회전

MIN_ORDER = 6000
FEE_RATE = 1.0005

positions = {}
blacklist = {}

bot_stats = {
    "start_time": datetime.datetime.utcnow() + datetime.timedelta(hours=9),
    "last_report_time": 0,
    "trades": 0,
    "wins": 0,
    "profit_krw": 0.0
}

# =========================
# 3. 유틸리티 함수
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except:
        pass

def send_system_briefing():
    try:
        now = get_kst()
        uptime = now - bot_stats["start_time"]
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600; minutes = (seconds % 3600) // 60
        
        krw_balance = float(upbit.get_balance("KRW") or 0.0)
        win_rate = (bot_stats["wins"] / bot_stats["trades"] * 100) if bot_stats["trades"] > 0 else 0.0
        
        msg = f"🐶 [쿠퍼춘봉 V18 실전 수익창출 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n"
        msg += f"🎯 보유 종목: {len(positions)}/{MAX_POSITIONS}개\n\n"
        msg += f"📈 [누적 통계]\n- 수익: {bot_stats['profit_krw']:,.0f}원\n- 거래: {bot_stats['trades']}회 (승률 {win_rate:.1f}%)"
        
        send_msg(msg)
    except: pass

# =========================
# 4. 웹 서버 (기절 방지용)
# =========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("🚀 춘봉봇 V18 실전 가동중!".encode('utf-8'))

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# =========================
# 5. 거래대금 TOP 코인 스캔 (API 과부하 방지 적용)
# =========================
def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        all_data = []
        
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            res = requests.get(url, headers={"accept": "application/json"}, params={"markets": ",".join(batch)}, timeout=10)
            if res.status_code == 200:
                all_data.extend(res.json())
            time.sleep(0.2) 
        
        data = []
        for item in all_data:
            if item['market'] == "KRW-BTC": continue
            if item['acc_trade_price_24h'] >= 100000000: # 1억 미만 유령 코인 제외
                data.append((item['market'], item['acc_trade_price_24h']))

        data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in data[:TOP_N]]
    except Exception as e:
        print(f"TOP_COINS ERROR: {e}") 
        return []

def ta_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# =========================
# 6. 매수 로직 (확실한 추세 탑승)
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=40)
        time.sleep(0.1) 
        
        if df is None or len(df) < 30:
            return False

        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['rsi'] = ta_rsi(df['close'])

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # 1. 상승 추세 확인: 현재가가 20일선 위에 있고, 5일선이 20일선 위에 있어야 함
        if cur['close'] < cur['ma20'] or cur['ma5'] < cur['ma20']:
            return False

        # 2. 확실한 양봉 진행 중
        if cur['close'] <= cur['open']:
            return False

        # 3. 수급 확인: 현재 캔들의 거래량이 최근 20평균보다 1.5배 이상 터졌는지 확인
        vol_avg = df['volume'].rolling(20).mean().iloc[-1]
        if cur['volume'] < vol_avg * 1.5:
            return False

        # 4. 과열 방지: RSI가 70 이상이면 고점 추격매수이므로 패스
        if cur['rsi'] > 70:
            return False

        return True

    except Exception as e:
        return False

# =========================
# 7. 장부 및 매매 실행 함수
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
                positions[ticker] = {'buy_price': avg_price, 'peak_price': avg_price, 'buy_time': time.time()}
                
    for t in list(positions.keys()):
        if t not in active_tickers:
            positions.pop(t, None)
            
    return krw_bal

def buy_coin(ticker, krw_unit):
    try:
        res = upbit.buy_market_order(ticker, krw_unit)
        if res is None or 'error' in res: return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'peak_price': current_price,
            'buy_time': time.time()
        }
        
        send_msg(f"🔥 [매수 완료] {ticker} (추세+수급 동시 포착!)")
    except Exception as e: pass

def sell_coin(ticker, reason, current_price, buy_price):
    try:
        actual_balance = upbit.get_balance(ticker)
        profit_rate = (current_price - buy_price) / buy_price * 100
        krw_profit = (current_price - buy_price) * actual_balance
        
        res = upbit.sell_market_order(ticker, actual_balance)
        if res is None or 'error' in res: return

        positions.pop(ticker, None)
        blacklist[ticker] = time.time()
        
        bot_stats["trades"] += 1
        bot_stats["profit_krw"] += krw_profit
        if krw_profit > 0: bot_stats["wins"] += 1
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 손익: {krw_profit:,.0f}원")
    except Exception as e: pass

# =========================
# 8. 메인 루프
# =========================
def main():
    start_msg = "== 업비트 봇 가동 시작 ==\n🚀 V18 실전 수익 창출 에디션이 출격합니다!"
    send_msg(start_msg)
    print(start_msg)

    bot_stats["last_report_time"] = time.time()
    last_top_coins_time = 0
    top_coins = []

    while True:
        try:
            now = time.time()
            if now - bot_stats["last_report_time"] > 3600:
                send_system_briefing()
                bot_stats["last_report_time"] = now

            krw = sync_balances()
            
            # 블랙리스트 30분 후 해제 (빠른 자금 회전)
            blacklist_keys = list(blacklist.keys())
            for k in blacklist_keys:
                if now - blacklist[k] > 1800:
                    del blacklist[k]

            # 종목 스캔 (API 과부하를 막기 위해 3분마다 갱신)
            if now - last_top_coins_time > 180:
                top_coins = get_top_coins()
                last_top_coins_time = now

            holding_tickers = list(positions.keys())

            # =================
            # 💡 스마트 매도 감시
            # =================
            for ticker in holding_tickers:
                current_price = pyupbit.get_current_price(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                held_time = now - pos.get('buy_time', now)
                profit = (current_price - buy_price) / buy_price
                
                # 최고가 갱신 (트레일링 익절용)
                if current_price > pos['peak_price']: 
                    pos['peak_price'] = current_price
                    
                drop_from_peak = (pos['peak_price'] - current_price) / pos['peak_price']

                # 1. 트레일링 익절 (이익 극대화)
                if profit >= TRAILING_ACTIVATE and drop_from_peak >= TRAILING_DROP:
                    sell_coin(ticker, "💸 [스마트 익절]", current_price, buy_price)
                    continue
                
                # 2. 칼손절 (손실 최소화)
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ [방어선 칼손절]", current_price, buy_price)
                    continue

                # 3. 45분 횡보 타임컷 (자금 묶임 방지)
                if held_time >= STAGNANT_SEC:
                    if profit > 0:
                        sell_coin(ticker, "🥱 [횡보 약수익 탈출]", current_price, buy_price)
                    else:
                        sell_coin(ticker, "⏰ [횡보 타임컷 손절]", current_price, buy_price)
                    continue
                
                time.sleep(0.1)

            # =================
            # 💡 기회 포착 매수
            # =================
            for ticker in top_coins:
                if ticker in positions or ticker in blacklist:
                    continue

                if len(positions) >= MAX_POSITIONS:
                    break

                if check_buy_signal(ticker):
                    if krw < MIN_ORDER * FEE_RATE:
                        continue

                    buy_amount = krw / (MAX_POSITIONS - len(positions))

                    if buy_amount < MIN_ORDER:
                        continue

                    buy_coin(ticker, buy_amount)
                    krw -= (buy_amount * FEE_RATE)
                    time.sleep(1)
                    
                time.sleep(0.1)

            time.sleep(3)

        except Exception as e:
            print(f"MAIN ERROR: {e}")
            time.sleep(5)

# =========================
# 실행부
# =========================
if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    main()
