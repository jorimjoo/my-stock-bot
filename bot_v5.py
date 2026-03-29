import time
import datetime
import requests
import pyupbit
import json
import os
import gc
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer 

# =========================
# 1. API 설정
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 시스템 설정 (초심 오리지널 셋팅)
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

MAX_POSITIONS = 5           
TOP_N = 15                  

TRAILING_ACTIVATE = 1.0     # 1. 최소 +1.0% 도달 시 트레일링 감시 시작
TRAILING_DROP = 0.5         # 2. 고점 대비 0.5% 하락 시 익절
STOP_LOSS = -3.0            # 3. 총 손절액 -3.0% 칼손절
STAGNANT_HOURS = 2          # 4. 장시간 횡보 기준 (2시간)

REPORT_INTERVAL = 3600      

bot = {
    "positions": {},   
    "blacklist": {},
    "last_report_time": 0,
    "daily_stats": {"trades": 0, "wins": 0, "profit": 0.0, "date": get_kst().date()},
    "total_stats": {"trades": 0, "wins": 0, "profit": 0.0, "start_time": get_kst()}
}

# =========================
# 3. 🌐 내장 웹 서버 (기절 방지)
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
# 4. 텔레그램 리모컨 & 브리핑
# =========================
def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
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
                                send_msg("✅ [성공] 6,000원어치 매수 체결!")
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
                                    bot["positions"].pop(ticker, None)
                                    send_msg(f"⚡ [수동 매도 완료] {ticker} 전량 매도 완료!")
                                except Exception as e:
                                    send_msg(f"❌ [수동 매도 실패] 에러: {e}")
                            else:
                                send_msg(f"⚠ 보유 중인 {ticker} 코인이 없습니다.")
        except: pass 
        time.sleep(2)

def check_daily_reset():
    now_date = get_kst().date()
    if bot["daily_stats"]["date"] != now_date:
        bot["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}

def send_system_briefing():
    try:
        check_daily_reset()
        now = get_kst()
        uptime = now - bot["total_stats"]["start_time"]
        days, seconds = uptime.days, uptime.seconds
        hours = seconds // 3600; minutes = (seconds % 3600) // 60
        
        try: krw_balance = float(upbit.get_balance("KRW") or 0.0)
        except: krw_balance = 0.0
            
        t_stats, d_stats = bot["total_stats"], bot["daily_stats"]
        total_win_rate = (t_stats["wins"] / t_stats["trades"] * 100) if t_stats["trades"] > 0 else 0
        daily_win_rate = (d_stats["wins"] / d_stats["trades"] * 100) if d_stats["trades"] > 0 else 0
        
        msg = f"🐶 [쿠퍼춘봉 V_초심 버그픽스 브리핑]\n"
        msg += f"⌚ 봇 가동: {t_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
        msg += f"📈 [전체 누적 통계]\n- 수익: {t_stats['profit']:,.0f}원\n- 거래: {t_stats['trades']}회 (승률 {total_win_rate:.1f}%)\n\n"
        msg += f"📅 [오늘 하루 통계]\n- 수익: {d_stats['profit']:,.0f}원\n- 거래: {d_stats['trades']}회 (승률 {daily_win_rate:.1f}%)"
        
        send_msg(msg)
    except: pass

def update_statistics(profit_krw, is_win):
    check_daily_reset()
    for key in ["daily_stats", "total_stats"]:
        bot[key]["trades"] += 1; bot[key]["profit"] += profit_krw
        if is_win: bot[key]["wins"] += 1

# =========================
# 5. 핵심 로직: 💡 버그 픽스 완료된 5분봉 매수 타점
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
        
        scored_data = []
        for item in all_data:
            if item['market'] == "KRW-BTC": continue
            val = item['acc_trade_price_24h']
            scored_data.append((item['market'], val))
        scored_data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in scored_data[:TOP_N]]
    except: return []

def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=25)
        if df is None or len(df) < 20: return False

        # 실시간 진행 중인 미완성 캔들
        current_close = df['close'].iloc[-1]
        current_open = df['open'].iloc[-1]
        
        ma5 = df['close'].rolling(5).mean().iloc[-1]
        ma20 = df['close'].rolling(20).mean().iloc[-1]
        
        # 💡 [치명적 버그 수정] 완료된 직전 봉과 그 전 봉의 거래량을 비교
        vol_prev_completed = df['volume'].iloc[-2] # 막 완성된 봉
        vol_older_completed = df['volume'].iloc[-3] # 그 전 완성된 봉

        # 조건 1: 정배열 (현재가가 5일선, 20일선 위)
        is_trend_up = current_close > ma5 and current_close > ma20
        # 조건 2: 현재 실시간 캔들이 양봉 (상승 진행 중)
        is_yangbong = current_close > current_open
        # 조건 3: 직전 완성봉에서 거래량이 증가하며 수급이 들어옴
        is_vol_up = vol_prev_completed > vol_older_completed

        if is_trend_up and is_yangbong and is_vol_up:
            return True
    except: pass
    return False

