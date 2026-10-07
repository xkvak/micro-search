#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

crawl=0 ai=1
for arg in "$@"; do
  case "$arg" in
    --crawl) crawl=1 ;;
    --no-ai) ai=0 ;;
    *) echo "사용법: $0 [--crawl] [--no-ai]" >&2; exit 1 ;;
  esac
done

command -v uv >/dev/null || { echo "uv가 필요합니다. 설치: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

[ -d data/inverted ] || uv run python search_engine.py --index

if [ "$ai" = 1 ]; then
  lms=$(command -v lms || echo ~/.lmstudio/bin/lms)
  [ -x "$lms" ] || { echo "AI 요약에는 LM Studio가 필요합니다 (https://lmstudio.ai). 검색만 쓰려면: $0 --no-ai" >&2; exit 1; }
  model=$(sed -n 's/^MODEL = "\(.*\)"/\1/p' app/main.py)
  "$lms" server start
  "$lms" ps | grep -F "$model" >/dev/null || "$lms" load "$model" -y
fi

if [ "$crawl" = 1 ]; then
  uv run python -m playwright install chromium
  set -m
  uv run python crawler.py >> data/crawler.log 2>&1 &
  crawler=$!
  trap 'kill -- -"$crawler" 2>/dev/null' EXIT
  echo "크롤러 실행 중 (로그: data/crawler.log). 새 글은 uv run python search_engine.py --index 후 재시작하면 검색됩니다."
fi

AI_ANSWER=$ai uv run uvicorn app.main:app --port 8000
