"""API 모드 끝까지 시험: 실제 ChatOpenAI 라이브러리가 만드는 HTTP 요청을 가짜 OpenAI 서버(로컬)로 받아, 개인정보 모양이 가려진 채 나가는지 본다. 인터넷 · API 키 불필요."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_graph as tg  # noqa: E402
from app import graph as g  # noqa: E402

pytest.importorskip("langchain_openai")


@pytest.fixture
def fake_openai(monkeypatch):
    bodies, headers = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            bodies.append(json.loads(raw))
            headers.append({k.lower(): v for k, v in self.headers.items()})                  # 클라이언트가 보내는 대소문자와 무관하게
            reply = {"id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "gpt-4.1-mini",
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "약관을 확인했어요."}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            out = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    yield bodies, headers
    srv.shutdown()


def test_the_whole_agent_runs_in_api_mode_and_nothing_personal_leaves(fake_openai):
    bodies, headers = fake_openai
    a = g.Agent(search_fn=tg.search_ok)
    q = "제 번호는 010-1234-5678이고 메일은 hong@example.com이에요. 정기예금 중도해지하면 이자는 어떻게 돼요?"
    res = a.ask(q, "http-1")
    assert res["error"] is None and res["answer"] and "약관을 확인했어요." in res["answer"]
    assert bodies, "모델 호출이 한 번도 없었다"
    wire = json.dumps(bodies, ensure_ascii=False)
    assert "5678" not in wire and "hong@example" not in wire and "[전화번호]" in wire and "[이메일]" in wire
    assert all(b.get("temperature") == 0 and b.get("tools") for b in bodies)                  # 결정적 설정과 도구 정의가 그대로 간다
    assert all(h.get("authorization") == "Bearer sk-test" for h in headers)
    human = [m for m in a.history("http-1") if m.type == "human"][0]
    assert "010-1234-5678" in human.content                                                    # 그래프 상태의 원본은 바뀌지 않는다
