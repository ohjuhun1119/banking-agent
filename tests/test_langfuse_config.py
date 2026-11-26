"""compose.langfuse.yaml · scripts/langfuse_env.sh 시험: 데이터가 컴퓨터 밖으로 나가지 않는 설정이 지켜지는지. Docker는 실행하지 않는다."""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.langfuse.yaml"


def load():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_every_published_port_is_bound_to_localhost_only():
    ports = [p for svc in load()["services"].values() for p in svc.get("ports", [])]
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports), ports


def test_telemetry_and_signup_are_off():
    cfg = load()
    assert str(cfg["x-langfuse-env"]["TELEMETRY_ENABLED"]).lower() == "false"           # Langfuse가 기본으로 보내는 익명 사용 통계
    assert str(cfg["services"]["langfuse-web"]["environment"]["AUTH_DISABLE_SIGNUP"]).lower() == "true"


def test_every_secret_is_required_from_the_env_file_with_no_default_password():
    text = COMPOSE.read_text(encoding="utf-8")
    for name in ("POSTGRES_PASSWORD", "CLICKHOUSE_PASSWORD", "MINIO_ROOT_PASSWORD", "REDIS_AUTH", "SALT", "ENCRYPTION_KEY", "NEXTAUTH_SECRET",
                 "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_ADMIN_PASSWORD"):
        assert re.search(r"\$\{%s:\?" % name, text), name                                # 비어 있으면 기동하지 않는다 (공식 구성의 mysalt · postgres 같은 기본값 없음)
    assert not re.search(r"\$\{[A-Z_]+:-(mysalt|postgres|myredissecret|miniosecret|clickhouse)\}", text)


def test_the_env_script_generates_exactly_the_variables_the_compose_requires():
    required = set(re.findall(r"\$\{([A-Z_]+):\?", COMPOSE.read_text(encoding="utf-8")))
    body = re.search(r"<<EOF\n(.*?)\nEOF", (ROOT / "scripts" / "langfuse_env.sh").read_text(encoding="utf-8"), re.S).group(1)       # 파일에 쓰는 줄만 (OUT= 같은 스크립트 변수 제외)
    generated = set(re.findall(r"^([A-Z_]+)=", body, re.M))
    assert required == generated


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None, reason="bash · openssl 없음")
def test_the_env_script_writes_a_private_file_once_and_never_overwrites_it(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "langfuse_env.sh", tmp_path / "scripts" / "langfuse_env.sh")
    run = lambda: subprocess.run(["bash", str(tmp_path / "scripts" / "langfuse_env.sh")], capture_output=True, text=True, cwd=tmp_path)
    assert run().returncode == 0
    out = tmp_path / ".env.langfuse"
    first = out.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    values = dict(line.split("=", 1) for line in first.splitlines())
    assert all(len(v) >= 24 for k, v in values.items() if k != "LANGFUSE_ADMIN_PASSWORD") and values["LANGFUSE_PUBLIC_KEY"].startswith("pk-lf-")
    assert run().returncode == 0 and out.read_text(encoding="utf-8") == first                 # 두 번째 실행은 키를 바꾸지 않는다


def test_the_generated_secrets_never_reach_git_or_the_image():
    assert ".env.langfuse" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env.langfuse" in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
