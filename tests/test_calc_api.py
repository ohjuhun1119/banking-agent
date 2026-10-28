"""이자 계산 API(/api/calc, /api/calc/cases) 시험. 화면과 에이전트가 같은 도구 결과를 받는지 확인한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
try:
    from fastapi.testclient import TestClient
    from app.api import app
except Exception as e:                                       # 검색 쪽 의존성(Chroma, Kiwi 등)이 없는 환경에서는 건너뛴다
    pytest.skip(f"app.api를 불러오지 못했습니다: {e}", allow_module_level=True)

client = TestClient(app)


def post(**body):
    r = client.post("/api/calc", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_cases_endpoint_serves_all_notion_cases():
    cases = client.get("/api/calc/cases").json()
    assert [c["id"] for c in cases] == list("ABCDEFG") and cases[3]["interest"] == 63013


def test_every_case_matches_through_the_api():
    for c in client.get("/api/calc/cases").json():
        r = post(**{k: c[k] for k in ("principal", "start_date", "maturity_date", "termination_date", "base_rate")})
        assert r["status"] == "ok" and r["interest_krw"] == c["interest"], c["id"]


def test_form_style_strings_are_accepted():
    r = post(principal="10,000,000", start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-07-20", base_rate="3.5%")
    assert r["status"] == "ok" and r["interest_krw"] == 63013


def test_blank_form_fields_are_treated_as_missing_not_as_errors():
    r = post(principal="", start_date="", maturity_date="", termination_date="", base_rate="")
    assert r["status"] == "missing_info" and r["missing"] == ["원금", "신규일", "만기일", "해지일"]


def test_validation_failures_are_returned_as_status_not_http_errors():
    r = post(principal=500000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-05-31")
    assert r["status"] == "invalid_input" and r["errors"][0]["label"] == "원금"


def test_need_base_rate_status_is_returned():
    r = post(principal=10000000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-07-20")
    assert r["status"] == "need_base_rate" and r["completed_months"] == 6
