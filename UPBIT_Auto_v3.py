import time
import pyupbit
import pandas as pd
import pandas_ta as ta
import requests
import datetime
import sys

# 1. API 키 및 텔레그램 설정 (본인 정보로 수정)
access = "QUA4RX6p9ZhFtZmbkx6xs3TPl9HJOfQY9FSXpiLd"
secret = "1qOhaGpd9unIYxinnaaHJYGGhZcQlc9eq0QP8euy"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
telegram_chat_id = "8403406400"

# 2. 통계 및 상태 관리 변수
daily_stats = {
    "trades": 0,
    "wins": 0,
    "profit": 0.0,
    "last_report_date": datetime.datetime.now().date()
}
bot_state = {}

def send_message(msg):
    """텔레그램 메시지 전송 함수"""
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except Exception as e:
        print(f"\n[Error] 텔레그램 전송 실패: {e}")

def get_target_tickers():
    """거래대금 상위 30개 종목 추출 (시장 주도주 집중)"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        df_list = []
        for ticker in tickers:
            df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
            if df is not None:
                df['ticker'] = ticker
                df_list.append(df)
            time.sleep(0.05)
        
        full_df = pd.concat(df_list)
        top_30 = full_df.sort_values(by='value', ascending=False).head(30)['ticker'].tolist()
        return top_30
    except Exception as e:
        print(f"\n[!] 종목 갱신 중 오류: {e}")
        return pyupbit.get_tickers(fiat="KRW")[:30]

def get_market_data(ticker):
    """지표 분석: ATR(20), ADX(14), RSI(14) 및 이평선 계산"""
    try:
        df = pyupbit.get_ohlcv(ticker, interval="minute30", count=100)
        if df is None: return None
        
        # 지표 계산
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=20)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        df = pd.concat([df, adx_df], axis=1)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['ema20'] = ta.ema(df['close'], length=20)
        
        # 캔들 패턴 (장악형, 망치형)
        patterns = ["engulfing", "hammer"]
        cdl_df = df.ta.cdl_pattern(name=patterns)
        df = pd.concat([df, cdl_df], axis=1)
        
        return df
    except:
        return None

def check_buy_signals(ticker):
    """V2.2 개선된 진입 필터: 과열 방지 및 동적 변동성 확인"""
    try:
        # 1. 일봉 추세 확인
        df_day = pyupbit.get_ohlcv(ticker, interval="day", count=10)
        df_day['ma5'] = df_day['close'].rolling(5).mean()
        if not (df_day['close'].iloc[-1] > df_day['ma5'].iloc[-1]): return False, 0, 0
        
        # 2. 30분봉 상세 분석
        df = get_market_data(ticker)
        if df is None: return False, 0, 0
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 필터: RSI 65 초과 시 과열로 간주 (진입 금지), ADX 20 미만 시 횡보로 간주
        if curr['rsi'] > 65 or curr['ADX_14'] < 20: return False, 0, 0
        
        # 필터: 거래량 (직전 5봉 평균 대비 150% 이상)
        avg_vol = df['volume'].iloc[-6:-1].mean()
        vol_cond = curr['volume'] > (avg_vol * 1.5)
        
        # 패턴 확인 (직전 봉에서 완성된 패턴)
        has_pattern = (prev.filter(like='CDL_').sum() > 0)
        
        if vol_cond and has_pattern:
            return True, curr['atr'], curr['ADX_14']
        return False, 0, 0
    except:
        return False, 0, 0

def manage_trade(ticker, krw_balance):
    global bot_state, daily_stats
    if ticker not in bot_state: bot_state[ticker] = {"status": "WAITING"}
    state = bot_state[ticker]
    
    price = pyupbit.get_current_price(ticker)
    if not price: return

    # [1] 매수 대기 상태
    if state["status"] == "WAITING":
        buy_ok, atr, adx = check_buy_signals(ticker)
        if buy_ok:
            # ADX가 높으면(강한 추세) 손절 배수 확대 (2.2), 낮으면 타이트하게 (1.8)
            k_val = 2.2 if adx > 30 else 1.8
            state.update({
                "status": "BUY_PENDING", 
                "target_price": price + (atr * 0.1), # 돌파 보정
                "atr": atr, 
                "k": k_val
            })
            print(f"\n[{ticker}] 신호 포착! 돌파 대기 (목표: {state['target_price']:,.0f})")

    # [2] 돌파 진입 대기 상태
    elif state["status"] == "BUY_PENDING":
        if price >= state["target_price"]:
            buy_amt = krw_balance * 0.25 # 비중 25%
            if buy_amt > 5000:
                upbit.buy_market_order(ticker, buy_amt * 0.9995)
                state.update({
                    "status": "HOLDING", 
                    "buy_price": price, 
                    "highest": price,
                    "initial_bal": upbit.get_balance(ticker),
                    "part1": True, "part2": True # 분할 매도 관리
                })
                send_message(f"🚀 [{ticker}] 매수 체결\n가: {price:,.0f}원\nATR손절배수: {state['k']}x")

    # [3] 보유 및 수익 관리 상태
    elif state["status"] == "HOLDING":
        balance = upbit.get_balance(ticker)
        if balance < 1e-8: # 정리 완료 시
            state["status"] = "WAITING"
            return

        if price > state.get("highest", 0): state["highest"] = price
        
        buy_price = state["buy_price"]
        atr = state["atr"]
        k = state["k"]
        roi = ((price - buy_price) / buy_price) * 100
        
        # 동적 손절가 계산
        stop_loss_price = buy_price - (atr * k)
        
        sell_qty = 0
        reason = ""

        # A. 동적 ATR 손절 (개선 포인트)
        if price <= stop_loss_price:
            sell_qty, reason = balance, f"동적 손절({k}xATR)"
        # B. 1차 익절 (고점 대비 1ATR 하락 시 절반)
        elif state["part1"] and price <= state["highest"] - atr and roi > 1.0:
            sell_qty, reason = balance * 0.5, "1차 익절(Trailing)"
            state["part1"] = False
        # C. 최종 목표가 익절 (3.5% 수익 시 전량)
        elif roi >= 3.5:
            sell_qty, reason = balance, "목표가 익절(+3.5%)"

        if sell_qty > 0:
            actual_qty = min(sell_qty, balance)
            if actual_qty * price < 5000: actual_qty = balance
            upbit.sell_market_order(ticker, actual_qty)
            
            # 통계 기록
            daily_stats["trades"] += 1
            if roi > 0: daily_stats["wins"] += 1
            daily_stats["profit"] += (price - buy_price) * actual_qty
            send_message(f"💸 [{ticker}] {reason}\n수익률: {roi:.2f}%")

# 4. 메인 무한 루프
send_message("== 쿠퍼춘봉 봇 V2.2 (변동성 적응형) 가동 ==")
target_list = get_target_tickers()

while True:
    try:
        now = datetime.datetime.now()
        
        # 자정 정산 및 리스트 갱신
        if daily_stats["last_report_date"] != now.date():
            wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 일일 결산 [{daily_stats['last_report_date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "last_report_date": now.date()})
            target_list = get_target_tickers()

        krw = upbit.get_balance("KRW")
        
        # 하트비트: 실시간 감시 현황 출력
        for i, ticker in enumerate(target_list):
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 감시: {ticker} ({i+1}/{len(target_list)}) | KRW: {krw:,.0f} ")
            sys.stdout.flush()
            
            manage_trade(ticker, krw)
            time.sleep(0.1)

    except Exception as e:
        print(f"\n[!] 에러 발생: {e}")
        time.sleep(10)
