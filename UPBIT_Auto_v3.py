import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys

# 1. API 키 및 텔레그램 설정 (본인 정보 입력)
access, secret = "QUA4RX6p9ZhFtZmbkx6xs3TPl9HJOfQY9FSXpiLd", "1qOhaGpd9unIYxinnaaHJYGGhZcQlc9eq0QP8euy"
upbit = pyupbit.Upbit(access, secret)
telegram_token, telegram_chat_id = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc", "8403406400"

# 2. 통계 및 상태 관리
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "last_date": datetime.datetime.now().date()}
bot_state = {}

def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """거래대금 상위 30개 종목 리스트 갱신"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        df_list = []
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is not None:
                df['ticker'] = ticker
                df_list.append(df)
            time.sleep(0.04)
        return pd.concat(df_list).sort_values(by='value', ascending=False).head(30)['ticker'].tolist()
    except: return pyupbit.get_tickers(fiat="KRW")[:30]

def check_buy_signals(ticker):
    """승률 강화를 위한 3중 필터 (추세 + 과열방지 + 눌림목 확인)"""
    try:
        # [필터 1] 거시 추세 (일봉 양봉 & 5일선 위)
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=10)
        df_day['ma5'] = df_day['close'].rolling(5).mean()
        if not (df_day['close'].iloc[-1] > df_day['open'].iloc[-1] and df_day['close'].iloc[-1] > df_day['ma5'].iloc[-1]):
            return False, 0
        
        # [필터 2] 미시 분석 (30분봉)
        df = pyupbit.get_ohlcv(ticker, interval="minute30", count=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['ema20'] = ta.ema(df['close'], length=20)
        
        curr_rsi = df['rsi'].iloc[-1]
        curr_price = df['close'].iloc[-1]
        ema20 = df['ema20'].iloc[-1]
        
        # 과열 방지: RSI 70 이상이면 진입 금지 (가장 중요)
        if curr_rsi > 70: return False, 0
        
        # 눌림목 확인: 가격이 EMA20선 근처에 있거나, 막 반등을 시작했는지 확인
        is_near_ema = (ema20 * 0.995 <= curr_price <= ema20 * 1.01)
        
        # 캔들 패턴 (상승장악형 또는 망치형)
        patterns = ["engulfing", "hammer"]
        cdl_df = df.ta.cdl_pattern(name=patterns)
        has_pattern = (cdl_df.iloc[-2].sum() > 0) # 직전 봉에서 패턴 완성
        
        # 거래량 필터 (평균 대비 1.2배 이상)
        vol_cond = df['volume'].iloc[-1] > (df['volume'].iloc[-6:-1].mean() * 1.2)
        
        if (has_pattern or is_near_ema) and vol_cond:
            return True, df['close'].mean() * 0.01 # ATR 대신 평균 변동폭의 1%를 기준점으로 사용
        return False, 0
    except: return False, 0

def manage_trade(ticker, krw_balance):
    global bot_state, daily_stats
    if ticker not in bot_state: bot_state[ticker] = {"status": "WAITING"}
    state = bot_state[ticker]
    price = pyupbit.get_current_price(ticker)
    if not price: return

    if state["status"] == "WAITING":
        buy_ok, vol_width = check_buy_signals(ticker)
        if buy_ok:
            # 돌파 가격을 더 타이트하게 조정 (0.3 -> 0.15) 하여 빠른 진입 유도
            state.update({"status": "PENDING", "target_price": price + (vol_width * 0.15), "width": vol_width})
            print(f"\n[{ticker}] 눌림목 포착, 진입 대기...")

    elif state["status"] == "PENDING":
        if price >= state["target_price"]:
            buy_amt = krw_balance * 0.2 # 종목당 20% 비중
            if buy_amt > 5000:
                upbit.buy_market_order(ticker, buy_amt * 0.9995)
                state.update({"status": "HOLDING", "buy_price": price, "highest": price})
                send_message(f"🚀 [{ticker}] 매수완료\n단가: {price:,.0f}원")

    elif state["status"] == "HOLDING":
        balance = upbit.get_balance(ticker)
        if balance < 1e-8:
            state["status"] = "WAITING"
            return
        
        if price > state["highest"]: state["highest"] = price
        buy_price = state["buy_price"]
        roi = ((price - buy_price) / buy_price) * 100
        
        # [V2.1 핵심 손익절]
        # 목표: 2~5%의 빠른 익절 / 손절은 평단가 대비 1.5% 하락 시 단호하게 실행
        sell_qty = 0
        if roi <= -1.5: # 손절선 단축 (3% 손실 방어용)
            sell_qty, reason = balance, "단호한 손절(-1.5%)"
        elif roi >= 2.5: # 2.5% 수익 시 전량 익절 (회전율 극대화)
            sell_qty, reason = balance, "목표 익절(+2.5%)"
        elif state["highest"] >= buy_price * 1.015 and price <= state["highest"] * 0.99:
            sell_qty, reason = balance, "익절 보존(고점대비 하락)"

        if sell_qty > 0:
            upbit.sell_market_order(ticker, sell_qty)
            daily_stats["trades"] += 1
            if roi > 0: daily_stats["wins"] += 1
            daily_stats["profit"] += (price - buy_price) * sell_qty
            send_message(f"💸 [{ticker}] {reason}\n수익률: {roi:.2f}%")
            state["status"] = "WAITING"

# --- 메인 실행 ---
target_list = get_target_tickers()
send_message("== 봇 V2.1(승률 강화) 가동 ==")

while True:
    try:
        now = datetime.datetime.now()
        if daily_stats["last_date"] != now.date():
            # 자정 결산
            wr = (daily_stats["wins"]/daily_stats["trades"]*100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 결산: 거래 {daily_stats['trades']}회, 승률 {wr:.1f}%, 수익 {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "last_date": now.date()})
            target_list = get_target_tickers()

        krw = upbit.get_balance("KRW")
        for ticker in target_list:
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] {ticker} 분석 중... 잔고: {krw:,.0f} ")
            sys.stdout.flush()
            manage_trade(ticker, krw)
            time.sleep(0.1)
    except Exception as e:
        time.sleep(10)
