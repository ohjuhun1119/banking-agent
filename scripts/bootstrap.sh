#!/usr/bin/env bash
# 새 Ubuntu 24.04 서버(Oracle ARM 등)에서 Docker 실행 환경을 한 번에 준비한다. 사용: sudo가 되는 기본 사용자로  bash scripts/bootstrap.sh
# 하는 일: Docker + compose 플러그인 설치, 기본 사용자를 docker 그룹에 추가, 80 · 443 방화벽 열기, (메모리가 작으면) 스왑 4GB.
# 이 스크립트는 문법만 시험으로 확인했고 실제 서버에서는 아직 돌려 보지 못했다. 되풀이해서 실행해도 되게 썼다.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx git curl python3
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# Oracle Ubuntu 이미지는 콘솔의 보안 목록과 별개로 서버 안의 iptables가 80 · 443을 막아 둔다.
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save

# 모델이 메모리에 올라가므로 24GB보다 작은 서버(예: 12GB)에서는 스왑을 둔다.
if [ "$(awk '/MemTotal/ {print int($2/1024/1024)}' /proc/meminfo)" -lt 20 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "준비 완료. docker 그룹이 적용되도록 한 번 로그아웃했다가 다시 접속하세요 (exit 후 ssh)."
