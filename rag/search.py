"""약관 검색. search_terms()가 5일차에 에이전트의 도구가 된다.

검색 방식 mode
  dense   벡터 검색 (의미가 비슷한 청크)
  bm25    키워드 검색 (같은 낱말이 많이 나오는 청크)
  hybrid  둘을 RRF(Reciprocal Rank Fusion)로 합친다. 벡터가 놓친 정확한 용어와 키워드가 놓친 의미를 서로 보완한다
리랭킹 rerank
  none    1차 검색 순서 그대로
  cross   교차 인코더가 상위 후보를 (질문, 문서) 쌍으로 다시 채점  /  llm  로컬 LLM이 채점  (rag/rerank.py)

반환은 항상 dict이고 status로 구분한다.
  ok         결과가 있음 (results에 순위, 거리, 본문, 근거 정보, 점수)
  not_found  조건에 맞는 결과가 없거나, 모두 max_distance보다 멀다
  error      인덱스가 비어 있음 (python rag/ingest.py 필요)
distance는 벡터 코사인 거리(0에 가까울수록 비슷)이며, 어떤 mode든 결과마다 계산해서 거리 한도에 쓴다.
"""
import os
import time

import numpy as np

import bm25 as bm25lib
import rerank as rerank_lib
from vectorstore import bm25_path, embed_texts, get_collection

DISTANCE_LIMIT = 0.6    # 이보다 먼 결과는 버린다. 22문항 측정: 정답이 있는 질문의 상위 결과는 0.28~0.52, 전혀 무관한 질문은 0.69.
                       # 도메인 안의 '문서에 없는 질문'(세후 이자 0.39, 대출 금리 0.41)은 거리로 못 거른다 (LLM 판단 몫). 문항 수가 적어 10~11일차에 다시 확인
SOURCE_KEYS = ["doc_title", "article", "section", "page", "review_no", "review_date",
               "revision_date", "revision_kind", "effective_date", "as_of"]
