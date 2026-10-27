"""PDF 추출 때 어긋난 띄어쓰기를 조심스럽게 고친다.

Kiwi의 띄어쓰기 교정(kiwi.space)을 그대로 쓰면 '중도 해지 금리', '일부 해지'처럼 복합어까지 쪼개서
약관 용어가 망가진다. 그래서 Kiwi의 제안 중 아래 경우만 받아들인다. 글자는 절대 바꾸지 않고 공백만 다룬다.

  넣기  1) 앞 형태소가 조사/어미이고 뒤 형태소가 새 단어의 시작           예) 계산은일단위로 -> 계산은 일단위로
        2) 명사 뒤에 따로 쓰는 말(또는, 및, 이상, 이하, 이내 ...)이 붙은 경우  예) 게시일또는 -> 게시일 또는, 5년이상 -> 5년 이상
  빼기  3) 어미가 단독으로 떨어져 있을 때 (용언 줄기 + 공백 + 어미)        예) 거부하 거나 -> 거부하거나

그 밖에 규칙 기반 정리: 숫자 사이 군더더기 공백(제 1 항, 0 원), 목록 번호(1.입출금), 약관 핵심 용어 안의 공백(거 래처).
추가 안전장치: Kiwi의 다른 분석(상위 5개)이 그 자리를 한 단어 안쪽으로 보면(내점할, 사유해제시, 같아서) 쪼개지 않고,
붙여 쓴 법령명(금융실명거래및비밀보장에관한법률)은 건드리지 않는다.
줄을 100자 안팎 구간으로 나눠 처리하므로, 특이한 글자가 있어도 그 구간만 건너뛴다 (건너뛴 구간 수는 STATS로 센다).
"""
import re
from functools import lru_cache

STATS = {"windows": 0, "failed": 0}

PREV_OK = ("J", "E")                                              # 조사, 어미
NEXT_OK = ("NN", "NP", "NR", "VV", "VA", "VX", "MM", "MAG", "MAJ", "XR", "SL")   # SN(숫자)은 뺀다: 제9조의2, 제1항을 다시 쪼개지 않으려고
SEPARATE_WORDS = {"이상", "이하", "이내", "이후", "이전", "미만", "초과",
                  "또는", "및", "그리고", "또한", "다만", "그러나", "따라서"}
DOMAIN_TERMS = ["거래처", "영업점", "예금주"]                       # 이 안에 공백이 끼면 붙인다 (3글자 이상만)
WINDOW = 100
MIN_GAP = 4.0        # '한 단어로 보는 분석'이 최선 분석과 이 점수 안에 있으면 쪼개지 않는다 (내점할, 사유해제시, 같아서 방지)
TOP_N = 5
LAW_NAME = re.compile(r"^[가-힣]{6,}(법률|법)(에|의|을|를|은|는|이|가|으로|로)?$")   # 붙여 쓴 법령명은 그대로 둔다

_SPACE_CHARS = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_NUM_RULES = [
    (re.compile(r"제\s*(\d+)\s*(조|항|호|목)"), r"제\1\2"),
    (re.compile(r"(조의)\s+(\d+)"), r"\1\2"),
    (re.compile(r"(\d)\s+(개월|년|회|%)"), r"\1\2"),
    (re.compile(r"(\d)\s+(개)(?=[)(\s]|$)"), r"\1\2"),
    (re.compile(r"(\d)\s+(원)(?=[으이은는을를에의과와도만]|[^가-힣])"), r"\1\2"),
    (re.compile(r"(?<![\d.])(\d{1,2})\.(?=[가-힣])"), r"\1. "),
]
_TERM_RULES = [(re.compile(r"[ \t]{0,2}".join(t)), t) for t in DOMAIN_TERMS]


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


def normalize(text: str) -> str:
    """특수 공백을 일반 공백으로, 폭 없는 글자는 지운다. 줄 끝 공백도 지운다."""
    text = _ZERO_WIDTH.sub("", _SPACE_CHARS.sub(" ", text))
    text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", text)          # 양끝맞춤 때문에 생긴 연속 공백
    return "\n".join(line.rstrip() for line in text.split("\n"))


