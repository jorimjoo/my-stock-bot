import time
import datetime
import requests
import pandas as pd
import pyupbit
import psutil

from flask import Flask
from threading import Thread

# =========================
# 1. API 키 및 텔레그램 설정 (직접 입력 반영)
# =========================
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)

# =========================
# 2. 시간
# =========================
def get_kst():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

# =========================
# 3. 시스템 설정 (V12 공격형 + 통계 브리핑 결합)
# =========================
BUY_RATIO = 0.2             # 종목당 총 할당 비중 (20%)
MAX_POSITIONS = 5           # 최대 보유 종목 수
TOP_N = 15                  # 스크리닝 대상 종목 수

TRAILING_START = 3.0        # 3.0% 수익 시 트레일링 스탑 가동
TRAILING_DROP = 0.8         # 최고점 대비 0.8% 하락 시 전량 익절
STOP_LOSS = -1.2            # 기본 손절률 (-1.2%)

BLACKLIST_HOURS = 1         # 손절 시 1시간 진입 금지
REPORT_INTERVAL = 3600      # 1시간 주기로 자동 브리핑 전송

# 봇 상태 및 통계 관리 딕셔너리 완벽 통합
bot = {
    "positions": {},   # ticker별 상태 (stage, half_sold, buy_price)
    "blacklist": {},
    "max_price": {},
    "last_report_time": 0,
    "daily_stats": {"trades": 0, "wins": 0, "profit": 0.0, "date": get_kst().date()},
    "total_stats": {"trades": 0, "wins": 0, "profit": 0.0, "start_time": get_kst()}
}

# =========================
# 4. Flask 서버
# =========================
app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 V12 공격형 통합 봇 (쿠퍼춘봉 에디션) 가동중"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# =========================
# 5. 텔레그램 통신 및 브리핑 로직 (V10 기능 완전 이식)
# =========================
def send_msg(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
    except:
        pass

def telegram_polling():
    """'/상태' 명령어 수신 스레드"""
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
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

def check_daily_reset():
    now_date = get_kst().date()
    if bot["daily_stats"]["date"] != now_date:
        bot["daily_stats"] = {"trades": 0, "wins": 0, "profit": 0.0, "date": now_date}

def get_server_status():
    try:
        return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
    except:
        return 0.0, 0.0

def send_system_briefing():
    """시스템 상태 및 누적/일간 통계 브리핑"""
    check_daily_reset()
    now = get_kst()
    uptime = now - bot["total_stats"]["start_time"]
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    cpu, ram = get_server_status()
    krw_balance = upbit.get_balance("KRW")
    
    t_stats = bot["total_stats"]
    d_stats = bot["daily_stats"]
    
    total_win_rate = (t_stats["wins"] / t_stats["trades"] * 100) if t_stats["trades"] > 0 else 0
    daily_win_rate = (d_stats["wins"] / d_stats["trades"] * 100) if d_stats["trades"] > 0 else 0
    
    msg = f"🤖 [쿠퍼춘봉 V12 브리핑]\n"
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
    
    send_msg(msg)

def update_statistics(profit_krw, is_win):
    """실제 원화 수익금을 통계에 업데이트"""
    check_daily_reset()
    for key in ["daily_stats", "total_stats"]:
        bot[key]["trades"] += 1
        bot[key]["profit"] += profit_krw
        if is_win: bot[key]["wins"] += 1

# =========================
# 6. BTC 필터
# =========================
def btc_ok():
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="minute15", count=50)
        ma20 = df['close'].rolling(20).mean()
        return df['close'].iloc[-1] > ma20.iloc[-1]
    except:
        return False

# =========================
# 7. RSI 계산 함수
# =========================
def rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(period).mean()
    ma_down = down.rolling(period).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))

# =========================
# 8. 거래대금 + 상승률 기준 종목 선정
# =========================
def get_top_coins():
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        data = []

        for t in tickers:
            df = pyupbit.get_ohlcv(t, interval="minute60", count=2)
            if df is None:
                continue

            value = df['value'].iloc[-1]
            change = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]

            score = (value * 0.7) + (change * 100000000 * 0.3)
            data.append((t, score))

        data.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in data[:TOP_N]]

    except:
        return []

# =========================
# 9. 매수 조건 확인
# =========================
def buy_check(ticker):
    if ticker in bot["blacklist"]:
        if time.time() < bot["blacklist"][ticker]:
            return False
        else:
            del bot["blacklist"][ticker]

    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute15", count=50)
        df5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=50)

        if df is None or df5 is None:
            return False

        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        df['rsi'] = rsi(df['close'])
        df5['ma5'] = df5['close'].rolling(5).mean()

        return (
            df['close'].iloc[-1] > df['ma5'].iloc[-1] > df['ma20'].iloc[-1] and 
            40 < df['rsi'].iloc[-1] < 65 and                                  
            df['volume'].iloc[-2] > df['volume'].iloc[-3] * 1.5 and           
            df5['close'].iloc[-1] > df5['ma5'].iloc[-1]                       
        )
    except:
        return False

