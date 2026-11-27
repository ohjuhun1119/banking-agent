"""Caddy 접근 로그 요약 스크립트 시험: 로그 줄을 읽어 내 IP를 빼고 IP · 경로 · 상태 · User-Agent를 센다. 주소창의 비밀(쿼리)은 출력하지 않는다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import access_report as ar  # noqa: E402


def line(ip, uri, status, ua="curl/8", method="GET", host="a.duckdns.org"):
    d = {"level": "info", "logger": "http.log.access.log0", "msg": "handled request", "status": status,
         "request": {"remote_ip": ip, "method": method, "host": host, "uri": uri, "headers": {"User-Agent": [ua]}}}
    return "caddy-1  | " + json.dumps(d)


LOGS = [line("1.1.1.1", "/", 303), line("1.1.1.1", "/login", 200), line("2.2.2.2", "/.env", 401, "scanner/1"), line("9.9.9.9", "/chat?api_key=SECRET-VALUE", 401),
        "caddy-1  | " + json.dumps({"level": "info", "logger": "tls.obtain", "msg": "certificate obtained successfully"}), "not json at all", "caddy-1  | {broken"]


def test_parse_keeps_only_access_lines_and_strips_the_query_string():
    rows = ar.parse(LOGS)
    assert len(rows) == 4 and ("9.9.9.9", "GET", "/chat", 401, "curl/8", "a.duckdns.org") in rows        # 쿼리(?api_key=...)는 경로에서 뗀다


def test_the_report_excludes_my_ips_and_counts_ips_paths_statuses_and_agents():
    text = ar.report(ar.parse(LOGS), mine={"9.9.9.9"})
    assert "접근 로그 총 4건" in text and "내 IP 제외: 3건 / 2개 IP" in text
    assert "1.1.1.1" in text and "2.2.2.2" in text and "/.env" in text and "scanner/1" in text
    assert {"303": 1, "200": 1, "401": 1} == {k: v for k, v in ar.status_counts(ar.parse(LOGS), {"9.9.9.9"}).items()}


def test_the_report_never_prints_a_secret_that_was_in_the_query():
    assert "SECRET-VALUE" not in ar.report(ar.parse(LOGS), mine=set())


def test_an_empty_log_gives_a_clear_message():
    assert "접근 로그가 없습니다" in ar.report([], mine=set())
