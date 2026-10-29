"""모델 답변 점검 (6일차 보강): 실제 대화에서 나온 오류 두 가지를 코드로 막는다.

1) 한국어가 아닌 글자가 섞임: 실제 대화에서 '…直接拨打以转接人工客服。'처럼 중국어가 답에 섞였다 (Qwen 계열의 흔한 증상).
   -> 한자 · 가나가 있으면 한 번 다시 시키고, 그래도 있으면 고정 문구로 바꾼다.
2) 지어낸 전화번호: 상담원 연결을 요청하자 모델이 '고객센터 번호 1544-1111'을 말했다. 하나은행 공식 고객센터는
   1599-1111 · 1588-1111이고 1544-1111은 어디에도 없다. 번호는 도구 결과나 사용자 말에 있는 것만 말할 수 있다.
3) 지어낸 날짜 · 금액이 답변 본문에 나옴: 가드가 계산에서는 뺐는데도 모델이 답변에 '알려주신 원금은 2000만원, 신규일은 2026-05-28입니다',
   '신규일은 2023년 1월 1일로 잡았습니다'라고 말했다 (검증용 실제 모델 결과). 답변 속 날짜 · 금액은 사용자가 말했거나 도구 결과에 있는 것만 허용한다.
4) 도구 호출 문법이 답변에 새어 나옴: 답변이 '(calc_interest {"termination_date": ...})' 그대로였다.
5) 검색 없이 '약관에서 확인되지 않습니다': 약관에 있는 규칙(일부해지)을 검색도 안 하고 없다고 했다. 이 말은 검색 결과가 not_found일 때만 허용한다.
6) 상담원 연결 요청: 상담원 연결은 8일차(request_handoff)에 만든다. 그 전에는 연결해 주는 것처럼 말하지 않도록
   (모델이 '잠시 기다려 주세요, 상담원이 연결될 때까지…'라고 했다) 모델을 거치지 않고 고정 문구로 답한다.
   8일차에 저장 도구가 생기면 이 고정 문구 대신 그 도구를 부른다.
"""
from __future__ import annotations

import re
from datetime import date

from app.grounding import extract_facts

# 가나(U+3040~30FF) · 한자 확장 A(U+3400~4DBF) · 한자(U+4E00~9FFF). 한글과 『』 같은 기호(U+3000대)는 포함하지 않는다.
_FOREIGN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
# 전화번호 모양: 1544-1111 (4-4), 02-123-4567 · 010-1234-5678 (2~3-3~4-4). 날짜 2026-12-07은 걸리지 않는다.
_PHONE = re.compile(r"(?<![\d-])(?:\d{2,3}-\d{3,4}-\d{4}|\d{4}-\d{4})(?![\d-])")
_HANDOFF = re.compile(r"상담원|상담사|상담\s?직원|(?:사람|직원|담당자)(?:과|와|이랑|랑|하고|한테)?\s?(?:통화|연결|상담)|고객센터(?:에)?\s?(?:연결|전화)|전화\s?연결|콜센터")


def foreign_script(text) -> bool:
    """한자나 가나(중국어 · 일본어)가 섞여 있는가."""
    return bool(_FOREIGN.search(text)) if isinstance(text, str) else False


def phone_numbers(text) -> list[str]:
    return _PHONE.findall(text) if isinstance(text, str) else []


def unsupported_phone_numbers(answer, *allowed_texts) -> list[str]:
    """답변 속 전화번호 중 허용된 글(도구 결과 · 사용자 말)에 없는 것."""
    allowed = "\n".join(t for t in allowed_texts if isinstance(t, str))
    return [n for n in phone_numbers(answer) if n not in allowed]


def handoff_request(text) -> bool:
    """상담원 · 사람과의 연결을 요청하는 말인가."""
    return bool(_HANDOFF.search(text)) if isinstance(text, str) else False


_TOOL_SYNTAX = re.compile(r"(?<![A-Za-z_])(?:calc_interest|search_terms)(?![A-Za-z_])|<tool_call>|\{\s*[\"'][A-Za-z_]+[\"']\s*:")
_NOT_FOUND = re.compile(r"약관(?:에서|에는|에)?\s*(?:는\s*)?확인(?:되지|할 수)\s*(?:않|없)")
_AFTER_TAX = re.compile(r"세후[^.?!\n]*(?:드리겠|드릴게|드릴께|드립니다|알려\s?드리|계산해\s?드리|제공해\s?드리|알려\s?줄|계산해\s?줄)")
_NEGATION = re.compile(r"못|수\s?없|않|불가")
MIN_AMOUNT = 10_000                                    # 이 금액 미만의 숫자(340일, 2.934%의 '934' 등)는 점검하지 않는다


