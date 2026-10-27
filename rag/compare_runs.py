"""check_search.py --save 로 저장한 두 실행 기록을 비교한다.

실행: 레포 루트에서  python rag/compare_runs.py eval/runs/dense.json eval/runs/hybrid.json

보는 것: 두 기록의 조건이 같은지, 정답이 있는 문항의 요약(1위 · 3위 안 · k위 안 · MRR · 함정), 정답 순위가 좋아지거나 나빠진 질문.
조건이 같은데 결과가 다르면 실행마다 흔들린다는 뜻이고, 조건이 다르면 그 차이가 원인 후보다.
"""
import json
import sys

CONDITION_KEYS = (("mode", "검색 방식"), ("rerank", "리랭킹"), ("rerank_model", "리랭커 모델"), ("rerank_top", "리랭크 후보 수"),
                  ("rerank_blend", "리랭크 합치기"), ("rerank_with_hint", "리랭커에 hint 제공"), ("chunks_sha", "chunks.jsonl 해시"),
                  ("hints", "인덱스의 hint 개수"), ("index", "인덱스 청크 수"), ("embed_model", "임베딩 모델"), ("k", "상위 개수"), ("limit", "거리 한도"))
SAME_KEYS = ("mode", "rerank", "rerank_model", "rerank_top", "rerank_blend", "rerank_with_hint", "chunks_sha", "hints", "index", "embed_model")


def load(path: str) -> dict:
    return json.load(open(path, encoding="utf-8"))


def answer_rank(row: dict):
    ids = [r["chunk_id"] for r in row["results"]]
    return next((i + 1 for i, cid in enumerate(ids) if cid in row["answer_ids"]), None)


def metrics(rows: list[dict]) -> dict:
    ranks = [answer_rank(r) for r in rows if r["has_answer"]]
    n = len(ranks)
    return {"n": n, "top1": sum(1 for x in ranks if x == 1), "top3": sum(1 for x in ranks if x and x <= 3),
            "topk": sum(1 for x in ranks if x), "mrr": sum(1 / x for x in ranks if x) / n if n else 0.0,
            "traps": sum(1 for r in rows if r["trapped"])}


def latency(rows: list[dict]):
    """(검색 시간 중앙값, 리랭크 시간 중앙값). 기록에 시간이 없으면 None."""
    import statistics
    tot = [r["ms"]["total"] for r in rows if r.get("ms") and r["ms"].get("total") is not None]
    rer = [r["ms"]["rerank"] for r in rows if r.get("ms") and r["ms"].get("rerank") is not None]
    return (int(statistics.median(tot)), int(statistics.median(rer)) if rer else None) if tot else None


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("사용법: python rag/compare_runs.py <기록A.json> <기록B.json>")
    a, b = load(sys.argv[1]), load(sys.argv[2])
    ma, mb, k = a["meta"], b["meta"], a["meta"].get("k", 5)
    print("=== 조건 비교 (A | B)")
    for key, label in CONDITION_KEYS:
        if ma.get(key) is None and mb.get(key) is None:
            continue
        print(f"  {label}: {ma.get(key)} | {mb.get(key)}  {'같음' if ma.get(key) == mb.get(key) else '다름 <-'}")
    same_cond = all(ma.get(x) == mb.get(x) for x in SAME_KEYS)

    xa, xb = metrics(a["rows"]), metrics(b["rows"])
    print(f"\n=== 정답이 있는 {xa['n']}문항 요약 (A | B)")
    print(f"  1위        {xa['top1']}/{xa['n']} | {xb['top1']}/{xb['n']}")
    print(f"  3위 안     {xa['top3']}/{xa['n']} | {xb['top3']}/{xb['n']}")
    print(f"  {k}위 안     {xa['topk']}/{xa['n']} | {xb['topk']}/{xb['n']}      <- 에이전트가 읽는 범위 (이 값이 가장 중요)")
    print(f"  MRR        {xa['mrr']:.3f} | {xb['mrr']:.3f}")
    print(f"  함정       {xa['traps']} | {xb['traps']}")
    la, lb = latency(a["rows"]), latency(b["rows"])
    if la and lb:
        ms = lambda v: f"{v}ms" if v is not None else "-"       # noqa: E731
        print(f"  검색 시간(중앙값)  {la[0]}ms | {lb[0]}ms" + (f"   (리랭크 {ms(la[1])} | {ms(lb[1])})" if la[1] is not None or lb[1] is not None else ""))

    rows_b = {r["question"]: r for r in b["rows"]}
    fmt = lambda r: f"{r}위" if r else f"{k}위 밖"          # noqa: E731
    better, worse, order_only, n_dist, max_dist, max_q = [], [], 0, 0, 0.0, ""
    for ra in a["rows"]:
        rb = rows_b.get(ra["question"])
        if not rb:
            continue
        da = {r["chunk_id"]: r["distance"] for r in ra["results"]}
        db = {r["chunk_id"]: r["distance"] for r in rb["results"]}
        for cid in da.keys() & db.keys():
            diff = abs(da[cid] - db[cid])
            n_dist += diff > 0
            if diff > max_dist:
                max_dist, max_q = diff, ra["question"]
        if ra["has_answer"]:
            x, y = answer_rank(ra), answer_rank(rb)
            key = lambda r: r if r else 10 ** 6             # noqa: E731  (k위 밖은 가장 나쁜 순위)
            if key(y) < key(x):
                better.append((ra["question"], x, y))
                continue
            if key(y) > key(x):
                worse.append((ra["question"], x, y))
                continue
        if [r["chunk_id"] for r in ra["results"]] != [r["chunk_id"] for r in rb["results"]]:
            order_only += 1
    print(f"\n=== 정답 순위가 좋아진 질문 {len(better)}개")
    for q, x, y in better:
        print(f"  ↑ {q}  ({fmt(x)} -> {fmt(y)})")
    print(f"=== 정답 순위가 나빠진 질문 {len(worse)}개")
    for q, x, y in worse:
        print(f"  ↓ {q}  ({fmt(x)} -> {fmt(y)})")
    print(f"=== 정답 순위는 같고 다른 결과의 순서만 바뀐 질문 {order_only}개")
    print(f"=== 벡터 거리가 달라진 (질문, 청크) 쌍 {n_dist}개, 최대 변화폭 {max_dist:.4f}" + (f"  ({max_q})" if max_q else ""))

    print("\n=== 해석")
    if same_cond and not (better or worse) and max_dist == 0 and order_only == 0:
        print("  조건이 같고 결과도 완전히 같다: 실행이 결정적이다. 이전과 결과가 다르다면 그 사이에 청크나 hint 문구가 바뀐 것이다.")
    elif same_cond:
        print("  조건이 같은데 결과가 다르다: 실행마다 거리가 조금씩 흔들린다. 한두 문항 차이를 변경의 효과로 단정하면 안 된다.")
    else:
        print("  조건이 다르다 (위에서 '다름 <-' 표시). 검색 방식이나 리랭킹을 일부러 바꾼 비교라면 그 차이가 결과 변화의 원인이다.")


if __name__ == "__main__":
    main()
