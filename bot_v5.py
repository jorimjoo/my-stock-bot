import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys
import psutil 
from flask import Flask
from threading import Thread

# ==========================================
# 1. API 키 및 텔레그램 설정
# ==========================================
access = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
secret = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
telegram_chat_id = "8403406400"

# ==========================================
# 2. 스윙 및 모멘텀 추적 설정 (트레일링 스탑)
# ==========================================
TRAILING_ACTIVATE_ROI = 3.0   # 수익률이 3% 이상일 때 트레일링 스탑 가동
TRAILING_DROP_RATE = 1.5      # 최고점 대비 1.5% 하락 시 모멘텀 종료로 판단 후 익절
STOP_LOSS_ROI = -2.5          # 기본 손절률
BLACKLIST_HOURS = 12          # 손절 시 쿨다운

# 🔥 고점 추적을 위한 max_prices 딕셔너리 추가
bot_state = {"loss_counts": {}, "blacklist_times": {}, "max_prices": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}
total_stats = {"trades": 0, "wins": 0, "profit": 0.0, "start_time": datetime.datetime.now()}

# ==========================================
# 3. 🌐 가짜 웹 서버
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스윙 봇 V9.1 (트레일링 스탑 가동 중!) 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 봇 핵심 로직
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_elite_tickers():
    send_message("🔍 [시스템] Top-Down 다중 시간프레임 스크리닝 시작...")
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        response = requests.get(url, headers=headers, params={"markets": ",".join(tickers)})
        data = response.json()
        
        candidates = sorted([item for item in data if item['acc_trade_price_24h'] > 10000000000], 
                            key=lambda x: x['acc_trade_price_24h'], reverse=True)[:25]
        top_tickers = [c['market'] for c in candidates]
        
        elite_tickers = []
        for ticker in top_tickers:
            time.sleep(0.1)
            df_day = pyupbit.get_ohlcv(ticker, interval="day", count=60)
            if df_day is None or len(df_day) < 60: continue
            
            ma20_day = df_day['close'].rolling(20).mean().iloc[-1]
            ma60_day = df_day['close'].rolling(60).mean().iloc[-1]
            curr_price = df_day['close'].iloc[-1]
            
            if curr_price > ma60_day and ma20_day > ma60_day:
                elite_tickers.append(ticker)
                
            if len(elite_tickers) >= 10: break
            
        send_message(f"🎯 [스크리닝 완료] 스윙 타겟 {len(elite_tickers)}개 확보!")
        return elite_tickers if elite_tickers else top_tickers[:5]
    except Exception as e:
        return pyupbit.get_tickers(fiat="KRW")[:10]

