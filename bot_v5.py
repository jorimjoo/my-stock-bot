import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys
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
# 2. 핵심 설정 (손익비 & 블랙리스트)
# ==========================================
TARGET_ROI = 1.0          
STOP_LOSS_ROI = -0.7      
BLACKLIST_HOURS = 24      

bot_state = {"loss_counts": {}, "blacklist_times": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}
total_stats = {"trades": 0, "wins": 0, "profit": 0.0, "start_time": datetime.datetime.now()}

# ==========================================
# 3. 🌐 가짜 웹 서버
# ==========================================
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "쿠퍼춘봉 스캘핑 봇 V6.1 (타겟 확장 & MACD 필터 모드) 가동 중! 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 봇 핵심 로직 (V6.1)
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_elite_tickers():
    """🔥 V6.1: 1시간봉 기반으로 스크리닝 조건을 완화하여 타겟 15개로 확장"""
    send_message("🔍 [시스템] 타겟 확장을 위한 1시간봉 주도주 스크리닝을 시작합니다...")
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        
        response = requests.get(url, headers=headers, params={"markets": ",".join(tickers)})
        data = response.json()
        
        candidates = []
        for item in data:
            # 거래대금 50억 이상, 당일 상승 중인 코인으로 1차 허들 낮춤
            if item['acc_trade_price_24h'] > 5000000000 and item['signed_change_rate'] > 0:
                candidates.append(item)
                
        candidates = sorted(candidates, key=lambda x: x['acc_trade_price_24h'], reverse=True)[:30]
        top_tickers = [c['market'] for c in candidates]
        
        elite_tickers = []
        for ticker in top_tickers:
            time.sleep(0.1) 
            # 일봉 대신 1시간봉(minute60)을 사용하여 더 많은 종목 포착
            df_hour = pyupbit.get_ohlcv(ticker, interval="minute60", count=30)
            if df_hour is None or len(df_hour) < 30: continue
                
            ma5 = df_hour['close'].rolling(5).mean().iloc[-1]
            ma20 = df_hour['close'].rolling(20).mean().iloc[-1]
            curr_price = df_hour['close'].iloc[-1]
            
            # 1시간봉 기준 단기 우상향 (현재가 > 20선, 5선 > 20선)
            if curr_price > ma20 and ma5 > ma20:
                elite_tickers.append(ticker)
                
            if len(elite_tickers) >= 15: # 타겟을 15개까지 넉넉하게 확장
                break
                
        send_message(f"🎯 [스크리닝 완료] 1시간봉 주도주 {len(elite_tickers)}개 포착!\n" + ", ".join([t.replace("KRW-", "") for t in elite_tickers]))
        return elite_tickers if elite_tickers else top_tickers[:10]
        
    except Exception as e:
        send_message(f"⚠️ 스크리닝 에러: {e}")
        return pyupbit.get_tickers(fiat="KRW")[:15]

def sell_manager(valid_krw_tickers):
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

            if roi >= TARGET_ROI or roi <= STOP_LOSS_ROI:
                if balance * curr_price > 5000:
                    upbit.sell_market_order(ticker, balance)
                    reason = f"✅ 목표 익절 (+{roi:.2f}%)" if roi > 0 else f"❌ 칼손절 ({roi:.2f}%)"
                    profit_krw = (curr_price - buy_price) * balance
                    
                    daily_stats["trades"] += 1
                    total_stats["trades"] += 1
                    daily_stats["profit"] += profit_krw
                    total_stats["profit"] += profit_krw
                    
                    if roi > 0: 
                        daily_stats["wins"] += 1
                        total_stats["wins"] += 1
                    
                    if roi < 0:
                        bot_state["loss_counts"][ticker] = bot_state["loss_counts"].get(ticker, 0) + 1
                        if bot_state["loss_counts"][ticker] >= 2:
                            bot_state["blacklist_times"][ticker] = now
                            send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n🚨 2회 손절로 24시간 매수 금지!")
                        else:
                            send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n⚠️ 1회 경고 누적")
                    else:
                        bot_state["loss_counts"][ticker] = 0
                        send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}")
                    continue 

            display_info.append(f"[{ticker} {roi:+.2f}%]")
            
        return " ".join(display_info) if display_info else "없음"
    except Exception as e: 
        return f"에러({e})"

def buy_manager(ticker, krw_balance):
    global bot_state
    now = datetime.datetime.now()

    blacklist_time = bot_state["blacklist_times"].get(ticker)
    if blacklist_time and (now - blacklist_time).total_seconds() / 3600 < BLACKLIST_HOURS: return
    if upbit.get_balance(ticker) > 0: return

    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=60)
    if df is None or len(df) < 60: return
        
    # 이동평균 및 RSI
    df['ma20'] = ta.sma(df['close'], length=20)
    df['ma60'] = ta.sma(df['close'], length=60)
    df['rsi'] = ta.rsi(df['close'], length=14)
    
    # 🔥 가짜 반등 방지용 MACD 직접 계산 (가장 안정적)
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=9, adjust=False).mean()
    df['macd_hist'] = macd - signal # 히스토그램 (MACD - 시그널)
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    
    # [조건 1] 단기 추세 우상향 유지
    trend_ok = curr['ma20'] > curr['ma60']
    
    # [조건 2] 과매도 구간 (낙폭 발생)
    rsi_ok = curr['rsi'] < 40 
    
    # 🔥 [조건 3] 가짜 반등 방지: MACD 히스토그램이 상승으로 꺾여야 함 (하락 에너지가 끝나는 지점)
    macd_ok = (curr['macd_hist'] > prev['macd_hist']) and (prev['macd_hist'] > prev2['macd_hist'])
    
    # 🔥 [조건 4] 캔들 확인: 떨어지는 음봉이 아니라 반등하는 양봉일 때만
    candle_ok = curr['close'] > curr['open']
    
    if trend_ok and rsi_ok and macd_ok and candle_ok:
        buy_amt = krw_balance * 0.20 
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            send_message(f"🚀 [{ticker}] 안전 반등(MACD 턴어라운드) 포착!\n- 매수가: {curr['close']:,.0f}원\n- RSI: {curr['rsi']:.1f}")

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
                        wr = (total_stats['wins'] / total_stats['trades'] * 100) if total_stats['trades'] > 0 else 0
                        d_wr = (daily_stats['wins'] / daily_stats['trades'] * 100) if daily_stats['trades'] > 0 else 0
                        start_str = total_stats['start_time'].strftime('%m/%d %H:%M')
                        
                        reply = (
                            f"📊 [쿠퍼춘봉 스나이퍼 현황]\n"
                            f"⏱ 가동: {start_str}\n\n"
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
# 5. 메인 실행부
# ==========================================
if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    Thread(target=telegram_listener, daemon=True).start()
    
    send_message("== 쿠퍼춘봉 스캘핑 봇 V6.1 가동 ==\n(🦅 타겟 확장 & MACD 가짜반등 완벽 방어)")
    
    valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")
    
    target_list = get_elite_tickers()
    last_screen_time = datetime.datetime.now()

    while True:
        try:
            now = datetime.datetime.now()
            
            # 매 1시간마다 새로운 대장주 스크리닝 (두뇌 리셋)
            if (now - last_screen_time).total_seconds() >= 3600:
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
                sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 타겟: {ticker} | 잔고: {krw:,.0f}원    ")
                sys.stdout.flush()
                
                if krw > 5000: buy_manager(ticker, krw)
                time.sleep(0.3) 

        except Exception as e:
            time.sleep(5)
