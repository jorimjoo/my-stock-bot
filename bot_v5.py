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
# 2. 설정값
# =========================
TOP_N = 15
MAX_POSITIONS = 15
STOP_LOSS = -0.03       
TAKE_PROFIT = 0.05      
TRAILING_STOP = 0.02    
TRAILING_START = 0.015  

BLACKLIST_HOLD = 1800   

MIN_ORDER = 6000        
REPORT_INTERVAL = 3600  
FEE_RATE = 1.0005       

blacklist = {}
entry_price = {}
peak_price = {}

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
        
        msg = f"🐶 [쿠퍼춘봉 V7.1 불장 탑승 에디션 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n\n"
        msg += f"📈 [전체 누적 통계]\n- 수익: {bot_stats['profit_krw']:,.0f}원\n- 거래: {bot_stats['trades']}회 (승률 {win_rate:.1f}%)"
        
        send_msg(msg)
    except Exception as e:
        print(f"브리핑 에러: {e}")

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
                                    peak_price.pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량 시장가 매도!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패] 에러: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

# =========================
# 5. 핵심 지표 계산
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
    except Exception as e:
        print(f"TOP 코인 스캔 에러: {e}")
        return []

# =========================
# 매수 조건 (🔥 불장 맞춤형 대폭 완화)
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)
        if df is None or len(df) < 20: 
            return False

        current_price = df['close'].iloc[-1]
        ma5 = get_ma(df, 5)
        ma20 = get_ma(df, 20)
        rsi = ta_rsi(df['close']).iloc[-1]

        # 1. 💡 상승 추세 (가격이 5일선, 20일선 위에만 있으면 OK)
        cond1 = current_price > ma5 and current_price > ma20
        
        # 2. 양봉 유지 (현재 캔들 기준)
        cond2 = df['close'].iloc[-1] > df['open'].iloc[-1]
        
        # 3. 💡 거래량 조건 완화 (이전 봉 대비 10% 상승만 해도 수급 인정)
        vol1 = df['volume'].iloc[-2]
        vol2 = df['volume'].iloc[-3]
        cond3 = vol1 > (vol2 * 1.1)
        
        # 4. 💡 RSI 과열 기준 완화 (70 미만 -> 80 미만으로 올려서 불장 탑승)
        cond4 = rsi < 80

        return cond1 and cond2 and cond3 and cond4
    except Exception as e:
        print(f"[{ticker}] 매수 조건 체크 중 에러 발생: {e}")
        return False

# =========================
# 보유 코인 조회
# =========================
def get_balances():
    balances = upbit.get_balances()
    result = {}

    if isinstance(balances, list):
        for b in balances:
            if b['currency'] == 'KRW':
                result['KRW'] = float(b['balance'])
            else:
                ticker = f"KRW-{b['currency']}"
                amount = float(b['balance']) * float(b['avg_buy_price'])
                if amount > 5000:
                    result[ticker] = {
                        'avg_buy_price': float(b['avg_buy_price']),
                        'balance': float(b['balance'])
                    }
                    if ticker not in peak_price:
                        peak_price[ticker] = float(b['avg_buy_price'])
    return result

# =========================
# 매수/매도 실행
# =========================
def buy_coin(ticker, krw):
    try:
        res = upbit.buy_market_order(ticker, krw)
        if res is None or 'error' in res:
            send_msg(f"❌ [매수 거절] {ticker}: {res}")
            return
            
        peak_price[ticker] = pyupbit.get_current_price(ticker)
        send_msg(f"🔥 [신규 매수] {ticker} 완료! (불장 탑승)")
    except Exception as e:
        send_msg(f"❌ [매수 에러] {ticker}: {e}")

def sell_coin(ticker, reason, current_price, buy_price, balance_amt):
    try:
        profit_rate = (current_price - buy_price) / buy_price * 100
        krw_profit = (current_price - buy_price) * balance_amt
        
        actual_balance = upbit.get_balance(ticker)
        res = upbit.sell_market_order(ticker, actual_balance)
        
        if res is None or 'error' in res:
            send_msg(f"🚨 [긴급] {ticker} 매도 실패! (직접 확인 요망)\n사유: {res}")
            return

        peak_price.pop(ticker, None)
        
        bot_stats["trades"] += 1
        bot_stats["profit_krw"] += krw_profit
        if krw_profit > 0: bot_stats["wins"] += 1
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 수익: {krw_profit:,.0f}원")
    except Exception as e:
        send_msg(f"🚨 [시스템 에러] {ticker} 매도 중 에러 발생: {e}")

# =========================
# 메인 루프
# =========================
def main():
    start_msg = f"🚀 V7.1 불장 탑승 에디션 가동! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ RSI 제한 80으로 완화, 수급 조건 대폭 완화!"
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

            balances = get_balances()
            krw = balances.get("KRW", 0)
            blacklist = {k: v for k, v in blacklist.items() if now - v < BLACKLIST_HOLD}

            if now - last_top_coins_time > 300:
                top_coins = get_top_coins()
                last_top_coins_time = now

            holding_tickers = [k for k in balances.keys() if k != "KRW"]
            current_prices = {}
            if holding_tickers:
                res_prices = pyupbit.get_current_price(holding_tickers)
                if isinstance(res_prices, float) or isinstance(res_prices, int): 
                    current_prices = {holding_tickers[0]: float(res_prices)}
                elif isinstance(res_prices, dict): 
                    current_prices = res_prices

            # ======================
            # 매도 로직
            # ======================
            for ticker, data in list(balances.items()):
                if ticker == "KRW": continue

                current_price = current_prices.get(ticker)
                if current_price is None: continue
                
                buy_price = data['avg_buy_price']
                balance_amt = data['balance']
                profit = (current_price - buy_price) / buy_price

                if ticker in peak_price:
                    peak_price[ticker] = max(peak_price[ticker], current_price)
                else:
                    peak_price[ticker] = current_price

                # 1. 손절 (-3%)
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "😭 [칼손절]", current_price, buy_price, balance_amt)
                    blacklist[ticker] = now
                    continue

                # 2. 익절 (+5%)
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "✅ [목표가 익절]", current_price, buy_price, balance_amt)
                    continue

                # 3. 트레일링 스탑
                if profit >= TRAILING_START:
                    drop = (peak_price[ticker] - current_price) / peak_price[ticker]
                    if drop >= TRAILING_STOP:
                        sell_coin(ticker, "🚀 [트레일링 익절]", current_price, buy_price, balance_amt)
                        continue

            # ======================
            # 매수 로직
            # ======================
            holding_count = len(holding_tickers)
            
            for ticker in top_coins:
                if ticker in balances: continue
                if ticker in blacklist: continue
                if holding_count >= MAX_POSITIONS: break

                if check_buy_signal(ticker):
                    buy_amount = max(krw * 0.2, MIN_ORDER)
                    
                    if krw >= (buy_amount * FEE_RATE) and buy_amount >= MIN_ORDER:
                        buy_coin(ticker, buy_amount)
                        krw -= (buy_amount * FEE_RATE)
                        holding_count += 1
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
