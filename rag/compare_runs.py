"""check_search.py --save 로 저장한 두 실행 기록을 비교한다.

실행: 레포 루트에서  python rag/compare_runs.py eval/runs/run1.json eval/runs/run2.json

보는 것: 두 기록의 조건(청크 파일 해시, hint 개수)이 같은지, 순위가 달라진 질문이 있는지, 거리가 얼마나 달라졌는지.
조건이 같은데 결과가 다르면 실행마다 결과가 흔들린다는 뜻이고, 조건이 다르면 그 차이가 원인 후보다.
"""
import json
import sys


def load(path: str) -> dict:
    return json.load(open(path, encoding="utf-8"))


def answer_rank(row: dict):
    ids = [r["chunk_id"] for r in row["results"]]
    return next((i + 1 for i, cid in enumerate(ids) if cid in row["answer_ids"]), None)


def answer_distance(row: dict):
    return next((r["distance"] for r in row["results"] if r["chunk_id"] in row["answer_ids"]), None)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("사용법: python rag/compare_runs.py <기록A.json> <기록B.json>")
    a, b = load(sys.argv[1]), load(sys.argv[2])
    ma, mb = a["meta"], b["meta"]
    print("=== 조건 비교 (A | B)")
    for key, label in (("chunks_sha", "chunks.jsonl 해시"), ("hints", "인덱스의 hint 개수"), ("index", "인덱스 청크 수"),
                       ("embed_model", "임베딩 모델"), ("k", "상위 개수"), ("limit", "거리 한도")):
        same = "같음" if ma.get(key) == mb.get(key) else "다름 <-"
        print(f"  {label}: {ma.get(key)} | {mb.get(key)}  {same}")
    same_cond = all(ma.get(k) == mb.get(k) for k in ("chunks_sha", "hints", "index", "embed_model"))

    rows_b = {r["question"]: r for r in b["rows"]}
    changed, max_dist, max_q, n_dist = [], 0.0, "", 0
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
        order_a, order_b = [r["chunk_id"] for r in ra["results"]], [r["chunk_id"] for r in rb["results"]]
        if ra["has_answer"] and answer_rank(ra) != answer_rank(rb):
            changed.append((ra["question"], answer_rank(ra), answer_rank(rb), answer_distance(ra), answer_distance(rb)))
        elif order_a != order_b:
            changed.append((ra["question"], "순서만", "바뀜", None, None))

    print(f"\n=== 결과 비교")
    print(f"  거리가 조금이라도 달라진 (질문, 청크) 쌍: {n_dist}개, 최대 변화폭 {max_dist:.4f}" + (f"  ({max_q})" if max_q else ""))
    print(f"  정답 순위나 결과 순서가 바뀐 질문: {len(changed)}개")
    for q, ra_, rb_, da_, db_ in changed:
        dist = f"  정답 거리 {da_} -> {db_}" if da_ is not None and db_ is not None else ""
        print(f"    - {q}\n        {ra_}위 -> {rb_}위{dist}" if isinstance(ra_, int) or ra_ is None else f"    - {q}  ({ra_} {rb_})")
    print("\n=== 해석")
    if same_cond and not changed and max_dist == 0:
        print("  조건이 같고 결과도 완전히 같다: 실행이 결정적이다. 이전과 결과가 다르다면 그 사이에 청크나 hint 문구가 바뀐 것이다.")
    elif same_cond:
        print("  조건이 같은데 결과가 다르다: 실행마다 거리가 조금씩 흔들린다. 거리가 비슷한 청크끼리는 순위가 바뀔 수 있어서,")
        print("  한두 문항 차이는 hint 같은 변경의 효과로 단정하면 안 된다.")
    else:
        print("  조건이 다르다 (위에서 '다름 <-' 표시). 그 차이가 결과 변화의 원인 후보다.")


if __name__ == "__main__":
    main()
