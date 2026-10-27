"""구조 기반 청킹 유틸.

- 약관: '제N조(제목)' 단위로 자르고, 너무 길면 ①②③ 항 단위로 나눈다.
- 상품설명서: 항목이 적어서 rag/manual/*.md 에 손으로 정리한 청크를 읽는다.
"""
import re
from collections import Counter
from pathlib import Path
from statistics import median

PAGE_RE = re.compile(r"(?m)^<<PAGE (\d+)>>[ \t]*$")
ARTICLE_RE = re.compile(
    r"(?m)^[ \t]*(?P<label>제[ \t]*(?P<no>\d+)[ \t]*조(?:의[ \t]*(?P<sub>\d+))?)"
    r"(?:[ \t]*[(（](?P<ptitle>[^)）\n]{1,40})[)）]|[ \t]+(?P<ntitle>[^\n]{1,40}?)[ \t]*$)"
)
PARA_SPLIT = re.compile(r"(?=[①-⑳])")
# 줄바꿈을 공백으로 합치되, 항(①), 호(1. / 가.), 불릿 앞의 줄바꿈은 남긴다
JOIN_RE = re.compile(r"\n(?![①-⑳]|\d{1,2}\.\s|[가-힣]\.\s|※|[•●■□\-]\s)")
MANUAL_HEAD = re.compile(r"(?m)^### \[(?P<key>[^\]]+)\][ \t]*(?P<attrs>.*)$")
SENTENCE_HINT = re.compile(r"(에 따라|에 따른|에 의하여|에 의해|하며|하고|한다|합니다|입니다|[.,]$)")
PARTICLE_START = re.compile(r"^(및|또는|또한|에|에서|와|과|을|를|으로|로)(\s|$)")
TAIL_RE = re.compile(r"(※[ \t]*이[ \t]*약관은[ \t]*금융소비자|<부칙[ \t]*\d+>)")
APPLY_DATE_RE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*부터\s*적용")
ADDENDUM_RE = re.compile(r"(?m)^[ \t]*부[ \t]*칙[ \t]*$")
PAGE_NUM_RE = re.compile(r"^[-– ]*\d{1,3}[-– ]*$|^\d{1,3}\s*/\s*\d{1,3}$")


def _mk(chunk_id: str, text: str, meta: dict, **extra) -> dict:
    return {"chunk_id": chunk_id, "text": text, "metadata": {**meta, **extra}}


def split_pages(text: str) -> list[tuple[int, str]]:
    marks = list(PAGE_RE.finditer(text))
    if not marks:
        return [(1, text)]
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append((int(m.group(1)), text[m.end():end].strip("\n")))
    return pages


def strip_repeated_lines(pages, min_pages=3, ratio=0.6, max_len=80):
    """여러 쪽에 반복되는 머리글/바닥글과 쪽번호만 있는 줄을 지운다. 지운 줄 목록도 돌려준다."""
    if len(pages) < min_pages:
        return pages, []
    counts = Counter()
    for _, t in pages:
        counts.update({ln.strip() for ln in t.splitlines() if ln.strip()})
    repeated = {ln for ln, c in counts.items() if c >= ratio * len(pages) and len(ln) <= max_len}
    cleaned = []
    for p, t in pages:
        kept = [ln for ln in t.splitlines()
                if ln.strip() not in repeated and not PAGE_NUM_RE.match(ln.strip())]
        cleaned.append((p, "\n".join(kept)))
    return cleaned, sorted(repeated)


