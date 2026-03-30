import pyupbit
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
# 2. 직관적 매매 설정값
# =========================
TOP_N = 15
MAX_POSITIONS = 15      # 💡 여유롭게 15개로 확장 (5,000원 미만 코인은 개수에 안 샘)

# 매도 원칙 (손실은 짧게, 수익은 길게)
TAKE_PROFIT = 0.05      # +5% 잭팟 익절
STOP_LOSS = -0.02       # -2% 칼손절 (절대 원칙)
TRAILING_START = 0.015  # +1.5% 이상 수익 시
TRAILING_DROP = 0.005   # 고점 대비 -0.5% 꺾이면 수익 실현

# 횡보 원칙
STAGNANT_SEC = 3600     # 1시간(3600초) 횡보 시 컷

MIN_ORDER = 6000        
FEE_RATE = 1.0005       
REPORT_INTERVAL = 3600  # 1시간 정기 브리핑

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
        self.wfile.write("🚀 춘봉봇 V8.2 먼지청소판 가동중!".encode('utf-8'))

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# =========================
# 4. 텔레그램 리모컨 & 브리핑
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
        
        msg = f"🐶 [쿠퍼춘봉 V8.2 필터링 브리핑]\n"
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
# 5. 종목 스캔 & 매수 신호 
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
    "STRONG" : 1분봉, 5분봉 모두 폭발적 상승 (비중 크게)
    "NORMAL" : 5분봉 기준 상승 시작 (비중 기본)
    "NONE"   : 매수 금지
    """
    try:
        df5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=25)
        if df5 is None or len(df5) < 20: return "NONE"

        c5 = df5['close'].iloc[-1]
        o5 = df5['open'].iloc[-1]
        ma5_5 = df5['close'].rolling(5).mean().iloc[-1]
        ma20_5 = df5['close'].rolling(20).mean().iloc[-1]
        
        vol_current = df5['volume'].iloc[-2]
        vol_prev = df5['volume'].iloc[-3]
        vol_spike = vol_current > (vol_prev * 1.5)

        if not (c5 > o5 and c5 > ma5_5 > ma20_5 and vol_spike):
            return "NONE"

        df1 = pyupbit.get_ohlcv(ticker, interval="minute1", count=25)
        if df1 is not None and len(df1) >= 20:
            c1 = df1['close'].iloc[-1]
            o1 = df1['open'].iloc[-1]
            ma5_1 = df1['close'].rolling(5).mean().iloc[-1]
            ma20_1 = df1['close'].rolling(20).mean().iloc[-1]
            
            if c1 > o1 and c1 > ma5_1 > ma20_1:
                return "STRONG"
                
        return "NORMAL"
    except: return "NONE"

def is_obvious_loser(ticker, current_price):
    try:
        df5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=25)
        if df5 is None: return False
        
        ma20_5 = df5['close'].rolling(20).mean().iloc[-1]
        if current_price < ma20_5:
            return True
        return False
    except: return False

# =========================
# 6. 장부 관리 및 실행 (💡 5,000원 필터링 완벽 적용)
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
        
        # 💡 [핵심] 매수원금이 5,000원 미만인 코인(자투리, 에어드랍, 거래불가)은 투명인간 취급!
        buy_amount = amt * avg_price
        
        if buy_amount >= 5000:
            active_tickers.append(ticker)
            if ticker not in positions:
                positions[ticker] = {
                    'buy_price': avg_price,
                    'peak_price': avg_price,
                    'buy_time': time.time()
                }
                
    # 장부에는 있는데 현재 5000원 이상 유효 잔고에 없으면 장부에서 깔끔하게 삭제
    for t in list(positions.keys()):
        if t not in active_tickers:
            positions.pop(t, None)
            
    return krw_bal

def buy_coin(ticker, krw, signal_type):
    try:
        res = upbit.buy_market_order(ticker, krw)
        if res is None or 'error' in res: 
            send_msg(f"❌ [매수 에러] {ticker} 주문 거절: {res}")
            return
            
        current_price = pyupbit.get_current_price(ticker)
        positions[ticker] = {
            'buy_price': current_price,
            'peak_price': current_price,
            'buy_time': time.time()
        }
        
        prefix = "🔥🔥 [강력 매수]" if signal_type == "STRONG" else "🔥 [일반 매수]"
        send_msg(f"{prefix} {ticker} 진입! (거래량 급증 및 정배열 돌파)")
    except Exception as e:
        send_msg(f"❌ [시스템 에러] 매수 실패: {e}")

def sell_coin(ticker, reason, current_price, buy_price):
    try:
        actual_balance = upbit.get_balance(ticker)
        profit_rate = (current_price - buy_price) / buy_price * 100
        krw_profit = (current_price - buy_price) * actual_balance
        
        res = upbit.sell_market_order(ticker, actual_balance)
        if res is None or 'error' in res: 
            send_msg(f"🚨 [매도 실패] {ticker} 주문 거절: {res}")
            return

        positions.pop(ticker, None)
        
        bot_stats["trades"] += 1
        bot_stats["profit_krw"] += krw_profit
        if krw_profit > 0: bot_stats["wins"] += 1
        
        send_msg(f"{reason} {ticker} ({profit_rate:+.2f}%) / 손익: {krw_profit:,.0f}원")
    except Exception as e:
        send_msg(f"🚨 [시스템 에러] 매도 실패: {e}")

# =========================
# 7. 메인 루프
# =========================
def main():
    start_msg = f"🚀 V8.2 5천원 필터링 봇 시작! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 거래불가/먼지 코인 완벽 제외! 유효 종목만 관리합니다."
    send_msg(start_msg)
    
    bot_stats["last_report_time"] = time.time()
    last_top_coins_time = 0
    top_coins = []
    
    global blacklist

    while True:
        try:
            now = time.time()
            
            # 브리핑 전송
            if now - bot_stats["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_stats["last_report_time"] = now

            krw = sync_balances()
            blacklist = {k: v for k, v in blacklist.items() if now - v < 1800}

            # 3분마다 종목 스캔
            if now - last_top_coins_time > 180: 
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
            # 스마트 매도 로직
            # ======================
            for ticker in holding_tickers:
                current_price = current_prices.get(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                profit = (current_price - buy_price) / buy_price
                held_time = now - pos['buy_time']

                # 최고가 갱신
                if current_price > pos['peak_price']: pos['peak_price'] = current_price
                drop_from_peak = (pos['peak_price'] - current_price) / pos['peak_price']

                # 1. 절대 원칙: -2.0% 칼손절
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ [-2% 룰 칼손절]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue
                
                # 2. 버리기: -2%가 안 됐어도 20일선을 하향 돌파하며 쏟아지면 컷
                if profit < 0 and is_obvious_loser(ticker, current_price):
                    sell_coin(ticker, "✂️ [추세 이탈 빠른 컷]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue

                # 3. 잭팟 목표가 익절
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "🎯 [목표가 잭팟 익절]", current_price, buy_price)
                    continue

                # 4. 수익 방어 트레일링 스탑
                if profit >= TRAILING_START and drop_from_peak >= TRAILING_DROP:
                    sell_coin(ticker, "🚀 [수익 방어 트레일링]", current_price, buy_price)
                    continue
                
                # 5. 횡보 타임컷 (1시간 경과 시)
                if held_time >= STAGNANT_SEC:
                    if -0.01 < profit < 0.005: 
                        sell_coin(ticker, "🥱 [1시간 횡보 탈출]", current_price, buy_price)
                    continue

            # ======================
            # 직관적 매수 로직
            # ======================
            for ticker in top_coins:
                if ticker in positions or ticker in blacklist: continue
                
                # 💡 유효하게 관리 중인 종목 개수만 비교
                if len(positions) >= MAX_POSITIONS: break

                signal = check_buy_signal(ticker)
                
                if signal != "NONE":
                    weight = 0.20 if signal == "STRONG" else 0.10
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
