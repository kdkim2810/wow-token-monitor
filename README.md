# wow-token-monitor

Blizzard 공식 한국 지역 API로 토큰 시세를 확인합니다.
외부 cron-job.org의 기존 30분 workflow_dispatch 실행을 유지합니다.

- Discord: 실행마다 시세와 이전 전송 가격 대비 증감 보고
- 목표가: **230,000 골드 이하**, 기존 두 사용자 멘션 유지
- Telegram: 목표가 이하, 기존 600초 쿨타임 유지. 두 Secrets가 모두 없으면 선택 채널로 생략
- 인증/가격 조회는 연결을 재사용하고 일시 오류에 재시도 및 타임아웃 적용
- 누락/0/비정상 가격은 실패로 처리해 잘못된 목표가 알림 방지
- 알림 HTTP 응답과 Telegram API의 ok 값을 확인한 후에만 완료 기록
- Discord 전송 확인 후 이전 가격 저장, Telegram 확인 후에만 쿨타임 저장
- 실패 시 GitHub Actions도 실패 표시. 실패한 실행에서도 이미 확인된 상태를 저장
- 동시 실행 직렬화 및 최신 main 체크아웃으로 중복 실행/상태 충돌 완화
- 네트워크 응답 유실/상태 push 실패에는 중복 전송 가능성이 있음

Python 3.12, `python -m pip install requests`

- `python monitor.py`: 기존 Secrets로 정상 운영
- `python monitor.py --dry-run`: 인증/가격 조회만 검증. 메시지 및 상태 변경 없음
- `python -m unittest -v`: 외부 전송 없는 회귀 테스트

코드 push 시 테스트와 실제 API dry-run이 실행됩니다.

## 추가 장애 보호

- Discord 장애 중에도 목표가 Telegram 알림을 별도로 시도합니다. 각 채널의 확인된 상태만 저장합니다.
- 가격 갱신 시각이 제공되면 6시간 초과 지연/10분 초과 미래/잘못된 형식을 거부합니다.
- Telegram 중첩 parameters.retry_after를 처리하고, 60초 초과 제한은 다음 실행으로 넘깁니다.
- 인증/알림 요청 리다이렉트와 손상된 상태 파일을 명시적 실패로 처리합니다.

## 상태 동기화 복구

`save_state.sh`로 상태 파일만 커밋하고 pull/rebase 및 push를 최대 3회 시도합니다. 일시 전송 실패는 재시도하며 충돌 시 rebase를 중단하고 원격 상태를 강제 덮어쓰지 않습니다. 실행 로그는 버퍼링 없이 출력합니다.

미래 알림 시각으로 손상된 상태를 거부합니다. 조회용 HTTP 408과 잘못된 JSON 응답은 재시도하고, TLS 오류는 즉시 실패로 표시합니다. 알림 전송 응답이 불명확한 경우 즉시 재전송하지 않습니다.
