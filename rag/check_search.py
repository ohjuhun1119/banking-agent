"""검색이 제대로 되는지 질문 목록으로 확인한다. 정답 청크가 몇 위에 나오는지, 함정 청크가 앞서는지 본다.

실행: 레포 루트에서  python rag/check_search.py            (상위 5개까지 확인)
      python rag/check_search.py --k 8
      python rag/check_search.py --save eval/runs/run1.json     (실행 기록 저장: 두 실행을 compare_runs.py로 비교)
먼저 python rag/ingest.py 로 청크를 적재해 둔다.
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from search import DISTANCE_LIMIT, search_terms

S = "hana-deposit-sheet__"
T = "hana-deposit-terms__"
B = "hana-deposit-basic-terms__"
ET, IC = S + "early_termination", S + "interest_calc"

# (그룹, 질문, 정답 청크 id들 (이 중 하나면 정답), 정답보다 앞서면 안 되는 함정 id들)
QUESTIONS = [
    ("중도해지", "정기예금 중도해지하면 이율이 어떻게 돼요?", {ET}, {IC, T + "art7"}),
    ("중도해지", "중도해지하면 이자 얼마 받아요?", {ET}, {IC, T + "art7"}),
    ("중도해지", "가입한 지 두 달 됐는데 지금 해지하면 금리가 얼마예요?", {ET}, {IC, T + "art7"}),
    ("중도해지", "1년짜리 예금을 8개월 만에 깨면 이율은 어떻게 계산해요?", {ET}, {IC, T + "art7"}),
    ("중도해지", "만기 전에 해지하면 손해인가요?", {ET}, {IC, T + "art7"}),
    ("중도해지", "6개월 안에 해지하면 이자를 거의 못 받나요?", {ET}, {IC, T + "art7"}),
    ("중도해지", "중도해지금리 차등율이 뭐예요?", {ET}, set()),
    ("중도해지", "경과율은 어떻게 계산해요?", {ET}, set()),
    ("중도해지", "중도해지 이율은 소수점 몇째 자리까지 적용돼요?", {ET}, set()),
    ("중도해지", "급전이 필요해서 예금을 중간에 깼는데 이자는 얼마나 나와요?", {ET}, {IC, T + "art7"}),
    ("기타", "만기가 지난 뒤에 찾으면 이자를 더 주나요?", {S + "after_maturity"}, set()),
    ("기타", "예금 일부만 먼저 해지할 수 있나요?", {S + "partial_withdrawal", T + "art9"}, set()),
    ("기타", "만기 때 자동으로 다시 예치하려면 어떻게 해요?", {S + "renewal", T + "art10"}, set()),
    ("기타", "만기일이 공휴일이면 해지는 어떻게 되나요?", {S + "cancel_method"}, set()),
    ("기타", "오래 거래 안 한 계좌는 휴면예금이 되나요?", {S + "dormant", B + "art9-2"}, set()),
    ("기타", "이 예금은 예금자보호가 되나요?", {S + "deposit_protection"}, set()),
    ("기타", "최소 가입금액이 얼마예요?", {S + "overview", T + "art5"}, set()),
    ("기타", "이자는 어떻게 계산해요?", {IC, T + "art7"}, set()),
    ("기타", "비과세종합저축으로 가입할 수 있나요?", {S + "tax"}, set()),
    # 검증용: hint를 쓴 뒤에 새로 만든 표현. 튜닝에 쓰지 않은 문항으로 일반화가 되는지 본다
    ("검증-중도해지", "계약 기간이 끝나기 전에 돈을 찾으면 이자가 어떻게 되나요?", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "예치한 지 3개월 됐는데 인출하면 이율이 몇 퍼센트인가요?", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "10개월 넣어뒀다가 해약하면 이자율은요?", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "만기를 채우지 않고 해지할 때 적용되는 금리를 알려주세요", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "일찍 해약하면 약정금리 그대로 받나요?", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "가입한 지 얼마나 됐는지에 따라 해지 이율이 달라지나요?", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "중도해지 이율은 가입할 때 금리를 기준으로 계산하나요?", {ET}, {IC, T + "art7"}),
    ("검증-중도해지", "한 달도 안 돼서 해지하면 이자를 얼마나 주나요?", {ET}, {IC, T + "art7"}),
    ("검증-기타", "원금 중 일부만 빼서 쓸 수 있나요?", {S + "partial_withdrawal", T + "art9"}, set()),
    ("검증-기타", "만기 후에 한참 뒤에 가면 이자가 줄어드나요?", {S + "after_maturity"}, set()),
    ("검증-기타", "이 상품은 5천만 원 넣어도 원금이 보호되나요?", {S + "deposit_protection"}, set()),
    ("없음", "세후 이자는 얼마예요?", set(), set()),
    ("없음", "대출 금리는 얼마예요?", set(), set()),
    ("없음", "오늘 서울 날씨 어때요?", set(), set()),
]


def short(chunk_id: str) -> str:
    return chunk_id.replace("hana-deposit-sheet__", "설명서:").replace("hana-deposit-terms__", "특약:") \
                   .replace("hana-deposit-basic-terms__", "기본:").replace("hana-lumpsum-terms__", "거치식:")


def run_checks(k: int = 5) -> list[dict]:
    """질문마다 검색해 정답 순위, 함정 여부, 상위 3개를 모은다. 인덱스가 비어 있으면 RuntimeError."""
    rows = []
    for group, question, answers, avoid in QUESTIONS:
        res = search_terms(question, k=k, max_distance=None)       # 거리 한도 없이 측정한다
        if res["status"] == "error":
            raise RuntimeError(res["message"])
        found = res["results"]
        ids = [r["chunk_id"] for r in found]
        top = [{"chunk_id": r["chunk_id"], "short": short(r["chunk_id"]), "distance": r["distance"],
                "chunk_type": r["chunk_type"]} for r in found[:3]]
        row = {"group": group, "question": question, "has_answer": bool(answers), "top": top,
               "answer_ids": sorted(answers), "results": [{"chunk_id": r["chunk_id"], "distance": r["distance"]} for r in found],
               "nearest": found[0]["distance"] if found else None, "rank": None, "trapped": False}
        row["rejected"] = bool(DISTANCE_LIMIT is not None and row["nearest"] is not None and row["nearest"] > DISTANCE_LIMIT)
        if answers:
            rank = next((i + 1 for i, cid in enumerate(ids) if cid in answers), None)
            trapped = (rank is not None and any(cid in avoid for cid in ids[:rank - 1])) \
                or (rank is None and any(cid in avoid for cid in ids))
            row.update(rank=rank, trapped=trapped)
        rows.append(row)
    return rows


def summarize(rows: list[dict], k: int) -> dict:
    no_answer = [r for r in rows if not r["has_answer"]]
    out: dict = {"k": k, "groups": {}, "traps": sum(1 for r in rows if r["trapped"]),
                 "no_answer_distances": [r["nearest"] for r in no_answer],
                 "limit": DISTANCE_LIMIT, "no_answer_rejected": sum(1 for r in no_answer if r["rejected"])}
    groups = list(dict.fromkeys(r["group"] for r in rows if r["has_answer"]))
    for group in groups:
        ranks = [r["rank"] for r in rows if r["group"] == group and r["has_answer"]]
        if ranks:
            n = len(ranks)
            out["groups"][group] = {
                "n": n, "top1": sum(1 for x in ranks if x and x <= 1), "top3": sum(1 for x in ranks if x and x <= 3),
                f"top{k}": sum(1 for x in ranks if x), "mrr": round(sum(1 / x for x in ranks if x) / n, 2)}
    return out


def run_meta(k: int) -> dict:
    """실행 기록에 남길 조건: 청크 파일 해시, 인덱스의 hint 개수 등. 두 기록이 같은 조건인지 비교할 때 쓴다."""
    chunks = Path(__file__).resolve().parent / "chunks" / "chunks.jsonl"
    sha = hashlib.sha256(chunks.read_bytes()).hexdigest()[:12] if chunks.exists() else None
    try:
        from vectorstore import EMBED_MODEL, get_collection
        col = get_collection()
        metas = col.get(include=["metadatas"])["metadatas"]
        hints, index = sum(1 for m in metas if m.get("hint")), col.count()
        model = EMBED_MODEL
    except Exception:
        hints, index, model = None, None, None
    return {"k": k, "chunks_sha": sha, "index": index, "hints": hints, "embed_model": model,
            "limit": DISTANCE_LIMIT, "saved_at": datetime.now().isoformat(timespec="seconds")}


def main() -> None:
    k = int(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else 5
    save = sys.argv[sys.argv.index("--save") + 1] if "--save" in sys.argv else None
    try:
        rows = run_checks(k)
    except RuntimeError as e:
        raise SystemExit(str(e))
    for r in rows:
        top = " | ".join(f"{i + 1}.{t['short']} d={t['distance']:.2f}" for i, t in enumerate(r["top"]))
        if not r["has_answer"]:
            d = r["nearest"] if r["nearest"] is not None else float("nan")
            verdict = "거리 한도로 거부됨" if r["rejected"] else "한도 통과 (LLM이 판단해야 함)"
            print(f"[없음] {r['question']}\n   가장 가까운 거리 {d:.2f} -> {verdict}  ({top})")
            continue
        rank = r["rank"]
        mark = "✓" if rank == 1 else ("△" if rank else "✗")
        print(f"[{r['group']}] {mark} {r['question']}\n   정답 순위 {rank if rank else f'{k}위 밖'}{'  함정 청크가 앞섬' if r['trapped'] else ''}  ({top})")
    summary = summarize(rows, k)
    if save:
        out = Path(save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"meta": run_meta(k), "summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n실행 기록 저장: {out}")
    print("\n=== 요약 ===")
    for group, g in summary["groups"].items():
        print(f"{group}: {g['n']}문항  1위 {g['top1']}/{g['n']}  3위 안 {g['top3']}/{g['n']}  {k}위 안 {g[f'top{k}']}/{g['n']}  MRR {g['mrr']:.2f}")
    print(f"함정 청크(약정금리 이자계산 등)가 정답보다 앞선 질문: {summary['traps']}개")
    if summary["no_answer_distances"]:
        print("답 없는 질문의 가장 가까운 거리: " + ", ".join(f"{d:.2f}" for d in summary["no_answer_distances"])
              + f"  (거리 한도 {summary['limit']}: {summary['no_answer_rejected']}/{len(summary['no_answer_distances'])}개 거부)")


if __name__ == "__main__":
    main()
