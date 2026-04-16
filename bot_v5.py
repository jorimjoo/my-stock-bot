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
# 2. V19.1 럭키텐(10개) 올인원 설정값
# =========================
TOP_N = 50              
MAX_POSITIONS = 10      

# 💡 수익 & 손실 관리
TRAILING_ACTIVATE = 0.015  # +1.5% 도달 시 트레일링 익절 시작
TRAILING_DROP = 0.005      # 고점 대비 -0.5% 하락 시 익절
STOP_LOSS = -0.015         # -1.5% 칼손절

# ⏰ 타임 컷 설정
STAGNANT_SEC = 2700        # 45분 횡보 시 무조건 탈출

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
        
        msg = f"🐶 [쿠퍼춘봉 V19.1 올인원 브리핑]\n"
        msg += f"⌚ 가동: {bot_stats['start_time'].strftime('%m/%d %H:%M')}\n"
        msg += f"⏳ 구동: {days}일 {hours}시간 {minutes}분\n"
        msg += f"💰 KRW: {krw_balance:,.0f}원\n"
        msg += f"🎯 보유 종목: {len(positions)}/{MAX_POSITIONS}개\n\n"
        msg += f"📈 [누적 통계]\n- 수익: {bot_stats['profit_krw']:,.0f}원\n- 거래: {bot_stats['trades']}회 (승률 {win_rate:.1f}%)"
        
        send_msg(msg)
    except: pass

# =========================
# 4. 텔레그램 통신 (💡 올인원 명령어 탑재)
# =========================
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
                    
                    # 1. 상태 브리핑
                    if "/상태" in msg_text or "상태" == msg_text:
                        send_system_briefing()

                    # 2. 💡 매수 권한 테스트 (올인원 탑재)
                    elif "/매수테스트" in msg_text or "매수테스트" in msg_text:
                        send_msg("🛠️ [테스트 시작] 비트코인 6,000원어치 시장가 매수 시도 중...")
                        krw_bal = upbit.get_balance("KRW")
                        
                        if krw_bal is None:
                            send_msg("🚨 [실패] 잔고 조회 불가! (IP 차단 또는 API 만료 의심)")
                        elif krw_bal < 6000:
                            send_msg(f"🚨 [실패] 원화 부족 (현재: {krw_bal:,.0f}원)")
                        else:
                            try:
                                res_buy = upbit.buy_market_order("KRW-BTC", 6000)
                                if res_buy is None or 'error' in res_buy:
                                    err_detail = res_buy.get('error', {}).get('message', '알 수 없는 에러') if res_buy else '응답 없음'
                                    send_msg(f"❌ [매수 실패] 주문 권한 없음!\n사유: {err_detail}\n💡 업비트 Open API '주문하기' 권한을 켜주세요.")
                                else:
                                    send_msg("🎯 [매수 성공!!!] 비트코인 6,000원 체결 완료!\nAPI 주문 권한이 100% 정상입니다.\n(테스트로 매수한 코인도 봇이 알아서 관리합니다.)")
                                    # 장부에 등록하여 자동 매도 관리
                                    time.sleep(1)
                                    cur_price = pyupbit.get_current_price("KRW-BTC")
                                    positions["KRW-BTC"] = {'buy_price': cur_price, 'peak_price': cur_price, 'buy_time': time.time()}
                            except Exception as e:
                                send_msg(f"❌ [에러] 매수 중 문제 발생: {e}")

                    # 3. 수동 매도 기능
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
# 5. 웹 서버
# =========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("🚀 춘봉봇 V19.1 올인원 에디션 가동중!".encode('utf-8'))

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# =========================
# 6. 종목 스캔 (상위 50개)
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
            time.sleep(0.1) 
        
        data = []
        for item in all_data:
            if item['market'] == "KRW-BTC": continue
            if item['acc_trade_price_24h'] >= 100000000: 
                data.append((item['market'], item['acc_trade_price_24h']))

        data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in data[:TOP_N]]
    except Exception as e:
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
# 7. 매수 로직 
# =========================
def check_buy_signal(ticker):
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=40)
        time.sleep(0.05) 
        
        if df is None or len(df) < 30:
            return False

        df['ma20'] = df['close'].rolling(20).mean()
        df['rsi'] = ta_rsi(df['close'])

        cur = df.iloc[-1]
        
        if cur['close'] < cur['ma20']: return False
        if cur['close'] <= cur['open']: return False
        
        vol_avg = df['volume'].rolling(20).mean().iloc[-1]
        if cur['volume'] < vol_avg * 1.1: return False
        
        if cur['rsi'] > 75: return False

        return True

    except Exception as e:
        return False

# =========================
# 8. 장부 및 매매 실행
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
        
        send_msg(f"🍀 [럭키텐 진입] {ticker}\n(10개 슬롯 가동! 적극적으로 사냥합니다!)")
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
# 9. 메인 루프
# =========================
def main():
    start_msg = "== 업비트 봇 가동 시작 ==\n🚀 V19.1 올인원(럭키텐+테스트명령어) 에디션 가동!"
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
            
            blacklist_keys = list(blacklist.keys())
            for k in blacklist_keys:
                if now - blacklist[k] > 1800:
                    del blacklist[k]

            if now - last_top_coins_time > 60:
                top_coins = get_top_coins()
                last_top_coins_time = now

            holding_tickers = list(positions.keys())

            # =================
            # 💡 매도 감시
            # =================
            for ticker in holding_tickers:
                current_price = pyupbit.get_current_price(ticker)
                if current_price is None: continue
                
                pos = positions[ticker]
                buy_price = pos['buy_price']
                held_time = now - pos.get('buy_time', now)
                profit = (current_price - buy_price) / buy_price
                
                if current_price > pos['peak_price']: 
                    pos['peak_price'] = current_price
                    
                drop_from_peak = (pos['peak_price'] - current_price) / pos['peak_price']

                if profit >= TRAILING_ACTIVATE and drop_from_peak >= TRAILING_DROP:
                    sell_coin(ticker, "💸 [스마트 익절]", current_price, buy_price)
                    continue
                
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ [방어선 칼손절]", current_price, buy_price)
                    continue

                if held_time >= STAGNANT_SEC:
                    if profit > 0:
                        sell_coin(ticker, "🥱 [횡보 약수익 탈출]", current_price, buy_price)
                    else:
                        sell_coin(ticker, "⏰ [횡보 타임컷 손절]", current_price, buy_price)
                    continue
                
                time.sleep(0.05)

            # =================
            # 💡 10개 종목 폭격 매수
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
                    time.sleep(0.5)
                    
                time.sleep(0.05)

            time.sleep(2)

        except Exception as e:
            time.sleep(5)

# =========================
# 실행부
# =========================
if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
