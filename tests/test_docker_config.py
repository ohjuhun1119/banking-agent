"""Docker 설정 시험 (12일차). Docker는 실행하지 않는다: 구문 · 경로가 코드의 실제 경로와 맞는지 · 약관 본문이 이미지에 안 들어가는지 ·
이미지에 복사되는 파일만으로 서버가 뜨는지를 확인한다. `compose.host-ollama.yaml`은 macOS에서 실제로 띄워 확인했고(2026-11-02), `compose.yaml`(컨테이너 안의 Ollama)은 확인하지 못했다 (README '검증 상태')."""
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
    assert set(env) == {"OLLAMA_HOST", "TOOL_MODEL", "EMBED_MODEL", "API_KEY", "LLM_PROVIDER", "LLM_ALLOW_EXTERNAL", "LLM_MODEL", "OPENAI_API_KEY", "LLM_TEMPERATURE", "OPENAI_BASE_URL", "LLM_DAILY_CALL_LIMIT", "CHAT_LOG_DB", "CHAT_LOG_DAYS"}
    assert env["LLM_PROVIDER"].endswith(":-ollama}") and env["LLM_ALLOW_EXTERNAL"].endswith(":-}") and env["OPENAI_API_KEY"].endswith(":-}")      # 기본은 로컬 모델이고 API는 꺼져 있다
    assert env["CHAT_LOG_DB"].endswith(":-}")                                      # 상담 기록은 기본이 꺼져 있다
    assert env["API_KEY"].endswith(":-}") and 'os.environ.get("API_KEY")' in (ROOT / "app" / "api.py").read_text(encoding="utf-8")      # 기본은 비어 있어 열려 있고, 코드가 읽는 이름과 같다
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


