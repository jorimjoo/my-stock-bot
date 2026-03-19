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
    return "쿠퍼춘봉 스캘핑 봇 V6.0 (최정예 주도주 스크리닝 모드) 가동 중! 🦅"

def run_server():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 4. 봇 핵심 로직 (V6.0 종목 선정 특화)
# ==========================================
def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_elite_tickers():
    """🔥 V6.0 핵심: 3중 필터링을 거친 최정예 주도주 10개 추출"""
    send_message("🔍 [시스템] 새로운 최정예 주도주 스크리닝을 시작합니다...")
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        url = "https://api.upbit.com/v1/ticker"
        headers = {"accept": "application/json"}
        
        # 1차 필터링 (스냅샷 API): 당일 양봉 & 거래대금 100억 이상
        response = requests.get(url, headers=headers, params={"markets": ",".join(tickers)})
        data = response.json()
        
        candidates = []
        for item in data:
            # acc_trade_price_24h (24시간 거래대금), signed_change_rate (부호 있는 변화율)
            if item['acc_trade_price_24h'] > 10000000000 and item['signed_change_rate'] > 0.01:
                candidates.append(item)
                
        # 거래대금 순으로 1차 후보군 상위 20개 압축
        candidates = sorted(candidates, key=lambda x: x['acc_trade_price_24h'], reverse=True)[:20]
        top_20_tickers = [c['market'] for c in candidates]
        
        # 2차 필터링 (일봉 정배열): 지하실 코인 원천 차단
        elite_tickers = []
        for ticker in top_20_tickers:
            time.sleep(0.1) # API Rate Limit 보호
            df_day = pyupbit.get_ohlcv(ticker, interval="day", count=60)
            if df_day is None or len(df_day) < 60: continue
                
            ma20 = df_day['close'].rolling(20).mean().iloc[-1]
            ma60 = df_day['close'].rolling(60).mean().iloc[-1]
            curr_price = df_day['close'].iloc[-1]
            
            # 완벽한 정배열 상승 추세 (현재가 > 20일선 > 60일선)
            if curr_price > ma20 and ma20 > ma60:
                elite_tickers.append(ticker)
                
            if len(elite_tickers) >= 10: # 최정예 10개만 확보하면 스크리닝 종료
                break
                
        send_message(f"🎯 [스크리닝 완료] 최정예 대장주 {len(elite_tickers)}개 포착!\n" + ", ".join([t.replace("KRW-", "") for t in elite_tickers]))
        return elite_tickers if elite_tickers else top_20_tickers[:5]
        
    except Exception as e:
        send_message(f"⚠️ 스크리닝 에러: {e}")
        return pyupbit.get_tickers(fiat="KRW")[:10]

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
        
    df['ma20'] = ta.sma(df['close'], length=20)
    df['ma60'] = ta.sma(df['close'], length=60)
    df['rsi'] = ta.rsi(df['close'], length=14)
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    trend_ok = curr['ma20'] > curr['ma60']
    rsi_ok = curr['rsi'] < 40 # 과매도 눌림목
    rebound_ok = curr['close'] > prev['close'] # 반등 확인
    
    if trend_ok and rsi_ok and rebound_ok:
        buy_amt = krw_balance * 0.20 
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            send_message(f"🚀 [{ticker}] 1급 주도주 눌림목 포착!\n- 매수가: {curr['close']:,.0f}원\n- RSI: {curr['rsi']:.1f}")

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
    
    send_message("== 쿠퍼춘봉 스캘핑 봇 V6.0 가동 ==\n(🦅 3중 필터링 주도주 압축 알고리즘 탑재)")
    
    valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")
    
    # 최초 스크리닝 실행
    target_list = get_elite_tickers()
    last_screen_time = datetime.datetime.now()

    while True:
        try:
            now = datetime.datetime.now()
            
            # 매 1시간마다 새로운 대장주 스크리닝 (두뇌 리셋)
            if (now - last_screen_time).total_seconds() >= 3600:
                target_list = get_elite_tickers()
                last_screen_time = now
            
            # 자정 통계 정산 및 블랙리스트 사면
            if daily_stats["date"] != now.date():
                wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
                send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
                daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
                bot_state["loss_counts"] = {}
                bot_state["blacklist_times"] = {}
                valid_krw_tickers = pyupbit.get_tickers(fiat="KRW")
                
                # 자정에도 강력한 스크리닝 1회 실행
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
