import os
import FinanceDataReader as fdr
from pykrx import stock
import requests
import pandas as pd
import time
from datetime import datetime, timedelta, time as d_time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# --- [1. 보안 및 설정: GitHub Secrets 연동] ---
# 깃허브 Settings > Secrets에 저장한 이름을 os.environ으로 불러옵니다.
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
TARGET_USER = "s_trader91"
MAX_RETRIES = 3

# --- [2. 시장 지표 및 유틸리티] ---

def get_market_index():
    """KOSPI, KOSDAQ 지수 현황 브리핑"""
    try:
        ks = fdr.DataReader('KS11').tail(2)
        kq = fdr.DataReader('KQ11').tail(2)
        def fmt(df):
            chg = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
            return f"{'📈' if chg > 0 else '📉'} {df['Close'].iloc[-1]:.2f} ({chg:+.2f}%)"
        return f"🇰🇷 코스피: {fmt(ks)}\n🇰🇷 코스닥: {fmt(kq)}"
    except: return "📊 지수 데이터 확인 중..."

def get_safe_krx_list():
    """컬럼명 호환 패치 및 리스크 종목 필터링"""
    try:
        df = fdr.StockListing('KRX')
        rename_map = {'종목명': 'Name', 'Symbol': 'Code', '업종': 'Sector'}
        for old, new in rename_map.items():
            if old in df.columns and new not in df.columns: df = df.rename(columns={old: new})
        if 'Sector' not in df.columns: df['Sector'] = "기타 테마"
        filter_words = "스팩|ETF|ETN|우|관리|투자주의"
        return df[~df['Name'].str.contains(filter_words, na=False)]
    except: return pd.DataFrame()

def is_market_open():
    """현재 한국 장중 여부 확인"""
    now = datetime.now()
    if now.weekday() < 5:
        return d_time(9, 0) <= now.time() <= d_time(15, 30)
    return False

# --- [3. 핵심 분석 엔진] ---

def get_leading_stocks():
    """⭐ 주도주: 시총 800억~10조, 10%↑, 거래대금 상위 15위"""
    try:
        now = datetime.now(); today = now.strftime("%Y%m%d")
        b_days = stock.get_market_ohlcv((now - timedelta(days=7)).strftime("%Y%m%d"), today, "005930").index
        target = b_days[-1].strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_ticker(target, market="ALL")
        cap = stock.get_market_cap_by_ticker(target, market="ALL")
        combined = pd.concat([df, cap[['시가총액']]], axis=1)
        combined['종목명'] = [stock.get_market_ticker_name(t) for t in combined.index]
        filtered = combined[
            (~combined['종목명'].str.contains("스팩|ETF|ETN")) & 
            (combined['시가총액'].between(80_000_000_000, 10_000_000_000_000)) & (combined['등락률'] >= 10.0)
        ]
        top_15 = combined.sort_values(by='거래대금', ascending=False).head(15).index.tolist()
        return [stock.get_market_ticker_name(t) for t in filtered.index if t in top_15]
    except: return []

def get_short_term_signals():
    """⚡ 단기 급등: 거래량 500%↑ + 등락률 15%↑ + RSI 50~75"""
    try:
        krx = get_safe_krx_list()
        candidates = get_leading_stocks()
        signals = []
        for name in candidates[:10]:
            code = krx[krx['Name'] == name]['Code'].values[0]
            df = fdr.DataReader(code).tail(20)
            vol_ratio = (df['Volume'].iloc[-1] / df['Volume'].iloc[:-1].mean()) * 100
            change = ((df['Close'].iloc[-1] - df['Close'].iloc[-2]) / df['Close'].iloc[-2]) * 100
            
            diff = df['Close'].diff()
            u, d = diff.copy(), diff.copy()
            u[u<0]=0; d[d>0]=0
            au = u.ewm(com=13, adjust=False).mean(); ad = d.abs().ewm(com=13, adjust=False).mean()
            rsi = 100 - (100 / (1 + au.iloc[-1] / ad.iloc[-1]))
            
            if vol_ratio >= 500 and change >= 15 and 50 <= rsi <= 75:
                signals.append(name)
        return signals
    except: return []

def get_strong_buy_stocks():
    """🔥 수급: 외인/기관 쌍끌이 순매수 상위 5개"""
    try:
        now = datetime.now()
        b_days = stock.get_market_ohlcv((now - timedelta(days=7)).strftime("%Y%m%d"), now.strftime("%Y%m%d"), "005930").index
        last = b_days[-1].strftime("%Y%m%d")
        df = stock.get_market_net_purchase_of_equities_by_ticker(last, last, "ALL")
        strong = df[(df['외국인'] > 0) & (df['기관합계'] > 0)]
        codes = strong.sort_values(by='합계', ascending=False).head(5).index.tolist()
        return [stock.get_market_ticker_name(c) for c in codes]
    except: return []

