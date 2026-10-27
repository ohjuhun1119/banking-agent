"""확인용 화면을 띄우는 FastAPI 앱.

실행: 레포 루트에서  uvicorn app.api:app --reload      (브라우저에서 http://127.0.0.1:8000)

지금은 청크 탐색, 검색 결과 확인, 22개 질문 일괄 확인 화면을 제공한다.
6~8일차에 에이전트가 생기면 같은 앱에 /api/chat 을 추가해 채팅 화면으로 확장한다.
"""
import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))        # rag/ 스크립트가 'from search import ...' 방식이라 경로를 추가한다

import check_search  # noqa: E402
import vectorstore  # noqa: E402
from search import search_terms  # noqa: E402

CHUNKS = ROOT / "rag" / "chunks" / "chunks.jsonl"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="금융 상담 Agent 확인 화면")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def load_chunks() -> list[dict]:
    if not CHUNKS.exists():
        return []
    return [json.loads(line) for line in CHUNKS.open(encoding="utf-8")]


def _label(meta: dict) -> str:
    return meta.get("article") or meta.get("section") or ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    index_count, hints = None, None
    if vectorstore.CHROMA_DIR.exists():
        try:
            col = vectorstore.get_collection()
            index_count = col.count()
            hints = sum(1 for m in col.get(include=["metadatas"])["metadatas"] if m.get("hint"))   # 인덱스에 실제로 들어간 것
        except Exception:
            index_count = None
    return {"chunks": len(load_chunks()), "index": index_count, "hints": hints,
            "embed_backend": vectorstore.EMBED_BACKEND, "embed_model": vectorstore.EMBED_MODEL}


@app.get("/api/meta")
def meta():
    rows = load_chunks()
    docs: dict[str, str] = {}
    for r in rows:
        docs[r["metadata"].get("doc_id", "")] = r["metadata"].get("doc_title", "")
    return {
        "docs": [{"id": k, "title": v} for k, v in docs.items()],
        "chunk_types": sorted({r["metadata"]["chunk_type"] for r in rows if r["metadata"].get("chunk_type")}),
        "products": sorted({r["metadata"]["product"] for r in rows if r["metadata"].get("product")}),
    }


@app.get("/api/chunks")
def list_chunks(q: str = "", doc: str = "", type: str = "", limit: int = Query(300, ge=1, le=1000)):
    items = []
    for r in load_chunks():
        m = r["metadata"]
        if doc and m.get("doc_id") != doc:
            continue
        if type and m.get("chunk_type") != type:
            continue
        label = _label(m)
        if q and q not in r["text"] and q not in label and q not in r["chunk_id"]:
            continue
        items.append({"chunk_id": r["chunk_id"], "doc_id": m.get("doc_id"), "doc_title": m.get("doc_title"),
                      "chunk_type": m.get("chunk_type"), "label": label, "length": len(r["text"])})
    return {"total": len(items), "items": items[:limit]}


@app.get("/api/chunks/{chunk_id}")
def get_chunk(chunk_id: str):
    for r in load_chunks():
        if r["chunk_id"] == chunk_id:
            return r
    raise HTTPException(status_code=404, detail=f"청크를 찾을 수 없습니다: {chunk_id}")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    product: str | None = None
    chunk_types: list[str] | None = None
    k: int = Field(5, ge=1, le=20)
    max_distance: float | None = None


@app.post("/api/search")
def api_search(req: SearchRequest):
    kwargs = {"product": req.product or None, "chunk_types": req.chunk_types or None, "k": req.k}
    if req.max_distance is not None:               # 안 주면 search.py의 기본 거리 한도를 쓴다
        kwargs["max_distance"] = req.max_distance
    try:
        return search_terms(req.query, **kwargs)
    except SystemExit as e:                        # vectorstore가 Ollama 문제를 SystemExit로 알린다
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"검색 중 오류: {e}")


@app.get("/api/examples")
def examples():
    return [{"group": g, "question": q} for g, q, _, _ in check_search.QUESTIONS]


@app.get("/api/check")
def api_check(k: int = Query(5, ge=1, le=20)):
    try:
        rows = check_search.run_checks(k)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SystemExit as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"k": k, "rows": rows, "summary": check_search.summarize(rows, k)}
