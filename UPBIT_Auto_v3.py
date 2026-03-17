import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys

# 1. API 키 및 텔레그램 설정
# 🚨 경고: 이 코드를 절대 외부에 공개하지 마세요!
access = "QUA4RX6p9ZhFtZmbkx6xs3TPl9HJOfQY9FSXpiLd"
secret = "1qOhaGpd9unIYxinnaaHJYGGhZcQlc9eq0QP8euy"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
telegram_chat_id = "8403406400"

# 2. 핵심 설정 변수
STOP_LOSS_COOL_DOWN = 30  # 손절 후 재매수 금지 시간 (분)
TARGET_ROI = 0.5          # 초단타 전량 목표 수익률 (%)

# 3. 통계 및 상태 관리
daily_stats = {
    "trades": 0, "wins": 0, "profit": 0.0,
    "last_report_date": datetime.datetime.now().date()
}
bot_state = {}

def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """당일 거래대금 상위 30개 종목 추출"""
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

def get_all_balances():
    """계좌 전체를 한 번에 스캔하여 API 지연(기억 상실) 오류 방지"""
    try:
        balances = upbit.get_balances()
        bal_dict = {}
        for b in balances:
            if b['currency'] == 'KRW':
                bal_dict['KRW'] = float(b['balance'])
            else:
                ticker = f"KRW-{b['currency']}"
                bal_dict[ticker] = {
                    'balance': float(b['balance']) + float(b['locked']),
                    'avg_buy_price': float(b['avg_buy_price'])
                }
        return bal_dict
    except: return None

def get_market_data(ticker):
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
    try:
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=10)
        df_day['ma5'] = df_day['close'].rolling(5).mean()
        if not (df_day['close'].iloc[-1] > df_day['ma5'].iloc[-1]): return False, 0, 0
        
        df = get_market_data(ticker)
        if df is None: return False, 0, 0
        curr, prev = df.iloc[-1], df.iloc[-2]
        
        if curr['rsi'] > 65 or curr['ADX_14'] < 20: return False, 0, 0
        
        avg_vol = df['volume'].iloc[-6:-1].mean()
        if curr['volume'] > (avg_vol * 1.5) and (prev.filter(like='CDL_').sum() > 0):
            return True, curr['atr'], curr['ADX_14']
        return False, 0, 0
    except: return False, 0, 0

def manage_trade(ticker, krw_balance, my_balances):
    global bot_state, daily_stats
    now = datetime.datetime.now()
    
    if ticker not in bot_state: 
        bot_state[ticker] = {"status": "WAITING", "last_loss_time": None}
    state = bot_state[ticker]
    price = pyupbit.get_current_price(ticker)
    if not price: return

    # [중요] 지갑에 코인이 있는지 먼저 스캔 (자동 복구 로직)
    hold_info = my_balances.get(ticker)
    balance = hold_info['balance'] if hold_info else 0.0
    
    # 봇이 WAITING 인데 실제 지갑엔 코인이 있다면 즉시 감시(HOLDING) 모드로 강제 복원
    if balance * price > 5000 and state["status"] in ["WAITING", "BUY_PENDING"]:
        avg_buy = hold_info['avg_buy_price']
        if avg_buy == 0: avg_buy = price
        state.update({
            "status": "HOLDING", "buy_price": avg_buy, "highest": price,
            "atr": price * 0.015, "k": 1.2, "buy_time": now
        })
        print(f"\n[🔄] {ticker} 보유 물량 확인! 익절/손절 감시 재개")
        return

    # [1] 매수 대기 상태
    if state["status"] == "WAITING":
        if state["last_loss_time"]:
            time_diff = (now - state["last_loss_time"]).total_seconds() / 60
            if time_diff < STOP_LOSS_COOL_DOWN: return # 손절 후 30분 절대 금지
                
        buy_ok, atr, adx = check_buy_signals(ticker)
        if buy_ok:
            invest_ratio = 0.3 if adx >= 25 else 0.1
            k_val = 1.5 if adx > 30 else 1.0 # 타이트한 스캘핑 손절 폭
            state.update({
                "status": "BUY_PENDING", "target_price": price + (atr * 0.1),
                "atr": atr, "k": k_val, "invest_ratio": invest_ratio
            })

    # [2] 돌파 진입
    elif state["status"] == "BUY_PENDING":
        if price >= state["target_price"]:
            buy_amt = krw_balance * state["invest_ratio"]
            if buy_amt > 5000:
                upbit.buy_market_order(ticker, buy_amt * 0.9995)
                state.update({"status": "HOLDING", "buy_price": price, "highest": price, "buy_time": now})
                send_message(f"🚀 [{ticker}] 매수 체결\n가: {price:,.0f}원")

    # [3] 전량 매도 관리 (0.5% 익절 / 동적 손절)
    elif state["status"] == "HOLDING":
        # 매수 직후 10초 내에는 API 지연을 고려해 섣불리 0원으로 판단하지 않음
        if balance == 0:
            if (now - state.get("buy_time", now)).total_seconds() < 10: return
            else:
                state["status"] = "WAITING"
                return

        if price > state.get("highest", 0): state["highest"] = price
        buy_price, atr, k = state["buy_price"], state["atr"], state["k"]
        roi = ((price - buy_price) / buy_price) * 100
        
        sell_qty, reason = 0, ""

        # [수정됨] 무조건 100% 전량 매도 처리
        if roi >= TARGET_ROI:
            sell_qty, reason = balance, f"초단타 전량 익절(+{TARGET_ROI}%)"
        elif price <= buy_price - (atr * k):
            sell_qty, reason = balance, f"타이트한 손절({k}xATR)"

        if sell_qty > 0:
            upbit.sell_market_order(ticker, sell_qty)
            
            daily_stats["trades"] += 1
            if roi > 0: daily_stats["wins"] += 1
            daily_stats["profit"] += (price - buy_price) * sell_qty
            send_message(f"💸 [{ticker}] {reason}\n수익률: {roi:.2f}%")
            
            state["status"] = "WAITING"
            if roi < 0:
                state["last_loss_time"] = now # 손절 시 30분 쿨다운 시작
                print(f"\n[!] {ticker} 손절 발생. 30분간 재진입 금지.")
            else:
                state["last_loss_time"] = None # 익절 시 즉시 초기화
                print(f"\n[+] {ticker} 익절 완료. 즉시 재진입 가능.")

# 4. 메인 무한 루프
send_message(f"== 쿠퍼춘봉 봇 V2.8 가동 ==\n(0.5% 전량 익절 & 지갑 동기화)")
target_list = get_target_tickers()

while True:
    try:
        now = datetime.datetime.now()
        
        if daily_stats["last_report_date"] != now.date():
            wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 일일 결산 [{daily_stats['last_report_date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "last_report_date": now.date()})
            target_list = get_target_tickers()

        # 전체 지갑을 한 번만 스캔해서 API 부하와 지연을 방지
        my_balances = get_all_balances()
        if my_balances is None:
            time.sleep(1)
            continue
            
        krw = my_balances.get('KRW', 0)
        
        for i, ticker in enumerate(target_list):
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 감시: {ticker} ({i+1}/{len(target_list)}) | KRW: {krw:,.0f} ")
            sys.stdout.flush()
            manage_trade(ticker, krw, my_balances)
            time.sleep(0.1)
    except Exception as e:
        time.sleep(10)
