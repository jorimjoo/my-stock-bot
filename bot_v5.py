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

# 향후 시스템 확장을 위한 프로그램 경로 세팅
IMAGEMAGICK_BINARY = r"C:\Program Files\ImageMagick-7.1.2-Q16"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 매매 설정값 (바닥 줍줍 전략)
# =========================
TOP_N = 15
MAX_POSITIONS = 15

TAKE_PROFIT = 0.025     # +2.5% 반등 목표가 (고무줄 스냅백 효과)
STOP_LOSS = -0.025      # -2.5% 지하실 붕괴 시 손절
STAGNANT_SEC = 7200     # 2시간(7200초) 내 반등 없으면 탈출

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
        self.wfile.write("🚀 쿠퍼춘봉봇 V9 바닥줍줍 에디션 가동중!".encode('utf-8'))

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
        
        msg = f"🐶 [쿠퍼춘봉 V9 바닥줍줍 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n"
        msg += f"🎯 관리 중인 유효 종목: {len(positions)}/{MAX_POSITIONS}개\n\n"
        msg += f"📈 [전체 누적 통계]\n- 수익: {bot_stats['profit_krw']:,.0f}원\n- 거래: {bot_stats['trades']}회 (승률 {win_rate:.1f}%)"
        
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

                    elif "/매수테스트" in msg_text:
                        send_msg("🔥 강제 매수 테스트! 비트코인 6,000원어치 매수를 시도합니다!")
                        try:
                            if upbit.get_balance("KRW") > 6000:
                                upbit.buy_market_order("KRW-BTC", 6000)
                                send_msg("✅ [성공] 6,000원어치 매수 체결 완료!")
                            else:
                                send_msg("❌ 현금이 부족합니다.")
                        except Exception as e:
                            send_msg(f"❌ 매수 실패: {e}")

                    elif "매도" in msg_text:
                        ticker_raw = msg_text.replace("매도", "").replace("/", "").strip()
                        if ticker_raw:
                            ticker = ticker_raw if ticker_raw.startswith("KRW-") else f"KRW-{ticker_raw}"
                            balance = upbit.get_balance(ticker)
                            if balance and balance > 0:
                                try:
                                    upbit.sell_market_order(ticker, balance)
                                    positions.pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량 처분!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패]: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

# =========================
# 5. 바닥 탐지 지표: 볼린저 밴드 & RSI
# =========================
def get_bollinger_bands(df, window=20, num_std=2):
    rolling_mean = df['close'].rolling(window=window).mean()
    rolling_std = df['close'].rolling(window=window).std()
    lower_band = rolling_mean - (rolling_std * num_std)
    return rolling_mean, lower_band

def ta_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

# =========================
# 6. 종목 스캔 & 💡 바닥 줍줍 매수 신호
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
    반환값: (상태, 비중) - 바닥을 찍고 반등하는 종목만 포착
    """
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=40)
        if df is None or len(df) < 30: return "NONE", 0.0

        df['ma20'], df['lower_band'] = get_bollinger_bands(df)
        df['rsi'] = ta_rsi(df['close'])
        
        cur_close = df['close'].iloc[-1]
        cur_open = df['open'].iloc[-1]
        cur_low = df['low'].iloc[-1]
        
        prev_close = df['close'].iloc[-2]
        prev_open = df['open'].iloc[-2]

        lower_band = df['lower_band'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        rsi_prev = df['rsi'].iloc[-2]

        # 1. 💡 공포의 바닥 진입: RSI가 35 미만으로 심하게 과매도 되었는가?
        cond_rsi_oversold = rsi_prev < 35 or rsi_val < 35
        
        # 2. 💡 밴드 하단 터치: 가격이 볼린저 밴드 하단(한계선)을 터치하거나 그 아래에 있는가?
        cond_band_touch = cur_low <= lower_band or prev_close <= lower_band
        
        # 3. 💡 반등 시작 확인: 직전 캔들은 파란 불(음봉)이었는데, 지금은 빨간 불(양봉)로 고개를 드는가?
        cond_bounce = (prev_close < prev_open) and (cur_close > cur_open)

        # 3가지가 모두 맞으면 "남들이 버린 헐값 코인이 고개를 든다"고 판단!
        if cond_rsi_oversold and cond_band_touch and cond_bounce:
            return "NORMAL", 0.15  # 비중 15% 진입
                
        return "NONE", 0.0
    except: return "NONE", 0.0

# =========================
# 7. 장부 관리 및 실행 (5,000원 필터 유지)
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
                    'buy_time': time.time()
                }
                
    for t in list(positions.keys()):
        if t not in active_tickers:
            positions.pop(t, None)
            
    return krw_bal

def buy_coin(ticker, krw, signal_type):
    try:
        res = upbit.buy_market_order(ticker, krw)
        if res is None or 'error' in res: return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'buy_time': time.time()
        }
        
        send_msg(f"🎯 [바닥 줍줍] {ticker} 진입! (볼린저 하단 + RSI 과매도 반등)")
    except Exception as e:
        pass

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
    except Exception as e:
        pass

# =========================
# 8. 메인 루프
# =========================
def main():
    start_msg = f"🚀 V9 바닥 줍줍 에디션 시작! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 폭락장 전용 방패 장착! 볼린저 밴드 하단과 RSI 낙폭 과대를 노립니다!"
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
            blacklist = {k: v for k, v in blacklist.items() if now - v < 1800}

            if now - last_top_coins_time > 180: 
                top_coins = get_top_coins()
                last_top_coins_time = now

            holding_tickers = list(positions.keys())
            
            # ======================
            # 스마트 매도 로직
            # ======================
            for ticker in holding_tickers:
                df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)
                if df is None or len(df) < 20: continue
                
                current_price = df['close'].iloc[-1]
                buy_price = positions[ticker]['buy_price']
                profit = (current_price - buy_price) / buy_price
                held_time = now - positions[ticker]['buy_time']

                # 1. 지하실 붕괴 손절 (-2.5%)
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ [-2.5% 바닥 붕괴 손절]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue

                # 2. 반등 목표가 익절 (+2.5%)
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "🎯 [반등 목표가 익절]", current_price, buy_price)
                    continue

                # 3. 횡보 타임컷 (2시간 경과)
                if held_time >= STAGNANT_SEC:
                    if profit > 0:
                        sell_coin(ticker, "🥱 [2시간 횡보 약익절 탈출]", current_price, buy_price)
                    else:
                        sell_coin(ticker, "⏰ [2시간 횡보 타임컷 손절]", current_price, buy_price)

            # ======================
            # 바닥 줍줍 매수 로직
            # ======================
            for ticker in top_coins:
                if ticker in positions or ticker in blacklist: continue
                if len(positions) >= MAX_POSITIONS: break

                signal, weight = check_buy_signal(ticker)
                
                if signal != "NONE":
                    buy_amount = max(krw * weight, MIN_ORDER)
                    
                    if krw >= (buy_amount * FEE_RATE) and buy_amount >= MIN_ORDER:
                        buy_coin(ticker, buy_amount, signal)
                        krw -= (buy_amount * FEE_RATE)
                        time.sleep(0.5)

                time.sleep(0.2) 

            time.sleep(2)

        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
