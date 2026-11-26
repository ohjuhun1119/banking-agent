"""API 모델로 보내기 전에 개인정보 모양을 가린다 (API 모드에서만 쓴다. 로컬 모델은 데이터가 컴퓨터 안에 있어 그대로 보낸다).

가리는 것: 주민등록번호 · 카드번호 · 전화번호 · 이메일 · 계좌번호 모양. 금액(1000만원, 10,000,000원) · 날짜(2026-01-01) · 금리 · 조항 번호는 그대로 둔다.
shortcut: 정규식으로 모양만 본다. 이름 · 주소 같은 서술형 개인정보는 못 가린다 (그래서 README에 '모델에 들어가는 입력은 나간다'를 적었다). 필요해지면 NER 모델을 붙인다.
"""
from __future__ import annotations

import re

_RULES = [
    (re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"), "[주민번호]"),
    (re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)"), "[카드번호]"),
    (re.compile(r"(?<!\d)01[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)0(?:2|[3-6][1-5]|70)[- .]\d{3,4}[- .]\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[이메일]"),
    (re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{4,8}(?:-\d{1,3})?(?!\d)"), "[계좌번호]"),      # 날짜(2026-01-01)는 마지막 묶음이 2자리라 걸리지 않는다
]


def mask(text: str) -> str:
    for pattern, tag in _RULES:
        text = pattern.sub(tag, text)
    return text


def _mask_content(content):
    if isinstance(content, str):
        return mask(content)
    if isinstance(content, list):
        return [{**b, "text": mask(b["text"])} if isinstance(b, dict) and isinstance(b.get("text"), str) else mask(b) if isinstance(b, str) else b for b in content]
    return content


def _mask_deep(value):
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {k: _mask_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_deep(v) for v in value]
    return value


def mask_messages(messages: list) -> list:
    """메시지 복사본의 글을 가려 돌려준다 (본문과 도구 호출 인자 모두: 코드가 부른 검색의 query에는 사용자 질문 원문이 들어간다). 원본(그래프 상태에 저장된 메시지)은 바꾸지 않는다."""
    out = []
    for m in messages:
        update = {"content": _mask_content(m.content)}
        if getattr(m, "tool_calls", None):
            update["tool_calls"] = [{**c, "args": _mask_deep(c.get("args") or {})} for c in m.tool_calls]
        raw = (getattr(m, "additional_kwargs", None) or {}).get("tool_calls")
        if raw:
            update["additional_kwargs"] = {**m.additional_kwargs, "tool_calls": _mask_deep(raw)}
        out.append(m.model_copy(update=update))
    return out
