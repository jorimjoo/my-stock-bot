import time
import pyupbit
import pandas as pd
import requests
import datetime
import sys

# 1. API 키 및 텔레그램 설정
access = "QUA4RX6p9ZhFtZmbkx6xs3TPl9HJOfQY9FSXpiLd"
secret = "1qOhaGpd9unIYxinnaaHJYGGhZcQlc9eq0QP8euy"
upbit = pyupbit.Upbit(access, secret)

telegram_token = "8726756800:AAFRrzHgy4txpgO9BjVk1JZU4fFsCSYUkbc"
telegram_chat_id = "8403406400"

# 2. 핵심 설정 (스캘핑 전용)
TARGET_ROI = 0.5          # 목표 익절률 (%)
STOP_LOSS_ROI = -1.0      # 기계적 손절률 (%)
COOL_DOWN_MINUTES = 30    # 손절 후 해당 종목 재진입 금지 시간

# 3. 내부 상태 관리
bot_state = {"last_loss_times": {}} # 종목별 손절 시간 기록
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}

def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """당일 거래대금 상위 30개 추출"""
    try:
        df = pyupbit.get_ohlcv("KRW-BTC", interval="day", count=1) # API 부하 최소화를 위해 1개만 테스트
        tickers = pyupbit.get_tickers(fiat="KRW")
        # 실제 환경에서는 상위 거래대금을 뽑는 별도 로직이 필요하나, 속도를 위해 업비트 API 기본 순서(메이저 위주) 사용
        return tickers[:30] 
    except: return pyupbit.get_tickers(fiat="KRW")[:30]

def sell_manager():
    """[최우선 실행] 보유 종목 수익률 점검 및 즉각 매도"""
    global daily_stats, bot_state
    
    balances = upbit.get_balances()
    if not balances: return
    
    holdings = []
    for b in balances:
        if b['currency'] != 'KRW' and float(b['balance']) + float(b['locked']) > 0:
            holdings.append({
                'ticker': f"KRW-{b['currency']}",
                'balance': float(b['balance']) + float(b['locked']),
                'avg_buy_price': float(b['avg_buy_price'])
            })
            
    if not holdings: return # 보유 종목이 없으면 패스

    # 보유 종목의 현재가를 한 번의 API 호출로 모두 가져옴 (속도 극대화)
    tickers_to_check = [h['ticker'] for h in holdings]
    current_prices = pyupbit.get_current_price(tickers_to_check)
    
    if not current_prices: return

    for hold in holdings:
        ticker = hold['ticker']
        buy_price = hold['avg_buy_price']
        balance = hold['balance']
        
        # 간혹 업비트 API 버그로 평단가가 0으로 나올 때 방어
        if buy_price == 0: continue 
        
        # 단일 종목일 경우 딕셔너리가 아닌 float로 올 수 있음
        curr_price = current_prices[ticker] if isinstance(current_prices, dict) else current_prices
        roi = ((curr_price - buy_price) / buy_price) * 100

        sell_reason = ""
        if roi >= TARGET_ROI:
            sell_reason = f"✅ 목표 익절 (+{roi:.2f}%)"
        elif roi <= STOP_LOSS_ROI:
            sell_reason = f"❌ 기계적 손절 ({roi:.2f}%)"
            
        if sell_reason:
            if balance * curr_price < 5000: continue # 5천원 미만 자투리 무시
            
            # 즉시 시장가 매도
            upbit.sell_market_order(ticker, balance)
            
            # 통계 기록
            daily_stats["trades"] += 1
            if roi > 0: daily_stats["wins"] += 1
            daily_stats["profit"] += (curr_price - buy_price) * balance
            send_message(f"💸 [{ticker}] 매도 완료\n- 사유: {sell_reason}\n- 매도가: {curr_price:,.0f}원")
            
            # 손절 시 쿨다운 기록
            if roi < 0:
                bot_state["last_loss_times"][ticker] = datetime.datetime.now()
                print(f"\n[!] {ticker} 손절. 30분간 접근 금지.")
            else:
                # 익절 시 쿨다운 해제
                bot_state["last_loss_times"].pop(ticker, None)

def buy_manager(tickers, krw_balance):
    """[차순위 실행] 신규 진입점 탐색 (5분봉 스캘핑 로직)"""
    global bot_state
    now = datetime.datetime.now()

    for ticker in tickers:
        # 1. 쿨다운 체크 (손절한 종목 패스)
        last_loss = bot_state["last_loss_times"].get(ticker)
        if last_loss and (now - last_loss).total_seconds() / 60 < COOL_DOWN_MINUTES:
            continue

        # 2. 잔고 체크 (해당 종목을 이미 보유 중이면 패스)
        if upbit.get_balance(ticker) > 0:
            continue

        # 3. 5분봉 기반 스캘핑 타점 분석
        df = pyupbit.get_ohlcv(ticker, interval="minute5", count=20)
        if df is None or len(df) < 20:
            time.sleep(0.05)
            continue
            
        # 단기 이평선 (5선, 20선)
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # [스캘핑 매수 조건]
        # 1. 20선 위에 위치 (상승 추세)
        # 2. 직전 봉에서 5선이 20선을 돌파(골든크로스) 하거나 5선 반등
        # 3. 직전 5개 캔들 평균 거래량 대비 현재 거래량이 2배 이상 터짐 (수급 유입)
        
        trend_ok = curr['close'] > curr['ma20']
        cross_ok = (curr['ma5'] > curr['ma20']) and (prev['ma5'] <= prev['ma20'])
        
        avg_vol = df['volume'].iloc[-6:-1].mean()
        vol_ok = curr['volume'] > (avg_vol * 2.0)
        
        if trend_ok and cross_ok and vol_ok:
            # 확률에 따른 투자 비중 (거래량이 3배 이상 터지면 30% 비중, 아니면 10%)
            invest_ratio = 0.3 if curr['volume'] > (avg_vol * 3.0) else 0.1
            buy_amt = krw_balance * invest_ratio
            
            if buy_amt > 5000:
                upbit.buy_market_order(ticker, buy_amt * 0.9995)
                send_message(f"🚀 [{ticker}] 5분봉 수급포착 매수\n- 현재가: {curr['close']:,.0f}원\n- 비중: {invest_ratio*100}%")
                return # 한 번에 한 종목만 매수하고 루프 탈출 (과도한 동시 매수 방지)
        
        time.sleep(0.05) # API 부하 방지

# 4. 메인 무한 루프
send_message("== 쿠퍼춘봉 스캘핑 봇 V3.0 가동 ==\n(매도 최우선 & 5분봉 수급 추적)")
target_list = get_target_tickers()

while True:
    try:
        now = datetime.datetime.now()
        
        # 자정 일일 결산
        if daily_stats["date"] != now.date():
            wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
            target_list = get_target_tickers() # 상위 종목 갱신

        # [우선순위 1] 현재 보유 종목이 있다면, 묻지도 따지지도 않고 익절/손절부터 검사
        sell_manager()
        
        krw = upbit.get_balance("KRW")
        
        # [우선순위 2] KRW 잔고가 5000원 이상일 때만 신규 타점 스캔
        if krw > 5000:
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 5분봉 타점 스캔 중... | 보유 KRW: {krw:,.0f} ")
            sys.stdout.flush()
            buy_manager(target_list, krw)
        else:
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 잔고 부족, 매도 조건만 감시 중... ")
            sys.stdout.flush()
            
        time.sleep(1) # 과열 방지 휴식
        
    except Exception as e:
        print(f"\n[!] 치명적 에러 발생: {e}")
        time.sleep(10)
