#!/usr/bin/env bash
# data/의 SQLite(상담 요청 · 트레이스)를 서비스가 돌고 있는 중에도 안전하게 복사한다 (SQLite 백업 API). 최근 14개만 남긴다.
# 사용: scripts/backup_data.sh [백업 폴더 (기본 backups)]     cron 예: 0 4 * * * /home/ubuntu/banking-agent/scripts/backup_data.sh
# shortcut: 같은 서버 안에 복사만 한다. 서버가 사라져도 남겨야 하면 이 폴더를 다른 곳으로 내려받는 단계를 붙인다.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:-backups}"
OUT="$DEST/$(date +%F-%H%M)"
mkdir -p "$OUT"
for f in data/*.sqlite3; do
  python3 - "$f" "$OUT/$(basename "$f")" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
done
ls -1d "$DEST"/*/ | sort -r | tail -n +15 | while read -r old; do rm -rf "$old"; done
echo "백업 완료: $OUT"