# =========================
# 6. 매도 로직 (물타기 없이 단판 승부)
# =========================
def sell_logic(ticker, avg_price, current_price, balance, pos):
    profit = (current_price - avg_price) / avg_price * 100

    max_p = pos.get("max_price", avg_price)
    if current_price > max_p:
        pos["max_price"] = current_price
        max_p = current_price

    drop = (max_p - current_price) / max_p * 100
    held_time = time.time() - pos.get("buy_time", time.time())

    # 원칙 1. 트레일링 익절 (+1.0% 이상 수익권 도달 후 0.5% 하락 시)
    if max_p >= avg_price * (1 + (TRAILING_ACTIVATE / 100)):
        if drop >= TRAILING_DROP:
            upbit.sell_market_order(ticker, balance)
            bot["positions"].pop(ticker, None)
            krw_profit = (current_price - avg_price) * balance
            update_statistics(krw_profit, True)
            send_msg(f"🚀 [트레일링 익절] {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
            return

    # 원칙 2. 장시간 횡보 익절 (2시간 초과 & +0.2% 이상)
    if held_time >= (STAGNANT_HOURS * 3600) and profit >= 0.2:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None)
        krw_profit = (current_price - avg_price) * balance
        update_statistics(krw_profit, True)
        send_msg(f"🥱 [2시간 횡보 약익절] {ticker} (+{profit:.2f}%) / 수익: {krw_profit:,.0f}원")
        return

    # 원칙 3. -3% 칼손절
    if profit <= STOP_LOSS:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None)
        bot["blacklist"][ticker] = time.time() + 1800 
        krw_profit = (current_price - avg_price) * balance
        update_statistics(krw_profit, False)
        send_msg(f"😭 [칼손절] {ticker} ({profit:.2f}%) / 손실: {krw_profit:,.0f}원")

# =========================
# 7. 메인 루프 
# =========================
def sync_existing_positions():
    try:
        my_balances = upbit.get_balances()
        count = 0
        if isinstance(my_balances, list):
            for b in my_balances:
                if b['currency'] == 'KRW': continue
                balance = float(b['balance']); avg_buy_price = float(b['avg_buy_price'])
                if balance > 0 and avg_buy_price > 0:
                    bot["positions"][f"KRW-{b['currency']}"] = {
                        "buy_price": avg_buy_price, 
                        "max_price": avg_buy_price, 
                        "buy_time": time.time()
                    }
                    count += 1
        return count
    except: return 0

def main():
    print(f"[{get_kst().strftime('%H:%M:%S')}] 🚀 시스템 초기화 시작...")
    synced_count = sync_existing_positions()
    
    start_msg = f"🚀 V_초심 버그픽스판 가동 시작! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 미완성 캔들 버그 완벽 해결! 진정한 5분봉 돌파 매매 장전!"
    send_msg(start_msg)
    
    bot["last_report_time"] = time.time()
    last_top_coins_time = 0
    buy_tickers = []

    while True:
        try:
            now_ts = time.time()
            if now_ts - bot["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot["last_report_time"] = now_ts

            my_balances = upbit.get_balances()              
            if not isinstance(my_balances, list):
                time.sleep(5)
                continue
                
            balance_dict = {}
            krw_balance = 0.0
            for b in my_balances:
                if b['currency'] == 'KRW': krw_balance = float(b['balance'])
                else:
                    balance_dict[f"KRW-{b['currency']}"] = {"balance": float(b['balance']), "avg_buy_price": float(b['avg_buy_price'])}

            holding_tickers = list(balance_dict.keys())
            
            if now_ts - last_top_coins_time > 300: # 종목 리스트 5분마다 갱신
                buy_tickers = get_top_coins()
                last_top_coins_time = now_ts
            
            all_target_tickers = list(set(holding_tickers + buy_tickers))
            all_prices = {}
            if all_target_tickers:
                res_prices = pyupbit.get_current_price(all_target_tickers)
                if isinstance(res_prices, float) or isinstance(res_prices, int): all_prices = {all_target_tickers[0]: float(res_prices)}
                elif isinstance(res_prices, dict): all_prices = res_prices

            # 파트 A: 매도 감시 (물타기 로직 완전 삭제)
            for ticker in holding_tickers:
                data = balance_dict[ticker]
                balance = data["balance"]; avg_price = data["avg_buy_price"]
                current_price = all_prices.get(ticker)
                
                if not current_price: continue
                    
                if ticker not in bot["positions"]:
                    bot["positions"][ticker] = {"buy_price": avg_price, "max_price": avg_price, "buy_time": time.time()}
                    
                pos = bot["positions"][ticker]
                sell_logic(ticker, avg_price, current_price, balance, pos)
                time.sleep(0.1)

            tracked_tickers = list(bot["positions"].keys())
            for t in tracked_tickers:
                if t not in balance_dict: del bot["positions"][t]

            # 파트 B: 신규 매수 (고쳐진 5분봉 타점 적용)
            if len(balance_dict) < MAX_POSITIONS:
                target_tickers = [t for t in buy_tickers if t not in balance_dict and (t not in bot["blacklist"] or time.time() > bot["blacklist"][t])]
                
                for ticker in target_tickers:
                    if len(balance_dict) >= MAX_POSITIONS: break 
                    
                    if check_buy_signal(ticker):
                        current_price = all_prices.get(ticker)
                        if not current_price: continue

                        target_amount = krw_balance * 0.2
                        amount = max(target_amount, 6000)
                        amount = min(amount, krw_balance) 
                        
                        if amount >= 6000:
                            try:
                                upbit.buy_market_order(ticker, amount)
                                bot["positions"][ticker] = {
                                    "buy_price": current_price, 
                                    "max_price": current_price, 
                                    "buy_time": time.time()
                                }
                                send_msg(f"🔥 [신규 진입] {ticker} 매수 완료! (5분봉 정배열 & 거래량 터짐)")
                                krw_balance -= amount
                                balance_dict[ticker] = True 
                            except Exception as e: print(f"매수 에러 ({ticker}): {e}")
                    
                    time.sleep(0.2) 

            gc.collect()
            time.sleep(1)
            
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
