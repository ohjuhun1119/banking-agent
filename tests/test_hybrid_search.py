"""하이브리드 검색(BM25 + 벡터 + RRF)과 리랭킹 시험.

임베딩은 가짜(글자 해시)를, 저장소는 임시 폴더를 쓴다. 그래서 Ollama도 모델 내려받기도 필요 없다.
검증하는 것은 '배선'(합치는 방식, 필터, 점수 정보, 오류 전달)이며 실제 검색 품질은 check_search.py로 잰다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rag"))
pytest.importorskip("kiwipiepy")
pytest.importorskip("chromadb")

import bm25 as bm25lib  # noqa: E402
import ingest  # noqa: E402
import rerank as rerank_lib  # noqa: E402
import search  # noqa: E402
import vectorstore  # noqa: E402

PRODUCT = "하나의 정기예금"
CORPUS = [
    ("et", "중도해지금리 가입 후 기간별 연 0.10% 0.15% 0.20% 경과일수 계약일수 중도해지 금리 표", PRODUCT, "중도해지"),
    ("ic", "이자계산방법 가입금액에 신규일부터 해지일 전일까지 기간에 약정금리로 계산하여 지급 일단위", PRODUCT, "이자계산_일반"),
    ("am", "만기 후 금리 만기일이 지난 뒤 1개월 이내 기본금리 절반", PRODUCT, "만기후금리"),
    ("pw", "일부해지 만기일 이전 2회까지 가능 일부해지 금액에 중도해지금리를 적용합니다", PRODUCT, "일부해지"),
    ("tax", "비과세종합저축 가능 세법 개정 시 세금이 부과될 수 있으며 계약기간 이후의 이자는 과세됩니다", PRODUCT, "세제혜택"),
    ("b9", "실제 받는 이자는 소득세법에 따라 원천징수한 세액을 뺀 금액이다", "공통", "약관_조"),
    ("dm", "휴면예금 최종거래일로부터 5년이 지나면 서민금융진흥원에 출연된다", "공통", "휴면예금"),
]


def rows():
    return [{"chunk_id": cid, "text": text,
             "metadata": {"doc_title": "시험 문서", "section": f"시험 > {ctype}", "product": product, "chunk_type": ctype}}
            for cid, text, product, ctype in CORPUS]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(vectorstore, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(vectorstore, "EMBED_BACKEND", "fake")
    bm25lib._cache.clear()
    ingest.ingest_rows(rows(), verbose=False)
    return tmp_path


def ids(res):
    return [r["chunk_id"] for r in res["results"]]


# ---------- RRF
def test_rrf_merges_rankings():
    fused = search.rrf([["a", "b", "c"], ["c", "a", "d"]])
    assert [c for c, _ in fused] == ["a", "c", "b", "d"]


def test_rrf_is_deterministic_on_ties():
    assert search.rrf([["x"], ["y"]]) == search.rrf([["x"], ["y"]])
    assert [c for c, _ in search.rrf([["x"], ["y"]])] == ["x", "y"]


# ---------- BM25
def build_bm25():
    docs = [bm25lib.tokenize(text) for _, text, _, _ in CORPUS]
    return bm25lib.BM25([c[0] for c in CORPUS], docs, [{"product": c[2], "chunk_type": c[3]} for c in CORPUS])


def test_bm25_prefers_exact_keyword():
    assert build_bm25().search("원천징수 세액")[0][0] == "b9"


def test_bm25_matches_morphological_variants():
    top = [cid for cid, _ in build_bm25().search("중도해지하면 어떻게 돼요")[:2]]
    assert "et" in top                       # '중도해지하면'이 '중도해지금리'와 같은 낱말(중도, 해지)로 맞물린다


def test_bm25_no_overlap_returns_nothing():
    assert build_bm25().search("오늘 날씨 어때요") == []


def test_bm25_metadata_filter():
    hits = build_bm25().search("중도해지", allow=lambda m: m["chunk_type"] == "일부해지")
    assert [cid for cid, _ in hits] == ["pw"]


# ---------- 검색 (벡터 + 키워드 + 합치기)
def test_dense_mode_has_scores(store):
    res = search.search_terms("중도해지 금리", mode="dense", max_distance=None, k=3)
    assert res["status"] == "ok" and res["mode"] == "dense" and len(res["results"]) == 3
    assert all("dense_rank" in r["scores"] and "bm25_rank" not in r["scores"] for r in res["results"])


def test_hybrid_combines_both_signals(store):
    res = search.search_terms("원천징수 세액", mode="hybrid", max_distance=None, k=3)
    assert res["results"][0]["chunk_id"] == "b9"
    top = res["results"][0]["scores"]
    assert "bm25_rank" in top and "rrf" in top
    assert res["ms"]["dense"] >= 0 and res["ms"]["bm25"] >= 0


def test_keyword_only_candidates_still_get_a_distance(store):
    res = search.search_terms("원천징수", mode="bm25", max_distance=None, k=3)
    assert ids(res) == ["b9"]
    assert isinstance(res["results"][0]["distance"], float) and 0 <= res["results"][0]["distance"] <= 2


def test_filters_apply_to_both_paths(store):
    res = search.search_terms("중도해지", mode="hybrid", chunk_types=["중도해지"], max_distance=None, k=10)
    assert ids(res) == ["et"]                                       # 벡터와 키워드 양쪽 모두 청크 종류 필터를 지킨다
    res = search.search_terms("이자", mode="hybrid", product="다른 상품", max_distance=None, k=10)
    assert set(ids(res)) <= {"b9", "dm"}                            # 다른 상품이면 '공통' 문서만 남는다


def test_product_filter_includes_common_documents(store):
    res = search.search_terms("원천징수", mode="bm25", product=PRODUCT, max_distance=None)
    assert ids(res) == ["b9"]                                       # '공통' 문서는 상품 필터에서도 나온다


def test_distance_limit_applies_in_every_mode(store):
    for mode in search.MODES:
        res = search.search_terms("중도해지 금리", mode=mode, max_distance=0.0)
        assert res["status"] == "not_found"


def test_invalid_mode_raises(store):
    with pytest.raises(ValueError):
        search.search_terms("중도해지", mode="semantic")


def test_missing_bm25_index_is_reported(store):
    vectorstore.bm25_path().unlink()
    bm25lib._cache.clear()
    res = search.search_terms("중도해지", mode="hybrid")
    assert res["status"] == "error" and "BM25" in res["message"]


# ---------- 리랭킹
def test_rerank_orders_by_rerank_score(store):
    res = search.search_terms("원천징수 세액", mode="dense", rerank="fake", max_distance=None, k=5)
    scores = [r["scores"]["rerank"] for r in res["results"]]
    assert scores == sorted(scores, reverse=True) and "rerank" in res["ms"]


def test_rerank_only_touches_top_candidates(store, monkeypatch):
    plain = search.search_terms("원천징수 세액", mode="dense", max_distance=None, k=7)
    monkeypatch.setattr(search, "RERANK_TOP", 3)
    reranked = search.search_terms("원천징수 세액", mode="dense", rerank="fake", max_distance=None, k=7)
    assert sorted(ids(plain)[:3]) == sorted(ids(reranked)[:3])      # 앞 3개 안에서만 순서가 바뀌고
    assert ids(plain)[3:] == ids(reranked)[3:]                       # 나머지는 그대로다


def test_rerank_none_is_a_no_op(store):
    a = search.search_terms("중도해지", mode="dense", max_distance=None, k=4)
    b = search.search_terms("중도해지", mode="dense", rerank="none", max_distance=None, k=4)
    assert ids(a) == ids(b) and "rerank" not in b["ms"]


def test_rerank_error_is_propagated(store, monkeypatch):
    def boom(*args, **kwargs):
        raise rerank_lib.RerankError("모델을 불러오지 못했습니다")
    monkeypatch.setattr(rerank_lib, "score", boom)
    with pytest.raises(rerank_lib.RerankError):
        search.search_terms("중도해지", rerank="cross", max_distance=None)


def test_unknown_rerank_backend_raises():
    with pytest.raises(ValueError):
        rerank_lib.score("magic", "질문", ["문서"])


def test_rerank_text_shows_hint_only_when_enabled(monkeypatch):
    meta = {"doc_title": "문서", "section": "섹션", "hint": "깨기 손해 여부"}
    monkeypatch.setattr(search, "RERANK_WITH_HINT", False)
    assert "깨기" not in search._rerank_text(meta, "본문")
    monkeypatch.setattr(search, "RERANK_WITH_HINT", True)
    assert "깨기 손해 여부" in search._rerank_text(meta, "본문")
    assert "깨기" not in search._rerank_text({"doc_title": "문서", "section": "섹션"}, "본문")      # hint가 없는 청크는 그대로


def test_rerank_blend_merges_first_stage_and_rerank_orders(store, monkeypatch):
    first = ids(search.search_terms("원천징수 세액", mode="dense", max_distance=None, k=4))
    monkeypatch.setattr(rerank_lib, "score", lambda backend, query, docs: list(range(len(docs))))   # 일부러 1차 순서를 뒤집는 점수
    monkeypatch.setattr(search, "RERANK_TOP", 4)
    monkeypatch.setattr(search, "RERANK_BLEND", "replace")
    replaced = ids(search.search_terms("원천징수 세액", mode="dense", rerank="fake", max_distance=None, k=4))
    assert replaced == list(reversed(first))                         # 리랭커를 그대로 따르면 완전히 뒤집힌다
    monkeypatch.setattr(search, "RERANK_BLEND", "rrf")
    blended = ids(search.search_terms("원천징수 세액", mode="dense", rerank="fake", max_distance=None, k=4))
    assert blended == [first[0], first[3], first[1], first[2]]       # 합치면 서로 반대 순위인 쌍이 동점이 되고, 동점은 1차 순서를 따른다


def test_summarize_reports_latency():
    import check_search
    rows = [{"group": "g", "has_answer": True, "rank": 1, "trapped": False, "nearest": 0.3, "rejected": False,
             "ms": {"total": t, "rerank": r}} for t, r in [(100, 40), (300, 60), (200, 50)]]
    summary = check_search.summarize(rows, 5)
    assert summary["latency"] == {"median_total": 200, "max_total": 300, "median_rerank": 50}
    assert check_search.summarize([{**r, "ms": {}} for r in rows], 5)["latency"] is None        # 시간 기록이 없으면 None
