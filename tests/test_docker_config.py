"""Docker 설정 시험 (12일차). Docker는 실행하지 않는다: 구문 · 경로가 코드의 실제 경로와 맞는지 · 약관 본문이 이미지에 안 들어가는지 ·
이미지에 복사되는 파일만으로 서버가 뜨는지를 확인한다. 실제 `docker compose up`은 이 레포에서 해 보지 못했다 (README '검증 상태')."""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "rag"))

COMPOSE_FILES = ["compose.yaml", "compose.host-ollama.yaml"]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = [ln.strip() for ln in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def ignored(rel: str) -> bool:
    """.dockerignore에 걸리는 경로인가 (폴더 이름이나 글롭이 경로의 어느 앞부분과 맞으면 제외)."""
    parts = rel.split("/")
    for pat in DOCKERIGNORE:
        pat = pat.rstrip("/")
        for i in range(1, len(parts) + 1):
            head = "/".join(parts[:i])
            if fnmatch.fnmatch(head, pat) or fnmatch.fnmatch(parts[i - 1], pat):
                return True
    return False


def default_data_dirs() -> set[str]:
    """상담 요청 · 트레이스 DB의 **기본** 위치. tests/conftest.py가 시험마다 이 경로를 임시 폴더로 바꾸므로, 환경을 비운 별도 프로세스에서 읽는다."""
    code = ("import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s'); from pathlib import Path; from app import traces; from app.tools import handoff; root = Path(r'%s'); "
            "print(*[p.parent.relative_to(root).as_posix() for p in (traces.DB_PATH, handoff.DB_PATH)])") % (ROOT, ROOT / "rag", ROOT)
    env = {k: v for k, v in os.environ.items() if k not in ("TRACE_DB", "HANDOFF_DB")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    return {"/app/" + d for d in out.stdout.split()}


# ---------- compose ----------
@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_compose_files_define_the_api_and_the_one_off_ingest(name):
    services = load(name)["services"]
    assert {"api", "ingest"} <= set(services)
    assert services["ingest"]["profiles"] == ["build"] and services["ingest"]["command"] == ["python", "rag/ingest.py"]     # 평소 `up`에는 안 뜬다
    assert services["api"]["build"] == "." and services["api"].get("restart") == "unless-stopped"


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_api_port_is_published_on_localhost_only_because_the_service_has_no_login(name):
    ports = load(name)["services"]["api"]["ports"]
    assert ports == ["127.0.0.1:8000:8000"]


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_volumes_land_exactly_where_the_code_reads_and_writes(name):
    """색인 · 상담 요청 DB 위치는 코드에 고정돼 있다: 볼륨의 컨테이너 쪽 경로가 어긋나면 서버가 빈 색인을 본다."""
    import vectorstore
    chroma = "/app/" + vectorstore.CHROMA_DIR.relative_to(ROOT).as_posix()
    data = default_data_dirs()
    assert chroma == "/app/rag/chroma_db" and data == {"/app/data"}
    services = load(name)["services"]
    api_volumes = services["api"]["volumes"]
    assert f"./rag/chroma_db:{chroma}" in api_volumes and "./data:/app/data" in api_volumes
    ingest = services["ingest"]["volumes"]
    import ingest as ingest_module
    chunks_dir = "/app/" + Path(ingest_module.CHUNKS).parent.as_posix()
    assert f"./rag/chunks:{chunks_dir}:ro" in ingest and f"./rag/chroma_db:{chroma}" in ingest                  # 청크는 읽기만, 색인은 쓴다


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_healthcheck_calls_a_route_that_exists_and_does_not_need_ollama(name):
    from app import api
    cmd = " ".join(load(name)["services"]["api"]["healthcheck"]["test"])
    assert re.search(r"http://127\.0\.0\.1:8000(/\S*?)'", cmd).group(1) == "/health" and "curl" not in cmd         # 경로를 정확히 비교한다 (/healthz도 '/health'를 포함하므로). slim 이미지에는 curl이 없다
    assert "/health" in {r.path for r in api.app.routes}


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_the_environment_names_are_the_ones_the_code_reads(name):
    env = load(name)["services"]["api"]["environment"]
    assert set(env) == {"OLLAMA_HOST", "TOOL_MODEL", "EMBED_MODEL"}
    assert 'os.environ.get("TOOL_MODEL"' in (ROOT / "app" / "tool_calling.py").read_text(encoding="utf-8")
    assert 'os.environ.get("EMBED_MODEL"' in (ROOT / "rag" / "vectorstore.py").read_text(encoding="utf-8")
    assert env["TOOL_MODEL"].endswith(":-qwen2.5:7b}") and env["EMBED_MODEL"].endswith(":-bge-m3}")               # 기본값이 코드의 기본 모델과 같다
    from app import tool_calling
    assert tool_calling.TOOL_MODEL == "qwen2.5:7b" or os.environ.get("TOOL_MODEL")


def test_the_ollama_address_really_comes_from_the_environment_for_both_clients():
    """채팅 모델과 임베딩이 모두 OLLAMA_HOST를 따르는지 (컨테이너 설정의 핵심 가정)."""
    code = ("import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s'); import ollama; from app import tool_calling as tc; "
            "print(ollama._client._client.base_url); print(tc.get_model().bound._client._client.base_url)") % (ROOT, ROOT / "rag")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "OLLAMA_HOST": "http://ollama:11434"}, timeout=120)
    assert out.stdout.split() == ["http://ollama:11434", "http://ollama:11434"], out.stderr


