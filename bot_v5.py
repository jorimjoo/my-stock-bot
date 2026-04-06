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

# 시스템 경로 유지
IMAGEMAGICK_BINARY = r"C:\Program Files\ImageMagick-7.1.2-Q16"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 럭키세븐 스윙 & 로켓 설정값
# =========================
TOP_N = 20              
MAX_POSITIONS = 7       

# 매도 시나리오
TRAILING_ACTIVATE = 0.05 
TRAILING_DROP = 0.02     
HARD_STOP_LOSS = -0.03   

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
        self.wfile.write("🚀 춘봉봇 V13 투트랙 에디션 가동중!".encode('utf-8'))

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
        
        msg = f"🐶 [쿠퍼춘봉 V13 투트랙 엔진 브리핑]\n"
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
# 5. 종목 스캔 (거래대금 + 급등 위주)
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
            data.append({
                'market': item['market'], 
                'volume': item['acc_trade_price_24h'],
                'change_rate': item['signed_change_rate'] 
            })

        data.sort(key=lambda x: x['volume'], reverse=True)
        top_50_vol = data[:50]

        top_50_vol.sort(key=lambda x: x['change_rate'], reverse=True)
        return [x['market'] for x in top_50_vol[:TOP_N]]
    except: return []

# =========================
# 6. 💡 투트랙 매수 신호 (로켓 엔진 + 스윙 엔진)
# =========================
def check_buy_signal(ticker):
    """반환값: (진입여부, 일봉20일선, 진입타입)"""
    try:
        # --- 🚀 [트랙 1] 로켓 돌파 엔진 (레드스톤 잡기!) ---
        df_5m = pyupbit.get_ohlcv(ticker, interval="minute5", count=20)
        if df_5m is not None and len(df_5m) >= 15:
            # 최근 10봉 평균 거래량 대비 3배 이상 폭발 & 1.5% 이상 수직 장대양봉
            vol_avg = df_5m['volume'].iloc[-12:-2].mean()
            cur_vol = df_5m['volume'].iloc[-1]
            cur_close = df_5m['close'].iloc[-1]
            cur_open = df_5m['open'].iloc[-1]
            
            cond_vol_spike = cur_vol > (vol_avg * 3.0)
            cond_strong_bull = (cur_close - cur_open) / cur_open > 0.015 
            
            if cond_vol_spike and cond_strong_bull:
                return True, 0, "🚀로켓돌파" # 일봉 무시하고 즉시 탑승!

        # --- 🍀 [트랙 2] 스윙 눌림목 엔진 (기존 안전 장치) ---
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=30)
        if df_day is None or len(df_day) < 5: return False, 0, "NONE"
        
        # 25일 데이터가 없어도 에러 안 내고 현재 있는 데이터로 평균 계산
        if len(df_day) >= 20:
            day_ma20 = df_day['close'].rolling(20).mean().iloc[-1]
        else:
            day_ma20 = df_day['close'].mean() 
            
        day_close = df_day['close'].iloc[-1]
        if day_close < day_ma20: return False, 0, "NONE"
            
        df_15m = pyupbit.get_ohlcv(ticker, interval="minute15", count=30)
        if df_15m is None or len(df_15m) < 25: return False, 0, "NONE"
        
        df_15m['ma20'] = df_15m['close'].rolling(20).mean()
        
        cur_close_15 = df_15m['close'].iloc[-1]
        cur_open_15 = df_15m['open'].iloc[-1]
        cur_low_15 = df_15m['low'].iloc[-1]
        ma20_15m = df_15m['ma20'].iloc[-1]
        
        is_dipped = cur_low_15 <= (ma20_15m * 1.012) 
        is_bouncing = cur_close_15 > cur_open_15
        
        if is_dipped and is_bouncing:
            return True, day_ma20, "🍀눌림목"
            
        return False, 0, "NONE"
    except: return False, 0, "NONE"

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
                positions[ticker] = {'buy_price': avg_price, 'peak_price': avg_price, 'day_ma20': 0}
                
    for t in list(positions.keys()):
        if t not in active_tickers:
            positions.pop(t, None)
            
    return krw_bal

def buy_coin(ticker, krw_unit, day_ma20, buy_type):
    try:
        res = upbit.buy_market_order(ticker, krw_unit)
        if res is None or 'error' in res: return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'peak_price': current_price,
            'day_ma20': day_ma20
        }
        
        if buy_type == "🚀로켓돌파":
            send_msg(f"🔥 [{buy_type} 탑승] {ticker}\n(미친 거래량 폭발! 수직 상승 따라붙습니다!)")
        else:
            send_msg(f"✅ [{buy_type} 스윙] {ticker}\n(우상향 중 얕은 눌림목 포착)")
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
    # 💡 과거 지시사항에 따라 시작 메시지는 항상 고정
    start_msg = "== 업비트 봇 가동 시작 =="
    send_msg(start_msg)
    send_msg("🚀 V13 투트랙(로켓돌파 + 눌림목 스윙) 엔진 작동 준비 완료!")
    
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
            blacklist = {k: v for k, v in blacklist.items() if now - v < 3600} 

            if now - last_top_coins_time > 300: 
                top_coins = get_top_coins(); last_top_coins_time = now

            holding_tickers = list(positions.keys())
            
            # 매도 로직
            for ticker in holding_tickers:
                current_price = pyupbit.get_current_price(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                profit = (current_price - buy_price) / buy_price
                
                if current_price > pos['peak_price']: pos['peak_price'] = current_price
                drop_from_peak = (pos['peak_price'] - current_price) / pos['peak_price']

                # 일봉 붕괴 판단용
                df_day = pyupbit.get_ohlcv(ticker, interval="day", count=25)
                if df_day is not None and len(df_day) >= 20:
                    current_day_ma20 = df_day['close'].rolling(20).mean().iloc[-1]
                else:
                    current_day_ma20 = pos['day_ma20'] if pos['day_ma20'] > 0 else 0

                if profit >= TRAILING_ACTIVATE and drop_from_peak >= TRAILING_DROP:
                    sell_coin(ticker, "💸 [고점 익절]", current_price, buy_price); continue
                
                # 로켓으로 잡은 애들은 day_ma20이 0일 수 있으므로 예외 처리
                if profit < 0 and current_day_ma20 > 0 and current_price < current_day_ma20:
                    sell_coin(ticker, "✂️ [일봉 붕괴 컷]", current_price, buy_price); continue
                    
                if profit <= HARD_STOP_LOSS:
                    sell_coin(ticker, "☠️ [-3% 방어선 손절]", current_price, buy_price); continue

                time.sleep(0.1)

            # 투트랙 매수
            for ticker in top_coins:
                if ticker in positions or ticker in blacklist: continue
                if len(positions) >= MAX_POSITIONS: break

                is_buy, day_ma20, buy_type = check_buy_signal(ticker)
                if is_buy:
                    buy_unit = max(krw * 0.14, MIN_ORDER)
                    if krw >= (buy_unit * FEE_RATE) and buy_unit >= MIN_ORDER:
                        buy_coin(ticker, buy_unit, day_ma20, buy_type)
                        krw -= (buy_unit * FEE_RATE)
                        time.sleep(0.5)
                time.sleep(0.1) 

            time.sleep(3)
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
