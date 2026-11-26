"""Langfuse 트레이스 연결: 켜면 질문마다 LangGraph 실행 전체(입구 판별 · 모델 호출 · 도구 · 답변 점검)를 로컬 Langfuse에 보낸다.

데이터가 컴퓨터 밖으로 나가지 않게 한다: 기본은 꺼짐, 호스트가 로컬이 아니면 LANGFUSE_ALLOW_REMOTE=1이 없는 한 켜지 않는다.
(Langfuse SDK는 호스트를 정하지 않으면 기본값이 Langfuse 클라우드라서, 비어 있으면 로컬 주소를 직접 채운다.)
켜는 조건: LANGFUSE_PUBLIC_KEY · LANGFUSE_SECRET_KEY가 있고 LANGFUSE_TRACING이 0이 아님.  설정은 compose.langfuse.yaml · scripts/langfuse_env.sh.
트레이스 연결이 실패해도 답변은 그대로 나간다 (경고만).
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

DEFAULT_HOST = "http://localhost:3000"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "langfuse-web"}
_warned: set[str] = set()


def _warn(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(f"[tracing] {message}", file=sys.stderr)


def _make_handler():
    from langfuse.langchain import CallbackHandler          # 켜졌을 때만 불러온다 (패키지는 requirements-tracing.txt)
    return CallbackHandler()


def langfuse_config(session_id: str) -> dict:
    """graph.invoke의 설정에 합칠 값. 꺼져 있거나 안전하지 않으면 {}."""
    env = os.environ
    if not (env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY")) or env.get("LANGFUSE_TRACING") == "0":
        return {}
    hosts = [h for h in (env.get("LANGFUSE_HOST"), env.get("LANGFUSE_BASE_URL")) if h]
    if not hosts:
        env["LANGFUSE_HOST"] = DEFAULT_HOST                      # SDK의 기본값(클라우드)으로 가지 않게
        hosts = [DEFAULT_HOST]
    remote = [h for h in hosts if (urlparse(h).hostname or "") not in LOCAL_HOSTS]
    if remote and env.get("LANGFUSE_ALLOW_REMOTE") != "1":
        _warn("remote", f"트레이스를 켜지 않았습니다: 호스트 {remote[0]}가 이 컴퓨터가 아니라 질문과 답변이 밖으로 나갈 수 있습니다. 그래도 보내려면 LANGFUSE_ALLOW_REMOTE=1")
        return {}
    try:
        handler = _make_handler()
    except Exception as e:                                       # shortcut: 패키지 없음 · 설정 오류를 모두 같게 다룬다. 원인별 안내가 필요해지면 나눈다.
        _warn("handler", f"트레이스를 켜지 못했습니다 ({type(e).__name__}: {e}). 답변은 그대로 나갑니다.")
        return {}
    return {"callbacks": [handler], "metadata": {"langfuse_session_id": session_id, "langfuse_tags": ["banking-agent"]}}
