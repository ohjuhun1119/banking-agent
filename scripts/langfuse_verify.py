"""로컬 Langfuse에 트레이스가 실제로 올라오는지 확인한다 (실제 모델 · 로컬 Langfuse 필요, CI에서는 돌지 않는다).

준비:  scripts/langfuse_env.sh  →  docker compose -f compose.langfuse.yaml --env-file .env.langfuse up -d  →  pip install -r requirements-tracing.txt
실행:  python scripts/langfuse_verify.py
시험 질문 3개(계산 · 약관 · 범위 밖)를 보내고, Langfuse 공개 API(v2 observations)로 올라온 트레이스와 단계를 출력한다.
(Langfuse 4.x 서버는 옛 /api/public/traces를 열지 않아 v2 observations를 쓴다.)
"""
import base64
import collections
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env.langfuse").read_text(encoding="utf-8").splitlines():
    k, _, v = line.partition("=")
    if k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        os.environ[k] = v
os.environ.setdefault("LANGFUSE_HOST", "http://localhost:3000")

from app import tracing  # noqa: E402
from app.graph import Agent  # noqa: E402

QUESTIONS = [("lf-verify-calc", "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-12-07에 해지하면 이자 얼마예요? 기본금리는 3.5%예요."),
             ("lf-verify-search", "정기예금 중도해지하면 이자는 어떻게 돼요?"),
             ("lf-verify-decline", "대출 금리 알려줘")]


def main() -> int:
    if not tracing.langfuse_config("probe"):
        print("트레이스가 꺼져 있습니다 (키 · 호스트를 확인하세요)")
        return 1
    agent = Agent()
    for sid, q in QUESTIONS:
        r = agent.ask(q, sid)
        print(f"{sid}: 모델 호출 {r['model_calls']}회, 도구 {[t['name'] for t in r['trace']]}, {r['ms']}ms")
    from langfuse import get_client
    get_client().flush()
    time.sleep(8)                                                  # 워커가 처리할 시간
    auth = base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
    url = f"{os.environ['LANGFUSE_HOST']}/api/public/v2/observations?limit=200&fields=core,basic,io"
    obs = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Basic " + auth}), timeout=30))["data"]
    traces = collections.defaultdict(list)
    for o in obs:
        traces[o["traceId"]].append(o)
    print(f"\nLangfuse에 올라온 트레이스 {len(traces)}개 (관찰 {len(obs)}개)")
    for tid, items in traces.items():
        steps = collections.Counter(o.get("name") for o in items)
        print(f"- {tid[:8]}  단계: " + ", ".join(f"{k}×{v}" for k, v in steps.items()))
    return 0 if len(traces) >= len(QUESTIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
