"""4개 문서를 청킹해 rag/chunks/chunks.jsonl 로 저장하고 점검 보고서를 출력한다.

실행: 레포 루트에서  python rag/build_chunks.py            (띄어쓰기 복원 포함)
      python rag/build_chunks.py --no-spacing             (띄어쓰기 복원 없이)
먼저 python rag/extract_text.py 로 rag/extracted/*.txt 를 만들어 둔다.
"""
import json
import re
import sys
from pathlib import Path

from fix_spacing import normalize
from chunking import (chunk_articles, load_manual_chunks, parse_header, report, split_pages,
                      strip_repeated_lines)

EXTRACTED = Path("rag/extracted")
OUT = Path("rag/chunks/chunks.jsonl")
SPACING_LOG = Path("rag/chunks/spacing_changes.txt")
MAX_CHARS = 900
LONG_HEADER = 200   # 머리말이 이보다 길면 전문(前文)이 섞인 것이라 검색 대상 청크로 남긴다

COMMON = {"bank": "하나은행", "collected_at": "2026-10-27"}  # collected_at: PDF를 내려받은 날짜로 수정

DOCS = [
    {   # 상품설명서: 손으로 정리한 청크 파일을 읽는다
        "id": "hana-deposit-sheet", "kind": "manual",
        "path": "rag/manual/hana_deposit_product_sheet.md",
        "meta": {"product": "하나의 정기예금", "doc_type": "상품설명서",
                 "doc_title": "『하나의 정기예금』 요약 상품설명서",
                 "review_no": "제2025-설명서-199호", "review_date": "2025-08-22",
                 "as_of": "2025-09-01"},
    },
    {   # 약관 3개: stem은 rag/extracted/ 의 txt 파일명(확장자 제외). 심의번호와 제정/개정일은 머리말에서 자동으로 읽는다
        "id": "hana-deposit-terms", "kind": "terms", "stem": "하나은행 정기예금 약관",
        "meta": {"product": "하나의 정기예금", "doc_type": "약관", "doc_title": "『하나의 정기예금』 특약"},
    },
    {
        "id": "hana-lumpsum-terms", "kind": "terms", "stem": "거치식 예금 약관",
        "meta": {"product": "공통", "doc_type": "약관", "doc_title": "거치식예금 약관"},
    },
    {
        "id": "hana-deposit-basic-terms", "kind": "terms", "stem": "예금거래 기본약관",
        "meta": {"product": "공통", "doc_type": "약관", "doc_title": "예금거래 기본약관"},
    },
]

# 원문에 없는데 추출 과정에서 끼어든 글자를 문서별로 지운다 (정규식, 바꿀 말)
TEXT_FIXES = {
    # 제7조 이자계산 뒤에 붙은 '2'는 쪽 번호가 섞인 것 (원문에 없음)
    "hana-deposit-terms": [(r"(제7조[ \t]*\(?이자계산\)?)[ \t]+2(?=[ \t]+이 예금)", r"\1")],
}
STRAY_NUMBER = re.compile(r"[가-힣\)] (\d{1,2}) (?=[가-힣])")   # 위와 비슷한 오염이 더 있는지 찾는 용도


def apply_fixes(doc_id: str, chunks: list[dict]) -> int:
    n = 0
    for c in chunks:
        for pat, rep in TEXT_FIXES.get(doc_id, []):
            new, k = re.subn(pat, rep, c["text"])
            if k:
                c["text"], n = new, n + k
                art = c["metadata"].get("article", "")
                c["metadata"]["article"] = re.sub(r"(이자계산)\s*2\b", r"\1", art)
    return n


def restore_spacing(chunks: list[dict], log: list[str]) -> int:
    from fix_spacing import STATS, fix_spacing
    STATS["windows"] = STATS["failed"] = 0
    changed = 0
    for c in chunks:
        new = fix_spacing(c["text"])
        if new != c["text"]:
            changed += 1
            log.append(f"[{c['chunk_id']}]\n  전: {c['text']}\n  후: {new}\n")
            c["text"] = new
    if STATS["failed"]:
        print(f"   주의: 복원 구간 {STATS['windows']}개 중 {STATS['failed']}개는 글자가 달라져 건너뜀")
    return changed


