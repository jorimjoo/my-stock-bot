import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys

# 1. 업비트 API 키 설정
access = "QUA4RX6p9ZhFtZmbkx6xs3TPl9HJOfQY9FSXpiLd"
secret = "1qOhaGpd9unIYxinnaaHJYGGhZcQlc9eq0QP8euy"
upbit = pyupbit.Upbit(access, secret)

# 2. 텔레그램 설정
telegram_token = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
telegram_chat_id = "8403406400"

def send_message(msg):
    """텔레그램으로 메시지를 전송하는 함수"""
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except Exception as e:
        print(f"\n텔레그램 전송 에러: {e}")

# 상태 관리를 위한 딕셔너리 (전 종목 무제한 모니터링)
bot_state = {}

# 3. 봇 가동 시작 메시지
start_msg = "== 업비트 봇 가동 시작 =="
print(start_msg)
send_message(start_msg)

def get_target_tickers():
    """KRW 마켓의 모든 종목을 가져옵니다"""
    return pyupbit.get_tickers(fiat="KRW")

def get_custom_atr(ticker):
    """최근 50개 캔들의 TR 중 노이즈(상/하위 10개)를 제거한 30개의 평균 ATR 계산"""
    df = pyupbit.get_ohlcv(ticker, interval="minute30", count=50)
    if df is None: return 0
    
    tr = df['high'] - df['low']
    tr_sorted = tr.sort_values()
    
    if len(tr_sorted) == 50:
        tr_trimmed = tr_sorted.iloc[10:40]
        return tr_trimmed.mean()
    return tr.mean()

def check_candle_patterns(df_30m):
    """캔들 패턴 인식 (A그룹: 무조건 강세, B그룹: 20선 상승 시 강세)"""
    try:
        patterns = [
            "morningstar", "morningdojistar", "engulfing",
            "piercing", "hammer", "invertedhammer",
            "dragonflydoji", "3whitesoldiers"
        ]
        
        cdl_df = df_30m.ta.cdl_pattern(name=patterns)
        if cdl_df is None or cdl_df.empty: return False
            
        df = pd.concat([df_30m, cdl_df], axis=1)
        
        group_a = (
            (df.get('CDL_MORNINGSTAR', pd.Series([0])).iloc[-2] > 0) or
            (df.get('CDL_MORNINGDOJISTAR', pd.Series([0])).iloc[-2] > 0) or
            (df.get('CDL_ENGULFING', pd.Series([0])).iloc[-2] > 0)
        )
        
        group_b_pattern = (
            (df.get('CDL_PIERCING', pd.Series([0])).iloc[-2] > 0) or
            (df.get('CDL_HAMMER', pd.Series([0])).iloc[-2] > 0) or
            (df.get('CDL_INVERTEDHAMMER', pd.Series([0])).iloc[-2] > 0) or
            (df.get('CDL_DRAGONFLYDOJI', pd.Series([0])).iloc[-2] > 0) or
            (df.get('CDL_3WHITESOLDIERS', pd.Series([0])).iloc[-2] > 0)
        )
        
        ma20_increasing = df['ma20'].iloc[-2] > df['ma20'].iloc[-3]
        group_b = group_b_pattern and ma20_increasing
        
        return group_a or group_b
        
    except Exception:
        return False

def check_buy_signals(ticker):
    """1차(일봉, 4시간봉 추세) 및 2차(30분봉 캔들 패턴) 필터 확인"""
    try:
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=10)
        if df_day is None: return False
        df_day['ma5'] = df_day['close'].rolling(5).mean()
        if not ((df_day['close'].iloc[-1] > df_day['open'].iloc[-1]) and (df_day['close'].iloc[-1] > df_day['ma5'].iloc[-1])):
            return False
        
        df_4h = pyupbit.get_ohlcv(ticker, interval="minute240", count=10)
        if df_4h is None: return False
        df_4h['ma5'] = df_4h['close'].rolling(5).mean()
        df_4h['ma10'] = df_4h['close'].rolling(10).mean()
        
        prev_4h_close = df_4h['close'].iloc[-2]
        ma10_increasing = df_4h['ma10'].iloc[-2] > df_4h['ma10'].iloc[-3]
        if not ((prev_4h_close > df_4h['ma5'].iloc[-2]) and (ma10_increasing or prev_4h_close > df_4h['ma10'].iloc[-2])):
            return False
        
        df_30m = pyupbit.get_ohlcv(ticker, interval="minute30", count=40)
        if df_30m is None: return False
        df_30m['ma20'] = df_30m['close'].rolling(20).mean()
        
        return check_candle_patterns(df_30m)
    except Exception:
        return False

