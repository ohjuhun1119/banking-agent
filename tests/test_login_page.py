"""로그인 화면 시험: 장식 없는 은행 앱 로그인 모양, 외부 자원 없음, 오류 표시, 보안 헤더. 튜터 피드백: 'AI 티 나지 않게, 서비스처럼'."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app import api  # noqa: E402
from app.login_page import render  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")


def visible_text(html: str) -> str:
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def markup(html: str) -> str:
    return re.sub(r"<style.*?</style>", "", html, flags=re.S)                    # CSS 선택자(input[aria-invalid="true"])는 제외하고 실제 태그만 본다


def test_the_page_is_a_plain_korean_bank_style_login_form():
    html = render()
    assert '<html lang="ko">' in html and 'name="viewport"' in html and "<title>정기예금 상담</title>" in html
    assert re.search(r'<form[^>]*method="post"[^>]*action="/login"', html)
    assert re.search(r'<input[^>]*name="key"[^>]*type="password"|<input[^>]*type="password"[^>]*name="key"', html)
    text = visible_text(html)
    assert "접근 키" in text and "로그인" in text and "참고용" in text


def test_the_page_is_self_contained_and_undecorated():
    html = render()
    assert not re.search(r"https?://|<script|<link|<img|<svg|@import|url\(", html)                    # 외부 자원 없음 (접근 키 없이 /static은 막혀 있어 CSS도 인라인)
    assert not re.search(r"gradient|box-shadow|text-shadow|animation|transition|backdrop|filter", html)    # 장식 없음
    assert not EMOJI.search(html)


def test_the_page_does_not_sound_like_an_ai_demo():
    text = visible_text(render())
    for word in ("AI", "Agent", "에이전트", "챗봇", "LLM", "도구", "모델"):
        assert word not in text, word


def test_the_error_state_shows_an_alert_and_marks_the_field_and_the_normal_state_does_not():
    ok, bad = markup(render()), markup(render(error=True))
    assert 'role="alert"' not in ok and 'aria-invalid="true"' not in ok and "맞지 않습니다" not in visible_text(ok)
    assert 'role="alert"' in bad and 'aria-invalid="true"' in bad and "접근 키가 맞지 않습니다" in visible_text(bad)


def test_a_failed_login_is_401_with_the_alert_and_the_login_page_is_not_cacheable_or_frameable(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-1")
    client = TestClient(api.app, follow_redirects=False)
    page = client.get("/login")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store" and page.headers["x-frame-options"] == "DENY"
    bad = client.post("/login", content="key=nope", headers={"content-type": "application/x-www-form-urlencoded"})
    assert bad.status_code == 401 and 'role="alert"' in bad.text and bad.headers["cache-control"] == "no-store" and "set-cookie" not in bad.headers
