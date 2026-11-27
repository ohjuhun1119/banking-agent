"""Caddy 접근 로그를 읽어 누가 어떤 경로를 불렀는지 요약한다 (스캐너 확인용). 로그는 표준입력으로 받는다.

사용 (서버에서):
  docker compose -f compose.yaml -f compose.prod.yaml logs caddy | python3 scripts/access_report.py --me <내 IP>
접근 로그는 접근 키 · 쿠키 · 주소창 키를 이미 지운 채 남고(caddy/Caddyfile), 이 스크립트는 쿼리 문자열도 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys


def parse(lines) -> list[tuple]:
    """접근 로그 줄만 (IP, 메서드, 경로(쿼리 제외), 상태, User-Agent, Host)로 뽑는다. 다른 줄과 깨진 줄은 건너뛴다."""
    rows = []
    for line in lines:
        i = line.find("{")
        if i < 0 or "http.log.access" not in line:
            continue
        try:
            d = json.loads(line[i:])
        except ValueError:
            continue
        r = d.get("request") or {}
        ua = ((r.get("headers") or {}).get("User-Agent") or ["-"])[0]
        rows.append((r.get("remote_ip", "?"), r.get("method", "?"), str(r.get("uri", "?")).split("?")[0], d.get("status"), ua, r.get("host", "?")))
    return rows


def status_counts(rows, mine) -> dict:
    return dict(collections.Counter(str(x[3]) for x in rows if x[0] not in mine))


def report(rows, mine) -> str:
    if not rows:
        return "접근 로그가 없습니다."
    others = [x for x in rows if x[0] not in mine]
    out = [f"접근 로그 총 {len(rows)}건", f"내 IP 제외: {len(others)}건 / {len({x[0] for x in others})}개 IP", "", f"[상태 코드] {status_counts(rows, mine)}", "", "[IP별 요청 수 상위]"]
    out += [f"  {ip:<18}{n}" for ip, n in collections.Counter(x[0] for x in others).most_common(8)]
    out += ["", "[많이 부른 경로 상위]"] + [f"  {n:>4}  {p[:70]}" for p, n in collections.Counter(x[2] for x in others).most_common(15)]
    out += ["", f"[Host] {dict(collections.Counter(x[5] for x in others).most_common(6))}", "", "[User-Agent 상위]"]
    out += [f"  {n:>4}  {u[:90]}" for u, n in collections.Counter(x[4] for x in others).most_common(6)]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Caddy 접근 로그 요약")
    ap.add_argument("--me", action="append", default=[], help="요약에서 뺄 내 IP (여러 번 가능)")
    a = ap.parse_args(argv)
    print(report(parse(sys.stdin), set(a.me)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
