"""리랭킹: 1차 검색 후보를 (질문, 문서) 쌍으로 다시 점수 매겨 순서를 바꾼다.

1차 검색(벡터/키워드)은 질문과 문서를 따로 벡터로 만들어 비교해서 빠르지만 거칠다.
교차 인코더는 질문과 문서를 한 번에 읽고 관련도를 직접 계산해서 느리지만 정확하다. 그래서 후보 몇 개에만 쓴다.

backend
  cross  교차 인코더 (fastembed, ONNX). 기본 BAAI/bge-reranker-v2-m3-int8 (다국어, Apache-2.0, 약 0.57GB)
         pip install fastembed  /  첫 실행에 모델을 내려받는다
  llm    로컬 LLM(Ollama)에게 관련도를 0~10점으로 매기게 한다. 설치는 필요 없지만 후보마다 호출해서 느리다
  fake   시험용 (질문과 문서의 낱말 겹침)
"""
import os
import re
from functools import lru_cache
from pathlib import Path

RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3-int8")
RERANK_LLM_MODEL = os.environ.get("RERANK_LLM_MODEL", "qwen2.5:7b")
# 내려받은 모델을 둘 곳. 기본은 임시 폴더라 재부팅 때 지워질 수 있어서 프로젝트 안(.gitignore의 rag/models/)에 둔다
MODEL_DIR = os.environ.get("RERANK_CACHE", str(Path(__file__).resolve().parent / "models"))
BACKENDS = ("cross", "llm", "fake")


class RerankError(RuntimeError):
    """리랭커를 쓸 수 없을 때 (라이브러리 없음, 모델 내려받기 실패, Ollama 꺼짐 등)."""


def available() -> dict:
    """각 백엔드를 쓸 수 있는지 (모델을 불러오지 않고 import만 확인한다)."""
    info = {}
    try:
        import fastembed  # noqa: F401
        info["cross"] = True
    except ImportError:
        info["cross"] = False
    try:
        import ollama  # noqa: F401
        info["llm"] = True
    except ImportError:
        info["llm"] = False
    return info


@lru_cache(maxsize=2)
def _cross_encoder(model: str):
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError as e:
        raise RerankError("fastembed가 설치돼 있지 않습니다: pip install fastembed") from e
    try:
        return TextCrossEncoder(model_name=model, cache_dir=MODEL_DIR)   # 처음에는 모델을 내려받는다 (인터넷 필요, 시간이 걸림)
    except Exception as e:
        raise RerankError(f"리랭커 모델({model})을 불러오지 못했습니다: {e}") from e


def _llm_scores(query: str, docs: list[str]) -> list[float]:
    import httpx
    import ollama
    scores = []
    for d in docs:
        prompt = ("질문과 문서가 얼마나 관련 있는지 0부터 10까지 정수 하나로만 답해라. "
                  "10은 질문에 직접 답하는 문서, 0은 관련 없는 문서다.\n\n"
                  f"[질문]\n{query}\n\n[문서]\n{d[:1200]}\n\n점수:")
        try:
            resp = ollama.chat(model=RERANK_LLM_MODEL, messages=[{"role": "user", "content": prompt}],
                               options={"temperature": 0, "num_predict": 4})
        except ollama.ResponseError as e:
            raise RerankError(f"Ollama 오류: {e} (모델이 없다면: ollama pull {RERANK_LLM_MODEL})") from e
        except (ConnectionError, httpx.ConnectError) as e:
            raise RerankError("Ollama에 연결할 수 없습니다. Ollama 앱이 켜져 있는지 확인하세요.") from e
        m = re.search(r"\d+(?:\.\d+)?", resp.message.content or "")
        scores.append(float(m.group()) if m else 0.0)
    return scores


def _fake_scores(query: str, docs: list[str]) -> list[float]:
    q = set(query.replace(" ", ""))
    return [len(q & set(d.replace(" ", ""))) / (len(q) or 1) for d in docs]


def score(backend: str, query: str, docs: list[str]) -> list[float]:
    """docs와 같은 순서의 관련도 점수 (클수록 관련 있음)."""
    if not docs:
        return []
    if backend == "cross":
        return [float(x) for x in _cross_encoder(RERANK_MODEL).rerank(query, docs)]
    if backend == "llm":
        return _llm_scores(query, docs)
    if backend == "fake":
        return _fake_scores(query, docs)
    raise ValueError(f"알 수 없는 리랭커: {backend} (cross, llm 중에서 고르세요)")


if __name__ == "__main__":
    # 사용: python rag/rerank.py   -> 교차 인코더 모델을 미리 내려받고(처음 한 번, 약 0.57GB) 동작을 확인한다
    import time
    q = "정기예금 중도해지하면 이율이 어떻게 돼요?"
    docs = ["중도해지금리: 1개월 미만 연 0.10%, 1개월 이상 3개월 미만 연 0.15%, 3개월 이상 6개월 미만 연 0.20%",
            "휴면예금은 최종거래일로부터 5년이 지나면 서민금융진흥원에 출연될 수 있다"]
    print(f"리랭커 모델: {RERANK_MODEL}\n처음에는 모델을 내려받느라 시간이 걸립니다...")
    t0 = time.time()
    try:
        scores = score("cross", q, docs)
    except RerankError as e:
        raise SystemExit(f"실패: {e}")
    print(f"완료 ({time.time() - t0:.1f}초)")
    for d, sc in zip(docs, scores):
        print(f"  {sc:8.3f}  {d[:40]}...")
    print("관련 있는 문서가 더 높은 점수면 정상입니다." if scores[0] > scores[1] else "주의: 점수 순서가 예상과 다릅니다.")