# ---------- 공개 배포용 Caddy 덮개 (compose.prod.yaml) ----------
def test_the_prod_overlay_adds_caddy_on_80_and_443_and_leaves_the_api_unpublished():
    prod = load("compose.prod.yaml")
    assert set(prod["services"]) == {"caddy"}                                       # api 포트는 건드리지 않는다: 기본 구성의 127.0.0.1만 남는다
    assert sorted(prod["services"]["caddy"]["ports"]) == ["443:443", "80:80"]
    assert prod["services"]["caddy"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_the_caddyfile_proxies_to_the_api_service_and_port_and_limits_the_rate():
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy api:8000" in caddyfile and "EXPOSE 8000" in DOCKERFILE
    assert "api" in load("compose.yaml")["services"]
    assert "order rate_limit before basicauth" in caddyfile and re.search(r"events\s+\d+", caddyfile) and "key {remote_host}" in caddyfile
    assert "request_body" in caddyfile and "max_size" in caddyfile                  # 질문 2000자 제한 밖의 큰 본문을 앞에서 막는다


def test_the_site_address_env_name_matches_between_caddyfile_and_overlay():
    env = load("compose.prod.yaml")["services"]["caddy"]["environment"]
    assert "{$SITE_ADDRESS:" in (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8") and set(env) == {"SITE_ADDRESS"}


def test_the_caddy_image_is_built_with_the_rate_limit_plugin():
    text = (ROOT / "caddy" / "Dockerfile").read_text(encoding="utf-8")
    assert "xcaddy build --with github.com/mholt/caddy-ratelimit" in text and "COPY Caddyfile" in text


# ---------- 배포 준비물: .env.example · 백업 · 서버 설치 스크립트 · 배포 문서 ----------
def test_env_example_lists_exactly_the_variables_the_prod_compose_reads_and_dotenv_is_git_ignored():
    names = {ln.split("=")[0] for ln in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines() if re.match(r"^[A-Z_]+=", ln)}
    assert names == {"API_KEY", "SITE_ADDRESS", "LLM_PROVIDER", "LLM_ALLOW_EXTERNAL", "LLM_MODEL", "OPENAI_API_KEY", "LLM_TEMPERATURE", "OPENAI_BASE_URL", "LLM_DAILY_CALL_LIMIT"}
    assert "SITE_ADDRESS" in load("compose.prod.yaml")["services"]["caddy"]["environment"] and "API_KEY" in load("compose.yaml")["x-app-env"]
    assert ".env" in [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()] and ".env" in DOCKERIGNORE
    assert "backups/" in (ROOT / ".gitignore").read_text(encoding="utf-8")                      # 백업에는 상담 요청이 들어 있다


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 없음")
@pytest.mark.parametrize("name", ["scripts/bootstrap.sh", "scripts/backup_data.sh"])
def test_the_shell_scripts_parse_and_fail_on_the_first_error(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text
    assert subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True).returncode == 0


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None, reason="bash · python3 없음")
def test_the_backup_script_copies_every_sqlite_file_readably_and_keeps_only_the_newest_14(tmp_path):
    import sqlite3
    (tmp_path / "scripts").mkdir(); (tmp_path / "data").mkdir()
    shutil.copy(ROOT / "scripts" / "backup_data.sh", tmp_path / "scripts" / "backup_data.sh")
    for name in ("handoff.sqlite3", "traces.sqlite3"):
        con = sqlite3.connect(tmp_path / "data" / name); con.execute("create table t(x)"); con.execute("insert into t values ('행')"); con.commit(); con.close()
    for i in range(16):                                                                              # 오래된 백업이 이미 16개 쌓여 있다
        (tmp_path / "backups" / f"2020-01-{i + 1:02d}-0000").mkdir(parents=True)
    res = subprocess.run(["bash", str(tmp_path / "scripts" / "backup_data.sh")], capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    dirs = sorted((tmp_path / "backups").iterdir())
    assert len(dirs) == 14 and not (tmp_path / "backups" / "2020-01-01-0000").exists()
    newest = dirs[-1]
    assert sorted(p.name for p in newest.iterdir()) == ["handoff.sqlite3", "traces.sqlite3"]
    assert sqlite3.connect(newest / "handoff.sqlite3").execute("select x from t").fetchone() == ("행",)


def test_the_deploy_doc_names_only_files_and_commands_that_exist():
    doc = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    for path in ("scripts/bootstrap.sh", "scripts/backup_data.sh", ".env.example", "compose.yaml", "compose.prod.yaml", "rag/chunks/chunks.jsonl"):
        assert path in doc, path
    for path in ("scripts/bootstrap.sh", "scripts/backup_data.sh", ".env.example", "compose.yaml", "compose.prod.yaml"):
        assert (ROOT / path).exists(), path
    assert "-f compose.yaml -f compose.prod.yaml" in doc and "--profile build run --rm ingest" in doc
    assert "https://github.com/ohjuhun1119/banking-agent.git" in doc


def test_the_deploy_doc_prepares_the_volume_folders_for_the_container_user_before_the_first_start():
    """서버에서 실제로 겪은 문제: 폴더가 없으면 Docker가 root 소유로 만들어 앱(uid 1000)이 색인 · 상담 요청 DB를 쓰지 못한다. 서버 사용자의 uid가 1000이 아닐 수 있어 chown까지 필요하다."""
    doc = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    prep = "mkdir -p rag/chroma_db data"
    own = "sudo chown -R 1000:1000 rag/chroma_db data"
    assert prep in doc and own in doc and doc.index(prep) < doc.index(own) < doc.index("up -d --build")
    assert re.search(r"useradd --create-home --uid 1000", DOCKERFILE)                              # 문서의 1000은 이미지의 앱 사용자 uid다


# ---------- 서버(CPU)에서 /chat이 끝까지 받아지게: 모델을 계속 올려 두고, 프록시 대기를 넉넉히 ----------
def test_the_container_ollama_keeps_the_model_loaded_because_a_cold_load_takes_over_a_minute():
    """서버(CPU)에서 모델을 메모리에 올리는 데 91초가 걸렸고, 기본 5분 뒤에 내려가 오래 쉰 뒤의 첫 질문이 매번 이 시간을 더했다."""
    env = load("compose.yaml")["services"]["ollama"]["environment"]
    assert str(env["OLLAMA_KEEP_ALIVE"]) == "-1"


def test_the_proxy_waits_longer_than_a_cold_cpu_answer_takes():
    """서버에서 모델이 올라간 상태의 한 질문이 228초, 모델 로딩 91초를 더하면 300초를 넘겨 Caddy가 끊었다."""
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert int(re.search(r"response_header_timeout\s+(\d+)s", caddyfile).group(1)) >= 600


def test_the_image_installs_the_api_model_dependency_so_api_mode_works_on_the_server():
    """서버 컨테이너에서 LLM_PROVIDER=openai를 켜면 langchain-openai가 있어야 한다 (requirements-api.txt를 이미지에 설치하지 않으면 ImportError)."""
    assert "requirements-api.txt" in DOCKERFILE
    assert "langchain-openai" in (ROOT / "requirements-api.txt").read_text(encoding="utf-8")
    assert not ignored("requirements-api.txt")                                                 # .dockerignore가 빌드 문맥에서 빼지 않는다


def test_the_caddyfile_sets_basic_security_headers_for_the_https_site():
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert "X-Content-Type-Options nosniff" in caddyfile and "Referrer-Policy no-referrer" in caddyfile


# ---------- Caddy 접근 로그: 켜되 접근 키 · 쿠키가 남지 않게 ----------
def test_the_caddy_access_log_is_on_and_filters_out_every_credential():
    """접근 키는 X-API-Key 헤더로 오간다: Caddy가 기본으로 가리는 것은 Cookie · Authorization뿐이라 따로 지우지 않으면 로그에 그대로 남는다."""
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"\blog \{", caddyfile) and "output stdout" in caddyfile and "format filter" in caddyfile
    for needle in ("request>headers>X-Api-Key delete", "request>headers>Cookie delete", "request>headers>Authorization delete", "request>headers>Set-Cookie delete", "resp_headers>Set-Cookie delete"):
        assert needle in caddyfile, needle
    assert re.search(r"request>uri query \{[^}]*delete api_key", caddyfile, re.S)              # 주소창 키(?api_key=)도 지운다 (받지는 않지만 시도는 로그에 남는다)


def test_the_caddy_container_log_has_a_size_limit_so_a_scanner_cannot_fill_the_disk():
    opts = load("compose.prod.yaml")["services"]["caddy"]["logging"]["options"]
    assert opts["max-size"] and int(opts["max-file"]) >= 2
