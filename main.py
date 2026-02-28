from flask import Flask
import threading
import time
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    # Render는 기본적으로 10000번 포트를 사용합니다.
    app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    # 1. 웹 서버를 별도 쓰레드로 실행
    t = threading.Thread(target=run_flask)
    t.start()

    # 2. 실제 본인이 돌리고 싶은 파이썬 로직 시작
    while True:
        print("24시간 작동 중...")
        time.sleep(60)