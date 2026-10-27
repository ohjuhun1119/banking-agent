"""약관 검색. search_terms()가 5일차에 에이전트의 도구가 된다.

반환은 항상 dict이고 status로 구분한다.
  ok         결과가 있음 (results에 순위, 거리, 본문, 근거 정보)
  not_found  조건에 맞는 결과가 없거나, 모두 max_distance보다 멀다
  error      인덱스가 비어 있음 (python rag/ingest.py 필요)
거리는 코사인 거리(0에 가까울수록 비슷)이다.
"""
from vectorstore import embed_texts, get_collection

DISTANCE_LIMIT = 0.6    # 이보다 먼 결과는 버린다. 22문항 측정: 정답이 있는 질문의 상위 결과는 0.28~0.52, 전혀 무관한 질문은 0.69.
                       # 도메인 안의 '문서에 없는 질문'(세후 이자 0.39, 대출 금리 0.41)은 거리로 못 거른다 (LLM 판단 몫). 문항 수가 적어 10~11일차에 다시 확인
SOURCE_KEYS = ["doc_title", "article", "section", "page", "review_no", "review_date",
               "revision_date", "revision_kind", "effective_date", "as_of"]


def _where(product, chunk_types):
    conds = []
    if product:                                   # 상품 전용 문서 + 모든 상품에 공통인 문서
        conds.append({"product": {"$in": [product, "공통"]}})
    if chunk_types:
        conds.append({"chunk_type": {"$in": list(chunk_types)}})
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def search_terms(query: str, product: str | None = None, chunk_types: list[str] | None = None,
                 k: int = 5, max_distance: float | None = DISTANCE_LIMIT) -> dict:
    col = get_collection()
    if col.count() == 0:
        return {"status": "error", "message": "인덱스가 비어 있습니다. python rag/ingest.py 를 먼저 실행하세요."}
    res = col.query(query_embeddings=[embed_texts([query])[0]], n_results=k,
                    where=_where(product, chunk_types), include=["documents", "metadatas", "distances"])
    results = []
    for rank, (cid, doc, meta, dist) in enumerate(
            zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]), start=1):
        if max_distance is not None and dist > max_distance:
            continue
        results.append({
            "rank": rank, "chunk_id": cid, "distance": round(dist, 4), "chunk_type": meta.get("chunk_type"),
            "text": doc, "source": {key: meta[key] for key in SOURCE_KEYS if key in meta},
        })
    return {"status": "ok" if results else "not_found", "query": query, "results": results}
