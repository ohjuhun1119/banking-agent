# MCP 서버 설계 (A안 4단계: 이자 계산 도구를 MCP로)

## 왜
이자 계산은 모델이 아니라 파이썬 함수(`app/tools/interest.py`의 `calc_interest`)가 한다는 것이 이 프로젝트의 핵심 결정이다. 이 함수를 MCP 도구로 열면 Claude Desktop · Claude Code 같은 어떤 MCP 클라이언트도 같은 결정적 계산기를 쓸 수 있다.

## 목표 / 비목표 / 하지 말 것
- 목표: `calc_interest`를 MCP 도구 하나로 노출한다(stdio). 호출 결과는 함수를 직접 부른 결과와 같다.
- 비목표: 약관 검색 도구(Ollama · 색인 필요), 원격(HTTP) 노출과 인증, 계산 로직 변경.
- 하지 말 것: 계산을 다시 구현하지 않는다(얇은 껍데기). 검증해 둔 Docker 이미지의 의존성(`requirements.txt`)은 건드리지 않는다.

## 접근 (사용자 승인: A)
- A stdio 서버, 도구 1개 (채택). B 원격 HTTP 마운트와 C 도구 2개는 보안 표면 · 의존성 때문에 제외했다.

## 설계
- `app/mcp_server.py`: `MCPServer("banking-agent-calc")`에 `calc_interest`를 도구로 등록한다. 인자는 모두 선택이고(모르는 값은 도구가 `missing_info`로 알려 준다) 설명문은 함수의 docstring을 그대로 쓴다. 실행: `python -m app.mcp_server`.
- 잘못된 입력은 예외가 아니라 함수가 돌려주는 `status`(`invalid_input` · `missing_info` · ...)로 나간다.
- 의존성: `requirements-mcp.txt`(mcp 2.3.0)에 따로 둔다. CI는 두 파일을 모두 설치해 시험이 건너뛰어지지 않게 한다.
- 시험(`tests/test_mcp_server.py`, 모델 · 색인 불필요): 도구 목록과 스키마, 직접 호출과 같은 결과, 잘못된 입력은 `status`, 실제 stdio 서브프로세스 호출, 도구를 끄면 실패하는 변형 확인.

## 빌더에게 남기는 것 / 위험
- `mcp` 2.x는 1.x와 API가 다르다(서버 클래스 `MCPServer`). 시험으로 확인한다.
- 입력 타입은 JSON 스키마용으로 `principal: int`, `base_rate: float`, 날짜는 문자열이다. 함수가 받는 `1000만원` 같은 문자열 표기는 MCP 스키마에서 열지 않는다.