def _accept_articles(full: str, add_pos, strict_first: bool = True) -> list[dict]:
    """정규식 후보 중 진짜 조 제목만 고른다.
    - 괄호 없는 제목은 '제3항에 따라 ...'처럼 본문 줄이 오인될 수 있어, 제목이 '제'로 시작하거나 문장처럼 보이면('~에 따라', '~한다', 끝의 '.' 등) 버린다.
    - 번호는 앞 조보다 커야 한다 (제9조 다음 제9조의2는 허용, 괄호 없는 제목은 10개 넘게 건너뛰면 본문 줄로 본다). 부칙을 지나면 번호가 다시 시작해도 된다.
    """
    accepted, prev, last_in_add = [], None, False
    for m in ARTICLE_RE.finditer(full):
        in_add = add_pos is not None and m.start() > add_pos
        if in_add != last_in_add:
            prev, last_in_add = None, in_add
        no = int(m.group("no"))
        sub = int(m.group("sub")) if m.group("sub") else 0
        paren = m.group("ptitle") is not None
        title = (m.group("ptitle") or m.group("ntitle") or "").strip()
        if not paren and (title.startswith("제") or PARTICLE_START.match(title) or SENTENCE_HINT.search(title)):
            continue
        if prev is None:
            ok = paren or no <= 2 or not strict_first
        else:
            pno, psub = prev
            ok = (no == pno and sub > psub) or (no > pno and (paren or no <= pno + 10))
        if ok:
            prev = (no, sub)
            accepted.append({"start": m.start(), "no": no, "sub": sub or None,
                             "label": re.sub(r"\s+", "", m.group("label")), "title": title})
    return accepted


