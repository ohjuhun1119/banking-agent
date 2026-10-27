"""rag/chunks/chunks.jsonl 을 읽어 임베딩하고 Chroma(rag/chroma_db)에 넣는다.

실행: 레포 루트에서  python rag/ingest.py
먼저 python rag/build_chunks.py 로 chunks.jsonl을 만들고, ollama pull bge-m3 를 해 둔다.
실행할 때마다 컬렉션을 지우고 처음부터 다시 만든다 (청크가 62개라 몇 초면 끝나고, 옛 청크가 남지 않는다).

검색용 설명(hint)의 효과를 비교할 때: 환경변수 NO_HINT=1 로 실행하면 hint 없이 적재한다 (chunks.jsonl은 그대로).
  PowerShell:  $env:NO_HINT = "1"; python rag/ingest.py; Remove-Item Env:NO_HINT
"""
import json
import os
import time
from collections import Counter
from pathlib import Path

from chunking import embedding_text
from vectorstore import EMBED_BACKEND, EMBED_MODEL, clean_metadata, embed_texts, get_collection

CHUNKS = Path("rag/chunks/chunks.jsonl")


def main() -> None:
    if not CHUNKS.exists():
        raise SystemExit(f"{CHUNKS} 가 없습니다. 먼저 python rag/build_chunks.py 를 실행하세요.")
    rows = [json.loads(line) for line in CHUNKS.open(encoding="utf-8")]
    use_hint = os.environ.get("NO_HINT") != "1"
    if not use_hint:
        for r in rows:
            r["metadata"] = {k: v for k, v in r["metadata"].items() if k != "hint"}
    n_hint = sum(1 for r in rows if r["metadata"].get("hint"))
    print(f"검색용 설명(hint): {'사용' if use_hint else '끔 (NO_HINT=1)'} · 적용된 청크 {n_hint}개")
    t0 = time.time()
    vectors = embed_texts([embedding_text(r) for r in rows])      # 문서명 · 조항을 앞에 붙인 문장을 임베딩
    col = get_collection(reset=True)
    col.add(
        ids=[r["chunk_id"] for r in rows],
        documents=[r["text"] for r in rows],                        # 저장하는 본문은 원래 텍스트
        metadatas=[clean_metadata(r["metadata"]) for r in rows],
        embeddings=vectors,
    )
    print(f"적재 완료: {col.count()}개 청크, 벡터 {len(vectors[0])}차원, 모델 {EMBED_MODEL if EMBED_BACKEND != 'fake' else 'fake(테스트)'}, {time.time() - t0:.1f}초")
    for ctype, n in Counter(r["metadata"].get("chunk_type") for r in rows).most_common():
        print(f"  {ctype}: {n}")


if __name__ == "__main__":
    main()