def rule_fixes(text: str) -> str:
    for pat, rep in _NUM_RULES:
        text = pat.sub(rep, text)
    for pat, term in _TERM_RULES:
        text = pat.sub(term, text)
    return text


def _nonspace(s: str):
    return [(i, ch) for i, ch in enumerate(s) if not ch.isspace()]


def _fix_window(text: str) -> str:
    """text 한 구간에서 공백을 넣거나 뺀다. 정렬에 실패하면 None."""
    kiwi = _kiwi()
    spaced = kiwi.space(text)
    a, b = _nonspace(text), _nonspace(spaced)
    if len(a) != len(b) or any(x[1] != y[1] for x, y in zip(a, b)):
        return None                                  # Kiwi가 글자까지 바꿨다: 이 구간은 건드리지 않는다
    results = kiwi.analyze(text, top_n=TOP_N)
    if not results:
        return text
    tokens, best = results[0]
    by_start = {t.start: t for t in tokens}
    by_end = {t.start + t.len: t for t in tokens}

    def compound_possible(j: int) -> bool:
        """j 위치를 단어 안쪽으로 보는 분석이 최선과 MIN_GAP 이내로 경쟁하는가."""
        return any(sc >= best - MIN_GAP and not any(t.start == j for t in toks) for toks, sc in results[1:])

    def inside_law_name(j: int) -> bool:
        lo = text.rfind(" ", 0, j) + 1
        hi = text.find(" ", j)
        return bool(LAW_NAME.match(text[lo:hi if hi != -1 else len(text)]))

    insert_at, remove_ranges = set(), []
    for k in range(1, len(a)):
        orig_gap = a[k][0] - a[k - 1][0] > 1
        new_gap = b[k][0] - b[k - 1][0] > 1
        j = a[k][0]
        if new_gap and not orig_gap:                 # Kiwi: 여기에 공백을 넣자
            prev, nxt = by_end.get(j), by_start.get(j)
            if not (prev and nxt):
                continue
            if (prev.tag.startswith(PREV_OK) and nxt.tag.startswith(NEXT_OK)) \
                    or (nxt.form in SEPARATE_WORDS and prev.tag.startswith(("N", "SN", "XSN"))):
                if not compound_possible(j) and not inside_law_name(j):
                    insert_at.add(j)
        elif orig_gap and not new_gap:               # Kiwi: 여기 공백은 빼자
            prev, nxt = by_end.get(a[k - 1][0] + 1), by_start.get(j)
            if prev and nxt and nxt.tag.startswith("E") and prev.tag.startswith(("V", "XS")):
                remove_ranges.append((a[k - 1][0] + 1, j))
    if not insert_at and not remove_ranges:
        return text
    drop = {i for s, e in remove_ranges for i in range(s, e)}
    out = []
    for idx, ch in enumerate(text):
        if idx in drop:
            continue
        if idx in insert_at:
            out.append(" ")
        out.append(ch)
    return "".join(out)


def _windows(line: str):
    words, cur, out = line.split(" "), [], []
    for w in words:
        if cur and sum(len(x) + 1 for x in cur) + len(w) > WINDOW:
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
    out.append(" ".join(cur))
    return out


def fix_line(line: str) -> str:
    if not line.strip():
        return line
    pieces = []
    for w in _windows(line):
        STATS["windows"] += 1
        fixed = _fix_window(w) if w.strip() else w
        if fixed is None:
            STATS["failed"] += 1
            fixed = w
        pieces.append(fixed)
    return " ".join(pieces)


def fix_spacing(text: str) -> str:
    """줄 단위로 처리해 항(①)·불릿·표 구조를 건드리지 않는다."""
    text = rule_fixes(normalize(text))
    return "\n".join(fix_line(line) for line in text.split("\n"))