def test_the_full_compose_pulls_both_models_before_the_api_starts():
    s = load("compose.yaml")["services"]
    assert s["ollama"]["healthcheck"]["test"][:2] == ["CMD", "ollama"] and s["ollama"]["volumes"] == ["ollama:/root/.ollama"]
    assert s["ollama-pull"]["depends_on"]["ollama"]["condition"] == "service_healthy" and s["ollama-pull"]["restart"] == "no"
    pull = " ".join(s["ollama-pull"]["entrypoint"])
    assert "ollama pull ${TOOL_MODEL:-qwen2.5:7b}" in pull and "ollama pull ${EMBED_MODEL:-bge-m3}" in pull
    assert s["api"]["depends_on"]["ollama-pull"]["condition"] == "service_completed_successfully"
    assert s["ingest"]["depends_on"]["ollama-pull"]["condition"] == "service_completed_successfully"                # 색인에도 임베딩 모델이 필요하다
    assert "ollama" not in load("compose.host-ollama.yaml")["services"] and "volumes" not in load("compose.host-ollama.yaml")


def test_the_host_ollama_file_can_reach_the_host_on_linux_too():
    for svc in ("api", "ingest"):
        s = load("compose.host-ollama.yaml")["services"][svc]
        assert "host.docker.internal:host-gateway" in s["extra_hosts"] and s["environment"]["OLLAMA_HOST"].endswith("http://host.docker.internal:11434}")