def tool_syntax(text) -> bool:
    """도구 이름이나 호출 JSON이 답변에 글로 들어 있는가."""
    return bool(_TOOL_SYNTAX.search(text)) if isinstance(text, str) else False


def not_found_claim(text) -> bool:
    """'약관에서 확인되지 않습니다' 같은, 규칙이 없다는 말을 하는가."""
    return bool(_NOT_FOUND.search(text)) if isinstance(text, str) else False


def ungrounded_values(answer, allowed_texts, today: date | None = None) -> list[str]:
    """답변에 나온 날짜 · 금액 중 허용된 글(사용자 말 · 도구 결과 · 확인된 값)에 없는 것. 오늘 날짜는 허용한다."""
    if not isinstance(answer, str):
        return []
    today = today or date.today()
    a = extract_facts(answer, today, require_digit=True, normalize=False)     # 답변의 '만원' · '천만 원' 같은 한글 숫자는 읽지 않는다
    ok = extract_facts("\n".join(t for t in allowed_texts if isinstance(t, str)), today)
    years = {today.year} | {d.year for d in ok.dates}
    ok_md = set(ok.month_days) | {(d.month, d.day) for d in ok.dates} | {(today.month, today.day)}     # 연도 없이 쓴 날짜(12월 7일)의 허용 범위
    out = []
    for d in sorted(a.dates):                              # 연도까지 쓴 날짜는 같은 날짜여야 한다 (월 · 일만 같다고 통과시키지 않는다)
        if d not in ok.dates and d != today and not ((d.month, d.day) in ok.month_days and d.year in years):
            out.append(d.isoformat())
    for md in sorted(a.month_days):
        if md not in ok_md:
            out.append(f"{md[0]}월 {md[1]}일")
    for amount in sorted(a.amounts):
        if amount >= MIN_AMOUNT and amount not in ok.amounts:
            out.append(f"{amount:,}원")
    return out


def promises_after_tax(text) -> bool:
    """세후 이자(세금을 뺀 금액)를 알려 주겠다고 약속하는 문장이 있는가. 이 도구는 세전 이자만 계산한다 (검증용 실제 모델 결과 H20)."""
    if not isinstance(text, str):
        return False
    return any(_AFTER_TAX.search(sent) and not _NEGATION.search(sent) for sent in re.split(r"[.?!\n]", text))


_AFFIRM = re.compile(r"^\s*(?:네|넵|예|응|어|그래|좋아|맞아|저장|부탁|해\s?줘|해\s?주세요|그렇게|그럼)")
_NEGATIVE = re.compile(r"아니|아뇨|괜찮|됐|취소|싫|안\s?해|하지\s?마")
_HANDOFF_DONE = re.compile(r"접수번호|(?:상담|요청|문의)[^.?!\n]{0,12}(?:접수|저장)\s?(?:했|됐|되었|해\s?드렸|완료)|상담원[^.?!\n]{0,14}(?:연결해\s?드(?:리|렸)|연결됩니다|연락\s?드리)|연결해\s?드릴게")
_NOT_DONE = re.compile(r"못|수\s?없|않|아직|전에|할까요|드릴까요|원하시면")


def is_affirmative(text) -> bool:
    """'네' · '네, 저장해 주세요' 같은 짧은 긍정 답인가 (질문이 섞였거나 부정이 있으면 아니다)."""
    return (isinstance(text, str) and len(text.strip()) <= 30 and "?" not in text and "?" not in text
            and bool(_AFFIRM.search(text)) and not _NEGATIVE.search(text))


def claims_handoff_done(text) -> bool:
    """상담 요청이 접수 · 저장됐거나 상담원이 연결된다고 말하는가. 접수번호는 코드가 저장한 뒤에만 만든다: 모델이 쓴 답변의 접수 주장은 근거가 없다."""
    if not isinstance(text, str):
        return False
    return any(_HANDOFF_DONE.search(s) and not _NOT_DONE.search(s) for s in re.split(r"[.?!\n]", text))
