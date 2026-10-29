"""사용자 말에서 날짜 표현을 찾고, 각 날짜가 신규일 · 만기일 · 해지일 중 무엇인지 문맥으로 읽는다.

왜 코드로 하는가: 모델이 '다음 달 20일'을 2026-04-20으로 계산했다(맞는 값은 2026-11-20). 7B 모델의 날짜 계산은 믿을 수 없어서
상대 날짜는 오늘 날짜 기준으로 코드가 계산한다. 모델이 넘긴 날짜가 이 값과 같으면 근거가 있는 값으로 본다.

찾는 표현 (kind)
  full      2026-01-01 · 2026.1.1 · 2026/01/01 · 2026년 1월 1일 · 20260101
  monthday  12월 7일 · 1/1  (연도 없음: 올해로 본다)
  relative  어제 · 그제 · 내일 · 모레 · 오늘 · 지금 · 당장 / 올해 · 작년 · 내년 + M월 N일 / 다음 달 · 이번 달 · 지난 달 + N일
지원하지 않는 것: '7월 말', 'N일 후', '다음 주', '3개월 전' 같은 대략적이거나 계산이 필요한 표현 (정확한 날짜를 되묻는다).

역할(role): 날짜 바로 앞의 라벨('신규일은', '만기는', '해지:')이나 날짜 바로 뒤에 붙은 키워드('에 가입', ' 만기인', '에 해지')만 믿는다.
  멀리 있는 키워드로는 추측하지 않는다: 잘못 읽으면 말한 값을 거짓으로 거절하기 때문이다 ('1년 만기로 넣었는데'의 만기는 기간이지 만기일이 아니고,
  'A부터 B까지 넣었다가'의 B는 신규일이 아니다). 모르면 None (역할을 모르는 날짜는 어느 칸에도 넣거나 빼지 않는다).
  start 가입 · 들었 · 넣었 · 예치 · 신규 · 개설 / maturity 만기 / termination 해지 · 해약 · 깨 · 중도 · 인출 · 찾
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, replace
from datetime import date

_FULL_SEP = re.compile(r"(?<!\d)(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?:\s*일)?(?!\d)")        # 뒤따르는 공백 · 줄바꿈은 날짜에 넣지 않는다
_EIGHT = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)(?!\s?원)")
_YEAR_MD = re.compile(r"(올해|금년|작년|지난해|내년|명년)\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_MONTH_REL = re.compile(r"(다음\s?달|이번\s?달|지난\s?달|저번\s?달)\s*(\d{1,2})\s*일")
_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SLASH = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])")
_DAY_WORD = re.compile(r"그저께|그제|어제|모레|내일|오늘|당장|지금(?!까지)")

_YEAR_SHIFT = {"올해": 0, "금년": 0, "작년": -1, "지난해": -1, "내년": 1, "명년": 1}
_MONTH_SHIFT = {"다음": 1, "이번": 0, "지난": -1, "저번": -1}
_DAY_SHIFT = {"그저께": -2, "그제": -2, "어제": -1, "오늘": 0, "당장": 0, "지금": 0, "내일": 1, "모레": 2}
RELATIVE_WORDS = frozenset(_DAY_SHIFT)

_ROLE_WORDS = (("maturity", re.compile("만기")), ("termination", re.compile("해지|해약|깨|중도|인출|찾")),
               ("start", re.compile("가입|들었|넣었|넣은|넣고|예치|신규|개설")))
ROLE_FIELD = {"start": "start_date", "maturity": "maturity_date", "termination": "termination_date"}
FIELD_ROLE = {v: k for k, v in ROLE_FIELD.items()}


@dataclass(frozen=True)
class Mention:
    date: date
    start: int
    end: int
    kind: str                          # full | monthday | relative
    month_day: tuple | None = None     # (월, 일): 연도를 말하지 않은 경우
    role: str | None = None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _blank_out(text: str, m: re.Match) -> str:
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]   # 같은 길이의 공백으로 지워 위치를 유지한다


def resolve_word(word: str, today: date) -> date | None:
    """'오늘' · '어제' 같은 낱말을 오늘 날짜 기준 날짜로. 모르는 낱말이면 None."""
    shift = _DAY_SHIFT.get(word.strip())
    if shift is None:
        return None
    return date.fromordinal(today.toordinal() + shift)


def find_mentions(text: str, today: date | None = None) -> list[Mention]:
    """글 속 날짜 표현을 등장 순서대로 찾는다 (역할은 정하지 않음)."""
    today = today or date.today()
    if not isinstance(text, str):
        return []
    found: list[Mention] = []
    rest = text

    def take(pattern, build):
        nonlocal rest
        for m in list(pattern.finditer(rest)):
            built = build(m)
            if built is not None:
                found.append(replace(built, start=m.start(), end=m.end()))
                rest = _blank_out(rest, m)

    take(_FULL_SEP, lambda m: (lambda d: Mention(d, 0, 0, "full") if d else None)(_safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    take(_EIGHT, lambda m: (lambda d: Mention(d, 0, 0, "full") if d else None)(_safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    take(_YEAR_MD, lambda m: (lambda d: Mention(d, 0, 0, "relative") if d else None)(
        _safe_date(today.year + _YEAR_SHIFT[m.group(1)], int(m.group(2)), int(m.group(3)))))

    def month_rel(m):
        shift = _MONTH_SHIFT[re.sub(r"\s", "", m.group(1))[:2]]
        month0 = today.year * 12 + today.month - 1 + shift
        d = _safe_date(month0 // 12, month0 % 12 + 1, int(m.group(2)))
        return Mention(d, 0, 0, "relative") if d else None
    take(_MONTH_REL, month_rel)
    take(_MD, lambda m: (lambda d: Mention(d, 0, 0, "monthday", (int(m.group(1)), int(m.group(2)))) if d else None)(
        _safe_date(today.year, int(m.group(1)), int(m.group(2)))))
    take(_SLASH, lambda m: (lambda d: Mention(d, 0, 0, "monthday", (int(m.group(1)), int(m.group(2)))) if d else None)(
        _safe_date(today.year, int(m.group(1)), int(m.group(2)))))
    take(_DAY_WORD, lambda m: Mention(resolve_word(m.group(0), today), 0, 0, "relative"))
    return sorted(found, key=lambda x: x.start)


def mask_mentions(text: str, mentions: list[Mention]) -> str:
    """날짜 표현을 같은 길이의 공백으로 지운 글 (날짜의 숫자가 금액 · 금리로 읽히지 않게)."""
    out = text
    for m in mentions:
        out = out[:m.start] + " " * (m.end - m.start) + out[m.end:]
    return out


# 날짜 바로 앞의 라벨('신규일은 2026-01-01', '만기 2027-01-01', '해지: 2026-05-31')
_LABELS = (("start", re.compile(r"(?:신규일|신규|가입일|가입|시작일|개설일|예치일)\s*(?:은|는|이|가|:)?\s*(\()?\s*$")),
           ("maturity", re.compile(r"(?:만기일|만기)\s*(?:은|는|이|가|:)?\s*(\()?\s*$")),
           ("termination", re.compile(r"(?:해지일|해지|해약일|해약)\s*(?:은|는|이|가|:)?\s*(\()?\s*$")))
# 날짜 바로 뒤에 붙은 키워드 ('2026-02-01에 가입하고', '2026-06-01 만기인데', '10월 15일에 해지하면')
_AFTER = (("maturity", re.compile(r"만기")), ("termination", re.compile(r"해지|해약|깨|중도|인출|찾")),
          ("start", re.compile(r"가입|들었|넣었|넣은|예치|신규|개설")))
_PARTICLE = re.compile(r"^\s*(?:(?:에는|에도|에|으로|로|이|가|은|는|을|를|도)(?=\s|$))?\s*")      # 조사는 뒤에 공백이 있을 때만 ('가입'의 '가'를 조사로 읽지 않는다)


def _label_role(head: str, tail: str) -> str | None:
    for role, pat in _LABELS:
        m = pat.search(head)
        if m:
            if m.group(1) and not tail.lstrip().startswith(")"):   # '만기(2026-01-01 ~ 2028-01-01)'의 범위는 한 날짜의 라벨이 아니다
                return None
            return role
    return None


def _adjacent(tail: str) -> tuple[str | None, int]:
    """날짜 바로 뒤에 붙은 키워드와 그 키워드가 끝나는 위치(tail 안). 없으면 (None, 0)."""
    if re.match(r"\s*부터", tail):
        return "start", re.match(r"\s*부터", tail).end()
    lead = _PARTICLE.match(tail).end()                        # ('까지'는 조사 목록에 없어서 뒤의 키워드가 바로 붙은 것으로 읽히지 않는다)
    for role, pat in _AFTER:
        m = pat.match(tail, lead)
        if m:
            return role, m.end()
    return None, 0


def assign_roles(text: str, mentions: list[Mention]) -> list[Mention]:
    """각 날짜의 역할을 날짜 바로 앞의 라벨 또는 바로 뒤에 붙은 키워드로 정한다. 멀리 있는 키워드로는 추측하지 않는다 (모르면 role=None).

    '2026-01-01 신규 2027-01-01 만기'처럼 라벨을 날짜 뒤에 쓰는 사람도 있다: 앞 날짜의 뒤쪽 키워드로 쓰인 낱말('신규')은
    다음 날짜의 앞 라벨로 다시 읽지 않는다.
    """
    out = []
    consumed_end = 0                                          # 앞 날짜의 뒤쪽 키워드가 끝나는 글 속 위치
    for i, m in enumerate(mentions):
        prev_end = mentions[i - 1].end if i else 0
        head = text[max(prev_end, consumed_end, m.start - 10):m.start].split("\n")[-1]
        tail = text[m.end:m.end + 25].split("\n")[0]
        adj, adj_len = _adjacent(tail)
        out.append(replace(m, role=_label_role(head, tail) or adj))
        consumed_end = m.end + adj_len if adj else 0
    return out


def date_mentions(text: str, today: date | None = None) -> list[Mention]:
    """날짜 표현과 역할을 함께 돌려준다."""
    ms = find_mentions(text, today)
    return assign_roles(text, ms) if isinstance(text, str) else []


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
