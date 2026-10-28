"""확인용 화면을 띄우는 FastAPI 앱.

실행: 레포 루트에서  uvicorn app.api:app --reload      (브라우저에서 http://127.0.0.1:8000)

지금은 청크 탐색, 검색 결과 확인, 22개 질문 일괄 확인 화면을 제공한다.
6~8일차에 에이전트가 생기면 같은 앱에 /api/chat 을 추가해 채팅 화면으로 확장한다.
"""
import json
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))        # rag/ 스크립트가 'from search import ...' 방식이라 경로를 추가한다

import check_search  # noqa: E402
import vectorstore  # noqa: E402
import bm25 as bm25lib  # noqa: E402
import rerank as rerank_lib  # noqa: E402
from search import DEFAULT_MODE, DEFAULT_RERANK, search_terms  # noqa: E402

from app import chat  # noqa: E402
from app.tools import interest as interest_tool  # noqa: E402
from app.tools import interest_cases  # noqa: E402

CHUNKS = ROOT / "rag" / "chunks" / "chunks.jsonl"
STATIC = Path(__file__).resolve().parent / "static"

def _warm_up() -> None:
    """형태소 분석기(Kiwi)는 처음 부를 때 몇 초 걸린다. 서버가 뜨는 동안 백그라운드에서 미리 불러 둔다."""
    try:
        bm25lib.tokenize("워밍업")
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_warm_up, daemon=True).start()
    yield


app = FastAPI(title="금융 상담 Agent 확인 화면", lifespan=lifespan)
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
            "bm25": bm25lib.load_index(vectorstore.bm25_path()) is not None, "rerank_available": rerank_lib.available(),
            "rerank_model": rerank_lib.RERANK_MODEL, "defaults": {"mode": DEFAULT_MODE, "rerank": DEFAULT_RERANK},
            "chat_model": chat.CHAT_MODEL if chat.CHAT_BACKEND != "fake" else "fake(테스트)",
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
    mode: Literal["dense", "bm25", "hybrid"] | None = None
    rerank: Literal["none", "cross", "llm"] | None = None
    max_distance: float | None = None


@app.post("/api/search")
def api_search(req: SearchRequest):
    kwargs = {"product": req.product or None, "chunk_types": req.chunk_types or None, "k": req.k,
              "mode": req.mode, "rerank": req.rerank}
    if req.max_distance is not None:               # 안 주면 search.py의 기본 거리 한도를 쓴다
        kwargs["max_distance"] = req.max_distance
    try:
        return search_terms(req.query, **kwargs)
    except (SystemExit, rerank_lib.RerankError) as e:   # Ollama 문제(SystemExit)나 리랭커 사용 불가는 서비스 불가로 알린다
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"검색 중 오류: {e}")


@app.get("/api/examples")
def examples():
    return [{"group": g, "question": q} for g, q, _, _ in check_search.QUESTIONS]


@app.get("/api/check")
def api_check(k: int = Query(5, ge=1, le=20), mode: Literal["dense", "bm25", "hybrid"] | None = None,
              rerank: Literal["none", "cross", "llm"] | None = None):
    try:
        rows = check_search.run_checks(k, mode, rerank)
    except rerank_lib.RerankError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SystemExit as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"k": k, "mode": mode or DEFAULT_MODE, "rerank": rerank or DEFAULT_RERANK, "rows": rows, "summary": check_search.summarize(rows, k)}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    product: str | None = None
    k: int = Field(5, ge=1, le=10)
    mode: Literal["dense", "bm25", "hybrid"] | None = None
    rerank: Literal["none", "cross", "llm"] | None = None


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """검색 -> 근거 전송 -> 답변을 토큰 단위로 스트리밍(SSE). 동기 제너레이터라 스레드풀에서 돌아 서버가 막히지 않는다."""
    events = chat.answer_events(req.question, req.product or None, req.k, req.mode, req.rerank)
    return StreamingResponse(chat.sse(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class CalcRequest(BaseModel):
    principal: int | float | str | None = None
    start_date: str | None = None
    maturity_date: str | None = None
    termination_date: str | None = None
    base_rate: float | str | None = None


@app.post("/api/calc")
def api_calc(req: CalcRequest):
    """중도해지 이자 계산 도구를 그대로 호출한다. 검증 실패도 오류가 아니라 status로 돌려준다 (에이전트가 읽는 형식과 같다)."""
    return interest_tool.calc_interest(**req.model_dump())


@app.get("/api/calc/cases")
def api_calc_cases():
    return interest_cases.CASES
