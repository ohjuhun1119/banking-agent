"""rag/raw/*.pdf -> rag/extracted/<파일명>.txt  (페이지 구분 표시 포함)

실행: 레포 루트에서  python rag/extract_text.py
"""
from pathlib import Path

from pypdf import PdfReader

RAW = Path("rag/raw")
OUT = Path("rag/extracted")


def extract(pdf_path: Path) -> tuple[str, int]:
    reader = PdfReader(pdf_path)
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        parts.append(f"<<PAGE {i}>>\n{text}")
    return "\n".join(parts), len(reader.pages)


if __name__ == "__main__":
    pdfs = sorted(RAW.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"{RAW}/ 에 PDF가 없습니다. 내려받은 4개 파일을 넣어주세요.")
    OUT.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        text, n_pages = extract(pdf)
        out = OUT / f"{pdf.stem}.txt"
        out.write_text(text, encoding="utf-8")
        per_page = len(text) / max(n_pages, 1)
        warn = "  <- 글자 수가 너무 적음: 스캔 이미지 PDF일 수 있음" if per_page < 100 else ""
        print(f"{pdf.name}: {n_pages}쪽, {len(text):,}자, 쪽당 {per_page:,.0f}자 -> {out}{warn}")
