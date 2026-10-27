"""청크 확인용.

  python rag/inspect_chunks.py                       전체 목록
  python rag/inspect_chunks.py hana-deposit-terms    chunk_id에 해당 글자가 들어간 청크 목록
  python rag/inspect_chunks.py <정확한 chunk_id>     그 청크의 메타데이터와 전체 본문
"""
import json
import sys

rows = [json.loads(line) for line in open("rag/chunks/chunks.jsonl", encoding="utf-8")]
arg = sys.argv[1] if len(sys.argv) > 1 else ""
exact = [r for r in rows if r["chunk_id"] == arg]
if exact:
    r = exact[0]
    print(json.dumps(r["metadata"], ensure_ascii=False, indent=2))
    print("-" * 40)
    print(r["text"])
else:
    for r in rows:
        if arg in r["chunk_id"]:
            m = r["metadata"]
            head = r["text"][:45].replace("\n", " ")
            print(f"{r['chunk_id']:<44} {len(r['text']):>5}자  p{m.get('page', '-')}  {head}")
