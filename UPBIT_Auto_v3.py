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

# 3. 통계 및 상태 관리 변수
daily_stats = {
    "trades": 0,
    "wins": 0,
    "profit": 0.0,
    "last_report_date": datetime.datetime.now().date()
}
bot_state = {}

def send_message(msg):
    """텔레그램 메시지 전송"""
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except Exception as e:
        print(f"\n[Error] 텔레그램 전송 실패: {e}")

def get_target_tickers():
    """당일 거래대금 상위 30개 종목 추출 (시장 주도주 집중)"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        df_list = []
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is not None:
                df['ticker'] = ticker
                df_list.append(df)
            time.sleep(0.04) # API 호출 제한 방지
        
        full_df = pd.concat(df_list)
        top_30 = full_df.sort_values(by='value', ascending=False).head(30)['ticker'].tolist()
        return top_30
    except Exception as e:
        print(f"\n[!] 종목 갱신 중 오류 발생: {e}")
        return pyupbit.get_tickers(fiat="KRW")[:30]

def get_custom_atr(ticker):
    """노이즈를 제거한 30분봉 기반 ATR 계산"""
    df = pyupbit.get_ohlcv(ticker, interval="minute30", count=50)
    if df is None: return 0
    tr = df['high'] - df['low']
    tr_sorted = tr.sort_values()
    if len(tr_sorted) == 50:
        tr_trimmed = tr_sorted.iloc[10:40]
        return tr_trimmed.mean()
    return tr.mean()

def check_buy_signals(ticker):
    """상승 추세 + 캔들 패턴 + 거래량 폭증 필터"""
    try:
        # 일봉 및 4시간봉 추세 확인
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=10)
        df_4h = pyupbit.get_ohlcv(ticker, interval="minute240", count=10)
        if df_day is None or df_4h is None: return False
        
        df_day['ma5'] = df_day['close'].rolling(5).mean()
        day_cond = (df_day['close'].iloc[-1] > df_day['open'].iloc[-1]) and (df_day['close'].iloc[-1] > df_day['ma5'].iloc[-1])
        if not day_cond: return False
        
        df_4h['ma5'] = df_4h['close'].rolling(5).mean()
        df_4h['ma10'] = df_4h['close'].rolling(10).mean()
        h4_cond = (df_4h['close'].iloc[-2] > df_4h['ma5'].iloc[-2]) and (df_4h['ma10'].iloc[-2] > df_4h['ma10'].iloc[-3])
        if not h4_cond: return False
        
        # 30분봉 상세 분석
        df_30m = pyupbit.get_ohlcv(ticker, interval="minute30", count=40)
        if df_30m is None: return False
        
        # 거래량 필터: 최근 5봉 평균 대비 150% 이상
        avg_vol = df_30m['volume'].iloc[-6:-1].mean()
        if df_30m['volume'].iloc[-1] <= (avg_vol * 1.5): return False
        
        # 캔들 패턴 인식
        df_30m['ma20'] = df_30m['close'].rolling(20).mean()
        patterns = ["morningstar", "engulfing", "hammer"]
        cdl_df = df_30m.ta.cdl_pattern(name=patterns)
        if cdl_df is None: return False
        
        df = pd.concat([df_30m, cdl_df], axis=1)
        # 이전 봉 완성 시점 기준 패턴 확인
        pattern_cond = (df.get('CDL_MORNINGSTAR', pd.Series([0])).iloc[-2] > 0) or \
                       (df.get('CDL_ENGULFING', pd.Series([0])).iloc[-2] > 0)
        
        return pattern_cond
    except:
        return False

def manage_virtual_orders(ticker, krw_balance):
    """가상 주문 관리 및 실전 매매 실행"""
    global bot_state, daily_stats
    if ticker not in bot_state: bot_state[ticker] = {"status": "WAITING"}
    state = bot_state[ticker]
    
    current_price = pyupbit.get_current_price(ticker)
    if current_price is None: return

    if state["status"] == "WAITING":
        if check_buy_signals(ticker):
            atr = get_custom_atr(ticker)
            stop_buy_price = current_price + (atr * 0.3)
            state.update({"status": "BUY_PENDING", "stop_buy_price": stop_buy_price, "target_atr": atr})
            print(f"\n[신호] {ticker} 돌파 매수 대기 시작 (목표가: {stop_buy_price:,.0f}원)")

    elif state["status"] == "BUY_PENDING":
        if current_price >= state["stop_buy_price"]:
            buy_amount = krw_balance * 0.25
            if buy_amount > 5000:
                upbit.buy_market_order(ticker, buy_amount * 0.9995)
                state.update({"status": "HOLDING", "buy_price": current_price, "initial_balance": upbit.get_balance(ticker)})
                state["highest"] = current_price
                state["part1"] = state["part2"] = True
                send_message(f"🚀 [{ticker}] 돌파 성공! 매수 체결\n체결가: {current_price:,.0f}원")

    elif state["status"] == "HOLDING":
        balance = upbit.get_balance(ticker)
        if balance < 1e-8: # 전량 매도 시 상태 초기화
            state["status"] = "WAITING"
            return

        if current_price > state.get("highest", 0): state["highest"] = current_price
        buy_price = state["buy_price"]
        atr = state["target_atr"]
        roi = ((current_price - buy_price) / buy_price) * 100

        sell_qty = 0
        reason = ""

        # 익절/손절 로직
        if current_price <= buy_price - (atr * 1.5):
            sell_qty, reason = balance, "절대손절(-1.5ATR)"
        elif state["part1"] and current_price <= state["highest"] - atr:
            sell_qty, reason = balance * 0.5, "1차익절(고점대비-1ATR)"
            state["part1"] = False
        elif state["part2"] and current_price >= buy_price + (atr * 3.0):
            sell_qty, reason = balance, "전량익절(+3ATR 도달)"

        if sell_qty > 0:
            actual_qty = min(sell_qty, balance)
            if actual_qty * current_price < 5000: actual_qty = balance
            upbit.sell_market_order(ticker, actual_qty)
            
            # 수익금 합계 계산 (간략화)
            daily_stats["trades"] += 1
            if roi > 0: daily_stats["wins"] += 1
            daily_stats["profit"] += (current_price - buy_price) * actual_qty
            send_message(f"💸 [{ticker}] {reason}\n가: {current_price:,.0f}원\n수익률: {roi:.2f}%")

# --- 메인 루프 ---
send_message("== 쿠퍼춘봉 단타 봇 V2.0 가동 ==\n(실시간 감시 모드 활성화)")
target_list = get_target_tickers()

while True:
    try:
        now = datetime.datetime.now()
        
        # 매일 자정 통계 리포트 및 종목 갱신
        if daily_stats["last_report_date"] != now.date():
            wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 일일 결산 [{daily_stats['last_report_date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "last_report_date": now.date()})
            target_list = get_target_tickers()

        krw = upbit.get_balance("KRW")
        
        # [시스템 진행상황 업데이트]
        # 터미널의 한 줄에서 정보가 계속 바뀝니다 (Heartbeat 기능)
        for i, ticker in enumerate(target_list):
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 감시 중: {ticker} ({i+1}/{len(target_list)}) | KRW: {krw:,.0f}원  ")
            sys.stdout.flush()
            
            manage_virtual_orders(ticker, krw)
            time.sleep(0.1) # API 부하 분산

    except Exception as e:
        print(f"\n[!] 메인 루프 예외 발생: {e}")
        time.sleep(10)