# =========================
# 10. 분할 매도 로직 및 통계 연동
# =========================
def sell_logic(ticker, avg_price, current_price, balance):
    profit = (current_price - avg_price) / avg_price * 100

    if ticker not in bot["max_price"]:
        bot["max_price"][ticker] = avg_price

    if current_price > bot["max_price"][ticker]:
        bot["max_price"][ticker] = current_price

    max_p = bot["max_price"][ticker]
    drop = (max_p - current_price) / max_p * 100

    # 1. 절반 익절 (2.0% 도달 시)
    if profit >= 2.0 and not bot["positions"].get(ticker, {}).get("half_sold", True):
        sell_amount = balance * 0.5
        upbit.sell_market_order(ticker, sell_amount)
        bot["positions"][ticker]["half_sold"] = True
        
        # 수익 기록
        profit_krw = (current_price - avg_price) * sell_amount
        update_statistics(profit_krw, True)
        send_msg(f"✅ 1차(절반) 익절 {ticker} (+{profit:.2f}%) / 수익: {profit_krw:,.0f}원")
        return

    # 2. 최종 익절 (트레일링 스탑)
    if profit >= TRAILING_START and drop >= TRAILING_DROP:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None)
        bot["max_price"].pop(ticker, None)
        
        # 수익 기록
        profit_krw = (current_price - avg_price) * balance
        update_statistics(profit_krw, True)
        send_msg(f"🚀 최종 트레일링 익절 {ticker} (+{profit:.2f}%) / 수익: {profit_krw:,.0f}원")
        return

    # 3. 손절
    if profit <= STOP_LOSS:
        upbit.sell_market_order(ticker, balance)
        bot["positions"].pop(ticker, None)
        bot["max_price"].pop(ticker, None)
        bot["blacklist"][ticker] = time.time() + 3600 * BLACKLIST_HOURS
        
        # 손실 기록
        profit_krw = (current_price - avg_price) * balance
        update_statistics(profit_krw, False)
        send_msg(f"😭 손절 {ticker} ({profit:.2f}%) / 손실: {profit_krw:,.0f}원")

# =========================
# 11. 메인 루프 (1시간 브리핑 통합)
# =========================
def main():
    send_msg(f"🚀 V12 공격형 통합 봇 가동 시작! ({get_kst().strftime('%m/%d %H:%M')})")
    print("=======================================")
    print("쿠퍼춘봉 V12 완전체 가동 중...")
    print("=======================================")
    
    # 봇 가동 시 초기 브리핑 발송
    send_system_briefing()
    bot["last_report_time"] = time.time()

    while True:
        try:
            now_ts = time.time()
            
            # 정기 브리핑 확인 (1시간 간격)
            if now_ts - bot["last_report_time"] > REPORT_INTERVAL:
                send_system_briefing()
                bot["last_report_time"] = now_ts

            if not btc_ok():
                time.sleep(5)
                continue

            tickers = get_top_coins()

            for ticker in tickers:
                price = pyupbit.get_current_price(ticker)
                balance = upbit.get_balance(ticker)

                # --- 1차 신규 매수 ---
                if (balance is None or balance == 0):
                    if len(bot["positions"]) >= MAX_POSITIONS:
                        continue

                    if buy_check(ticker):
                        krw = upbit.get_balance("KRW")
                        amount = krw * (BUY_RATIO * 0.5) # 비중의 절반(10%) 진입
                        if amount > 5000:
                            upbit.buy_market_order(ticker, amount)
                            bot["positions"][ticker] = {
                                "stage": 1,
                                "half_sold": False,
                                "buy_price": price
                            }
                            send_msg(f"🔥 1차 매수 진입 {ticker}")

                # --- 보유 종목 관리 (2차 물타기 & 매도) ---
                else:
                    avg = upbit.get_avg_buy_price(ticker)

                    if avg and price:
                        # 방어 로직 (2차 물타기 확인)
                        if ticker in bot["positions"]:
                            pos = bot["positions"][ticker]
                            if pos["stage"] == 1:
                                drop = (price - pos["buy_price"]) / pos["buy_price"] * 100
                                if drop <= -1.0: # -1.0% 하락 시 물타기
                                    krw = upbit.get_balance("KRW")
                                    amount = krw * (BUY_RATIO * 0.5)
                                    if amount > 5000:
                                        upbit.buy_market_order(ticker, amount)
                                        pos["stage"] = 2  
                                        send_msg(f"🛡️ 2차 매수(물타기) {ticker}")
                                        continue 

                        # 익절/손절 확인 (통계 업데이트 포함)
                        sell_logic(ticker, avg, price, balance)

                time.sleep(0.1)

        except Exception as e:
            print("에러:", e)
            time.sleep(5)

# =========================
# 12. 실행
# =========================
if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_polling, daemon=True).start()
    main()
