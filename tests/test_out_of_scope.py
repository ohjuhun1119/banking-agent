"""범위 밖 질문(대출 · 카드 등)은 모델을 거치지 않고 코드가 정해진 문구로 거절한다 (개발용 Q080: 모델에게 맡기니 대출 금리를 정기예금 금리로 읽고 기본금리를 요구했다).
강제 검색에서 빼는 것만으로는 부족했다: 모델이 질문을 오해했고, 맞지 않는 약관을 주면 엉뚱한 말을 했다."""
import json
import re
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_graph as tg  # noqa: E402
from app import graph as g  # noqa: E402
from app import traces  # noqa: E402
from app.answer_check import foreign_script, phone_numbers  # noqa: E402
from eval import build_questions as bq  # noqa: E402
from eval import evaluate as ev  # noqa: E402

agent, TODAY = tg.agent, tg.TODAY_H
OUT = ["대출 금리는 얼마예요?", "카드 발급은 어떻게 해요?", "적금 이자는 얼마예요?", "주식 투자 추천해줘", "보험 가입하고 싶어요", "대출 이자 계산해 줘", "환전 수수료가 얼마예요?"]


def never_called(msgs, n):
    raise AssertionError("범위 밖 질문에는 모델을 부르면 안 된다")


@pytest.mark.parametrize("question", OUT)
def test_an_out_of_scope_question_is_declined_by_the_code_without_calling_the_model(question):
    a, m = agent(never_called, search_fn=tg.rule_search())
    res = a.ask(question, "s", today=TODAY)
    assert res["answer"] == g.OUT_OF_SCOPE_TEXT and res["stop_reason"] == "out_of_scope" and m.n == 0
    assert res["model_calls"] == 0 and res["tool_calls"] == 0 and res["trace"] == [] and res["evidence"] == [] and res["error"] is None
    assert res["notes_stripped"] == 0 and res["rejected_replies"] == []


def test_a_question_with_a_deposit_word_is_not_declined_and_still_reaches_the_model_and_the_search():
    a, m = agent(lambda msgs, n: AIMessage(content="약관상 그래요." if n == 1 else "검색 결과에 따르면 그래요."), search_fn=tg.rule_search())
    res = a.ask("예금 담보로 대출 받을 수 있어요?", "s", today=TODAY)
    assert res["stop_reason"] is None and m.n >= 1 and [t["name"] for t in res["trace"]] == ["search_terms"] and res["answer"] != g.OUT_OF_SCOPE_TEXT


def test_a_handoff_request_wins_over_the_decline():
    a, m = agent(never_called)
    res = a.ask("대출 문제로 상담원 연결해줘", "s", today=TODAY)
    assert res["stop_reason"] == "handoff_saved" and "접수번호" in res["answer"] and m.n == 0


def test_the_conversation_goes_on_normally_after_a_decline_and_the_decline_is_remembered():
    a, m = agent(lambda msgs, n: AIMessage(content="약관상 그래요." if n == 1 else "검색 결과에 따르면 그래요."), search_fn=tg.rule_search())
    assert a.ask("대출 금리는 얼마예요?", "s", today=TODAY)["stop_reason"] == "out_of_scope"
    res = a.ask("중도해지 이율은 어떻게 돼요?", "s", today=TODAY)
    assert res["stop_reason"] is None and res["trace"][0]["name"] == "search_terms" and res["answer"] == "검색 결과에 따르면 그래요."
    kinds = [type(x).__name__ for x in a.history("s")]
    assert kinds[:2] == ["HumanMessage", "AIMessage"] and a.history("s")[1].content == g.OUT_OF_SCOPE_TEXT and a.ask("카드 발급은 어떻게 해요?", "s")["stop_reason"] == "out_of_scope"


def test_the_decline_text_states_the_scope_and_invents_nothing():
    text = g.OUT_OF_SCOPE_TEXT
    assert "하나의 정기예금" in text and "약관" in text and "이자 계산" in text and "고객센터" in text
    assert not re.search(r"\d", text) and not phone_numbers(text) and not foreign_script(text)             # 숫자 · 전화번호 · 외국어 없음


def test_the_graders_decline_patterns_were_fixed_before_the_text_was_written_and_the_text_meets_them_naturally():
    """채점기의 거절 표현 목록을 문구에 맞춰 고치지 않았다: 목록은 그대로이고 문구는 그중 자연스러운 표현('안내해 드릴 수 없')을 쓴다."""
    assert bq.DECLINE == [r"확인되지", r"제공할 수 없", r"도와드릴 수 없", r"안내해 드릴 수 없", r"알 수 없", r"약관에(?:는)? (?:없|나와 있지)"]
    assert [p for p in bq.DECLINE if re.search(p, g.OUT_OF_SCOPE_TEXT)] == [r"안내해 드릴 수 없"]


def row_of(qid):
    return next(r for r in map(json.loads, (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines()) if r["id"] == qid)


def test_the_dev_loan_question_now_passes_the_grader_end_to_end_and_the_wrong_earlier_answers_still_fail():
    row = row_of("Q080")
    a, m = agent(never_called)
    res = a.ask(row["question"], "s", today=row["today"])
    gr = ev.grade(row, res)
    assert gr["behavior_ok"] and gr["clean"] and gr["facts_ok"] and gr["flags"] == []
    asks_base_rate = {"answer": "죄송합니다, 현재 정기예금의 금리 정보는 확인할 수 없습니다. 기본금리를 알려주시면 도와드리겠습니다.", "error": None, "stop_reason": None, "trace": [], "evidence": [],
                      "model_calls": 1, "ms": 1}
    earlier = ev.grade(row, asks_base_rate)                                                            # 모델에게 맡겼을 때의 답(질문을 오해): 거절이 아니므로 실패한다
    assert earlier["facts_ok"] is False and earlier["behavior_ok"] is False


def test_the_dataset_has_no_other_question_that_is_declined():
    rows = [json.loads(line) for line in (ROOT / "eval" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [(r["id"], r["split"]) for r in rows if g.out_of_scope(r["question"])] == [("Q080", "dev")]               # 검증용 문항은 하나도 거절되지 않는다


def test_the_trace_marks_a_decline_so_it_can_be_counted():
    assert "out_of_scope" in traces.signals_of({"stop_reason": "out_of_scope", "trace": []}, "대출 금리는 얼마예요?")
    assert "out_of_scope" not in traces.signals_of({"stop_reason": None, "trace": []}, "중도해지 이율은?")