def build(use_spacing: bool = True) -> list[dict]:
    all_chunks: list[dict] = []
    spacing_log: list[str] = []
    for doc in DOCS:
        meta = {**COMMON, **doc["meta"], "doc_id": doc["id"]}
        if doc["kind"] == "manual":
            if not Path(doc["path"]).exists():
                raise SystemExit(f"{doc['path']} 가 없습니다. 상품설명서 본문이 들어 있어 git에는 올리지 않는 파일입니다.\n"
                                 "rag/manual/FORMAT.md를 보고 상품설명서를 항목별로 정리해 같은 이름으로 만들어 주세요.")
            chunks = load_manual_chunks(doc["path"], doc["id"], meta)
        else:
            path = EXTRACTED / f"{doc['stem']}.txt"
            if not path.exists():
                have = sorted(p.stem for p in EXTRACTED.glob("*.txt"))
                raise SystemExit(f"{path} 가 없습니다. build_chunks.py의 stem을 아래 중 하나로 고치세요:\n  " + "\n  ".join(have))
            pages, removed = strip_repeated_lines(split_pages(path.read_text(encoding="utf-8")))
            if removed:
                print(f"[{doc['id']}] 반복 줄 {len(removed)}개 제거:")
                for ln in removed:
                    print("   -", ln)
            chunks = chunk_articles(pages, doc["id"], {**meta, "source_file": f"{doc['stem']}.pdf"}, MAX_CHARS)
            if not chunks:
                print(f"[{doc['id']}] '제N조(제목)' 형태를 찾지 못했습니다. extracted txt를 열어 조 제목 모양을 확인하세요.")
            pre = [c for c in chunks if c["metadata"].get("chunk_type") == "약관_머리말"]
            if pre:
                header = re.sub(r"\s+", " ", " ".join(c["text"] for c in pre)).strip()
                version = parse_header(header)
                if len(header) > LONG_HEADER:
                    for c in pre:
                        c["metadata"]["chunk_type"] = "약관_전문"
                    print(f"[{doc['id']}] 머리말이 길어(전문 포함) 검색 대상 청크로 남깁니다. 버전 정보: {version}")
                else:
                    chunks = [c for c in chunks if c["metadata"].get("chunk_type") != "약관_머리말"]
                    print(f"[{doc['id']}] 머리말은 검색 대상에서 제외: {header}\n   -> 버전 정보: {version}")
                for c in chunks:
                    for k, v in version.items():
                        c["metadata"].setdefault(k, v)
            odd = sorted({repr(ch) for c in chunks for ch in c["text"] if ch.isspace() and ch not in " \n"})
            if odd:
                print(f"[{doc['id']}] 특수 공백 문자 발견(일반 공백으로 바꿉니다): {odd}")
            for c in chunks:                      # 특수 공백 · 줄 끝 공백 정리를 먼저 해야 아래 제거 규칙이 어긋나지 않는다
                c["text"] = normalize(c["text"])
            n_fix = apply_fixes(doc["id"], chunks)
            if n_fix:
                print(f"[{doc['id']}] 원문에 없는 글자 {n_fix}곳 제거")
            if use_spacing:
                n_sp = restore_spacing(chunks, spacing_log)
                print(f"[{doc['id']}] 띄어쓰기 복원: 청크 {n_sp}개 변경")
            for c in chunks:
                for m in STRAY_NUMBER.finditer(c["text"]):
                    a, b = max(0, m.start() - 12), min(len(c["text"]), m.end() + 12)
                    print(f"  의심(낱개 숫자): {c['chunk_id']}: ...{c['text'][a:b]}...")
        report(doc["id"], chunks)
        all_chunks += chunks
    ids = [c["chunk_id"] for c in all_chunks]
    assert len(ids) == len(set(ids)), "chunk_id가 중복됩니다"
    if spacing_log:
        SPACING_LOG.parent.mkdir(parents=True, exist_ok=True)
        SPACING_LOG.write_text("\n".join(spacing_log), encoding="utf-8")
        print(f"\n띄어쓰기 변경 내역(전/후): {SPACING_LOG}")
    return all_chunks


if __name__ == "__main__":
    chunks = build(use_spacing="--no-spacing" not in sys.argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\n총 {len(chunks)}개 -> {OUT}")
