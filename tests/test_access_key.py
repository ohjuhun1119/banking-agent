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

client = TestClient(api.app)
PROTECTED = [("get", "/"), ("get", "/api/chunks"), ("post", "/chat"), ("post", "/api/agent/ask"), ("delete", "/chat/s1")]


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
