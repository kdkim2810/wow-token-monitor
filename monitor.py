import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TARGET_PRICE = 230000
STATE_FILE = 'state.json'
ALERT_COOLDOWN = 600
MAX_PRICE_AGE = 6 * 60 * 60
DISCORD_USERS = ['1093168060166307840', '829217044645281813']
TIMEOUT = (10, 30)
SESSION = requests.Session()


class MonitorError(RuntimeError):
    pass


def load_state():
    if not Path(STATE_FILE).exists():
        return {'previous_token_price': None, 'last_alert_time': 0}
    try:
        state = json.loads(Path(STATE_FILE).read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise MonitorError('상태 파일을 읽을 수 없습니다. 원본을 보존하고 실행 중단') from None
    if not isinstance(state, dict):
        raise MonitorError('상태 파일은 JSON 객체여야 합니다. 원본 보존')
    previous = state.get('previous_token_price')
    last_alert = state.get('last_alert_time', 0)
    if previous is not None and (type(previous) is not int or previous <= 0):
        raise MonitorError('저장된 이전 가격이 올바르지 않습니다')
    if type(last_alert) not in (int, float) or not math.isfinite(last_alert) or last_alert < 0:
        raise MonitorError('저장된 알림 시간이 올바르지 않습니다')
    if last_alert > time.time() + 600:
        raise MonitorError('저장된 알림 시간이 미래입니다. 상태 원본 보존')
    state.setdefault('previous_token_price', None)
    state.setdefault('last_alert_time', 0)
    return state


def save_state(state):
    temporary = Path(STATE_FILE + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    temporary.replace(STATE_FILE)


def request_json(method, url, service, retry_safe=False, **kwargs):
    for attempt in range(3):
        try:
            response = SESSION.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) and not isinstance(exc, requests.exceptions.SSLError)
            if retry_safe and transient and attempt < 2:
                print(f'{service} 일시 접속 장애 [{attempt + 1}/3]: {type(exc).__name__}', flush=True)
                time.sleep(2 ** attempt)
                continue
            raise MonitorError(f'{service} 접속 실패: {type(exc).__name__}') from None
        if response.status_code == 429 and attempt < 2:
            try:
                limit = response.json()
                parameters = limit.get('parameters', {})
                wait = float(limit.get('retry_after', parameters.get('retry_after', response.headers.get('Retry-After', 5))))
                if not math.isfinite(wait):
                    wait = 5
            except (ValueError, TypeError, AttributeError):
                wait = 5
            if wait > 60:
                raise MonitorError(f'{service}: 요청 제한 대기 {wait:g}초, 다음 정기 실행에서 재시도')
            time.sleep(max(wait, 1))
            continue
        if (response.status_code == 408 or response.status_code >= 500) and retry_safe and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        if not 200 <= response.status_code < 300:
            raise MonitorError(f'{service} 오류: HTTP {response.status_code}')
        if response.status_code == 204:
            return {}
        try:
            data = response.json()
        except ValueError:
            if retry_safe and attempt < 2:
                print(f'{service} JSON 응답 오류 [{attempt + 1}/3], 재조회', flush=True)
                time.sleep(2 ** attempt)
                continue
            raise MonitorError(f'{service}: JSON 응답 해석 실패') from None
        if not isinstance(data, dict):
            raise MonitorError(f'{service}: 잘못된 응답 형식')
        return data
    raise MonitorError(f'{service}: 재시도 소진')


def send_telegram(msg):
    token = os.environ.get('TELEGRAM_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token and not chat_id:
        print('텔레그램 설정 없음: 선택 채널 생략')
        return False
    if not token or not chat_id:
        raise MonitorError('텔레그램 설정 불완전: TOKEN/CHAT_ID 모두 필요')
    data = request_json('POST', f'https://api.telegram.org/bot{token}/sendMessage', '텔레그램',
                        json={'chat_id': chat_id, 'text': msg})
    if data.get('ok') is not True:
        raise MonitorError('텔레그램 전송 실패: API ok=false 또는 확인값 누락')
    return True


def send_discord(msg, mention=False):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not webhook_url:
        raise MonitorError('DISCORD_WEBHOOK_URL 설정 누락')
    request_json('POST', webhook_url, '디스코드',
                 json={'content': msg, 'allowed_mentions': {'parse': [], 'users': DISCORD_USERS if mention else []}})


def get_access_token():
    client_id = os.environ.get('CLIENT_ID', '').strip()
    client_secret = os.environ.get('CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        raise MonitorError('Blizzard CLIENT_ID/CLIENT_SECRET 설정 누락')
    data = request_json('POST', 'https://oauth.battle.net/token', 'Blizzard 인증', retry_safe=True,
                        data={'grant_type': 'client_credentials'}, auth=(client_id, client_secret))
    token = data.get('access_token')
    if not isinstance(token, str) or not token.strip():
        raise MonitorError('Blizzard 인증 응답에 access_token 없음')
    return token


def get_wow_token_price(access_token):
    data = request_json('GET', 'https://kr.api.blizzard.com/data/wow/token/', 'Blizzard 토큰 가격',
                        retry_safe=True, headers={'Authorization': f'Bearer {access_token}'},
                        params={'namespace': 'dynamic-kr', 'locale': 'ko_KR'})
    copper = data.get('price')
    if type(copper) is not int or copper < 10000:
        raise MonitorError('Blizzard 가격 응답 오류: 유효한 양수 price 없음 (0골드 알림 차단)')
    updated = data.get('last_updated_timestamp')
    if updated is not None:
        if type(updated) is not int or updated <= 0:
            raise MonitorError('Blizzard 가격 갱신 시각 오류')
        age = time.time() - updated / 1000
        if age > MAX_PRICE_AGE or age < -600:
            raise MonitorError('Blizzard 가격 데이터가 6시간 이상 오래되었거나 미래 시각입니다')
    return copper // 10000


def main(dry_run=False):
    try:
        state = load_state()
        price = get_wow_token_price(get_access_token())
        print(f'현재 한국 토큰 가격: {price:,} G, 목표가: {TARGET_PRICE:,} G')
        if dry_run:
            print('API 인증/가격 검증 완료. 알림 및 상태 변경 생략')
            return 0
        previous = state['previous_token_price']
        previous_text = '기록 없음 (첫 검색)' if previous is None else f'{previous:,} 골드'
        difference = ''
        if previous is not None:
            diff = price - previous
            difference = '(➖ 변동 없음)' if diff == 0 else f'({"🔺" if diff > 0 else "🔻"} {diff:+,} G)'
        now = time.time()
        target_reached = price <= TARGET_PRICE
        mention = ''
        if target_reached:
            mention = f'\n\n🚨 {" ".join(f"<@{user}>" for user in DISCORD_USERS)} 긴급 매수 알림! 목표가({TARGET_PRICE:,}G) 도달! 당장 접속하세요!'
        now_str = datetime.fromtimestamp(now, timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        msg = (f'📊 **[시세 보고]** ({now_str})\n> ⏳ 이전 가격: {previous_text}\n'
               f'> 🪙 현재 가격: **{price:,}** 골드 {difference}{mention}')
        errors = []
        try:
            send_discord(msg, mention=target_reached)
        except MonitorError as exc:
            errors.append(str(exc))
        else:
            print('디스코드 전송 확인 완료')
            state['previous_token_price'] = price
            save_state(state)
        # 한 채널의 장애가 다른 채널의 목표가 알림까지 막지 않도록 각각 처리한다.
        if target_reached and now - state['last_alert_time'] > ALERT_COOLDOWN:
            tel_msg = f'🔔 [긴급 매수 알림]\n현재가: {price:,}G {difference}\n목표가: {TARGET_PRICE:,}G 이하 도달!'
            try:
                delivered = send_telegram(tel_msg)
            except MonitorError as exc:
                errors.append(str(exc))
            else:
                if delivered:
                    state['last_alert_time'] = now
                    save_state(state)
                    print('텔레그램 전송 확인 완료')
        if errors:
            raise MonitorError('; '.join(errors))
        return 0
    except Exception as exc:
        # requests 예외에는 토큰을 포함한 URL이 담길 수 있다.
        reason = str(exc) if isinstance(exc, MonitorError) else type(exc).__name__
        print(f'실행 실패: {reason}')
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='API 인증/조회만 점검, 알림/상태 변경 없음')
    raise SystemExit(main(dry_run=parser.parse_args().dry_run))
