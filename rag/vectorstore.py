"""임베딩과 Chroma 접근을 한곳에 모은 모듈. ingest.py(적재)와 search.py(검색)가 같이 쓴다.

임베딩은 Ollama의 bge-m3를 쓴다 (PyTorch 없이 동작, 한국어 지원, 질문에 접두어 불필요).
  준비: ollama pull bge-m3   (Ollama 앱이 켜져 있어야 함)
"""
import hashlib
import math
import os
from pathlib import Path

EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "ollama")     # "fake"는 배선 점검용 (실제 검색 품질과 무관)
CHROMA_DIR = Path(__file__).resolve().parent / "chroma_db"    # .gitignore의 chroma_db/ 에 해당
COLLECTION = "hana_terms"


def bm25_path() -> Path:
    """키워드(BM25) 인덱스 파일. CHROMA_DIR을 호출 시점에 읽어서 시험에서 임시 폴더로 바꿀 수 있다."""
    return CHROMA_DIR / "bm25.json"


BATCH = 16


def _embed_ollama(texts: list[str]) -> list[list[float]]:
    import ollama
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        try:
            resp = ollama.embed(model=EMBED_MODEL, input=texts[i:i + BATCH])
        except ollama.ResponseError as e:
            raise SystemExit(f"Ollama 오류: {e}\n모델이 없다면: ollama pull {EMBED_MODEL}") from e
        except ConnectionError as e:
            raise SystemExit("Ollama에 연결할 수 없습니다. Ollama 앱이 켜져 있는지 확인하세요.") from e
        out.extend(list(v) for v in resp.embeddings)
    return out


def _embed_fake(texts: list[str], dim: int = 512) -> list[list[float]]:
    """글자 1·2-gram을 해시해 만든 가짜 임베딩. Ollama 없이 적재/검색 배선을 시험할 때만 쓴다."""
    out = []
    for t in texts:
        v = [0.0] * dim
        t = "".join(t.split())
        grams = list(t) + [t[i:i + 2] for i in range(len(t) - 1)]
        for g in grams:
            v[int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16) % dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _embed_fake(texts) if EMBED_BACKEND == "fake" else _embed_ollama(texts)


def get_client():
    import chromadb
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection(reset: bool = False):
    """코사인 거리를 쓰는 컬렉션. reset=True면 지우고 새로 만든다."""
    client = get_client()
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    return client.get_or_create_collection(COLLECTION, configuration={"hnsw": {"space": "cosine"}})


def clean_metadata(meta: dict) -> dict:
    """Chroma 메타데이터는 문자열/숫자/불리언만 되고 None은 안 된다."""
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out
