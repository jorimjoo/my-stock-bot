import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys

# 1. API 키 및 텔레그램 설정 (본인 정보 입력 필수)
access = "QUA4RX6p9ZhFtZmbkx6xs3TPl9HJOfQY9FSXpiLd"
secret = "1qOhaGpd9unIYxinnaaHJYGGhZcQlc9eq0QP8euy"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
telegram_chat_id = "8403406400"

# 2. 설정 변수
STOP_LOSS_COOL_DOWN = 30  # 손절 후 재매수 금지 시간 (분)

# 3. 통계 및 상태 관리 변수
daily_stats = {
    "trades": 0, "wins": 0, "profit": 0.0,
    "last_report_date": datetime.datetime.now().date()
}
bot_state = {}

def send_message(msg):
    """텔레그램 메시지 전송"""
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """당일 거래대금 상위 30개 종목 추출 (시장 주도주 파악)"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        df_list = []
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is not None:
                df['ticker'] = ticker
                df_list.append(df)
            time.sleep(0.05)
        return pd.concat(df_list).sort_values(by='value', ascending=False).head(30)['ticker'].tolist()
    except: return pyupbit.get_tickers(fiat="KRW")[:30]

def get_market_data(ticker):
    """지표 분석: ATR, ADX, RSI 및 캔들 패턴"""
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute30", count=100)
        if df is None: return None
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=20)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        df = pd.concat([df, adx_df], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        cdl_df = df.ta.cdl_pattern(name=["engulfing", "hammer"])
        df = pd.concat([df, cdl_df], axis=1)
        return df
    except: return None

def check_buy_signals(ticker):
    """다중 필터: 추세 + 과열방지 + 캔들패턴 + 거래량 폭증"""
    try:
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=10)
        df_day['ma5'] = df_day['close'].rolling(5).mean()
        if not (df_day['close'].iloc[-1] > df_day['ma5'].iloc[-1]): return False, 0, 0
        
        df = get_market_data(ticker)
        if df is None: return False, 0, 0
        curr, prev = df.iloc[-1], df.iloc[-2]
        
        # 과열권(RSI>65) 및 완전 횡보(ADX<20) 진입 금지
        if curr['rsi'] > 65 or curr['ADX_14'] < 20: return False, 0, 0
        
        avg_vol = df['volume'].iloc[-6:-1].mean()
        if curr['volume'] > (avg_vol * 1.5) and (prev.filter(like='CDL_').sum() > 0):
            return True, curr['atr'], curr['ADX_14']
        return False, 0, 0
    except: return False, 0, 0

def manage_trade(ticker, krw_balance):
    global bot_state, daily_stats
    now = datetime.datetime.now()
    
    if ticker not in bot_state: 
        bot_state[ticker] = {"status": "WAITING", "last_loss_time": None}
        
    state = bot_state[ticker]
    price = pyupbit.get_current_price(ticker)
    if not price: return

    # [1] 매수 대기 상태 (진입 타점 및 비중 결정)
    if state["status"] == "WAITING":
        if state["last_loss_time"]:
            time_diff = (now - state["last_loss_time"]).total_seconds() / 60
            if time_diff < STOP_LOSS_COOL_DOWN: return # 손절 후 30분 유예
                
        buy_ok, atr, adx = check_buy_signals(ticker)
        if buy_ok:
            # 💡 최초 요청하신 "확률에 따른 투자 비중 동적 조절" 복구!
            # ADX가 25 이상이면 강한 추세(고승률)로 보아 30% 투자, 낮으면 10% 소액 투자
            invest_ratio = 0.3 if adx >= 25 else 0.1
            
            # 동적 손절 배수 (추세가 강하면 2.2배로 넉넉하게, 약하면 1.8배로 타이트하게)
            k_val = 2.2 if adx > 30 else 1.8
            
            state.update({
                "status": "BUY_PENDING", 
                "target_price": price + (atr * 0.1),
                "atr": atr, "k": k_val,
                "invest_ratio": invest_ratio
            })

    # [2] 돌파 진입 대기 (실제 매수 실행)
    elif state["status"] == "BUY_PENDING":
        if price >= state["target_price"]:
            buy_amt = krw_balance * state["invest_ratio"] # 동적 비중 적용
            if buy_amt > 5000:
                upbit.buy_market_order(ticker, buy_amt * 0.9995)
                state.update({"status": "HOLDING", "buy_price": price, "highest": price, "part1": True})
                send_message(f"🚀 [{ticker}] 매수 체결\n가: {price:,.0f}원\n비중: {state['invest_ratio']*100}%")

    # [3] 보유 및 수익 관리 (다중 스탑 로직)
    elif state["status"] == "HOLDING":
        balance = upbit.get_balance(ticker)
        if balance < 1e-8:
            state["status"] = "WAITING"
            return

        if price > state.get("highest", 0): state["highest"] = price
        buy_price, atr, k = state["buy_price"], state["atr"], state["k"]
        roi = ((price - buy_price) / buy_price) * 100
        
        sell_qty, reason = 0, ""

        # 익/손절 판별
        if price <= buy_price - (atr * k):
            sell_qty, reason = balance, f"동적 손절({k}xATR)"
        elif state["part1"] and price <= state["highest"] - atr and roi > 1.0:
            sell_qty, reason = balance * 0.5, "1차 익절(Trailing)"
            state["part1"] = False
        elif roi >= 3.5:
            sell_qty, reason = balance, "목표가 익절(+3.5%)"

        if sell_qty > 0:
            actual_qty = min(sell_qty, balance)
            if actual_qty * price < 5000: actual_qty = balance
            upbit.sell_market_order(ticker, actual_qty)
            
            # 일일 통계 누적
            daily_stats["trades"] += 1
            if roi > 0: daily_stats["wins"] += 1
            daily_stats["profit"] += (price - buy_price) * actual_qty
            send_message(f"💸 [{ticker}] {reason}\n수익률: {roi:.2f}%")
            
            # 전량 매도 발생 시 쿨다운 처리
            if balance - actual_qty < 1e-8:
                state["status"] = "WAITING"
                if roi < 0:
                    state["last_loss_time"] = now # 손절 시 30분 제한 시작
                    print(f"\n[!] {ticker} 손절 발생. 30분간 재진입 금지.")
                else:
                    state["last_loss_time"] = None # 익절 시 즉시 제한 해제
                    print(f"\n[+] {ticker} 익절 완료. 즉시 재진입 가능.")

# 4. 메인 무한 루프
send_message(f"== 쿠퍼춘봉 봇 V2.5 가동 ==\n(모든 최적화 조건 100% 적용)")
target_list = get_target_tickers()

while True:
    try:
        now = datetime.datetime.now()
        
        # 자정 결산 및 종목 갱신
        if daily_stats["last_report_date"] != now.date():
            wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 일일 결산 [{daily_stats['last_report_date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "last_report_date": now.date()})
            target_list = get_target_tickers()

        krw = upbit.get_balance("KRW")
        
        # 실시간 상태 하트비트
        for i, ticker in enumerate(target_list):
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 감시: {ticker} ({i+1}/{len(target_list)}) | KRW: {krw:,.0f} ")
            sys.stdout.flush()
            manage_trade(ticker, krw)
            time.sleep(0.1)
    except Exception as e:
        time.sleep(10)