def _split_long(body: str, heading: str, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    pieces = [p.strip() for p in PARA_SPLIT.split(body) if p.strip()]
    if len(pieces) <= 1:
        return [body]  # 항 구분이 없으면 나누지 않는다 (보고서에 길이 경고가 나온다)
    if not pieces[0].startswith(tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")):
        pieces = [f"{pieces[0]}\n{pieces[1]}"] + pieces[2:]   # 조 제목만 따로 남지 않게 첫 항에 붙인다
    parts, cur = [], ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 1 > max_chars:
            parts.append(cur)
            cur = f"{heading} (계속)\n{piece}"
        else:
            cur = f"{cur}\n{piece}" if cur else piece
    if cur:
        parts.append(cur)
    return parts


def chunk_articles(pages, doc_id: str, base_meta: dict, max_chars: int = 900) -> list[dict]:
    full, starts = "", []
    for p, t in pages:
        starts.append((len(full), p))
        full += t + "\n"

    def page_at(pos: int) -> int:
        page = starts[0][1]
        for off, p in starts:
            if off <= pos:
                page = p
            else:
                break
        return page

    add = ADDENDUM_RE.search(full)
    add_pos = add.start() if add else None   # '부칙' 줄 이후의 조는 본문 조와 구분한다
    arts = _accept_articles(full, add_pos) or _accept_articles(full, add_pos, strict_first=False)
    chunks: list[dict] = []
    if not arts:
        return chunks
    pre = full[:arts[0]["start"]].strip()
    if len(pre) >= 30:
        chunks.append(_mk(f"{doc_id}__pre", JOIN_RE.sub(" ", pre), base_meta,
                          chunk_type="약관_머리말", article="머리말", page=page_at(0)))
    seen: Counter = Counter()
    tails: list[str] = []
    last_page = page_at(0)
    for i, a_ in enumerate(arts):
        start = a_["start"]
        end = arts[i + 1]["start"] if i + 1 < len(arts) else len(full)
        in_addendum = add_pos is not None and start > add_pos
        if add_pos is not None and start < add_pos < end:
            end = add_pos                      # 본문 마지막 조가 '부칙' 제목까지 먹지 않게 자른다
        raw = full[start:end].strip()
        body = re.sub(r"[ \t]{2,}", " ", JOIN_RE.sub(" ", raw))
        tm = TAIL_RE.search(body)
        if tm:                                  # 마지막 조 뒤에 붙은 안내 문구와 부칙 목록은 따로 모은다
            tails.append(body[tm.start():].strip())
            body = body[:tm.start()].strip()
        no, sub = a_["no"], a_["sub"]
        heading = f"{a_['label']}({a_['title']})"
        key = ("add_" if in_addendum else "") + f"art{no}" + (f"-{sub}" if sub else "")
        seen[key] += 1
        if seen[key] > 1:
            key += f"_dup{seen[key] - 1}"
        parts = _split_long(body, heading, max_chars)
        for k, part in enumerate(parts, start=1):
            cid = f"{doc_id}__{key}" + (f"__p{k}" if len(parts) > 1 else "")
            extra = {"section": "부칙"} if in_addendum else {}
            chunks.append(_mk(cid, part, base_meta, chunk_type="약관_부칙" if in_addendum else "약관_조",
                              article=heading, art_no=no, art_sub=sub, part=f"{k}/{len(parts)}",
                              page=page_at(start), page_end=page_at(max(start, end - 1)), **extra))
            last_page = page_at(max(start, end - 1))
    if tails:
        tail = re.sub(r"\s+", " ", " ".join(tails)).strip()
        dates = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in APPLY_DATE_RE.findall(tail)]
        if dates or "<부칙" in tail:              # '※ 이 약관은 금융소비자...' 안내 문구만 있는 꼬리는 버린다
            chunks.append(_mk(f"{doc_id}__addenda", tail, base_meta, chunk_type="약관_부칙",
                              article="부칙(적용일 이력)", page=last_page))
        if dates:                               # 가장 늦은 '~부터 적용' 날짜를 이 문서의 적용일로 본다
            for c in chunks:
                c["metadata"].setdefault("effective_date", max(dates))
    return chunks


def load_manual_chunks(path, doc_id: str, base_meta: dict) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    heads = list(MANUAL_HEAD.finditer(text))
    chunks = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = re.sub(r"(?m)^---[ \t]*$", "", text[h.end():end]).strip()
        attrs: dict = {}
        for part in h.group("attrs").split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                v = v.strip()
                attrs[k.strip()] = True if v == "true" else int(v) if v.isdigit() else v
        chunks.append(_mk(f"{doc_id}__{h.group('key')}", body, base_meta, **attrs))
    return chunks

REVIEW_RE = re.compile(r"심[의사]필\s*:?\s*(제?\d{4}-[^\s(]+호)\s*\(\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*\)")
REVISION_RE = re.compile(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*(제정|개정)")


def parse_header(header: str) -> dict:
    """약관 첫머리의 '2021.12.03 제정', '심의필 제2021-약관-528호(2021.11.22)' 같은 줄에서 버전 정보를 뽑는다."""
    meta: dict = {}
    m = REVIEW_RE.search(header)
    if m:
        meta["review_no"] = m.group(1)
        meta["review_date"] = f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    m = REVISION_RE.search(header)
    if m:
        meta["revision_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        meta["revision_kind"] = m.group(4)
    return meta


def embedding_text(chunk: dict) -> str:
    """임베딩에 넣을 문장: 문서명과 조항 제목을 머리에 붙여 검색이 문맥을 알게 한다.

    chunk 메타데이터에 hint(검색용 설명)가 있으면 그것도 붙인다. 사용자가 쓰는 말(깨다, 중간에 해지, 손해)과
    문서의 용어(중도해지)를 이어 주려는 용도이며, 임베딩에만 쓰고 저장하는 본문(근거)에는 넣지 않는다.
    """
    m = chunk["metadata"]
    where = m.get("article") or m.get("section") or ""
    hint = f"검색용 설명: {m['hint']}\n" if m.get("hint") else ""
    return f"[{m.get('doc_title', '')} | {where}]\n{hint}{chunk['text']}"


def check_article_sequence(chunks) -> list[str]:
    """조 번호가 순서대로 이어지는지 확인한다. 추출이 깨졌거나 정규식이 놓친 조를 찾는 용도."""
    warnings, prev = [], None
    for c in chunks:
        m = c["metadata"]
        if "art_no" not in m or m.get("section") == "부칙" or m.get("part", "1/1").split("/")[0] != "1":
            continue
        cur = (m["art_no"], m.get("art_sub") or 0)
        if prev is not None:
            if cur[0] < prev[0]:
                warnings.append(f"번호가 거꾸로 감: {prev[0]}조 -> {cur[0]}조 (추출 순서가 꼬였을 수 있음)")
            elif cur[0] - prev[0] > 1:
                warnings.append(f"건너뜀: {prev[0]}조 -> {cur[0]}조 (사이 조를 놓쳤을 수 있음)")
        prev = cur
    return warnings


def report(doc_id: str, chunks, long_limit: int = 1200) -> None:
    lens = [len(c["text"]) for c in chunks]
    if not lens:
        print(f"[{doc_id}] 청크 0개")
        return
    print(f"[{doc_id}] 청크 {len(chunks)}개, 길이 최소 {min(lens)} / 중앙 {int(median(lens))} / 최대 {max(lens)}자")
    for c in chunks:
        if len(c["text"]) > long_limit:
            print(f"  긴 청크: {c['chunk_id']} ({len(c['text'])}자)")
    for w in check_article_sequence(chunks):
        print("  주의:", w)