MODES = ("dense", "bm25", "hybrid")
DEFAULT_MODE = os.environ.get("SEARCH_MODE", "dense")
DEFAULT_RERANK = os.environ.get("SEARCH_RERANK", "none")
CANDIDATES = 20                                          # 1차 검색에서 뽑는 후보 수
RERANK_TOP = int(os.environ.get("RERANK_TOP", "10"))     # 리랭커가 다시 채점하는 후보 수 (llm은 느려서 줄이는 게 좋다)
RERANK_WITH_HINT = os.environ.get("RERANK_WITH_HINT", "1") == "1"   # 리랭커에게도 검색용 설명(hint)을 보여준다. 안 보여주면 1차 검색의 hint 효과를 되돌렸다 (1위 22/30 -> 보여주면 29/30)
RERANK_BLEND = os.environ.get("RERANK_BLEND", "replace")        # replace: 리랭커 점수 순서로 교체 / rrf: 1차 순서와 리랭커 순서를 RRF로 합침
RRF_K = 60                                               # RRF 상수: 클수록 상위 순위의 비중이 줄어든다


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """여러 순위 목록을 합친다. 점수 = 각 목록에서 1 / (k + 순위)의 합. 점수의 단위가 다른 벡터 거리와 BM25 점수를 순위로만 합치는 게 장점이다."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for pos, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + pos)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def _where(product, chunk_types):
    conds = []
    if product:                                   # 상품 전용 문서 + 모든 상품에 공통인 문서
        conds.append({"product": {"$in": [product, "공통"]}})
    if chunk_types:
        conds.append({"chunk_type": {"$in": list(chunk_types)}})
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def _allow(product, chunk_types):
    """BM25 쪽의 메타데이터 필터 (_where와 같은 조건)."""
    def ok(meta: dict) -> bool:
        if product and meta.get("product") not in (product, "공통"):
            return False
        if chunk_types and meta.get("chunk_type") not in chunk_types:
            return False
        return True
    return ok


def _rerank_text(meta: dict, doc: str) -> str:
    where = meta.get("article") or meta.get("section") or ""
    hint = f"검색용 설명: {meta['hint']}\n" if RERANK_WITH_HINT and meta.get("hint") else ""
    return f"[{meta.get('doc_title', '')} | {where}]\n{hint}{doc}"


def search_terms(query: str, product: str | None = None, chunk_types: list[str] | None = None,
                 k: int = 5, max_distance: float | None = DISTANCE_LIMIT,
                 mode: str | None = None, rerank: str | None = None) -> dict:
    mode = mode or DEFAULT_MODE
    rerank = DEFAULT_RERANK if rerank is None else rerank
    rerank = None if rerank in ("", "none") else rerank
    if mode not in MODES:
        raise ValueError(f"알 수 없는 검색 방식: {mode} ({', '.join(MODES)} 중에서 고르세요)")
    t_all = time.perf_counter()
    ms: dict[str, int] = {}

    def lap(name, t0):
        ms[name] = int((time.perf_counter() - t0) * 1000)

    col = get_collection()
    if col.count() == 0:
        return {"status": "error", "message": "인덱스가 비어 있습니다. python rag/ingest.py 를 먼저 실행하세요."}

    t0 = time.perf_counter()
    qvec = embed_texts([query])[0]                # 거리 계산에 모든 방식이 쓰므로 항상 만든다
    lap("embed", t0)
    n = min(max(k, CANDIDATES), col.count())

    dense: list[tuple[str, float]] = []
    if mode in ("dense", "hybrid"):
        t0 = time.perf_counter()
        res = col.query(query_embeddings=[qvec], n_results=n, where=_where(product, chunk_types), include=["distances"])
        dense = list(zip(res["ids"][0], res["distances"][0]))
        lap("dense", t0)

    keyword: list[tuple[str, float]] = []
    if mode in ("bm25", "hybrid"):
        t0 = time.perf_counter()
        index = bm25lib.load_index(bm25_path())
        if index is None:
            return {"status": "error", "message": "키워드(BM25) 인덱스가 없습니다. python rag/ingest.py 를 다시 실행하세요."}
        keyword = index.search(query, n=n, allow=_allow(product, chunk_types))
        lap("bm25", t0)

    dense_rank = {cid: i for i, (cid, _) in enumerate(dense, start=1)}
    bm_rank = {cid: i for i, (cid, _) in enumerate(keyword, start=1)}
    bm_score = dict(keyword)
    rrf_score: dict[str, float] = {}
    if mode == "dense":
        order = [cid for cid, _ in dense]
    elif mode == "bm25":
        order = [cid for cid, _ in keyword]
    else:
        fused = rrf([[c for c, _ in dense], [c for c, _ in keyword]])
        order, rrf_score = [c for c, _ in fused], dict(fused)
    order = order[:max(n, k)]

    # 후보의 본문 · 메타데이터 · 벡터 (벡터 거리는 키워드로만 뽑힌 후보에도 필요하다)
    got = col.get(ids=order, include=["documents", "metadatas", "embeddings"]) if order else {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
    rec = {cid: (doc, meta, emb) for cid, doc, meta, emb in zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"])}
    dist_map = dict(dense)
    qn = np.asarray(qvec, dtype=float)
    for cid in order:
        if cid not in dist_map and cid in rec:
            e = np.asarray(rec[cid][2], dtype=float)
            dist_map[cid] = float(1 - e @ qn / ((np.linalg.norm(e) * np.linalg.norm(qn)) or 1.0))

    cands = [c for c in order if c in rec and (max_distance is None or dist_map[c] <= max_distance)]

    rr_score: dict[str, float] = {}
    if rerank and len(cands) > 1:
        t0 = time.perf_counter()
        top = cands[:RERANK_TOP]
        scores = rerank_lib.score(rerank, query, [_rerank_text(rec[c][1], rec[c][0]) for c in top])
        rr_score = dict(zip(top, scores))
        by_score = sorted(top, key=lambda c: -rr_score[c])    # 파이썬 정렬은 안정적이라 동점이면 1차 순서를 지킨다
        if RERANK_BLEND == "rrf":                             # 리랭커를 그대로 믿지 않고 1차 순서와 함께 합친다
            fused = dict(rrf([top, by_score]))
            first_pos = {c: i for i, c in enumerate(top)}
            top = sorted(top, key=lambda c: (-fused[c], first_pos[c]))
        else:
            top = by_score
        cands = top + cands[RERANK_TOP:]
        lap("rerank", t0)

    results = []
    for rank, cid in enumerate(cands[:k], start=1):
        doc, meta, _ = rec[cid]
        scores_info = {}
        if cid in dense_rank:
            scores_info["dense_rank"] = dense_rank[cid]
        if cid in bm_rank:
            scores_info["bm25_rank"], scores_info["bm25"] = bm_rank[cid], bm_score[cid]
        if cid in rrf_score:
            scores_info["rrf"] = round(rrf_score[cid], 5)
        if cid in rr_score:
            scores_info["rerank"] = round(rr_score[cid], 4)
        results.append({
            "rank": rank, "chunk_id": cid, "distance": round(dist_map[cid], 4), "chunk_type": meta.get("chunk_type"),
            "text": doc, "source": {key: meta[key] for key in SOURCE_KEYS if key in meta}, "scores": scores_info,
        })
    lap("total", t_all)
    return {"status": "ok" if results else "not_found", "query": query, "mode": mode, "rerank": rerank, "ms": ms, "results": results}
