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
# 2. 💡 손익비 역전 매매 설정값
# =========================
TOP_N = 15
MAX_POSITIONS = 15

TAKE_PROFIT = 0.03      # +3% 목표가 익절
STOP_LOSS = -0.012      # 💡 -1.2% 칼손절 (손실폭 대폭 축소)
TRAILING_START = 0.015  # 💡 최소 +1.5% 이상 수익 시 방어 시작
TRAILING_DROP = 0.005   # 고점 대비 -0.5% 하락 시 익절

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
        self.wfile.write("🚀 춘봉봇 오리지널 손익비 개선판 가동중!".encode('utf-8'))

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
        
        msg = f"🐶 [쿠퍼춘봉 오리지널 손익비개선 브리핑]\n"
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
# 5. 오리지널 지표: HMA & ADX (방향성 필터 유지)
# =========================
def wma(series, length):
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda prices: np.dot(prices, weights) / weights.sum(), raw=True)

def hma(series, length):
    half_length = int(length / 2)
    sqrt_length = int(np.sqrt(length))
    wma_half = wma(series, half_length)
    wma_full = wma(series, length)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_length)

def calc_adx_di(df, period=14):
    plus_dm = df['high'].diff()
    minus_dm = df['low'].shift(1) - df['low']
    
    plus_dm_val = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)
    minus_dm_val = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)
    
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift(1)).abs()
    tr3 = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (pd.Series(plus_dm_val, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm_val, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr)
    
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx, plus_di, minus_di

# =========================
# 6. 종목 스캔 & 오리지널 매수 신호 (💡 가짜 반등 필터 추가)
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
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=60)
        if df is None or len(df) < 50: return "NONE", 0.0

        df['hma'] = hma(df['close'], 14)
        df['ma20'] = df['close'].rolling(20).mean()
        
        adx, pdi, mdi = calc_adx_di(df, 14)
        df['adx'] = adx
        df['pdi'] = pdi
        df['mdi'] = mdi
        
        hma_cur = df['hma'].iloc[-1]
        hma_prev = df['hma'].iloc[-2]
        hma_old = df['hma'].iloc[-3]
        
        cur_price = df['close'].iloc[-1]
        ma20_cur = df['ma20'].iloc[-1]
        
        # 💡 거래량 가짜 반등 방지: 이전 캔들보다 거래량이 20% 이상 터져야 인정
        vol_cur = df['volume'].iloc[-2]
        vol_prev = df['volume'].iloc[-3]
        vol_spike = vol_cur > (vol_prev * 1.2)
        
        is_hma_turned = (hma_cur > hma_prev) and (hma_prev < hma_old)
        
        adx_val = df['adx'].iloc[-1]
        pdi_val = df['pdi'].iloc[-1]
        mdi_val = df['mdi'].iloc[-1]

        if is_hma_turned and (pdi_val > mdi_val) and (cur_price > ma20_cur) and vol_spike:
            if adx_val >= 25:
                return "STRONG", 0.30  
            elif 15 <= adx_val < 25:
                return "NORMAL", 0.10  
                
        return "NONE", 0.0
    except: return "NONE", 0.0

# =========================
# 7. 장부 관리 및 실행 
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
                    'peak_price': avg_price,
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
            'peak_price': current_price,
            'buy_time': time.time()
        }
        
        prefix = "🔥🔥 [강력 매수]" if signal_type == "STRONG" else "🔥 [일반 매수]"
        send_msg(f"{prefix} {ticker} 진입! (HMA 반등 + 거래량 펌핑)")
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
    start_msg = f"🚀 오리지널 손익비 개선판 시작! ({get_kst().strftime('%m/%d %H:%M')})\n"
    start_msg += f"✅ 가짜 반등 차단 및 -1.2% 짧은 칼손절 장착 완료!"
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
            # 💡 스마트 손익비 매도 로직
            # ======================
            for ticker in holding_tickers:
                df = pyupbit.get_ohlcv(ticker, interval="minute5", count=30)
                if df is None or len(df) < 20: continue
                
                current_price = df['close'].iloc[-1]
                buy_price = positions[ticker]['buy_price']
                profit = (current_price - buy_price) / buy_price
                
                # 최고가 갱신
                if current_price > positions[ticker]['peak_price']:
                    positions[ticker]['peak_price'] = current_price
                
                drop_from_peak = (positions[ticker]['peak_price'] - current_price) / positions[ticker]['peak_price']

                df['hma'] = hma(df['close'], 14)
                hma_cur = df['hma'].iloc[-1]
                hma_prev = df['hma'].iloc[-2]

                # 1. 절대 손절 (-1.2%로 단축)
                if profit <= STOP_LOSS:
                    sell_coin(ticker, "☠️ [-1.2% 짧은 손절]", current_price, buy_price)
                    blacklist[ticker] = now
                    continue

                # 2. 목표가 익절 (+3.0%)
                if profit >= TAKE_PROFIT:
                    sell_coin(ticker, "🎯 [오리지널 +3% 잭팟]", current_price, buy_price)
                    continue
                    
                # 3. 트레일링 익절 방어 (최소 +1.5% 돌파 후 고점 대비 -0.5% 하락 시)
                if profit >= TRAILING_START and drop_from_peak >= TRAILING_DROP:
                    sell_coin(ticker, "🚀 [수익 보존 트레일링]", current_price, buy_price)
                    continue

                # 4. HMA 수익 보존 컷 (수익이 +1.0% 이상 났을 때만 HMA 꺾임을 반영)
                if hma_cur < hma_prev and profit > 0.01:
                    sell_coin(ticker, "🛡️ [HMA 꺾임 수익 굳히기]", current_price, buy_price)

            # ======================
            # 오리지널 매수 로직
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