# ---------- .dockerignore: 약관 본문이 이미지에 들어가지 않는다 ----------
def test_everything_the_gitignore_keeps_private_is_also_kept_out_of_the_image():
    gitignore = [ln.strip().rstrip("/") for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    private = [p for p in gitignore if p.startswith("rag/") or p in ("chroma_db", "*.sqlite3", ".env")]
    assert {"rag/raw", "rag/extracted", "rag/chunks", "rag/models", "chroma_db", "*.sqlite3", ".env"} <= set(private)
    for p in private:
        sample = {"chroma_db": "rag/chroma_db/chroma.sqlite3", "*.sqlite3": "data/handoff.sqlite3"}.get(p, p + "/x" if not p.startswith(".") and "*" not in p else p)
        assert ignored(sample), f"{p}가 이미지에 들어간다"


def test_the_ignore_rules_keep_the_data_out_and_the_code_in():
    for secret in ("rag/chroma_db/chroma.sqlite3", "rag/chunks/chunks.jsonl", "rag/raw/a.pdf", "rag/extracted/a.txt", "rag/models/m.onnx", "data/handoff.sqlite3", "data/traces.sqlite3", ".env",
                   "data/anything_else.txt", "rag/manual/hana_deposit_product_sheet.md", "eval/runs/a.json", "tests/test_graph.py", ".git/config", ".venv/bin/python"):
        assert ignored(secret), secret
    for needed in ("requirements.txt", "app/api.py", "app/graph.py", "app/static/index.html", "app/tools/interest.py", "rag/search.py", "rag/vectorstore.py", "rag/ingest.py",
                   "eval/tool_cases.py", "eval/tool_check.py"):
        assert not ignored(needed), needed


# ---------- Dockerfile ----------
def test_the_dockerfile_runs_the_right_app_as_a_non_root_user_with_the_volume_mount_points():
    assert 'CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]' in DOCKERFILE and "EXPOSE 8000" in DOCKERFILE
    from app import api
    assert api.app.title
    assert re.search(r"^USER app$", DOCKERFILE, re.M) and "useradd --create-home --uid 1000 app" in DOCKERFILE
    assert "mkdir -p /app/data /app/rag/chroma_db" in DOCKERFILE                                                 # 볼륨 자리가 있어야 권한 문제가 덜하다


def test_the_dockerfile_copies_the_code_the_server_imports_and_nothing_else():
    copies = re.findall(r"^COPY (?:--chown=\S+ )?(?!--from)(\S+) \./?(\S*)$", DOCKERFILE, re.M)
    assert {src for src, _ in copies} >= {"app", "rag", "eval"}                                                  # app/api.py가 eval/tool_cases.py를 가져온다
    assert "tests" not in {src for src, _ in copies} and "data" not in {src for src, _ in copies}
    assert "--from=builder /opt/venv /opt/venv" in DOCKERFILE and DOCKERFILE.count("FROM python:${PYTHON_VERSION}-slim") == 2      # 빌드 도구는 1단계에만


# ---------- 이미지 구성만으로 서버가 뜬다 ----------
def build_image_layout(dst: Path) -> None:
    """Dockerfile이 하는 일을 흉내 낸다: app · rag · eval을 .dockerignore를 적용해 복사하고 data와 색인 자리를 만든다."""
    for top in ("app", "rag", "eval"):
        for src in (ROOT / top).rglob("*"):
            rel = src.relative_to(ROOT).as_posix()
            if "__pycache__" in rel or src.is_dir() or ignored(rel):
                continue
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / rel)
    (dst / "data").mkdir()
    (dst / "rag" / "chroma_db").mkdir(parents=True, exist_ok=True)


def test_the_files_the_image_would_contain_are_enough_to_start_the_server_without_the_index_or_ollama(tmp_path):
    build_image_layout(tmp_path)
    assert not (tmp_path / "tests").exists() and not (tmp_path / "rag" / "chunks").exists() and not list((tmp_path / "eval").glob("runs*"))
    script = (
        "import sys; sys.path.insert(0, '.')\n"
        "from fastapi.testclient import TestClient\n"
        "from app.api import app\n"
        "c = TestClient(app)\n"
        "h = c.get('/health'); print('HEALTH', h.status_code, h.json()['status'])\n"
        "o = c.post('/chat', json={'question': '대출 금리는 얼마예요?', 'session_id': 's'}); print('DECLINE', o.status_code, o.json()['stop_reason'], o.json()['model_calls'])\n"
        "d = c.post('/chat', json={'question': '중도해지 이율이 어떻게 돼요?', 'session_id': 's2'}); print('NOMODEL', d.status_code)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=180,
                         env={**os.environ, "OLLAMA_HOST": "http://127.0.0.1:9", "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1"})
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line and line.split(" ", 1)[0] in ("HEALTH", "DECLINE", "NOMODEL"))
    assert lines.get("HEALTH") == "200 ok", out.stderr[-800:]
    assert lines.get("DECLINE") == "200 out_of_scope 0"              # 모델도 색인도 없이 코드가 거절한다
    assert lines.get("NOMODEL", "").startswith("503"), lines          # Ollama가 없으면 서버가 죽지 않고 503으로 알린다
    assert (tmp_path / "data").is_dir() and not (tmp_path / "rag" / "chunks").exists()
