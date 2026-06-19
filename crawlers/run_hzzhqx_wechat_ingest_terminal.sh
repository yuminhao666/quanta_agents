#!/usr/bin/env bash
set -uo pipefail

ROOT_SOURCE="${GJ_QUANTA_DATA_ROOT:-${QUANTA_DATA_ROOT:-}}"
if [ -z "$ROOT_SOURCE" ]; then
  for candidate in \
    "$HOME/quanta_data" \
    "$HOME/Documents/quanta_data" \
    "$HOME/document/quanta_data" \
    "/Volumes/数字大脑/quanta_data"
  do
    if [ -d "$candidate" ]; then
      ROOT_SOURCE="$candidate"
      break
    fi
  done
fi
if [ -z "$ROOT_SOURCE" ]; then
  ROOT_SOURCE="$HOME/quanta_data"
fi

ROOT="$ROOT_SOURCE"
if [ -d "$ROOT" ]; then
  ROOT="$(cd "$ROOT" 2>/dev/null && pwd -P)"
fi
LINK_TARGET="/tmp/quanta_data_link"
ln -sfn "$ROOT" "$LINK_TARGET"
ROOT="$LINK_TARGET"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CRAWLER_DIR="$SCRIPT_DIR"
PYTHON="${GJ_AGENTS_PYTHON:-}"
if [ -z "$PYTHON" ] && [ -x "$CRAWLER_DIR/../.venv/bin/python" ]; then
  PYTHON="$CRAWLER_DIR/../.venv/bin/python"
fi
if [ -z "$PYTHON" ]; then
  PYTHON="${PYTHON:-python3}"
elif [ ! -x "$PYTHON" ]; then
  echo "python executable not found: $PYTHON" >&2
  PYTHON="python3"
fi

LOG_DIR="$ROOT/agent_workspace/logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/hzzhqx_wechat_ingest_terminal_${STAMP}.log"
META="$LOG_DIR/hzzhqx_wechat_ingest_terminal.json"

mkdir -p "$LOG_DIR"
printf '{"root":"%s","log":"%s","pid":%s,"started_at":"%s"}\n' \
  "$ROOT" "$LOG" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$META"

cd "$CRAWLER_DIR" || exit 1

echo "hzzhqx wechat ingest"
echo "root=$ROOT"
echo "log=$LOG"
echo "pid=$$"
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

"$PYTHON" "$CRAWLER_DIR/hzzhqx_wechat_raw_ingest.py" \
  --quanta-root "$ROOT" \
  --delay 8 \
  --jitter 4 \
  --max-retries 1 \
  --max-consecutive-blocks 3 \
  --verify-cooldown 1800 \
  2>&1 | tee -a "$LOG"

status="${PIPESTATUS[0]}"
echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) status=$status" | tee -a "$LOG"
exit "$status"