def sell_manager(valid_krw_tickers):
    """🔥 모멘텀 추적(Trailing Stop) 및 단기 이평 이탈 매도 로직 반영"""
    global daily_stats, total_stats, bot_state
    now = datetime.datetime.now()
    try:
        balances = upbit.get_balances()
        if not balances: return "조회실패"
        
        holdings = []
        for b in balances:
            if b['currency'] != 'KRW' and float(b['balance']) + float(b['locked']) > 0:
                ticker = f"KRW-{b['currency']}"
                if ticker in valid_krw_tickers:
                    holdings.append({
                        'ticker': ticker,
                        'balance': float(b['balance']) + float(b['locked']),
                        'avg_buy_price': float(b['avg_buy_price'])
                    })
                
        if not holdings: return "없음"

        tickers_to_check = [h['ticker'] for h in holdings]
        current_prices = pyupbit.get_current_price(tickers_to_check)
        if not current_prices: return "가격로딩중"

        display_info = []
        for hold in holdings:
            ticker = hold['ticker']
            buy_price = hold['avg_buy_price']
            balance = hold['balance']
            if buy_price == 0: continue 
            
            curr_price = current_prices[ticker] if isinstance(current_prices, dict) else current_prices
            roi = ((curr_price - buy_price) / buy_price) * 100
            
            # 1. 고점(Max Price) 갱신 로직
            max_price = bot_state["max_prices"].get(ticker, buy_price)
            if curr_price > max_price:
                bot_state["max_prices"][ticker] = curr_price
                max_price = curr_price
            
            max_roi = ((max_price - buy_price) / buy_price) * 100
            drop_from_peak = ((max_price - curr_price) / max_price) * 100

            sell_reason = ""

            # 🚨 매도 조건 1: 트레일링 스탑 (수익 3% 이상 도달 후, 고점 대비 1.5% 하락 시)
            if max_roi >= TRAILING_ACTIVATE_ROI and drop_from_peak >= TRAILING_DROP_RATE:
                sell_reason = f"🚀 모멘텀 종료 (트레일링 익절: +{roi:.2f}%) / 최고수익: +{max_roi:.2f}%"

            # 🚨 매도 조건 2: 단기 상승 추세 붕괴 (5분봉 5일선 이탈) - 수익권일 때만 발동
            if not sell_reason and roi > 1.0:
                df_5 = pyupbit.get_ohlcv(ticker, interval="minute5", count=10)
                if df_5 is not None and len(df_5) > 5:
                    ma5 = df_5['close'].rolling(5).mean().iloc[-1]
                    if curr_price < ma5:
                        sell_reason = f"📉 5분봉 상승추세 붕괴 익절 (+{roi:.2f}%)"

            # 🚨 매도 조건 3: 기본 손절 라인
            if not sell_reason and roi <= STOP_LOSS_ROI:
                sell_reason = f"❌ 스윙 지지선 이탈 손절 ({roi:.2f}%)"

            if sell_reason and (balance * curr_price > 5000):
                upbit.sell_market_order(ticker, balance)
                profit_krw = (curr_price - buy_price) * balance
                
                daily_stats["trades"] += 1
                total_stats["trades"] += 1
                daily_stats["profit"] += profit_krw
                total_stats["profit"] += profit_krw
                
                if roi > 0: 
                    daily_stats["wins"] += 1
                    total_stats["wins"] += 1
                else:
                    bot_state["blacklist_times"][ticker] = now
                
                # 매도 후 기록 삭제
                bot_state["max_prices"].pop(ticker, None)
                
                send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {sell_reason}")
                continue 

            display_info.append(f"[{ticker} {roi:+.2f}% (Max: {max_roi:+.2f}%)]")
            
        return " ".join(display_info) if display_info else "없음"
    except Exception as e: 
        return f"에러({e})"

def buy_manager(ticker, krw_balance):
    global bot_state
    now = datetime.datetime.now()

    blacklist_time = bot_state["blacklist_times"].get(ticker)
    if blacklist_time and (now - blacklist_time).total_seconds() / 3600 < BLACKLIST_HOURS: return
    if upbit.get_balance(ticker) > 0: return

    df_60 = pyupbit.get_ohlcv(ticker, interval="minute60", count=60)
    if df_60 is None or len(df_60) < 60: return
    
    df_60['ma22'] = df_60['close'].rolling(22).mean()
    curr_60 = df_60.iloc[-1]
    prev_60 = df_60.iloc[-2]
    
    trend_60m_ok = curr_60['ma22'] > prev_60['ma22']
    
    df_10 = pyupbit.get_ohlcv(ticker, interval="minute10", count=60)
    if df_10 is None or len(df_10) < 60: return
    
    bb44_10 = ta.bbands(df_10['open'], length=44, std=2)
    df_10 = pd.concat([df_10, bb44_10], axis=1)
    lower_44_10m = df_10.columns[df_10.columns.str.contains('BBL')][0]
    
    curr_10 = df_10.iloc[-1]
    
    touch_bottom = curr_10['low'] <= curr_10[lower_44_10m]
    
    body = abs(curr_10['open'] - curr_10['close'])
    lower_shadow = min(curr_10['open'], curr_10['close']) - curr_10['low']
    hammer_candle = lower_shadow > (body * 1.5) and lower_shadow > 0
    
    if trend_60m_ok and touch_bottom and hammer_candle:
        buy_amt = krw_balance * 0.30 
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            # 매수 직후 max_price 초기화
            bot_state["max_prices"][ticker] = curr_10['close']
            send_message(f"🚀 [{ticker}] 스윙 타점 포착!\n- 매수가: {curr_10['close']:,.0f}원\n- 다중시간 필터 통과 (트레일링 스탑 대기)")

