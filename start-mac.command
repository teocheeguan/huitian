#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '请先安装 Python 3.9 或更新版本。'
  exit 1
fi
python3 -B app.py start "$@"
