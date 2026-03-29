import pyupbit
import pandas as pd
import time
import datetime
import requests
import os
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer 

# =========================
# 1. API KEY (춘봉님 정보 연동 완료)
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 설정값 (가져오신 값 그대로 유지)
# =========================
TOP_N = 15
MAX_POSITIONS = 15
STOP_LOSS = -0.03       # -3% 칼손절
TAKE_PROFIT = 0.05      # +5% 1차 익절
TRAILING_STOP = 0.02    # 고점 대비 2% 하락 시 트레일링 스탑
BLACKLIST_HOLD = 1800   # 30분 (손절 후 쿨타임)

MIN_ORDER = 6000        # 최소 주문 금액 보장 (업비트 에러 방지)
REPORT_INTERVAL = 3600  # 1시간 브리핑 간격

blacklist = {}
entry_price = {}
peak_price = {}

# 통계 및 브리핑용 데이터
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
# 3. 🌐 웹 서버 (클라우드 기절 방지용)
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
        
        msg = f"🐶 [쿠퍼춘봉 ChatGPT 에디션 브리핑]\n"
        msg += f"⌚ 봇 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
        msg += f"📈 [전체 누적 통계]\n- 누적 수익: {bot_stats['profit_krw']:,.0f}원\n- 매매 횟수: {bot_stats['trades']}회 (승률 {win_rate:.1f}%)"
        
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
                                    entry_price.pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량 시장가 매도!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패] 에러: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

# =========================
# 5. 거래대금 TOP 코인 (💡 API 초과 에러 방지용 최적화)
# =========================
def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        all_data = []
        # 코인 110개를 한 번에 차트로 부르면 서버가 다운되므로, 배치 통신으로 변경했습니다!
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
        print("TOP 코인 스캔 에러:", e)
        return []

# =========================
# MA 계산
# =========================
def get_ma(df, period):
    return df['close'].rolling(period).mean().iloc[-1]

# =========================
# 매수 조건 (가져오신 조건 그대로 적용)
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)

        if df is None or len(df) < 20:
            return False

        current_price = df['close'].iloc[-1]
        ma5 = get_ma(df, 5)
        ma20 = get_ma(df, 20)

        # 1. 정배열
        cond1 = current_price > ma5 > ma20

        # 2. 양봉 (진행 중인 캔들 기준)
        cond2 = df['close'].iloc[-1] > df['open'].iloc[-1]

        # 3. 거래량 증가 (직전 완성 캔들 vs 그 전 완성 캔들)
        vol1 = df['volume'].iloc[-2]
        vol2 = df['volume'].iloc[-3]
        cond3 = vol1 > vol2

        return cond1 and cond2 and cond3
    except:
        return False

# =========================
# 보유 코인 조회 및 동기화
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
                    result[ticker] = float(b['avg_buy_price'])
                    # 봇 재시작 시 기존 종목 최고가 동기화
                    if ticker not in peak_price:
                        peak_price[ticker] = float(b['avg_buy_price'])
                        entry_price[ticker] = float(b['avg_buy_price'])
    return result

# =========================
# 매수
# =========================
def buy_coin(ticker, krw):
    try:
        upbit.buy_market_order(ticker, krw)
        entry_price[ticker] = pyupbit.get_current_price(ticker)
        peak_price[ticker] = entry_price[ticker]
        send_msg(f"🔥 [신규 진입] {ticker} 매수 완료! (정배열 & 거래량 상승)")
    except Exception as e:
        print("매수 실패:", e)

# =========================
# 매도
# =========================
def sell_coin(ticker, reason, current_price, buy_price, balance_amt):
    try:
        profit_rate = (current_price - buy_price) / buy_price * 100
        krw_profit = (current_price - buy_price) * balance_amt
        
        balance = upbit.get_balance(ticker)
        upbit.sell_market_order(ticker, balance)
        
        peak_price.pop(ticker, None)
        entry_price.pop(ticker, None)
        
        bot_stats["trades"] += 1
        bot_stats["profit_krw"] += krw_profit
        if krw_profit > 0: bot_stats["wins"] += 1
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 수익: {krw_profit:,.0f}원")
    except Exception as e:
        print("매도 실패:", e)

# =========================
# 메인 루프
# =========================
def main():
    print(f"[{get_kst().strftime('%H:%M:%S')}] 🚀 춘봉봇 시스템 초기화 시작...")
    
    start_msg = f"🚀 ChatGPT 합작 오리지널 봇 가동! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 브리핑 & 매도 리모컨 활성화 완료!"
    send_msg(start_msg)
    
    bot_stats["last_report_time"] = time.time()
    last_top_coins_time = 0
    top_coins = []
    
    global blacklist

    while True:
        try:
            now = time.time()
            
            # 1시간 브리핑 체크
            if now - bot_stats["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_stats["last_report_time"] = now

            balances = get_balances()
            krw = balances.get("KRW", 0)

            # 블랙리스트 정리
            blacklist = {k: v for k, v in blacklist.items() if now - v < BLACKLIST_HOLD}

            # TOP 코인 5분 단위 갱신 (API 최적화)
            if now - last_top_coins_time > 300:
                top_coins = get_top_coins()
                last_top_coins_time = now

            # ======================
            # 매수 로직
            # ======================
            # MAX_POSITIONS 기준은 KRW를 제외한 코인 개수
            holding_count = len([k for k in balances.keys() if k != "KRW"])
            
            for ticker in top_coins:
                if ticker in balances: continue
                if ticker in blacklist: continue
                if holding_count >= MAX_POSITIONS: break

                if check_buy_signal(ticker):
                    buy_amount = max(krw * 0.2, MIN_ORDER)
                    # 잔고가 매수금액보다 많고, 최소 주문금액을 넘길 때만
                    if krw >= buy_amount and buy_amount >= MIN_ORDER:
                        buy_coin(ticker, buy_amount)
                        krw -= buy_amount # 연속 매수 시 잔고 차감 반영
                        holding_count += 1
                        time.sleep(0.5)

            # ======================
            # 매도 로직
            # ======================
            for ticker in list(balances.keys()):
                if ticker == "KRW": continue

                current_price = pyupbit.get_current_price(ticker)
                if current_price is None: continue
                
                buy_price = balances[ticker]
                balance_amt = upbit.get_balance(ticker) # 실제 보유 수량
                
                profit = (current_price - buy_price) / buy_price

                # 최고가 갱신
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

                # 3. 트레일링 스탑 (최고가 대비 -2% 하락 시)
                if ticker in peak_price:
                    drop = (peak_price[ticker] - current_price) / peak_price[ticker]
                    if drop >= TRAILING_STOP:
                        sell_coin(ticker, "🚀 [트레일링 익절/손절]", current_price, buy_price, balance_amt)
                        continue

            time.sleep(2) # 5초에서 2초로 줄여 반응속도 향상

        except Exception as e:
            print("에러:", e)
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
