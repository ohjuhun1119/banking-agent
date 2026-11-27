"""공개 배포용 접근 키 시험: API_KEY 환경변수가 있으면 /health 말고는 X-API-Key 헤더가 맞아야 통과한다. 없으면 열려 있다(로컬 개발)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
try:
    from fastapi.testclient import TestClient
    from app import api
except Exception as e:
    pytest.skip(f"app.api를 불러오지 못했습니다: {e}", allow_module_level=True)

client = TestClient(api.app, follow_redirects=False)
PROTECTED = [("get", "/api/chunks"), ("post", "/chat"), ("post", "/api/agent/ask"), ("delete", "/chat/s1")]


def hit(method, path, **kw):
    return getattr(client, method)(path, **kw)


def test_without_a_configured_key_everything_is_open(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    assert hit("get", "/health").status_code == 200
    assert hit("get", "/api/chunks").status_code != 401


@pytest.mark.parametrize("method,path", PROTECTED)
def test_a_configured_key_blocks_requests_without_it(monkeypatch, method, path):
    monkeypatch.setenv("API_KEY", "secret-1")
    assert hit(method, path).status_code == 401
    assert hit(method, path, headers={"X-API-Key": "wrong"}).status_code == 401


def test_the_right_key_passes_the_gate(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    assert hit("get", "/api/chunks", headers={"X-API-Key": "secret-1"}).status_code != 401


def test_health_stays_open_for_the_container_healthcheck(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    assert hit("get", "/health").status_code == 200


def test_a_key_in_the_query_string_is_not_accepted(monkeypatch):
    """주소창 키는 로그와 기록에 남는다."""
    monkeypatch.setenv("API_KEY", "secret-1")
    assert hit("get", "/api/chunks?api_key=secret-1").status_code == 401


# ---------- 브라우저 로그인: 키를 쿠키로 받는다 (브라우저는 X-API-Key 헤더를 못 보낸다) ----------
def fresh():
    return TestClient(api.app, follow_redirects=False)                         # 쿠키 통을 시험마다 새로 쓴다


def login(client, key, **kw):
    return client.post("/login", content=f"key={key}", headers={"content-type": "application/x-www-form-urlencoded"}, **kw)


def test_the_login_page_is_open_and_the_home_page_sends_a_browser_there(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    client = fresh()
    page = client.get("/login")
    assert page.status_code == 200 and "<form" in page.text and 'type="password"' in page.text
    home = client.get("/")
    assert home.status_code == 303 and home.headers["location"] == "/login"


def test_the_right_key_sets_an_http_only_cookie_that_opens_the_gate(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    client = fresh()
    res = login(client, "secret-1")
    assert res.status_code == 303 and res.headers["location"] == "/"
    cookie = res.headers["set-cookie"].lower()
    assert "api_key=secret-1" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/chunks").status_code != 401                              # 응답의 쿠키를 클라이언트가 보관해 다음 요청에 보낸다
    assert fresh().get("/api/chunks").status_code == 401                              # 쿠키 없는 다른 브라우저는 그대로 막힌다


def test_a_wrong_key_gets_no_cookie_and_a_wrong_cookie_does_not_pass(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    client = fresh()
    res = login(client, "nope")
    assert res.status_code == 401 and "set-cookie" not in res.headers
    client.cookies.set("api_key", "nope")
    assert client.get("/api/chunks").status_code == 401
    assert client.post("/chat", json={"question": "x"}).status_code == 401


def test_the_login_page_does_nothing_without_a_configured_key(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    client = fresh()
    assert client.get("/login").status_code == 303                                   # 열려 있으니 로그인이 필요 없다
    assert client.get("/").status_code == 200


def test_the_login_cookie_is_marked_secure_only_when_the_request_came_over_https(monkeypatch):
    """Caddy(HTTPS 종료)가 X-Forwarded-Proto를 붙여 넘긴다. 로컬 개발(http)에서는 Secure를 붙이면 쿠키가 안 가므로 붙이지 않는다."""
    monkeypatch.setenv("API_KEY", "secret-1")
    form = {"content-type": "application/x-www-form-urlencoded"}
    plain = client.post("/login", content="key=secret-1", headers=form)
    secure = client.post("/login", content="key=secret-1", headers={**form, "x-forwarded-proto": "https"})
    assert "secure" not in plain.headers["set-cookie"].lower()
    cookie = secure.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