def manage_virtual_orders(ticker, krw_balance):
    """가상 주문(돌파매수, 분할 익절, 트레일링 스탑, 스탑로스) 관리"""
    global bot_state
    
    if ticker not in bot_state:
        bot_state[ticker] = {"status": "WAITING"}
        
    state = bot_state[ticker]
    current_price = pyupbit.get_current_price(ticker)
    if current_price is None: return

    # [상태 1] 매수 신호 감시
    if state["status"] == "WAITING":
        if check_buy_signals(ticker):
            atr = get_custom_atr(ticker)
            df_30m = pyupbit.get_ohlcv(ticker, interval="minute30", count=25)
            ma20 = df_30m['close'].rolling(20).mean().iloc[-1] if df_30m is not None else 0
            
            k_val = 0.5 if current_price < ma20 else 0.3
            stop_buy_price = current_price + (atr * k_val)
            
            state["status"] = "BUY_PENDING"
            state["stop_buy_price"] = stop_buy_price
            state["target_atr"] = atr
            
            msg = f"\n🔍 [{ticker}] 패턴 포착! 돌파 매수 대기\n- 현재가: {current_price:,.0f}원\n- 목표가: {stop_buy_price:,.0f}원"
            print(msg)

    # [상태 2] 돌파 매수 대기
    elif state["status"] == "BUY_PENDING":
        if current_price >= state["stop_buy_price"]:
            buy_amount = krw_balance * 0.2
            if buy_amount > 5000:
                upbit.buy_market_order(ticker, buy_amount * 0.9995)
                state["status"] = "HOLDING"
                state["buy_price"] = current_price
                
                msg = f"\n🚀 [{ticker}] 돌파 매수 체결!\n- 체결가: {current_price:,.0f}원\n- 매수금액: {buy_amount:,.0f}원"
                print(msg)
                send_message(msg)

    # [상태 3] 보유 및 4분할 매도 감시
    elif state["status"] == "HOLDING":
        balance = upbit.get_balance(ticker)
        if balance == 0 or balance is None: 
            state["status"] = "WAITING"
            state.pop("initial_balance", None)
            return

        if "initial_balance" not in state:
            state["initial_balance"] = balance
            state["global_highest"] = current_price
            state["part4_highest"] = 0.0
            state["part1"] = state["part2"] = state["part3"] = state["part4"] = True

        initial_balance = state["initial_balance"]
        buy_price = state.get("buy_price", current_price)
        atr = state.get("target_atr", current_price * 0.02)
        
        if current_price > state["global_highest"]:
            state["global_highest"] = current_price
        if current_price >= buy_price + (atr * 3) and current_price > state["part4_highest"]:
            state["part4_highest"] = current_price

        global_highest = state["global_highest"]
        part4_highest = state["part4_highest"]

        sell_all = False
        sell_reason = ""

        # 1. 절대 방어막 (전량 매도)
        if current_price <= buy_price - (atr * 1.5):
            sell_all = True
            sell_reason = "절대 손절 (-1.5 ATR 이탈)"
        elif global_highest >= buy_price + (atr * 2) and current_price <= global_highest - (atr * 1.5):
            sell_all = True
            sell_reason = "트레일링 스탑 (수익권 진입 후 -1.5 ATR 하락)"

        if sell_all:
            upbit.sell_market_order(ticker, balance)
            roi = ((current_price - buy_price) / buy_price) * 100
            msg = f"\n🚨 [{ticker}] 방어막 전량 매도\n- 사유: {sell_reason}\n- 매도가: {current_price:,.0f}원\n- 수익률: {roi:.2f}%"
            print(msg)
            send_message(msg)
            
            state["status"] = "WAITING"
            state.pop("initial_balance", None)
            return

        # 2. 4분할 매도
        chunks_to_sell = 0
        split_reason = ""
        is_small_amount = (balance * current_price < 21000)

        if state["part1"] and current_price <= global_highest - (atr * 1):
            chunks_to_sell += 1
            state["part1"] = False
            split_reason += "[1분할: 즉시 스탑] "
            
        if state["part2"] and current_price >= buy_price + (atr * 2):
            chunks_to_sell += 1
            state["part2"] = False
            split_reason += "[2분할: +2 ATR] "
            
        if state["part3"] and current_price >= buy_price + (atr * 3):
            chunks_to_sell += 1
            state["part3"] = False
            split_reason += "[3분할: +3 ATR] "
            
        if state["part4"] and part4_highest > 0 and current_price <= part4_highest - (atr * 1):
            chunks_to_sell += 1
            state["part4"] = False
            split_reason += "[4분할: +3 ATR 이후 스탑] "

        if chunks_to_sell > 0:
            if is_small_amount:
                sell_qty = balance 
            else:
                sell_qty = min(initial_balance * 0.25 * chunks_to_sell, balance)
                
            if (balance - sell_qty) * current_price < 5000:
                sell_qty = balance
                
            upbit.sell_market_order(ticker, sell_qty)
            roi = ((current_price - buy_price) / buy_price) * 100
            msg = f"\n💸 [{ticker}] 분할 매도 체결\n- 사유: {split_reason}\n- 매도가: {current_price:,.0f}원\n- 수익률: {roi:.2f}%"
            print(msg)
            send_message(msg)

# 4. 메인 루프 실행
while True:
    try:
        krw_balance = upbit.get_balance("KRW")
        tickers = get_target_tickers()
        
        # 현재 시간과 모니터링 상태를 터미널 한 줄에 계속 업데이트 (Heartbeat)
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sys.stdout.write(f"\r[{current_time}] 업비트 전 종목 타점 감시 중... 👀")
        sys.stdout.flush()
        
        for ticker in tickers:
            manage_virtual_orders(ticker, krw_balance)
            time.sleep(0.1)
            
        time.sleep(5)
        
    except Exception as e:
        error_msg = f"\n⚠️ 봇 메인 루프 에러 발생: {e}"
        print(error_msg)
        time.sleep(5)
