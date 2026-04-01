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
# 2. 매매 설정값
# =========================
TOP_N = 15
MAX_POSITIONS = 15

TAKE_PROFIT = 0.03      # +3.0% (저항선 뚫고 폭등할 때의 잭팟)
STOP_LOSS = -0.015      # -1.5% (눌림목이 아니라 지하실일 경우 빠른 손절)
STAGNANT_SEC = 3600     # 1시간 내 횡보 탈출

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
        self.wfile.write("🚀 쿠퍼춘봉봇 V11 유튜브 고수 에디션 가동중!".encode('utf-8'))

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
        
        msg = f"🐶 [쿠퍼춘봉 V11 유튜브 고수타점 브리핑]\n"
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
# 5. 종목 스캔 & 💡 쇼츠 영상 매수 타점 완벽 이식
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
    영상 속 3조건: 횡보 후 눌림 -> 거래량 터짐 -> 호가창 매도벽 사라짐
    """
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=40)
        if df is None or len(df) < 30: return "NONE", 0.0

        # --- 💡 조건 1: 주가가 횡보하다 살짝 눌린 자리 ---
        # 최근 15봉(약 1시간 15분) 동안의 최고-최저 변동폭이 3% 이내면 '횡보'로 간주
        recent_df = df.iloc[-16:-1]
        max_p = recent_df['high'].max()
        min_p = recent_df['low'].min()
        is_sideways = (max_p - min_p) / min_p < 0.03
        
        cur_close = df['close'].iloc[-1]
        cur_open = df['open'].iloc[-1]
        cur_low = df['low'].iloc[-1]
        ma20 = df['close'].rolling(20).mean().iloc[-1]
        
        # 저가가 20일선(또는 살짝 아래)을 터치하고 양봉으로 고개를 드는(눌림목 반등) 자리
        is_dip_and_bounce = (cur_low <= ma20 * 1.002) and (cur_close > cur_open)
        
        # --- 💡 조건 2: 거래량이 줄어들었다가 다시 터지는 순간 ---
        vol_ma10 = df['volume'].rolling(10).mean().iloc[-2] # 10주기 평균 거래량
        recent_vol_avg = df['volume'].iloc[-6:-2].mean()    # 최근 거래량 (말라있었는지 확인)
        cur_vol = df['volume'].iloc[-1]
        prev_vol = df['volume'].iloc[-2]
        
        is_vol_dried = recent_vol_avg < vol_ma10 # 거래량이 죽어있었다
        # 현재 혹은 직전 캔들의 거래량이 평균 대비 2배 이상 펌핑됨
        is_vol_spiked = (cur_vol > vol_ma10 * 2.0) or (prev_vol > vol_ma10 * 2.0)

        # 차트 조건 통과 여부 1차 확인 (통과한 놈만 호가창을 봐서 API 낭비 방지)
        if not (is_sideways and is_dip_and_bounce and is_vol_dried and is_vol_spiked):
            return "NONE", 0.0

        # --- 💡 조건 3: 호가창에 매도 물량이 갑자기 빠질 때 ---
        orderbook = pyupbit.get_orderbook(ticker)
        if orderbook is None: return "NONE", 0.0
        
        # 리스트로 반환될 경우 처리
        if isinstance(orderbook, list): orderbook = orderbook[0]
            
        total_ask = orderbook.get('total_ask_size', 1) # 팔려는 물량 (매도벽)
        total_bid = orderbook.get('total_bid_size', 0) # 사려는 물량 (매수벽)
        
        # 사려는 물량이 팔려는 물량보다 1.5배 이상 많음 (매도벽이 얇아져서 치고 올라가기 좋음)
        is_orderbook_good = total_bid > (total_ask * 1.5)

        if is_orderbook_good:
            return "STRONG", 0.20  # 3조건 모두 충족! 20% 비중으로 강력 매수!
                
        return "NONE", 0.0
    except: return "NONE", 0.0

# =========================
# 6. 장부 관리 및 실행 (5,000원 필터 유지)
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
        
        send_msg(f"🎯 [유튜브 고수 타점] {ticker} 진입!\n(횡보 후 눌림 + 거래량 폭발 + 매도벽 증발)")
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
# 7. 메인 루프
# =========================
def main():
    start_msg = f"🚀 V11 유튜브 고수 에디션 시작! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 3가지 조건 (눌림목, 거래량 펌핑, 호가창 분석) 완벽 탑재!"
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
            # 💡 매도 (차트도사 동적 저항 엑시트 유지)
            # ======================
            for ticker in holding_tickers:
                df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)
                if df is None or len(df) < 20: continue
                
                current_price = df['close'].iloc[-1]
                buy_price = positions[ticker]['buy_price']
                profit = (current_price - buy_price) / buy_price
                held_time = now - positions[ticker]['buy_time']

                df['ma20'] = df['close'].rolling(20).mean()
                ma20_cur = df['ma20'].iloc[-1]

                # 1. 동적 저항 익절 (20일선 저항 터치 시 먹튀)
                if (current_price >= ma20_cur) and (profit >= 0.005):
                    sell_coin(ticker, "🛡️ [20일선 저항 얄미운 먹튀]", current_price, buy_price)
                    continue

                # 2. 잭팟 목표가 (+3.0%) 
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "🎯 [잭팟! 오버슈팅 익절]", current_price, buy_price)
                    continue

                # 3. 칼손절 (-1.5%)
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ [눌림목 지지 실패 손절]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue

                # 4. 횡보 타임컷 단축 (1시간 경과)
                if held_time >= STAGNANT_SEC:
                    if profit > 0:
                        sell_coin(ticker, "🥱 [1시간 횡보 약수익 탈출]", current_price, buy_price)
                    else:
                        sell_coin(ticker, "⏰ [1시간 횡보 타임컷 손절]", current_price, buy_price)

            # ======================
            # 매수 (유튜브 쇼츠 3박자 타점)
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
