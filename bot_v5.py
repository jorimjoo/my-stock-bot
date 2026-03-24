import time
import datetime
import requests
import pandas as pd
import pyupbit
import psutil

from flask import Flask
from threading import Thread

# =========================
# 1. API 키 및 텔레그램 설정 (직접 입력 방식 적용 완료)
# =========================
access = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
secret = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
telegram_chat_id = "8403406400"

# =========================
# 2. 시간 및 공통 함수
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

# =========================
# 3. 시스템 설정
# =========================
BUY_RATIO = 0.1             # 매수 비중 (10%)
MAX_POSITIONS = 5           # 최대 보유 종목 수
MAX_TICKERS = 10            # 스크리닝할 상위 종목 수

TRAILING_ACTIVATE_ROI = 2.5 # 2.5% 수익 시 트레일링 가동
TRAILING_DROP_RATE = 0.8    # 고점 대비 0.8% 하락 시 익절
STOP_LOSS_ROI = -1.3        # 기본 손절률 (-1.3%)

BLACKLIST_HOURS = 1         # 손절 시 쿨다운
REPORT_INTERVAL = 3600      # 브리핑 간격 (1시간)

# 상태 및 통계 관리
bot_state = {
    "blacklist_times": {},
    "max_prices": {},
    "last_report_time": 0,
    "positions": set(),
    "daily_stats": {"trades": 0, "wins": 0, "profit": 0.0, "date": get_kst().date()},
    "total_stats": {"trades": 0, "wins": 0, "profit": 0.0, "start_time": get_kst()}
}

# =========================
# 4. 🌐 Flask 서버
# =========================
app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 실전형 코인봇 V10 가동중 (쿠퍼춘봉 에디션 🐶)"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# =========================
# 5. 텔레그램 발신/수신 로직
# =========================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg}, timeout=5)
    except:
        pass

def telegram_polling():
    """텔레그램 채팅창에서 '/상태' 명령어를 대기하는 수신 스레드"""
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{telegram_token}/getUpdates"
            params = {"timeout": 10}
            if last_update_id:
                params["offset"] = last_update_id
            
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for item in data.get("result", []):
                    last_update_id = item["update_id"] + 1
                    msg_text = item.get("message", {}).get("text", "")
                    
                    if msg_text == "/상태":
                        send_system_briefing()
        except Exception:
            pass
        time.sleep(2)

