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

# 2. 핵심 설정 (초단타 스캘핑)
TARGET_ROI = 0.5          # 목표 익절률 (%)
STOP_LOSS_ROI = -1.0      # 기계적 손절률 (%)
COOL_DOWN_MINUTES = 30    # 손절 후 해당 종목 재진입 금지 시간 (분)

# 3. 내부 상태 관리
bot_state = {"last_loss_times": {}} 
daily_stats = {"trades": 0, "wins": 0, "profit": 0.0, "date": datetime.datetime.now().date()}

def send_message(msg):
    try:
        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        requests.get(url, params={"chat_id": telegram_chat_id, "text": msg})
    except: pass

def get_target_tickers():
    """당일 거래대금 상위 30개 추출"""
    try:
        tickers = pyupbit.get_tickers(fiat="KRW")
        return tickers[:30] # 속도 최적화를 위해 상위 메이저 30개 고정 스캔
    except: return pyupbit.get_tickers(fiat="KRW")[:30]

def sell_manager():
    """[0.2초 단위 실행] 보유 종목 초고속 감시 및 화면 표시용 문자열 반환"""
    global daily_stats, bot_state
    
    try:
        balances = upbit.get_balances()
        if not balances: return "조회실패"
        
        holdings = []
        for b in balances:
            if b['currency'] != 'KRW' and float(b['balance']) + float(b['locked']) > 0:
                holdings.append({
                    'ticker': f"KRW-{b['currency']}",
                    'balance': float(b['balance']) + float(b['locked']),
                    'avg_buy_price': float(b['avg_buy_price'])
                })
                
        if not holdings: return "없음"

        # 보유 종목 현재가 일괄 조회 (속도 극대화)
        tickers_to_check = [h['ticker'] for h in holdings]
        current_prices = pyupbit.get_current_price(tickers_to_check)
        if not current_prices: return "가격로딩중"

        display_info = []
        
        for hold in holdings:
            ticker = hold['ticker']
            buy_price = hold['avg_buy_price']
            balance = hold['balance']
            
            if buy_price == 0: continue # 평단가 0원 버그 무시
            
            curr_price = current_prices[ticker] if isinstance(current_prices, dict) else current_prices
            roi = ((curr_price - buy_price) / buy_price) * 100

            # 🚨 매도 조건 즉각 판별 (전량 매도)
            if roi >= TARGET_ROI or roi <= STOP_LOSS_ROI:
                if balance * curr_price > 5000:
                    upbit.sell_market_order(ticker, balance)
                    
                    reason = f"✅ 목표 익절 (+{roi:.2f}%)" if roi > 0 else f"❌ 칼손절 ({roi:.2f}%)"
                    daily_stats["trades"] += 1
                    if roi > 0: daily_stats["wins"] += 1
                    daily_stats["profit"] += (curr_price - buy_price) * balance
                    
                    send_message(f"💸 [{ticker}] 전량 매도\n- 사유: {reason}\n- 매도가: {curr_price:,.0f}원")
                    
                    if roi < 0:
                        bot_state["last_loss_times"][ticker] = datetime.datetime.now()
                    else:
                        bot_state["last_loss_times"].pop(ticker, None) # 익절은 제한 해제
                    continue # 매도한 종목은 화면 표시에 넣지 않음

            # 화면에 보여줄 텍스트 저장
            display_info.append(f"[{ticker} {roi:+.2f}%]")
            
        return " ".join(display_info) if display_info else "없음"
    except Exception as e:
        return "에러"

def buy_manager(ticker, krw_balance):
    """단일 종목 진입점 분석 (5분봉 스캘핑 로직)"""
    global bot_state
    now = datetime.datetime.now()

    # 1. 손절 쿨다운 및 기보유 여부 체크
    last_loss = bot_state["last_loss_times"].get(ticker)
    if last_loss and (now - last_loss).total_seconds() / 60 < COOL_DOWN_MINUTES:
        return
    if upbit.get_balance(ticker) > 0:
        return

    # 2. 5분봉 타점 분석
    df = pyupbit.get_ohlcv(ticker, interval="minute5", count=20)
    if df is None or len(df) < 20: return
        
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()
    curr, prev = df.iloc[-1], df.iloc[-2]
    
    # 매수 조건: 20선 상승장 + 5선 반등/돌파 + 거래량 2배
    trend_ok = curr['close'] > curr['ma20']
    cross_ok = (curr['ma5'] > curr['ma20']) and (prev['ma5'] <= prev['ma20'])
    avg_vol = df['volume'].iloc[-6:-1].mean()
    vol_ok = curr['volume'] > (avg_vol * 2.0)
    
    if trend_ok and cross_ok and vol_ok:
        invest_ratio = 0.3 if curr['volume'] > (avg_vol * 3.0) else 0.1
        buy_amt = krw_balance * invest_ratio
        if buy_amt > 5000:
            upbit.buy_market_order(ticker, buy_amt * 0.9995)
            send_message(f"🚀 [{ticker}] 5분봉 매수\n- 매수가: {curr['close']:,.0f}원\n- 비중: {invest_ratio*100}%")

# --- 메인 무한 루프 ---
send_message("== 쿠퍼춘봉 스캘핑 봇 V3.1 가동 ==\n(0.2초 매도 락온 & 실시간 현황 모드)")
target_list = get_target_tickers()

while True:
    try:
        now = datetime.datetime.now()
        
        # 자정 정산
        if daily_stats["date"] != now.date():
            wr = (daily_stats["wins"] / daily_stats["trades"] * 100) if daily_stats["trades"] > 0 else 0
            send_message(f"📅 일일 결산 [{daily_stats['date']}]\n- 거래: {daily_stats['trades']}회\n- 승률: {wr:.1f}%\n- 수익: {daily_stats['profit']:,.0f}원")
            daily_stats.update({"trades": 0, "wins": 0, "profit": 0.0, "date": now.date()})
            target_list = get_target_tickers()

        # 💡 [핵심] 반복문이 한 바퀴 돌 때마다 종목을 '1개씩만' 검사하고 매도(sell_manager)로 돌아옴
        for ticker in target_list:
            krw = upbit.get_balance("KRW")
            
            # 매도 최우선: 내 잔고 털기 & 화면 출력용 문자열 받기
            holdings_str = sell_manager()
            
            # 하트비트 화면 표출 (이제 내 코인 수익률이 실시간으로 보임!)
            sys.stdout.write(f"\r[{now.strftime('%H:%M:%S')}] 보유: {holdings_str} | 스캔: {ticker} | 잔고: {krw:,.0f}원   ")
            sys.stdout.flush()
            
            # 스캔 및 매수 검사
            if krw > 5000:
                buy_manager(ticker, krw)
                
            time.sleep(0.3) # 업비트 API 초당 호출 제한(Rate Limit) 방지

    except Exception as e:
        time.sleep(5)
