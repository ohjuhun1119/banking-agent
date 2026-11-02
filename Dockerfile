# syntax=docker/dockerfile:1
# 금융 상담 Agent API 이미지. 약관 원문 · 청크 · Chroma 색인 · 상담 요청 DB는 이미지에 넣지 않는다 (.dockerignore, 볼륨으로 연결).
# Docker 빌드는 이 레포에서 직접 실행해 보지 못했다 (README '검증 상태' 참고). 설치 파일이 없는 패키지가 있으면 PYTHON_VERSION을 바꿔 본다.
ARG PYTHON_VERSION=3.14

# ---- 1단계: 의존성 설치 (미리 빌드된 설치 파일이 없는 패키지를 대비해 빌드 도구를 여기에만 둔다)
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- 2단계: 실행 이미지
FROM python:${PYTHON_VERSION}-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv
RUN useradd --create-home --uid 1000 app
WORKDIR /app
# 코드만 복사한다: app/api.py가 eval/tool_cases.py · eval/tool_check.py를 가져오므로 eval도 필요하다 (결과 파일 eval/runs 등은 .dockerignore로 제외)
COPY --chown=app:app app ./app
COPY --chown=app:app rag ./rag
COPY --chown=app:app eval ./eval
# 볼륨으로 연결할 자리: 색인(rag/chroma_db)과 상담 요청 · 트레이스 DB(data)
RUN mkdir -p /app/data /app/rag/chroma_db && chown -R app:app /app/data /app/rag/chroma_db
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