def send_system_briefing():
    """시스템 브리핑 전송 함수"""
    now_date = get_kst().date()
    if bot_state["daily_stats"]["date"] != now_date:
        bot_state["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}

    now = get_kst()
    uptime = now - bot_state["total_stats"]["start_time"]
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    try:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
    except:
        cpu, ram = 0.0, 0.0
        
    krw_balance = upbit.get_balance("KRW")
    
    t_stats = bot_state["total_stats"]
    d_stats = bot_state["daily_stats"]
    
    total_win_rate = (t_stats["wins"] / t_stats["trades"] * 100) if t_stats["trades"] > 0 else 0
    daily_win_rate = (d_stats["wins"] / d_stats["trades"] * 100) if d_stats["trades"] > 0 else 0
    
    msg = f"🐶 [쿠퍼춘봉 V10 브리핑]\n"
    msg += f"⌚ 봇 가동: {t_stats['start_time'].strftime('%m/%d %H:%M')}\n"
    msg += f"⏳ 구동 시간: {days}일 {hours}시간 {minutes}분\n"
    msg += f"💻 서버 상태: CPU {cpu}% / RAM {ram}%\n"
    msg += f"💰 보유 KRW: {krw_balance:,.0f}원\n\n"
    
    msg += f"📈 [전체 누적 통계]\n"
    msg += f"- 누적 수익: {t_stats['profit']:,.0f}원\n"
    msg += f"- 누적 거래: {t_stats['trades']}회 (승률 {total_win_rate:.1f}%)\n\n"
    
    msg += f"📅 [오늘 하루 통계]\n"
    msg += f"- 오늘 수익: {d_stats['profit']:,.0f}원\n"
    msg += f"- 오늘 거래: {d_stats['trades']}회 (승률 {daily_win_rate:.1f}%)"
    
    send_message(msg)

def update_statistics(profit_amount, is_win):
    """매도 완료 시 통계 업데이트"""
    now_date = get_kst().date()
    if bot_state["daily_stats"]["date"] != now_date:
        bot_state["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}
        
    for key in ["daily_stats", "total_stats"]:
        bot_state[key]["trades"] += 1
        bot_state[key]["profit"] += profit_amount
        if is_win: bot_state[key]["wins"] += 1

# =========================
# 6. 매매 핵심 필터 및 지표
# =========================
def ta_rsi(series, period=14):
    """트레이딩뷰 기준에 맞춘 정교한 RSI 계산"""
    delta = series.diff()
    up = delta.copy()
    down = delta.copy()
    up[up < 0] = 0
    down[down > 0] = 0
    _gain = up.ewm(com=(period - 1), min_periods=period).mean()
    _loss = down.abs().ewm(com=(period - 1), min_periods=period).mean()
    RS = _gain / _loss
    return 100 - (100 / (1 + RS))

def check_btc_trend():
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=50)
        if df is None:
            return False
        df['ma20'] = df['close'].rolling(20).mean()
        return df['close'].iloc[-1] > df['ma20'].iloc[-1]
    except:
        return False

def get_top_tickers():
    """업비트 서버 차단 방지를 위한 REST API 방식 적용"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        
        all_data = []
        for i in range(0, len(tickers), 50):
            batch = tickers[i:i+50]
            querystring = {"markets": ",".join(batch)}
            res = requests.get(url, headers=headers, params=querystring)
            if res.status_code == 200:
                all_data.extend(res.json())
            time.sleep(0.1)
            
        sorted_data = sorted(all_data, key=lambda x: x['acc_trade_price_24h'], reverse=True)
        return [item['market'] for item in sorted_data if item['market'] != "KRW-BTC"][:MAX_TICKERS]
    except Exception as e:
        print("티커 조회 에러:", e)
        return []

# =========================
# 7. 매수/매도 로직
# =========================
def check_buy(ticker):
    if ticker in bot_state["blacklist_times"]:
        if time.time() < bot_state["blacklist_times"][ticker]:
            return False
        else:
            del bot_state["blacklist_times"][ticker]

    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
        df5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=50)

        if df is None or df5 is None:
            return False

        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['rsi'] = ta_rsi(df['close'], 14)

        df5['ma5'] = df5['close'].rolling(5).mean()

        c = df['close'].iloc[-1]
        ma5 = df['ma5'].iloc[-1]
        ma20 = df['ma20'].iloc[-1]
        rsi = df['rsi'].iloc[-1]

        prev_vol = df['volume'].iloc[-2]
        pprev_vol = df['volume'].iloc[-3]

        c5 = df5['close'].iloc[-1]
        ma5_5 = df5['ma5'].iloc[-1]

        if (
            c > ma5 > ma20 and
            40 < rsi < 65 and
            prev_vol > pprev_vol * 1.5 and
            c5 > ma5_5
        ):
            return True
        return False
    except:
        return False

def check_sell(ticker, buy_price, current_price):
    profit = ((current_price - buy_price) / buy_price) * 100

    if ticker not in bot_state["max_prices"]:
        bot_state["max_prices"][ticker] = buy_price

    if current_price > bot_state["max_prices"][ticker]:
        bot_state["max_prices"][ticker] = current_price

    max_price = bot_state["max_prices"][ticker]
    drop = ((max_price - current_price) / max_price) * 100

    # 익절
    if profit >= TRAILING_ACTIVATE_ROI:
        if drop >= TRAILING_DROP_RATE:
            del bot_state["max_prices"][ticker]
            return True, f"익절 {profit:.2f}%"

    # 손절
    if profit <= STOP_LOSS_ROI:
        bot_state["blacklist_times"][ticker] = time.time() + (3600 * BLACKLIST_HOURS)
        if ticker in bot_state["max_prices"]: del bot_state["max_prices"][ticker]
        return True, f"손절 {profit:.2f}%"

    return False, ""

# =========================
# 8. 메인 루프
# =========================
def main():
    send_message(f"🚀 실전형 자동매매 V10 가동 시작!\n({get_kst().strftime('%m/%d %H:%M')})")
    print("=======================================")
    print("쿠퍼춘봉 V10 안정화 버전 가동 시작...")
    print("=======================================")
    
    send_system_briefing()
    bot_state["last_report_time"] = time.time()

    while True:
        try:
            now_ts = time.time()
            
            # 정기 브리핑
            if now_ts - bot_state["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot_state["last_report_time"] = now_ts

            # 대장주 필터 확인
            if not check_btc_trend():
                print(f"[{get_kst().strftime('%H:%M:%S')}] BTC 추세 약세. 대기 중...")
                time.sleep(10)
                continue

            tickers = get_top_tickers()
            if tickers:
                print(f"[{get_kst().strftime('%H:%M:%S')}] 정상 탐색 중 (상위: {tickers[0]}, {tickers[1]}...)")

            for ticker in tickers:
                balance = upbit.get_balance(ticker)
                price = pyupbit.get_current_price(ticker)

                # 매수 로직
                if (balance is None or balance == 0):
                    # 현재 보유 종목 수 체크 (실제 계좌 잔고 기반으로 안정성 확보)
                    balances = upbit.get_balances()
                    holding_count = len([b for b in balances if b['currency'] != 'KRW' and float(b['balance']) > 0])
                    
                    if holding_count >= MAX_POSITIONS:
                        continue

                    if check_buy(ticker):
                        krw = upbit.get_balance("KRW")
                        buy_amt = krw * BUY_RATIO

                        if buy_amt > 5000:
                            upbit.buy_market_order(ticker, buy_amt)
                            bot_state["positions"].add(ticker)
                            send_message(f"✅ [매수] {ticker}")
                            time.sleep(0.5)

                # 매도 로직
                else:
                    avg = upbit.get_avg_buy_price(ticker)

                    if avg > 0 and price:
                        sell, reason = check_sell(ticker, avg, price)

                        if sell:
                            upbit.sell_market_order(ticker, balance)
                            bot_state["positions"].discard(ticker)
                            
                            profit_amt = (price - avg) * balance
                            update_statistics(profit_amt, (price > avg))
                            send_message(f"💰 [매도] {ticker} / {reason}")
                            time.sleep(0.5)

                time.sleep(0.1) # 종목 간 API 딜레이

            time.sleep(1) # 무한 루프 과부하 방지

        except Exception as e:
            print(f"에러 발생: {e}")
            time.sleep(10)

# =========================
# 9. 실행
# =========================
if __name__ == "__main__":
    # Flask 서버 스레드 시작
    Thread(target=run_server, daemon=True).start()
    
    # 텔레그램 수신 스레드 시작 (/상태 명령어 인식)
    Thread(target=telegram_polling, daemon=True).start()
    
    # 메인 봇 가동
    main()
