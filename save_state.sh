#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "::error::저장할 상태 파일이 지정되지 않았습니다."
  exit 1
fi

git config user.email "actions@github.com"
git config user.name "GitHub Actions"
git add -- "$@"
if git diff --staged --quiet; then
  echo "상태 변경 없음"
  exit 0
fi
git commit -m "알리미 처리 상태 저장"

for attempt in 1 2 3; do
  echo "상태 동기화 시도 ${attempt}/3"
  if git pull --rebase origin main && git push origin HEAD:main; then
    echo "상태 저장 완료"
    exit 0
  fi
  if git rev-parse --verify REBASE_HEAD >/dev/null 2>&1; then
    git rebase --abort
    echo "::error::원격 상태와 충돌했습니다. 강제 덮어쓰기 없이 중단합니다."
    exit 1
  fi
  if [ "$attempt" -lt 3 ]; then
    sleep "$((attempt * 2))"
  fi
done

echo "::error::상태 동기화 3회 실패. GitHub Actions 로그를 확인하세요."
exit 1
