# 약관 조항 대조 (런타임 가드 + 채점) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 답변에 나온 퍼센트 · 기간 · 횟수가 사용자 말이나 도구 결과(검색된 조항, 계산 · 구간 조회 결과)에 없으면 막고(한 번 다시 시킴 → 고정 문구), 평가가 그 발생 횟수를 센다.

**Architecture:** `app/answer_check.py`에 순수 함수 `ungrounded_terms`를 추가하고, `app/graph.py`의 `check_reply`가 `ungrounded` 다음에 새 문제 `terms`로 부른다. 걸린 답은 기존 `rejected_replies`에 기록되므로 `eval/evaluate.py`는 그 기록에서 `terms` 횟수를 세어 지표로 낸다.

**Tech Stack:** Python 3.14, pytest, 표준 라이브러리(`re`, `decimal`).

**Spec:** `docs/superpowers/specs/2026-11-24-clause-grounding-design.md`

## Global Constraints

- 새 의존성을 추가하지 않는다. 모델 호출을 늘리지 않는다(다시 시키는 것은 기존 `ungrounded`와 같은 한 번).
- 약관 본문(원문 · 청크)을 시험이나 fixture에 넣지 않는다. 시험에는 직접 쓴 짧은 가짜 조항만 쓴다.
- 날짜 · 금액 점검(`ungrounded_values`)은 바꾸지 않는다.
- 점검을 통과시키려고 느슨하게 하지 않는다. 거짓 거절은 측정해서 보고한다.
- 기존 시험 전체와 `python eval/ci_gate.py`가 계속 통과해야 한다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- 날짜 속 숫자(`2026년 11월 20일`, `11월 20일`, `2026-11-20`)가 기간(`N년`, `N일`)으로 잘못 읽혀 맞는 답을 막는 경우: 날짜는 기간으로 세지 않는다.
- 같은 값의 다른 표기(`0.20%` / `0.2%` / `연 0.20 %`, `3개월` / `3 개월`): 같은 값으로 본다.
- 답변 끝에 코드가 덧붙인 `(질문에서 읽은 값: ...)`의 숫자: 모델의 주장이 아니므로 검사에서 뺀다(`READ_VALUES_RE`).
- 도구 결과에 `60%` 같은 비율이 있고 답변이 같은 값을 말하는 경우: 막지 않는다.
- 허용 글이 비어 있고(도구를 안 부른 턴) 답변에 `0.15%`가 있는 경우: 사용자가 말하지 않았으면 막는다(약관 근거 없는 금리 주장).

---

### Task 1: `ungrounded_terms` 순수 함수

**Files:**
- Modify: `app/answer_check.py` (`ungrounded_values` 아래)
- Test: `tests/test_clause_terms.py` (새 파일)

**Interfaces:**
- Consumes: 없음(표준 라이브러리만). 같은 파일의 `READ_VALUES_RE`.
- Produces: `ungrounded_terms(answer, allowed_texts) -> list[str]`: 답변에 나온 퍼센트 · 기간(`개월`/`일`/`년`/`영업일`) · 횟수(`회`) 중 허용 글에 없는 값을 `0.25%`, `3개월`, `2회` 형태로(중복 없이, 등장 순서) 돌려준다. 답변이 문자열이 아니면 `[]`. 허용 글의 문자열이 아닌 항목은 무시한다.

- [ ] **Step 1: Write the failing tests** (`tests/test_clause_terms.py`, 가짜 조항 `CLAUSE = "중도해지 이율은 가입 3개월 이상 6개월 미만 연 0.20%입니다. 일부해지는 2회까지 할 수 있습니다."`):
  - `test_a_value_in_the_clause_passes`: 답변 `"3개월 이상이면 연 0.20%예요."` → `[]`.
  - `test_a_percent_not_in_the_clause_is_reported`: 답변 `"연 0.25%예요."` → `["0.25%"]`.
  - `test_a_count_not_in_the_clause_is_reported`: 답변 `"일부해지는 3회까지 돼요."` → `["3회"]`.
  - `test_a_period_not_in_the_clause_is_reported`: 답변 `"9개월 미만이에요."` → `["9개월"]`.
  - `test_the_same_value_in_another_notation_passes`: 허용 글 `"연 0.2 %"`, 답변 `"0.20%"` → `[]`; 허용 글 `"3 개월"`, 답변 `"3개월"` → `[]`.
  - `test_values_the_user_said_are_allowed`: 허용 글에 `"6개월 전에 가입했어요"`만 있어도 답변 `"6개월이면…"` → `[]`.
  - `test_dates_are_not_read_as_periods`: 답변 `"2026년 11월 20일에 해지하면, 11월 20일 기준이에요. 2026-11-20"` → `[]`.
  - `test_the_read_values_suffix_added_by_code_is_not_checked`: 답변 `"…(질문에서 읽은 값: 가입 12개월)"`(허용 글에 12개월 없음) → `[]`.
  - `test_no_allowed_text_means_any_term_is_reported_once`: 허용 글 `[]`, 답변 `"0.15%, 0.15%"` → `["0.15%"]`.
  - `test_non_string_answer_and_non_string_allowed_items_are_ignored`: `ungrounded_terms(None, [CLAUSE]) == []`, `ungrounded_terms("0.20%", [None, CLAUSE]) == []`.
- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest tests/test_clause_terms.py -q -p no:cacheprovider`
  Expected: FAIL (ImportError: `ungrounded_terms`).
- [ ] **Step 3: Implement `ungrounded_terms(answer, allowed_texts) -> list[str]` in `app/answer_check.py`.** 값 추출은 하나의 내부 함수로 한다: 먼저 날짜 모양(`\d{4}[-.]\d{1,2}[-.]\d{1,2}`, `\d{4}년(\s?\d{1,2}월(\s?\d{1,2}일)?)?`, `\d{1,2}월\s?\d{1,2}일`)과 `READ_VALUES_RE`를 지운 뒤, 퍼센트는 `Decimal`로 정규화해(`0.20` = `0.2`) `"{값}%"`로, 기간 · 횟수는 `(\d+)\s?(영업일|개월|일|년|회)`로 `"{숫자}{단위}"`로 만든다. 허용 글의 값 집합에 없는 답변의 값만, 처음 나온 순서로 중복 없이 돌려준다.
- [ ] **Step 4: Run to verify they pass**
  Run: `.venv/bin/python -m pytest tests/test_clause_terms.py -q -p no:cacheprovider`
  Expected: 10 passed.
- [ ] **Step 5: Commit** `app/answer_check.py`, `tests/test_clause_terms.py` — `feat: check percentages, periods and counts in a reply against the allowed texts`

### Task 2: 런타임 가드 연결

**Files:**
- Modify: `app/graph.py` (`RETRY_NOTES`, `check_reply` 본문과 설명, `answer_check` import)
- Test: `tests/test_graph.py` 또는 새 파일 `tests/test_clause_guard.py` (기존 그래프 시험의 가짜 모델 · 에이전트 헬퍼를 같은 방식으로 쓴다)

**Interfaces:**
- Consumes: Task 1의 `ungrounded_terms`; `_allowed_texts(state, whole_conversation=True)`.
- Produces: `check_reply`가 `("terms", "0.25%, 3회")`를 돌려줄 수 있다(순서는 `ungrounded` 다음, `not_found` 앞). `RETRY_NOTES["terms"]`는 `{values}`를 포함한 한 문장(기존 `ungrounded` 문구와 같은 어조: 사용자가 말하지 않았고 도구 결과에도 없는 퍼센트 · 기간 · 횟수는 말하지 않는다, 모르면 질문한다).

- [ ] **Step 1: Write the failing tests:**
  - `test_check_reply_flags_a_percent_that_the_search_result_does_not_contain`: 검색 도구 결과(가짜 조항 `연 0.20%`)가 대화에 있고 모델 답변이 `연 0.25%`이면 `check_reply`가 `("terms", "0.25%")`.
  - `test_check_reply_accepts_a_percent_the_search_result_contains`: 같은 상황에서 답변 `연 0.20%` → `(None, "")`.
  - `test_the_graph_retries_once_then_sends_the_fixed_text`: 가짜 모델이 두 번 다 `연 0.25%`를 답하면 최종 답변은 `UNSAFE_TEXT`이고 `model_calls`가 2, `rejected_replies`에 `problem == "terms"`가 2개.
  - `test_the_retry_note_names_the_rejected_values`: 첫 답이 `3회`로 걸리면 두 번째 호출의 메시지에 `3회`가 들어 있다.
  - `test_a_value_the_user_said_is_not_rejected`: 사용자 질문에 `3개월`이 있고 답변이 `3개월이면…`이면 `terms`로 걸리지 않는다.
- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest tests/test_clause_guard.py -q -p no:cacheprovider`
  Expected: FAIL (`terms`가 없어 `check_reply`가 `None`을 돌려줌).
- [ ] **Step 3: Implement.** `check_reply`에서 `ungrounded` 검사 바로 뒤에 `ungrounded_terms(text, _allowed_texts(state, whole_conversation=True))`를 부르고 결과가 있으면 `("terms", ", ".join(extra))`를 돌려준다. `RETRY_NOTES`에 `"terms"`를 추가하고 `check_reply` 설명 문자열의 문제 목록에 `'terms'(근거 없는 퍼센트 · 기간 · 횟수)`를 더한다.
- [ ] **Step 4: Run the new tests, then the whole suite**
  Run: `.venv/bin/python -m pytest tests/test_clause_guard.py -q -p no:cacheprovider && .venv/bin/python -m pytest -q -p no:cacheprovider`
  Expected: 새 시험 통과, 전체 `passed`(기존 시험이 새 가드에 걸려 깨지면 그 시험의 가짜 답변이 근거 없는 값을 말하고 있는 것이므로 시험의 허용 글이나 답변을 고치고, 고친 시험과 이유를 보고한다. 가드를 약하게 하지 않는다).
