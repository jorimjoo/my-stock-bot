import pyupbit
import requests

# 성조님의 API 정보
ACCESS_KEY = "1MUrRTR1vfHUP4Ru1Ax5UgYk2dTCHesiOUCysR6Z"
SECRET_KEY = "y9XT6Q6CyEOp4RxG8FxYbmcwxKx4Uf0BBypwxxcP"
TELEGRAM_TOKEN = "8726756800:AAFyCDAQSXeYBjesH-Dxs-tnyFOnAhN4Uz0"
TELEGRAM_CHAT_ID = "8403406400"

print("====================================")
print("📡 1. 텔레그램 발송 테스트 시작...")
try:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    res = requests.get(url, params={"chat_id": TELEGRAM_CHAT_ID, "text": "✅ [테스트] 텔레그램 통신망 정상!"}, timeout=5)
    if res.status_code == 200:
        print("✅ [통과] 스마트폰 텔레그램을 확인해보세요! 메시지가 도착했습니다.")
    else:
        print(f"❌ [실패] 텔레그램 전송 실패 (코드: {res.status_code})")
except Exception as e:
    print(f"❌ [에러] 텔레그램 연결 자체가 안됩니다: {e}")

print("\n🏦 2. 업비트 내 계좌 접속 테스트 시작...")
try:
    upbit = pyupbit.Upbit(ACCESS_KEY, SECRET_KEY)
    krw_bal = upbit.get_balance("KRW")
    
    if krw_bal is not None:
        print(f"✅ [통과] 업비트 API 정상 연결! (보유 원화: {krw_bal:,.0f}원)")
    else:
        print("🚨 [차단됨] 잔고가 None으로 나옵니다! IP 주소가 바뀌었거나 API 키가 만료되었습니다.")
        print("   -> 해결책: 업비트 홈페이지 > 마이페이지 > Open API 관리에서 현재 PC의 IP를 새로 등록해주세요!")
except Exception as e:
    print(f"❌ [에러] 업비트 서버 연결 실패: {e}")
print("====================================")
