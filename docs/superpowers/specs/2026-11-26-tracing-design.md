# 트레이스 연결 설계 (A안 5단계: Langfuse를 내 컴퓨터에서 직접 실행)

## 왜
지금 트레이스(`app/traces.py`, SQLite)는 도구 이름 · 인자 · 상태 · 시간만 남긴다. 모델 호출과 그래프 단계(입구 판별 → agent ⇄ tools → 답변 점검)의 입출력, 단계별 시간은 보이지 않는다. Langfuse는 LangGraph 실행 전체를 트리로 보여 준다.

## 목표 / 비목표 / 하지 말 것
- 목표: 켜면 질문마다 그래프 전체가 로컬 Langfuse에 트레이스로 올라온다. 실제 질문으로 올라온 것을 Langfuse API로 확인한다.
- 비목표: 외부 클라우드(LangSmith · Langfuse Cloud) 연결, 평가 · 프롬프트 관리 기능, 운영 서버(Oracle)에서 켜기.
- 하지 말 것: **데이터가 컴퓨터 밖으로 나가게 하지 않는다.** 기본은 꺼짐이고, 호스트가 로컬이 아니면 켜지 않는다. 트레이스 실패가 답변을 막으면 안 된다.

## 접근 (사용자 승인: B 로컬 Langfuse)
- A LangSmith 클라우드(데이터가 나감)와 Langfuse Cloud는 제외했다.

## 설계
- `compose.langfuse.yaml` + `scripts/langfuse_env.sh`: 공식 구성에서 필요한 컨테이너(웹 · 워커 · ClickHouse · MinIO · Redis · Postgres)만 남기고 모든 포트를 127.0.0.1에만 연다. 익명 사용 통계(`TELEMETRY_ENABLED`)를 끄고 가입을 막는다. 계정 · 프로젝트 · 키는 처음 기동 때 `.env.langfuse`의 `LANGFUSE_INIT_*` 값으로 자동 생성된다(무작위 비밀값, 깃 제외).
- `app/tracing.py`: `langfuse_config(session_id) -> dict`. 켜져 있으면 `{"callbacks": [CallbackHandler()], "metadata": {...}}`, 아니면 `{}`.
  - 켜는 조건: `LANGFUSE_PUBLIC_KEY` · `LANGFUSE_SECRET_KEY`가 있고 `LANGFUSE_TRACING`이 `0`이 아님.
  - 호스트(`LANGFUSE_HOST`, 기본 `http://localhost:3000`)가 `localhost` · `127.0.0.1` · `::1` · `host.docker.internal` · `langfuse-web`이 아니면 `LANGFUSE_ALLOW_REMOTE=1`이 없는 한 켜지 않고 경고한다.
  - `langfuse` 패키지가 없거나 핸들러를 못 만들면 꺼진 채로 답은 그대로 나간다(경고만).
- `Agent.ask`: 이미 있는 LangSmith용 실행 설정(`run_name` · `tags` · `metadata`)에 `langfuse_config` 결과를 합친다. 세션 ID는 `langfuse_session_id`로 보낸다.
- 의존성: `requirements-tracing.txt`(langfuse)에 따로 둔다. Docker 이미지의 `requirements.txt`는 건드리지 않는다.
- 시험(`tests/test_tracing.py`, Langfuse 서버 불필요): 기본 꺼짐, 키 하나만 있으면 꺼짐, `LANGFUSE_TRACING=0`, 원격 호스트 거부와 허용 스위치, 로컬 호스트 허용, 핸들러 실패가 답을 막지 않음, 켜면 그래프 실행이 콜백에 전달되고 답은 꺼졌을 때와 같음.
- 실제 확인: 로컬 Langfuse를 띄우고 시험 질문으로 올라온 트레이스를 Langfuse 공개 API(`/api/public/traces`)로 조회한다.

## 위험
- Langfuse SDK 5.2.0과 서버 4.x의 호환은 실제로 보내 봐야 안다.
- 트레이스에는 질문 · 답 · 도구 결과(약관 본문 포함)가 그대로 들어간다. 로컬이라 나가지는 않지만 `langfuse-data`(볼륨)에 쌓인다.
- 이미지는 합쳐서 수 GB다.