# ==========================================
# 5. 텔레그램 명령어 수신
# ==========================================
def telegram_listener():
    global total_stats, daily_stats
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{telegram_token}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 10}
            res = requests.get(url, params=params).json()
            
            if res.get("ok"):
                for item in res["result"]:
                    last_update_id = item["update_id"]
                    msg = item.get("message", {}).get("text", "")
                    
                    if msg == "/상태" or msg.lower() == "/status":
                        now = datetime.datetime.now()
                        cpu_usage = psutil.cpu_percent(interval=0.1)
                        ram_usage = psutil.virtual_memory().percent
                        uptime_duration = now - total_stats['start_time']
                        hours, remainder = divmod(uptime_duration.total_seconds(), 3600)
                        minutes, seconds = divmod(remainder, 60)
                        
                        krw_balance = upbit.get_balance("KRW")
                        krw_str = f"{krw_balance:,.0f}원" if krw_balance is not None else "조회 실패"
                        
                        wr = (total_stats['wins'] / total_stats['trades'] * 100) if total_stats['trades'] > 0 else 0
                        d_wr = (daily_stats['wins'] / daily_stats['trades'] * 100) if daily_stats['trades'] > 0 else 0
                        start_str = total_stats['start_time'].strftime('%m/%d %H:%M')
                        
                        reply = (
                            f"🤖 [쿠퍼춘봉 시스템 브리핑]\n"
                            f"⏱ 봇 가동: {start_str}\n"
                            f"⏳ 구동 시간: {int(hours)}시간 {int(minutes)}분\n"
                            f"💻 서버 상태: CPU {cpu_usage}% / RAM {ram_usage}%\n"
                            f"💰 보유 KRW: {krw_str}\n\n"
                            f"📈 [전체 누적 통계]\n"
                            f"- 누적 수익: {total_stats['profit']:,.0f}원\n"
                            f"- 누적 거래: {total_stats['trades']}회 (승률 {wr:.1f}%)\n\n"
                            f"📅 [오늘 하루 통계]\n"
                            f"- 오늘 수익: {daily_stats['profit']:,.0f}원\n"
                            f"- 오늘 거래: {daily_stats['trades']}회 (승률 {d_wr:.1f}%)"
                        )
                        send_message(reply)
        except: pass
        time.sleep(2)

# ==========================================
# 6. 메인 실행부
# ==========================================
if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_listener, daemon=True).start()
    
    send_message("== 업비트 봇 가동 시작 ==")
    
    valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")
    target_list = get_elite_tickers()
    last_screen_time = datetime.datetime.now()

    while True:
        try:
            now = datetime.datetime.now()
            
            if (now - last_screen_time).total_seconds() >= 7200:
                target_list = get_elite_tickers()
                last_screen_time = now
            
            if daily_stats["date"] != now.date():
                wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
                send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
                daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
                bot_state["loss_counts"] = {}
                bot_state["blacklist_times"] = {}
                valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")
                
                target_list = get_elite_tickers()
                last_screen_time = now

            for ticker in target_list:
                krw = upbit.get_balance("KRW")
                if krw is None:
                    time.sleep(5)
                    continue

                holdings_str = sell_manager(valid_krw_tickers)
                
                # 터미널 화면에도 최고 수익률(Max)을 함께 표시하여 추적 상황을 볼 수 있게 했습니다.
                sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 타겟: {ticker} | 잔고: {krw:,.0f}원    ")
                sys.stdout.flush()
                
                if krw > 5000: buy_manager(ticker, krw)
                time.sleep(0.3) 

        except Exception as e:
            time.sleep(5)
