"""노션 설계의 '오류 상황 처리' 11가지를 하나씩 재현해 처리 방식을 확인한다 (8일차).

| # | 상황 | 처리 | 보장 |
| 1 | 검색 결과 없음 | '약관에서 확인되지 않습니다' (not_found일 때만 허용), 근거 카드 '관련 약관을 찾지 못했습니다' | 코드 |
| 2 | 계산에 필요한 값 부족 | 부족한 값만 골라 되묻기 (도구 메시지가 질문) | 코드 |
| 3 | 입력값 검사 실패 (단위 · 범위) | 도구가 검사해 값을 다시 확인해 달라고 안내 (예: 최소 가입금액 100만 원) | 코드 |
| 4 | 도구 실행 실패 · 대기 시간 초과 | 1회 재시도 후 실패 안내 + 상담 요청 저장 제안 | 코드 |
| 5 | 도구 호출 횟수 초과 | 반복 중단, 상담 요청 저장 제안 ('네'라고 하면 저장) | 코드 |
| 6 | 상담 요청 저장 실패 | 접수번호 없이 '저장하지 못했어요' | 코드 |
| 7 | 해지일이 만기일 이후 | 중도해지가 아니므로 계산하지 않고 안내 | 코드 |
| 8 | 만기일이 휴일(토 · 일)이고 해지일이 직전 영업일 | 계산은 하되 '만기 해지로 처리될 수 있어 은행 확인 필요' 경고 (공휴일은 감지 못함) | 코드 (주말만) |
| 9 | 경과 6개월 이상인데 기본금리를 모름 | '가입 당시 고시된 기본금리'를 되묻기 + 근거 카드 | 코드 |
| 10 | 일부해지 질문 | 약관 근거 안내, 계산하지 않음, 상담 요청 저장 제안 | 검색은 코드, 제안 문장은 프롬프트(모델 변동) |
| 11 | 세후 이자 · 세금 질문 | 세전 이자만, 세후 약속 차단, 기본약관 제9조 ⑤ 근거 안내 | 약속 차단은 코드, 근거 안내는 프롬프트(모델 변동) |
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_graph as tg  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from app import graph as g  # noqa: E402
from app.tools import handoff as hf  # noqa: E402

agent, ai, call, last_tool_json = tg.agent, tg.ai, tg.call, tg.last_tool_json
TODAY = "2026-10-28"


def echo_tool_message(msgs, n, first):
    """모델 흉내: 처음에는 first를 부르고, 도구 결과가 오면 그 message나 요약을 그대로 읽어 준다."""
    if n == 1:
        return first
    r = last_tool_json(msgs)
    return AIMessage(content=r.get("message") or r.get("summary") or "")


# 1
def test_case_1_nothing_found_is_said_only_after_a_not_found_search():
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="대출 한도")) if n == 1 else AIMessage(content="약관에서 확인되지 않습니다."),
                 search_fn=lambda q, **kw: {"status": "not_found", "query": q, "results": []})
    res = a.ask("예금 대출 한도가 얼마예요?", "s")
    assert res["answer"] == "약관에서 확인되지 않습니다." and res["evidence"][0]["role"] == "관련 약관을 찾지 못했습니다"


# 2
def test_case_2_only_the_missing_values_are_asked_for():
    a, m = agent(lambda msgs, n: echo_tool_message(msgs, n, ai(call("calc_interest"))))
    res = a.ask("1000만원을 2026-01-01에 가입했고 만기는 2027-01-01이에요. 이자 얼마예요?", "s", today=TODAY)
    assert res["answer"] == "계산에 필요한 값이 없습니다: 해지일. 이 값을 알려주세요."


# 3
def test_case_3_a_value_that_fails_the_range_check_is_sent_back_to_the_user():
    a, m = agent(lambda msgs, n: echo_tool_message(msgs, n, ai(call("calc_interest", principal=500_000, start_date="2026-01-01", maturity_date="2027-01-01",
                                                                    termination_date="2026-03-02"))))
    res = a.ask("50만원을 2026-01-01에 가입했고 만기는 2027-01-01이에요. 2026-03-02에 해지하면 이자 얼마예요?", "s", today=TODAY)
    assert res["trace"][0]["status"] == "invalid_input" and "최소 가입금액은 100만원입니다" in res["answer"]


# 4
def test_case_4_a_failing_tool_is_retried_once_then_reported_with_an_offer():
    calls = []

    def failing(q, **kw):
        calls.append(q)
        raise RuntimeError("색인 오류")
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="일부해지")) if n == 1 else AIMessage(content="모르겠어요."), search_fn=failing)
    res = a.ask("일부 해지가 되나요?", "s")
    assert len(calls) == 2 and res["trace"][0]["retried"] and res["answer"] == g.TOOL_ERROR_TEXT


def test_case_4_a_tool_that_times_out_is_treated_the_same_way():
    calls = []

    def slow(q, **kw):
        calls.append(q)
        time.sleep(0.4)
        return tg.search_ok(q)
    a, m = agent(lambda msgs, n: ai(call("search_terms", query="일부해지")) if n == 1 else AIMessage(content="모르겠어요."), search_fn=slow, tool_timeout=0.05)
    res = a.ask("일부 해지가 되나요?", "s")
    assert res["trace"][0]["status"] == "error" and res["trace"][0]["retried"] and len(calls) == 2 and res["answer"] == g.TOOL_ERROR_TEXT


# 5
def test_case_5_too_many_tool_calls_stop_the_loop_and_offer_to_save_a_request():
    a, m = agent(lambda msgs, n: ai(call("search_terms", query=f"검색{n}")), search_fn=tg.rule_search())
    res = a.ask("중도해지 규칙을 알려줘", "s")
    assert res["stop_reason"] == "tool_limit" and res["tool_calls"] == 5 and g.HANDOFF_CONFIRM_MARK in res["answer"]
    assert a.ask("네", "s")["stop_reason"] == "handoff_saved"


# 6
def test_case_6_a_failed_save_is_never_called_received(tmp_path, monkeypatch):
    folder = tmp_path / "f"
    folder.mkdir()
    monkeypatch.setattr(hf, "DB_PATH", folder)
    a, m = agent(lambda msgs, n: AIMessage(content="x"))
    res = a.ask("직원 연결해 주세요", "s")
    assert res["answer"] == g.HANDOFF_FAIL_TEXT and "접수번호" not in res["answer"] and hf.count_requests(tmp_path / "none") == 0


# 7
def test_case_7_a_termination_after_maturity_is_not_calculated():
    a, m = agent(lambda msgs, n: echo_tool_message(msgs, n, ai(call("calc_interest", principal=10_000_000, start_date="2025-06-01", maturity_date="2026-06-01",
                                                                    termination_date="2026-10-28"))))
    res = a.ask("정기예금 1,000만 원을 2025년 6월 1일에 들었고 2026년 6월 1일 만기인데, 오늘 해지하면 이자 얼마예요?", "s", today=TODAY)
    assert res["trace"][0]["status"] == "not_early_termination" and "중도해지가 아닙니다" in res["answer"] and res["evidence"] == []


# 8
def test_case_8_a_weekend_maturity_with_a_last_business_day_termination_is_flagged_for_a_bank_check():
    q = "1000만원을 2025-12-05에 가입했고 만기는 2026-12-05예요. 2026-12-04에 해지하면 이자 얼마예요? 기본금리는 3.5%예요."

    def fn(msgs, n):
        if n == 1:
            return ai(call("calc_interest", principal=10_000_000, start_date="2025-12-05", maturity_date="2026-12-05", termination_date="2026-12-04", base_rate=3.5))
        return AIMessage(content=f"세전 이자는 {last_tool_json(msgs)['interest_krw']:,}원이에요.")
    a, m = agent(fn, search_fn=tg.rule_search())
    res = a.ask(q, "s", today=TODAY)
    assert res["trace"][0]["status"] == "ok" and any("휴일" in w for w in res["evidence"][0]["warnings"])
    assert "주의: 만기일(2026-12-05)이 휴일(토 · 일)이고 해지일이 직전 영업일입니다" in res["evidence_text"]


# 9
def test_case_9_after_six_months_the_base_rate_is_asked_and_the_reason_is_cited():
    a, m = agent(lambda msgs, n: echo_tool_message(msgs, n, ai(call("calc_interest", principal=10_000_000, start_date="2026-01-01", maturity_date="2027-01-01",
                                                                    termination_date="2026-12-07"))), search_fn=tg.rule_search())
    res = a.ask("1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요?", "s", today=TODAY)
    assert res["trace"][0]["status"] == "need_base_rate" and "기본금리(연 %)는 얼마였나요?" in res["answer"]
    assert [c["kind"] for c in res["evidence"]] == ["약관"] and "경과 6개월 이상" in res["evidence"][0]["role"]


# 10
def test_case_10_a_partial_termination_question_is_searched_not_calculated_and_the_prompt_asks_for_the_offer():
    a, m = agent(lambda msgs, n: AIMessage(content="일부해지는 약관에 따라 가능해요."), search_fn=tg.rule_search())
    res = a.ask("1,200만 원 예금 중에 300만 원만 먼저 뺄 수 있어요?", "s")
    assert res["trace"][0]["name"] == "search_terms" and res["trace"][0]["forced_kind"] == "search" and not any(t["name"] == "calc_interest" for t in res["trace"])
    assert [c["kind"] for c in res["evidence"]] == ["약관", "약관"]
    text = g.system_text({"today": TODAY})                                              # 제안 문장은 프롬프트에 의존한다 (모델에 따라 달라질 수 있다)
    assert "일부해지 질문은 약관 안내만 하고 계산하지 않으며" in text and "상담 요청을 저장할까요?" in text


# 11
def test_case_11_after_tax_is_never_promised_and_the_prompt_points_to_the_withholding_clause():
    a, m = agent(lambda msgs, n: AIMessage(content="세후 이자도 계산해 드릴게요." if n == 1 else "세전 이자만 계산할 수 있어요. 원금과 날짜를 알려주세요."))
    res = a.ask("세금 떼고 실제로 얼마 받아요?", "s")
    assert res["answer"] == "세전 이자만 계산할 수 있어요. 원금과 날짜를 알려주세요."
    assert "세후 금액은 약속하지" in m.seen[1][-1].content                                   # 약속은 한 번 다시 시켜서 고쳤다
    assert res["trace"][0]["forced_kind"] == "calc"                                         # 금액을 묻는 질문이라 코드가 계산 도구를 값 없이 불러 값을 되묻게 한다 (7일차 규칙)
    text = g.system_text({"today": TODAY})
    assert "세후 이자(세금을 뺀 금액)는 계산하지 못하므로 약속하지 않는다" in text and "기본약관 제9조 ⑤" in text
