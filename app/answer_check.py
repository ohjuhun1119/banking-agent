"""모델 답변 점검 (6일차 보강): 실제 대화에서 나온 오류 두 가지를 코드로 막는다.

1) 한국어가 아닌 글자가 섞임: 실제 대화에서 '…直接拨打以转接人工客服。'처럼 중국어가 답에 섞였다 (Qwen 계열의 흔한 증상).
   -> 한자 · 가나가 있으면 한 번 다시 시키고, 그래도 있으면 고정 문구로 바꾼다.
2) 지어낸 전화번호: 상담원 연결을 요청하자 모델이 '고객센터 번호 1544-1111'을 말했다. 하나은행 공식 고객센터는
   1599-1111 · 1588-1111이고 1544-1111은 어디에도 없다. 번호는 도구 결과나 사용자 말에 있는 것만 말할 수 있다.
3) 상담원 연결 요청: 상담원 연결은 8일차(request_handoff)에 만든다. 그 전에는 연결해 주는 것처럼 말하지 않도록
   (모델이 '잠시 기다려 주세요, 상담원이 연결될 때까지…'라고 했다) 모델을 거치지 않고 고정 문구로 답한다.
   8일차에 저장 도구가 생기면 이 고정 문구 대신 그 도구를 부른다.
"""
from __future__ import annotations

import re

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
