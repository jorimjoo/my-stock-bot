import pyupbit
import pandas as pd
import time
import datetime
import requests
import os
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer 

# =========================
# 1. API KEY
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 설정값 (💡 매도 조건 전면 개편)
# =========================
TOP_N = 15
MAX_POSITIONS = 15

# 💡 스마트 매도 4원칙 셋팅
TAKE_PROFIT = 0.05      # 1. 목표가 익절 (+5%)
TRAILING_START = 0.015  # 2. 트레일링 감시 시작 (+1.5%)
TRAILING_DROP = 0.005   # 2. 고점 대비 하락 시 익절 (-0.5%) -> 최소 1% 수익 보장
STOP_LOSS = -0.015      # 3. 5분봉 하락 추세 꺾임 시 조기 칼손절 (-1.5%)
STAGNANT_SEC = 7200     # 4. 횡보 시간 초과 (2시간 = 7200초)

BLACKLIST_HOLD = 1800   

MIN_ORDER = 6000        
REPORT_INTERVAL = 3600  
FEE_RATE = 1.0005       

blacklist = {}
positions = {} # 💡 매수 시간과 최고점 관리를 위한 통합 장부

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
        self.wfile.write("🚀 춘봉봇 정상 가동중!".encode('utf-8'))

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
        
        msg = f"🐶 [쿠퍼춘봉 V7.2 스마트 엑시트 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n\n"
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

                    elif "매도" in msg_text:
                        ticker_raw = msg_text.replace("매도", "").replace("/", "").strip()
                        if ticker_raw:
                            ticker = ticker_raw if ticker_raw.startswith("KRW-") else f"KRW-{ticker_raw}"
                            balance = upbit.get_balance(ticker)
                            if balance and balance > 0:
                                try:
                                    upbit.sell_market_order(ticker, balance)
                                    positions.pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량 시장가 매도!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패] 에러: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

# =========================
# 5. 지표 및 스캔
# =========================
def ta_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def get_ma(df, period):
    return df['close'].rolling(period).mean().iloc[-1]

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
            volume = item['acc_trade_price_24h']
            data.append((item['market'], volume))

        data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in data[:TOP_N]]
    except: return []

# =========================
# 매수 조건 (불장 맞춤형)
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)
        if df is None or len(df) < 20: return False

        current_price = df['close'].iloc[-1]
        ma5 = get_ma(df, 5)
        ma20 = get_ma(df, 20)
        rsi = ta_rsi(df['close']).iloc[-1]

        cond1 = current_price > ma5 and current_price > ma20
        cond2 = df['close'].iloc[-1] > df['open'].iloc[-1]
        vol1 = df['volume'].iloc[-2]
        vol2 = df['volume'].iloc[-3]
        cond3 = vol1 > (vol2 * 1.1)
        cond4 = rsi < 80

        return cond1 and cond2 and cond3 and cond4
    except: return False

# =========================
# 통합 보유 코인 장부 관리 (💡 시간 및 고점 관리 추가)
# =========================
def sync_balances():
    balances = upbit.get_balances()
    if not isinstance(balances, list): return 0
    
    krw_bal = 0.0
    for b in balances:
        if b['currency'] == 'KRW': 
            krw_bal = float(b['balance'])
            continue
            
        ticker = f"KRW-{b['currency']}"
        avg_price = float(b['avg_buy_price'])
        amt = float(b['balance'])
        
        if amt * avg_price >= 5000:
            if ticker not in positions:
                # 봇 재시작 시 기존 코인들은 장부에 새로 등록 (시간은 현재로 초기화)
                positions[ticker] = {
                    'buy_price': avg_price,
                    'peak_price': avg_price,
                    'buy_time': time.time()
                }
                
    # 업비트에 없는 코인은 내 장부에서도 삭제
    current_holding_tickers = [f"KRW-{b['currency']}" for b in balances if b['currency'] != 'KRW' and float(b['balance']) * float(b['avg_buy_price']) >= 5000]
    for t in list(positions.keys()):
        if t not in current_holding_tickers:
            positions.pop(t, None)
            
    return krw_bal

