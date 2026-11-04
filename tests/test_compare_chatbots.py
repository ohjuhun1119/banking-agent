"""기존 챗봇과의 비교 도구 (eval/compare_chatbots.py) 시험. 점수 계산이 틀리면 비교 결과 전체가 틀리므로 점수 규칙 · 정답 검산 · 두 시스템에 똑같이 질문하는지를 확인한다."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import test_interest_boundaries as tb  # noqa: E402
from app.tools.interest import calc_interest  # noqa: E402
from eval import compare_chatbots as cc  # noqa: E402

QUESTIONS = cc.load_questions()
SCENARIOS = cc.load_scenarios()
BY_ID = {q["id"]: q for q in QUESTIONS}


# ---------- 문항과 시나리오
def test_the_comparison_set_is_the_39_dev_core_questions_in_five_categories():
    import collections
    assert len(QUESTIONS) == 39 and 30 <= len(QUESTIONS) <= 50                                 # 튜터 피드백: 30~50개
    assert dict(collections.Counter(q["category"] for q in QUESTIONS)) == {"rule": 14, "aftertax": 3, "calc": 8, "calc_base_rate": 4, "missing": 10}
    assert len({q["id"] for q in QUESTIONS}) == 39 and all(q["split"] == "dev" and q["core"] for q in QUESTIONS)
    assert not any(q["split"] == "held_out" for q in QUESTIONS)                                  # 고정한 검증용은 건드리지 않는다


def test_the_questions_that_can_be_scored_from_the_text_alone():
    kinds = [cc.kind_of(q["gold"]) for q in QUESTIONS]
    assert (kinds.count("calc"), kinds.count("ask"), kinds.count("fact")) == (6, 15, 6)          # 약관 규칙 14문항 중 6문항에 핵심 사실 기준이 있다


def test_the_scenarios_cover_changing_the_principal_dates_and_base_rate_in_a_conversation():
    scen = SCENARIOS["scenarios"]
    assert [s["id"] for s in scen] == ["S1", "S2", "S3", "S4", "S5", "S6"] and sum(len(s["turns"]) for s in scen) == 17
    assert all(len(s["turns"]) >= 2 for s in scen)
    titles = " ".join(s["title"] for s in scen)
    assert all(word in titles for word in ("원금", "해지일", "만기일", "기본금리", "정정"))


def all_turns():
    return [pytest.param(s["id"], i, t, id=f"{s['id']}-T{i}") for s in SCENARIOS["scenarios"] for i, t in enumerate(s["turns"], 1)]


@pytest.mark.parametrize("sid,n,turn", all_turns())
def test_every_scenario_answer_is_rechecked_by_the_tool_and_by_the_independent_formula(sid, n, turn):
    args = turn["calc"]
    tool = calc_interest(**args)
    if "interest_krw" in turn["expect"]:
        want = turn["expect"]["interest_krw"]
        assert tool["status"] == "ok" and tool["interest_krw"] == want
        d = tb.date.fromisoformat
        base = args.get("base_rate", tb.BASE)
        assert tb.ref(args["principal"], d(args["start_date"]), d(args["maturity_date"]), d(args["termination_date"]), base)[4] == want
    else:
        assert tool["status"] == "need_base_rate" and "기본금리" in turn["expect"]["asks"]


# ---------- 점수: 답변 글만 본다
def gold_of(behavior, **extra):
    return {"behavior": behavior, **extra}


def test_calc_is_correct_only_when_the_expected_amount_is_in_the_answer():
    g = gold_of("calc_ok", calc={"interest_krw": 8219})
    assert cc.score_answer(g, "세전 이자는 8,219원이에요.") == {"kind": "calc", "correct": True}
    assert cc.score_answer(g, "세전 이자는 8219원이에요.")["correct"] is True
    assert cc.score_answer(g, "세전 이자는 9,000원이에요.")["correct"] is False
    assert cc.score_answer(g, "중도 해지하면 낮은 이율이 적용될 수 있어요. 모바일뱅킹에서 확인해 주세요.")["correct"] is False        # 기존 챗봇처럼 금액을 말하지 않음
    assert cc.score_answer(g, "")["correct"] is False and cc.score_answer(g, None)["correct"] is False


def test_asking_for_the_base_rate_needs_both_a_question_and_the_words_base_rate():
    g = gold_of("ask_base_rate")
    assert cc.score_answer(g, "기본금리를 알려주세요.")["correct"] is True
    assert cc.score_answer(g, "기본금리가 얼마였나요?")["correct"] is True
    assert cc.score_answer(g, "기본금리가 필요합니다.")["correct"] is False                    # 되묻지 않고 말만 함
    assert cc.score_answer(g, "다른 값을 알려주세요.")["correct"] is False                    # 기본금리를 묻지 않음


def test_asking_for_missing_values_needs_a_question_and_one_of_the_missing_names():
    g = gold_of("ask_missing", missing=["만기일"])
    assert cc.score_answer(g, "만기일을 알려주세요.")["correct"] is True
    assert cc.score_answer(g, "만기일은 언제인가요?")["correct"] is True
    assert cc.score_answer(g, "다른 값을 알려주세요.")["correct"] is False
    assert cc.score_answer(g, "만기일이 필요합니다.")["correct"] is False
    assert cc.score_answer(gold_of("ask_missing"), "무엇을 알려주세요?")["correct"] is True      # 부족한 칸 이름이 정해지지 않은 문항


def test_facts_need_all_required_any_one_of_the_options_and_none_of_the_forbidden():
    g = gold_of("search", facts_all=[r"0\.15"], must_not=[r"(?:두 달|2개월)[^.!?]{0,20}3개월 이상"])
    assert cc.score_answer(g, "가입한 지 2개월이면 연 0.15%(고정)예요.") == {"kind": "fact", "correct": True}
    assert cc.score_answer(g, "두 달이므로 3개월 이상 ~ 6개월 미만 구간이라 연 0.20%예요.")["correct"] is False          # 개발용 Q023의 오답
    assert cc.score_answer(g, "두 달이 지나서 3개월 이상 되면 연 0.20%예요. 연 0.15%도 있어요.")["correct"] is False        # 사실(0.15)은 있어도 금지된 말(두 달 ... 3개월 이상)이 있음
    g2 = gold_of("search", facts_any=[r"2회", r"두 번"])
    assert cc.score_answer(g2, "일부해지는 두 번까지예요.")["correct"] is True and cc.score_answer(g2, "여러 번 가능해요.")["correct"] is False


def test_questions_without_a_text_only_rule_are_not_scored():
    for b in ("search", "scope", "not_early", "invalid_input"):
        assert cc.score_answer(gold_of(b), "아무 말") == {"kind": None, "correct": None}


def test_a_scenario_turn_is_scored_by_amount_or_by_asking():
    assert cc.score_expect({"interest_krw": 16438}, "이자는 16,438원이에요.") is True
    assert cc.score_expect({"interest_krw": 16438}, "이자는 8,219원이에요.") is False                       # 바꾼 원금을 반영하지 않은 옛 값
    assert cc.score_expect({"asks": ["기본금리"]}, "기본금리를 알려주세요.") is True
    assert cc.score_expect({"asks": ["기본금리"]}, "기본금리가 필요해요.") is False


def test_percentile_uses_the_nearest_rank():
    assert cc.percentile([], 0.5) is None and cc.percentile([5], 0.9) == 5
    assert cc.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5 and cc.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == 9
    assert cc.percentile([10, 1, 5], 0.5) == 5                                                       # 정렬해서 본다


# ---------- 기존 챗봇을 부르는 부분 (가짜 서버)
class Fake(BaseHTTPRequestHandler):
    seen: list = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        Fake.seen.append((self.path, body))
        if Fake.mode == "422":
            self.send_response(422)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"answer": "상품 조건을 확인해 주세요.", "route": "answered", "label": "만기,연장/해지,수신", "confidence": 0.9, "model": body["model"],
                                     "latency_s": {"total": 1.0}, "error": None, "masked": {}}, ensure_ascii=False).encode("utf-8"))


@pytest.fixture
def server():
    Fake.seen, Fake.mode = [], "ok"
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ask_old_sends_the_question_and_the_model_and_reads_the_answer(server):
    r = cc.ask_old(server, "예금을 중간에 깨면 이자를 못 받나요?", "local")
    assert Fake.seen == [("/chat", {"question": "예금을 중간에 깨면 이자를 못 받나요?", "model": "local"})]
    assert r["answer"] == "상품 조건을 확인해 주세요." and r["route"] == "answered" and r["error"] is None and r["evidence_shown"] is False and r["ms"] >= 0


def test_ask_old_turns_every_failure_into_an_error_row_instead_of_raising(server):
    Fake.mode = "422"
    assert cc.ask_old(server, "q")["error"] == "HTTP 422"
    dead = cc.ask_old("http://127.0.0.1:9", "q", timeout=2)                                        # 아무도 듣지 않는 포트
    assert dead["answer"] == "" and dead["error"] and dead["evidence_shown"] is False


def test_old_alive_tells_a_running_server_from_a_dead_one(server):
    assert cc.old_alive(server) is True and cc.old_alive("http://127.0.0.1:9", timeout=2) is False


# ---------- 실행: 두 시스템에 같은 질문이 똑같이 간다
def fake_new(calls):
    def ask(question, session, today=None):
        calls.append((question, session, today))
        return {"answer": "세전 이자는 8,219원이에요. 기본금리를 알려주세요?", "stop_reason": None, "error": None, "evidence_text": "근거\n- [계산] …", "model_calls": 1,
                "trace": [{"name": "calc_interest"}]}
    return SimpleNamespace(ask=ask)


def run_small(old_answers=None, limit=3):
    old_calls, new_calls = [], []

    def old(q):
        old_calls.append(q)
        return {"answer": "상품 조건을 확인해 주세요.", "route": "answered", "label": None, "error": None, "ms": 2000, "evidence_shown": False}
    agent = fake_new(new_calls)
    systems = {"old": old, "new": lambda q, s: cc.ask_new(agent, q, s, SCENARIOS["today"])}
    result = cc.run(QUESTIONS[:limit], SCENARIOS, systems, progress=lambda *a: None)
    return result, old_calls, new_calls


def test_both_systems_get_exactly_the_same_questions_and_the_old_one_gets_each_turn_alone():
    result, old_calls, new_calls = run_small()
    single = [q["question"] for q in QUESTIONS[:3]]
    turns = [t["question"] for s in SCENARIOS["scenarios"] for t in s["turns"]]
    assert old_calls == single + turns and [c[0] for c in new_calls] == single + turns          # 같은 질문, 같은 순서
    assert all(c[2] == "2026-10-28" for c in new_calls)                                          # 이번 에이전트에는 오늘 날짜도 같이
    rest, start = new_calls[3:], 0                                                               # 단일 질문 뒤는 시나리오 순서대로 (S1 · S2의 첫 질문은 글이 같아서 질문 글로는 구별되지 않는다)
    for sc in SCENARIOS["scenarios"]:
        chunk = rest[start:start + len(sc["turns"])]
        assert [c[0] for c in chunk] == [t["question"] for t in sc["turns"]] and {c[1] for c in chunk} == {f"cmp-{sc['id']}"}      # 시나리오의 턴은 같은 세션으로 이어진다 (기존 챗봇은 세션이 없다)
        start += len(sc["turns"])
    assert len({c[1] for c in new_calls[:3]}) == 3                                               # 단일 질문은 서로 다른 세션이다


def test_the_result_keeps_both_answers_with_scores_and_the_summary_counts_them():
    result, _, _ = run_small(limit=39)
    assert result["meta"]["systems"] == ["old", "new"] and result["meta"]["questions"] == 39 and len(result["scenarios"]) == 6
    first = result["rows"][0]
    assert first["old"]["answer"] == "상품 조건을 확인해 주세요." and first["new"]["evidence_shown"] is True and "model_calls" in first["new"] and first["new"]["tools"] == ["calc_interest"]
    s = cc.summarize(result)
    assert s["old"]["calc"][1] == s["new"]["calc"][1] == 6 and s["old"]["ask"][1] == 15 and s["old"]["fact"][1] == 6
    assert s["old"]["evidence"] == (0, 39) and s["new"]["evidence"] == (39, 39)                  # 기존 챗봇은 근거 표시 기능이 없다
    assert s["old"]["scenario_turns"][1] == 17 and s["old"]["scenario_first"][1] == 6 and s["old"]["scenario_later"][1] == 11
    assert s["old"]["p50_ms"] == 2000 and s["old"]["calc"][0] == 0 and s["old"]["scenario_turns"][0] == 0


def test_a_failed_call_is_counted_as_an_error_and_left_out_of_the_time():
    def failing(q):
        return {"answer": "", "route": None, "label": None, "error": "HTTP 500", "ms": 99999, "evidence_shown": False}
    result = cc.run(QUESTIONS[:2], {"today": "2026-10-28", "scenarios": []}, {"old": failing}, progress=lambda *a: None)
    s = cc.summarize(result)["old"]
    assert s["errors"] == 2 and s["p50_ms"] is None


def test_the_saved_answers_are_cut_so_that_long_terms_text_is_not_copied():
    long = {"answer": "약관 " * 400, "route": None, "label": None, "error": None, "ms": 1, "evidence_shown": False}
    result = cc.run(QUESTIONS[:1], {"today": "2026-10-28", "scenarios": []}, {"old": lambda q: long}, progress=lambda *a: None)
    assert len(result["rows"][0]["old"]["answer"]) == cc.SAVED_CHARS


def test_the_table_has_every_criterion_the_tutor_asked_for_and_is_honest_about_a_missing_system():
    result, _, _ = run_small(limit=39)
    table = cc.format_table(cc.summarize(result), result["meta"])
    for word in ("계산이 맞는지", "정보가 부족하면 되묻는지", "약관의 핵심 사실", "근거를 보여 주는지", "응답 시간", "값을 바꾼 사례", "첫 질문", "값을 바꾼 뒤", "오류"):
        assert word in table
    assert "0/39 (근거 표시 기능 없음)" in table and "39/39" in table and "2.0초" in table
    only_new = cc.run(QUESTIONS[:3], {"today": "2026-10-28", "scenarios": []}, {"new": lambda q, s: cc.ask_new(fake_new([]), q, s, "2026-10-28")}, progress=lambda *a: None)
    assert "(실행 안 함)" in cc.format_table(cc.summarize(only_new), only_new["meta"])


# ---------- 명령줄
def test_main_runs_the_new_agent_alone_saves_the_result_and_rescore_prints_the_same_table(tmp_path, capsys):
    out = tmp_path / "r.json"
    assert cc.main(["--skip-old", "--limit", "2", "--no-scenarios", "--save", str(out)], new_agent=fake_new([])) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["meta"]["systems"] == ["new"] and len(saved["rows"]) == 2 and saved["scenarios"] == []
    printed = capsys.readouterr().out
    assert cc.main(["--rescore", str(out)]) == 0
    assert cc.format_table(cc.summarize(saved)) in capsys.readouterr().out and "(실행 안 함)" in printed


def test_main_stops_with_a_clear_message_when_the_old_server_is_not_running(capsys):
    assert cc.main(["--old-url", "http://127.0.0.1:9", "--skip-new"]) == 2
    out = capsys.readouterr().out
    assert "닿지 않습니다" in out and "uvicorn app.main:app --port 8001" in out and "--skip-old" in out


def test_main_refuses_to_run_with_no_system(capsys):
    assert cc.main(["--skip-old", "--skip-new"]) == 2
