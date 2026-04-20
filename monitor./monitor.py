import os
import requests
import json
import time
from datetime import datetime

# ==========================================
# 🎯 목표 가격 설정 (여기에 원하는 가격 입력)
TARGET_PRICE = 180000 
# ==========================================

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"previous_token_price": None, "last_alert_time": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def send_telegram(msg):
    token = os.environ.get('TELEGRAM_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id: return
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": msg})

def send_discord(msg):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook_url: return
    requests.post(webhook_url, json={"content": msg})

def get_access_token():
    client_id = os.environ.get('CLIENT_ID', '').strip()
    client_secret = os.environ.get('CLIENT_SECRET', '').strip()
    response = requests.post("https://oauth.battle.net/token", data={"grant_type": "client_credentials"}, auth=(client_id, client_secret))
    response.raise_for_status()
    return response.json().get("access_token")

def get_wow_token_price(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": "dynamic-kr", "locale": "ko_KR"}
    response = requests.get("https://kr.api.blizzard.com/data/wow/token/", headers=headers, params=params)
    response.raise_for_status()
    return response.json().get("price", 0) // 10000 

def main():
    print("토큰 가격 스캔 시작...")
    state = load_state()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        access_token = get_access_token()
        token_price = get_wow_token_price(access_token)
        print(f"현재 토큰 가격: {token_price:,} G")
        
        previous_price = state["previous_token_price"]
        
        if previous_price is None:
            discord_prev_str = "기록 없음 (첫 검색)"
            diff_str = ""
        else:
            diff = token_price - previous_price
            if diff > 0: diff_str = f"(🔺 +{diff:,} G)"
            elif diff < 0: diff_str = f"(🔻 {diff:,} G)"
            else: diff_str = "(➖ 변동 없음)"
            discord_prev_str = f"{previous_price:,} 골드"

        # 디스코드 메시지 전송
        discord_msg = (
            f"📊 **[시세 보고]** ({now_str})\n"
            f"> ⏳ 이전 가격: {discord_prev_str}\n"
            f"> 🪙 현재 가격: **{token_price:,}** 골드 {diff_str}"
        )
        send_discord(discord_msg)
        print("디스코드 전송 완료.")

        # 텔레그램 긴급 알림 전송 (조건 및 쿨타임 확인)
        if token_price <= TARGET_PRICE:
            current_time = time.time()
            # 10분(600초) 쿨타임
            if current_time - state["last_alert_time"] > 600:
                tel_msg = f"🔔 [긴급 매수 알림]\n현재가: {token_price:,}G {diff_str}\n목표가: {TARGET_PRICE:,}G 이하 도달!"
                send_telegram(tel_msg)
                state["last_alert_time"] = current_time
                print("텔레그램 긴급 알림 발송 완료.")

        # 상태 저장
        state["previous_token_price"] = token_price
        save_state(state)
        print("상태 저장 완료.")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    main()
