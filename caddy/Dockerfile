# 앞단 프록시: HTTPS(자동 인증서) + 요청 속도 제한. 속도 제한은 기본 Caddy에 없어 플러그인을 넣어 직접 빌드한다.
# shortcut: 이미지 · 플러그인 버전을 고정하지 않았다. 재현이 필요해지면 태그를 고정한다.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/mholt/caddy-ratelimit

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
