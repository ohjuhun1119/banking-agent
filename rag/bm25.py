"""BM25 키워드 검색. 한국어는 Kiwi 형태소 분석으로 조사/어미를 떼고 내용어만 남긴다.

'중도해지하면'과 '중도해지금리'가 같은 낱말(중도, 해지)로 쪼개져 맞물리는 게 핵심이다.
인덱스는 ingest.py가 만들어 rag/chroma_db/bm25.json 에 저장하고, search.py가 읽어 쓴다.
"""
import json
import math
from collections import Counter
from functools import lru_cache
from pathlib import Path

KEEP_TAGS = ("NNG", "NNP", "NR", "SN", "SL", "VV", "VA", "XR")     # 명사, 수사, 숫자, 외래어, 용언 어간, 어근


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


def tokenize(text: str) -> list[str]:
    tokens = []
    for t in _kiwi().tokenize(text):
        if t.tag not in KEEP_TAGS:
            continue
        if t.tag in ("VV", "VA") and len(t.form) == 1:             # 하, 되, 있 같은 1글자 어간은 잡음
            continue
        tokens.append(t.form.lower())
    return tokens


class BM25:
    def __init__(self, ids, docs_tokens, metas, k1: float = 1.5, b: float = 0.75):
        self.ids, self.metas, self.k1, self.b = list(ids), list(metas), k1, b
        self.tf = [Counter(t) for t in docs_tokens]
        self.dl = [len(t) for t in docs_tokens]
        n = len(self.ids)
        self.avgdl = (sum(self.dl) / n) if n else 1.0
        df = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}   # log 안에 1을 더해 idf가 음수가 되지 않게 한다

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = [0.0] * len(self.ids)
        for w in set(query_tokens):
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(w, 0)
                if f:
                    out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avgdl))
        return out

    def search(self, query: str, n: int = 20, allow=None) -> list[tuple[str, float]]:
        """(chunk_id, 점수)를 점수 순으로. 점수가 0인 청크(낱말이 하나도 안 겹침)는 뺀다. allow(meta)가 False면 제외."""
        scored = [(s, i) for i, s in enumerate(self.scores(tokenize(query))) if s > 0 and (allow is None or allow(self.metas[i]))]
        scored.sort(key=lambda x: (-x[0], self.ids[x[1]]))
        return [(self.ids[i], round(s, 4)) for s, i in scored[:n]]


def save_index(path, ids, docs_tokens, metas) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ids": list(ids), "tokens": docs_tokens, "metas": list(metas)}, ensure_ascii=False), encoding="utf-8")


_cache: dict = {}


def load_index(path):
    """저장된 인덱스를 읽는다. 파일이 바뀌면(ingest 재실행) 다시 읽는다. 없으면 None."""
    path = Path(path)
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _cache:
        data = json.loads(path.read_text(encoding="utf-8"))
        _cache.clear()
        _cache[key] = BM25(data["ids"], data["tokens"], data["metas"])
    return _cache[key]