def get_threads_stocks():
    """📱 스레드: @s_trader91 관심주 실시간 크롤링"""
    opts = Options(); opts.add_argument("--headless")
    opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage")
    for attempt in range(MAX_RETRIES):
        driver = None
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            driver.get(f"https://www.threads.net/@{TARGET_USER}"); time.sleep(15)
            txt = driver.find_element(By.TAG_NAME, "body").text
            safe_names = get_safe_krx_list()['Name'].tolist()
            return [n for n in safe_names if n in txt and len(n) >= 2][:12]
        except:
            if driver: driver.quit()
            time.sleep(5)
    return []

# --- [4. 상세 분석 및 리포트 생성] ---

def analyze_stock_details(name):
    """프리미엄 지표 분석: 테마+추세🚀+RSI⚠️+매수범위+손절가(-3%)"""
    try:
        df_krx = get_safe_krx_list()
        row = df_krx[df_krx['Name'] == name].iloc[0]
        code, sector = row['Code'], row['Sector']
        df = fdr.DataReader(code).tail(30)
        close = int(df['Close'].iloc[-1])
        
        # 추세 및 RSI 계산
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        trend = "🚀" if close > ma5 > ma20 else ""
        
        diff = df['Close'].diff()
        u, d = diff.copy(), diff.copy()
        u[u<0]=0; d[d>0]=0
        au, ad = u.ewm(com=13, adjust=False).mean(), d.abs().ewm(com=13, adjust=False).mean()
        rsi = 100 - (100 / (1 + au.iloc[-1] / ad.iloc[-1]))
        rsi_msg = "⚠️과열" if rsi > 70 else ("💎저점" if rsi < 35 else "")

        v_ratio = (df['Volume'].iloc[-1] / df['Volume'].iloc[:-1].mean()) * 100
        label = "현재가" if is_market_open() else "전일종가"
        
        res = f"• {name} [{sector}] {trend} {rsi_msg}\n"
        res += f"  └ {label}: {close:,}원 (RSI:{int(rsi)} / 거래량:{int(v_ratio)}%)\n"
        res += f"  └ 매수범위: {int(close*0.995):,}~{int(close*1.005):,}\n"
        res += f"  └ 목표: {int(close*1.03):,} / ❌손절: {int(close*0.97):,}\n"
        return res
    except: return f"• {name}: 분석 데이터 부족\n"

def main_job():
    print(f"[{datetime.now()}] 🚀 프리미엄 마스터 리포트 생성 시작...")
    market_idx = get_market_index()
    short_term = get_short_term_signals()
    leading = get_leading_stocks()
    trends = get_strong_buy_stocks()
    threads = get_threads_stocks()

    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    status = "🟢 실시간" if is_market_open() else "🔴 종료/휴장"
    
    msg = f"📊 [{now}] 프리미엄 마스터 리포트 ({status})\n"
    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += f"{market_idx}\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    msg += "⚡ [단기 급등 시그널 포착]\n"
    if short_term:
        for s in short_term: msg += analyze_stock_details(s)
    else: msg += "현재 조건 부합 종목 없음\n"

    msg += "\n⭐ [오늘의 주도주 (09:19 기준)]\n"
    if leading:
        for s in leading: msg += analyze_stock_details(s)
    else: msg += "부합 종목 없음\n"

    msg += "\n🔥 [외인/기관 수급 강력추천]\n"
    if trends:
        for s in trends: msg += analyze_stock_details(s)
    else: msg += "데이터 집계 중...\n"

    msg += "\n📱 [스레드 s_trader91 관심주]\n"
    if threads:
        for s in threads: msg += analyze_stock_details(s)
    else: msg += "최근 언급 종목 없음\n"

    msg += "\n━━━━━━━━━━━━━━━━━━\n※ 손절가(-3%) 준수 필수"

    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                  data={"chat_id": CHAT_ID, "text": msg})
    print(f"[{now}] 리포트 전송 성공!")

# --- [5. 실행부: GitHub Actions용] ---
if __name__ == "__main__":
    # GitHub Actions 환경에서는 이 스크립트가 실행될 때 main_job()을 한 번 수행하고 종료합니다.
    main_job()