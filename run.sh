#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-}" in
  "") crawl=0 ;;
  --crawl) crawl=1 ;;
  *) echo "사용법: $0 [--crawl]" >&2; exit 1 ;;
esac

command -v uv >/dev/null || { echo "uv가 필요합니다. 설치: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2; exit 1; }

[ -d data/inverted ] || uv run python search_engine.py --index

if [ "$crawl" = 1 ]; then
  uv run python -m playwright install chromium
  set -m
  uv run python crawler.py >> data/crawler.log 2>&1 &
  crawler=$!
  trap 'kill -- -"$crawler" 2>/dev/null' EXIT
  echo "크롤러 실행 중 (로그: data/crawler.log). 새 글은 uv run python search_engine.py --index 후 재시작하면 검색됩니다."
fi

uv run uvicorn app.main:app --port 8000