- [ ] **Step 5: Mutation check.** `check_reply`의 새 호출을 주석 처리했을 때 Task 2의 시험 최소 3개가 실패함을 보이고 되돌린다.
- [ ] **Step 6: Commit** `app/graph.py`, `tests/test_clause_guard.py` — `feat: reject replies with percentages, periods or counts that no source contains`

### Task 3: 채점 지표 `terms_rejected`

**Files:**
- Modify: `eval/evaluate.py` (`grade`와 `summarize`), 필요하면 `eval/run_eval.py`의 요약 표 열
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: 결과 dict의 `rejected_replies`(각 항목은 `{"problem", "detail", "chars", "text"}`).
- Produces: `grade(q, res)`의 결과에 `terms_rejected: int`(= `problem == "terms"`인 `rejected_replies` 수), `summarize(rows)["overall"]["terms_rejected"]`는 문항 합계. 이 지표는 `UNSUPPORTED`에 넣지 않는다(최종 답변은 이미 고정 문구로 바뀌어 있어 사용자에게 나간 근거 없는 답이 아니다).

- [ ] **Step 1: Write the failing tests:** `test_terms_rejected_counts_only_the_terms_problem`(`rejected_replies`가 `terms` 1개 + `ungrounded` 1개면 `terms_rejected == 1`), `test_terms_rejected_is_zero_without_rejected_replies`, `test_terms_rejected_does_not_make_the_answer_unsupported`(고정 문구 답 + `terms` 기록이어도 `unsupported`가 늘지 않음), `test_summarize_adds_terms_rejected_over_all_rows`.
- [ ] **Step 2: Run to verify they fail**
  Run: `.venv/bin/python -m pytest tests/test_evaluate.py -q -p no:cacheprovider -k terms_rejected`
  Expected: FAIL (키 없음).
- [ ] **Step 3: Implement** `grade`에 `out["terms_rejected"]`를 추가하고(`out["rejected"]`와 같은 자리), `summarize`에서 합산한다. `run_eval`의 요약 표에는 기존 `모델호출` 열처럼 한 열로 넣는다(열 이름 `조항대조`).
- [ ] **Step 4: Run the suite and the CI gate**
  Run: `.venv/bin/python -m pytest -q -p no:cacheprovider && .venv/bin/python eval/ci_gate.py`
  Expected: 전체 passed, `평가 게이트: OK`(저장된 `eval_core_3` 재채점 수치는 그대로).
- [ ] **Step 5: Commit** `eval/evaluate.py`, `eval/run_eval.py`, `tests/test_evaluate.py` — `feat: count clause-grounding rejections in the evaluation summary`

### Task 4: 실제 모델로 측정하고 README에 기록

**Files:**
- Modify: `README.md` (한계 절과 평가 절), 필요하면 `docs/DEVLOG.md`
- Create: `eval/runs/eval_core_4.json` (측정 결과)

**Interfaces:**
- Consumes: Task 2의 가드, Task 3의 지표. Ollama(`qwen2.5:7b`)와 색인이 로컬에 있어야 한다.
- Produces: 거짓 거절 수치와 한계 문장.

- [ ] **Step 1: Check the environment.** Ollama와 색인이 있는지 확인한다. 없으면 이 태스크는 README에 "실제 모델로 측정하지 않음"이라고 적는 것으로 끝내고, 그 사실을 보고한다.
- [ ] **Step 2: Run the dev core set with the real model** (약 10분, 사용자 PC에서 실행 · 시간이 걸린다고 알린 뒤 백그라운드로):
  Run: `.venv/bin/python eval/run_eval.py --core --save eval/runs/eval_core_4.json`
  Expected: 46문항 완료, 요약 표에 `조항대조` 열.
- [ ] **Step 3: Read every `terms` rejection** (`--rescore` 또는 JSON의 `rejected_replies`)을 하나씩 보고 "맞는 답을 막음(거짓 거절)"과 "근거 없는 값을 막음"으로 나눈다. 거짓 거절이 있으면 그 값의 종류(파생 값, 일반 설명, 표기 차이)를 적는다. 기대한 행동 점수가 `eval_core_3`(41/46)보다 떨어졌으면 원인 문항을 보고한다.
- [ ] **Step 4: Decide with the evidence.** 거짓 거절이 막은 총 건수의 절반을 넘거나 행동 점수가 떨어졌으면 가드를 켠 채로 두지 않고 사용자에게 보고한다(표시만 하는 모드로 낮출지는 사용자가 정한다). 그렇지 않으면 그대로 둔다.
- [ ] **Step 5: Write README.** 한계 절에 "퍼센트 · 기간 · 횟수만 대조하고 문장의 의미는 보지 않는다", 평가 절에 이번 측정의 `terms` 건수와 거짓 거절 수(Step 3)와 측정일을 적고, 시험 개수를 새 총 개수로 고친다. 측정하지 못한 것은 못 했다고 적는다.
- [ ] **Step 6: Commit** `README.md`, `eval/runs/eval_core_4.json` (약관 본문이 길게 인용돼 있지 않은지 저장 전에 확인) — `docs: record the clause-grounding measurement and its limits`