# =========================
# 매수/매도 실행
# =========================
def buy_coin(ticker, krw):
    try:
        res = upbit.buy_market_order(ticker, krw)
        if res is None or 'error' in res: return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'peak_price': current_price,
            'buy_time': time.time()
        }
        send_msg(f"🔥 [신규 매수] {ticker} 돌파 확인!")
    except Exception as e:
        send_msg(f"❌ [매수 에러] {ticker}: {e}")

def sell_coin(ticker, reason, current_price, buy_price):
    try:
        actual_balance = upbit.get_balance(ticker)
        profit_rate = (current_price - buy_price) / buy_price * 100
        krw_profit = (current_price - buy_price) * actual_balance
        
        res = upbit.sell_market_order(ticker, actual_balance)
        if res is None or 'error' in res:
            send_msg(f"🚨 [매도 실패] {ticker}: {res}")
            return

        positions.pop(ticker, None)
        
        bot_stats["trades"] += 1
        bot_stats["profit_krw"] += krw_profit
        if krw_profit > 0: bot_stats["wins"] += 1
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 수익: {krw_profit:,.0f}원")
    except Exception as e:
        send_msg(f"🚨 [매도 에러] {ticker}: {e}")

# =========================
# 메인 루프
# =========================
def main():
    start_msg = f"🚀 V7.2 스마트 엑시트 에디션 가동! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 4단계 매도 로직(횡보 타임컷, 트레일링 수정, 조기 손절) 탑재 완료!"
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
            blacklist = {k: v for k, v in blacklist.items() if now - v < BLACKLIST_HOLD}

            if now - last_top_coins_time > 300:
                top_coins = get_top_coins()
                last_top_coins_time = now

            holding_tickers = list(positions.keys())
            current_prices = {}
            if holding_tickers:
                res_prices = pyupbit.get_current_price(holding_tickers)
                if isinstance(res_prices, float) or isinstance(res_prices, int): 
                    current_prices = {holding_tickers[0]: float(res_prices)}
                elif isinstance(res_prices, dict): 
                    current_prices = res_prices

            # ======================
            # 💡 스마트 매도 로직
            # ======================
            for ticker in holding_tickers:
                current_price = current_prices.get(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                profit = (current_price - buy_price) / buy_price
                held_time = now - pos['buy_time']

                # 최고가 갱신
                if current_price > pos['peak_price']:
                    pos['peak_price'] = current_price
                
                peak = pos['peak_price']
                drop_from_peak = (peak - current_price) / peak

                # 1. 조기 손절 (5분봉 추세 꺾임 방어)
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "😭 [조기 칼손절]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue

                # 2. 목표가 익절
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "✅ [목표 잭팟 익절]", current_price, buy_price)
                    continue

                # 3. 트레일링 스탑 (수익 굳히기)
                if profit >= TRAILING_START:
                    if drop_from_peak >= TRAILING_DROP:
                        sell_coin(ticker, "🚀 [트레일링 익절]", current_price, buy_price)
                        continue
                
                # 4. 횡보 탈출 (2시간 경과)
                if held_time >= STAGNANT_SEC:
                    if profit > 0.002: # 수수료 방어선 (+0.2% 이상)
                        sell_coin(ticker, "🥱 [횡보 약익절 탈출]", current_price, buy_price)
                    else:
                        sell_coin(ticker, "⏰ [횡보 타임컷 손절]", current_price, buy_price)
                    continue

            # ======================
            # 매수 로직
            # ======================
            for ticker in top_coins:
                if ticker in positions: continue
                if ticker in blacklist: continue
                if len(positions) >= MAX_POSITIONS: break

                if check_buy_signal(ticker):
                    buy_amount = max(krw * 0.2, MIN_ORDER)
                    
                    if krw >= (buy_amount * FEE_RATE) and buy_amount >= MIN_ORDER:
                        buy_coin(ticker, buy_amount)
                        krw -= (buy_amount * FEE_RATE)
                        time.sleep(0.5)

                time.sleep(0.2) 

            time.sleep(3)

        except Exception as e:
            print(f"메인 루프 에러: {e}")
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
