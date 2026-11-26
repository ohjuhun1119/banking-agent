#!/usr/bin/env bash
# compose.langfuse.yaml용 .env.langfuse를 무작위 비밀값으로 만든다 (이미 있으면 건드리지 않는다). 사용: scripts/langfuse_env.sh
# 만들어지는 LANGFUSE_PUBLIC_KEY · LANGFUSE_SECRET_KEY가 에이전트가 트레이스를 보낼 때 쓰는 키다 (.env.langfuse는 깃에 올라가지 않는다).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=".env.langfuse"
if [ -e "$OUT" ]; then echo "$OUT 이(가) 이미 있어 그대로 둡니다."; exit 0; fi
umask 077
cat > "$OUT" <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 16)
CLICKHOUSE_PASSWORD=$(openssl rand -hex 16)
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
REDIS_AUTH=$(openssl rand -hex 16)
SALT=$(openssl rand -hex 16)
ENCRYPTION_KEY=$(openssl rand -hex 32)
NEXTAUTH_SECRET=$(openssl rand -hex 32)
LANGFUSE_ADMIN_PASSWORD=$(openssl rand -hex 12)
LANGFUSE_PUBLIC_KEY=pk-lf-$(openssl rand -hex 16)
LANGFUSE_SECRET_KEY=sk-lf-$(openssl rand -hex 16)
EOF
echo "$OUT 을(를) 만들었습니다 (권한 600). 로그인: admin@banking.local / LANGFUSE_ADMIN_PASSWORD"
