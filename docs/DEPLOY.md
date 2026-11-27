# 서버 배포 순서 (Ubuntu 24.04 · Docker · Caddy)

서버 하나에 API · Ollama · Caddy(HTTPS · 요청 속도 제한)를 올리는 순서입니다. Oracle ARM(4 OCPU · 24GB) 기준이고 다른 서버도 같습니다.
이 순서는 Oracle ARM 서버(2 OCPU/12GB)에서 실제로 따라 해 보았고, 결과와 아직 못 본 것은 맨 아래에 적었습니다.

## 0. 준비물
- 서버의 공개 IP, SSH 개인 키 (아래 예는 `~/.ssh/oracle_banking`, 기본 사용자 `ubuntu`)
- 내 맥의 `rag/chunks/chunks.jsonl` (약관 청크, 깃에 없는 파일)
- (선택) HTTPS용 도메인. 없으면 HTTP로 먼저 확인하고 나중에 붙인다 (가입 없이 `sslip.io` 주소를 쓰는 방법은 7절).
- 서버의 보안 목록(Oracle 콘솔)에서 80 · 443이 열려 있어야 한다. 8000은 열지 않는다.

## 1. 접속해서 도구 설치
```bash
ssh -i ~/.ssh/oracle_banking ubuntu@<공개 IP>
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/ohjuhun1119/banking-agent.git && cd banking-agent
bash scripts/bootstrap.sh
exit
```
`scripts/bootstrap.sh`는 Docker와 compose 플러그인을 깔고, 서버 안의 방화벽(iptables)에서 80 · 443을 열고, 메모리가 24GB보다 작으면 스왑 4GB를 만든다. 끝나면 `docker` 그룹이 적용되도록 다시 접속한다.

## 2. 약관 청크 올리기 (내 맥에서)
```bash
ssh -i ~/.ssh/oracle_banking ubuntu@<공개 IP> 'mkdir -p ~/banking-agent/rag/chunks'
scp -i ~/.ssh/oracle_banking rag/chunks/chunks.jsonl ubuntu@<공개 IP>:~/banking-agent/rag/chunks/
```

## 3. 접근 키와 주소 정하기 (서버에서)
```bash
cd ~/banking-agent
cp .env.example .env
sed -i "s/^API_KEY=.*/API_KEY=$(openssl rand -hex 16)/" .env
grep API_KEY .env        # 이 값이 접근 키다. 안전한 곳에 적어 둔다.
```
도메인이 있으면 `.env`의 `SITE_ADDRESS=`에 적는다 (예: `mybank.duckdns.org`). 비워 두면 `:80`(HTTP)이다.

## 3-1. 데이터 폴더 준비 (서버에서, 처음 기동 전에 꼭)
```bash
cd ~/banking-agent
mkdir -p rag/chroma_db data
sudo chown -R 1000:1000 rag/chroma_db data
```
컨테이너 안의 앱은 uid 1000으로 돌아서 색인(`rag/chroma_db`)과 상담 요청 DB(`data/`)에 쓸 수 있어야 한다. 폴더가 없으면 Docker가 root 소유로 만들어 `unable to open database file`로 색인 만들기가 실패한다. Oracle Ubuntu의 `ubuntu` 사용자는 uid가 1001이라 `mkdir`만으로는 부족해서 `chown`까지 한다 (실제 서버에서 겪은 문제).

## 4. 기동
```bash
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
docker compose -f compose.yaml -f compose.prod.yaml logs -f ollama-pull   # 모델(약 6GB) 내려받기 진행. 끝나면 Ctrl+C
```
처음에는 모델을 내려받느라 오래 걸린다. API는 모델 내려받기가 끝나야 시작하고, Caddy는 API가 정상이어야 시작한다.

## 5. 색인 만들기
```bash
docker compose -f compose.yaml -f compose.prod.yaml --profile build run --rm ingest
docker compose -f compose.yaml -f compose.prod.yaml restart api
```
`rag/chunks/chunks.jsonl`을 읽어 색인(62개)을 만든다. 맥의 컨테이너에서는 5초가 걸렸다.

## 6. 확인
```bash
curl -s http://localhost/health                                  # {"status":"ok",...}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost/api/status   # 키 없이 401
KEY=$(grep ^API_KEY .env | cut -d= -f2)
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"question":"정기예금 중도해지하면 이자는 어떻게 돼요?","session_id":"t1"}' http://localhost/chat
```
내 맥에서도 `http://<공개 IP>/health`가 열리는지 본다 (안 열리면 보안 목록이나 서버 방화벽 문제). `/chat`은 CPU라 오래 걸린다 (4 OCPU/24GB에서 4~5분, 아래 '서버에서 실제로 확인한 것').
브라우저는 `http://<공개 IP>` (도메인이면 `https://<도메인>`)를 열고 접근 키를 입력한다.

## 7. 도메인과 HTTPS (선택)
`.env`의 `SITE_ADDRESS`를 도메인으로 바꾸고 `docker compose -f compose.yaml -f compose.prod.yaml up -d caddy`를 다시 실행하면 Caddy가 인증서를 자동으로 받고 갱신한다 (80 · 443이 열려 있어야 한다). `http`로 들어오면 `https`로 자동 이동한다(308).
- **가입 없이 쓰는 방법**: IP를 이름에 넣은 `sslip.io` 주소. 서버 IP가 `168.110.40.247`이면 `SITE_ADDRESS=168-110-40-247.sslip.io`. 이 이름은 그 IP로 연결되고(`python3 -c "import socket; print(socket.gethostbyname('168-110-40-247.sslip.io'))"`로 확인) 서버 쪽 설정은 위 한 줄이 전부다.
- **고정된 내 이름을 쓰려면**: DuckDNS 같은 무료 서비스에 가입해 서브도메인을 만들고 서버 IP를 연결한다 (가입은 직접 해야 하고, 인증서는 서버의 Caddy가 받으므로 DuckDNS 토큰은 필요 없다). 이름 여러 개를 함께 열려면 따옴표로 묶어 쉼표로 잇는다: `SITE_ADDRESS="banking-agent.duckdns.org, 168-110-40-247.sslip.io"` (이름마다 인증서를 따로 받는다).
- **DuckDNS로 확인함** (2026-11-27): `banking-agent.duckdns.org`가 서버 IP로 연결됨을 확인한 뒤 위 두 이름을 함께 넣자 두 이름 모두 Let's Encrypt 인증서가 발급되어 `https`가 정상이다. 서버 IP가 바뀌면 DuckDNS 화면에서 IP를 다시 입력해야 한다.
- **실제로 확인한 것** (2026-11-27, Oracle 서버): `SITE_ADDRESS=168-110-40-247.sslip.io`로 Let's Encrypt 인증서가 한 번에 발급됐다(`certificate obtained successfully`, 유효기간 2027-02-25까지, Caddy가 만료 전에 갱신). `https://.../health` 200, 인증서 검증 정상, `http`는 `https`로 이동. 로그인 쿠키는 HTTPS에서 `Secure`가 붙는다.
- **한계**: `sslip.io`는 외부 무료 서비스라 그 서비스의 DNS에 의존한다. 같은 이름 아래 인증서를 받는 사용자가 많아 발급 한도에 걸릴 수 있다 (Caddy가 다른 발급처로 넘어가 재시도한다). 오래 쓸 서비스라면 내 도메인을 권한다. HSTS(브라우저가 항상 https로만 접속하게 기억시키는 헤더)는 인증서 문제가 생기면 접속이 막힐 수 있어 일부러 넣지 않았다.

## 7-1. 접근 로그와 스캐너 확인
Caddy가 요청마다 접근 로그를 남긴다 (`caddy/Caddyfile`의 `log`). **접근 키(`X-API-Key`) · 쿠키 · `Authorization` · 주소창 키(`?api_key=`)는 로그에서 지운다**: Caddy가 기본으로 가리는 것은 Cookie와 Authorization뿐이라 `X-API-Key`는 따로 지우지 않으면 그대로 남는다. 가짜 비밀 값 5개로 보내 모두 지워지고 경로 · User-Agent는 남는 것을 서버에서 확인했다. 로그는 컨테이너 로그에만 쌓이고 10MB × 3개로 돌려 쓴다(`compose.prod.yaml`). 방문자 IP가 들어 있으니 밖으로 내보내지 않는다.
```bash
docker compose -f compose.yaml -f compose.prod.yaml logs caddy | python3 scripts/access_report.py --me <내 IP>
```
- **확인한 결과** (2026-11-27, 로그를 켠 뒤 약 5분): 외부 요청은 7개 IP에서 19건이었고 경로는 `/` · `/login` · `/favicon.ico` · `/robots.txt`뿐이었다 (`/chat` · `/api` · `/.env` 같은 공격용 경로 없음). Host는 전부 `banking-agent.duckdns.org`였다. 새 인증서를 받은 이름은 공개 인증서 기록(Certificate Transparency)에 올라 크롤러가 몇 분 안에 찾아오는 것으로 보인다 (이 부분은 추정이고 확인하지 못했다). 상태는 303(로그인 이동), 200, 401(키 없이 `favicon.ico` 등), 308(http→https)이었다.
- 접근 키는 무작위 32자리 16진수이고 한 IP당 1분 20번으로 제한해서 무차별 대입은 현실적이지 않다. 로그인 시도(`POST /login`의 401)가 한 IP에서 몰리면 이 보고서의 IP별 요청 수에 나타난다.

## 8. 백업
상담 요청과 트레이스(`data/*.sqlite3`)는 서버에만 있다. 매일 새벽에 복사하고 최근 14개를 남긴다.
```bash
crontab -e     # 아래 한 줄을 추가
0 4 * * * /home/ubuntu/banking-agent/scripts/backup_data.sh
```
`scripts/backup_data.sh`는 같은 서버 안에 복사만 한다. 서버를 지우기 전에는 `backups/` 폴더를 내려받는다.

## 9. 끝낼 때
서버를 지우기 전에 `backups/`를 내려받는다. Oracle은 인스턴스를 **Terminate**하면서 부트 볼륨도 함께 지운다. 지운 뒤 콘솔의 Billing에서 요금이 0인지 본다.

## 서버에서 실제로 확인한 것 (2026-11-26, Oracle ARM 2 OCPU/12GB)
- `scripts/bootstrap.sh` 실행, `compose.yaml` + `compose.prod.yaml` 전체 기동(Ollama가 모델 2개를 내려받음), 색인 만들기, 밖에서 접속 · 접근 키 401 · 계산 API 0.13초 · 범위 밖 거절(모델 호출 0회)이 동작했다.
- **2 OCPU/12GB에서는 `/chat`을 쓸 수 없었다.** 7B 모델이 CPU 2코어에서 글자 생성 3.7토큰/초, 입력 처리 약 10토큰/초이고 모델을 메모리에 올리는 데 92초가 걸렸다. 한 질문에 모델이 두세 번 불려 앱의 모델 제한 시간(180초)을 넘겼다.
- **4 OCPU/24GB로 키운 뒤에는 `/chat`이 끝까지 동작하지만 한 질문에 4~5분이 걸린다.** 글자 생성 7.1토큰/초, 입력 처리 약 19토큰/초. 모델이 올라간 상태에서 서버 안에서 직접 부르면 228초, 밖에서 Caddy를 거치면 330초(HTTP 200, 답 정상)였다. 대화형으로 쓰기엔 너무 느려서 "답이 나온다"를 보이는 정도에 맞다. 더 줄이려면 코어가 더 많은 서버나 더 작은 모델이 필요하다.
- 처음에는 Caddy 대기(300초)에 걸려 밖에서 끊겼다. 그래서 프록시 대기를 600초로 늘렸고(`caddy/Caddyfile`), Ollama가 모델을 내리지 않게 `OLLAMA_KEEP_ALIVE=-1`을 넣었다(`compose.yaml`). 모델을 처음 올리는 데 약 91초가 걸리기 때문이다.

## API 모델로 서버 운영 (2026-11-26, 실제로 적용해 확인함)
CPU 서버에서 로컬 7B 모델은 한 질문에 4~5분이라, 서버만 OpenAI API 모델(`gpt-6-luna`)로 바꿨다. 질문과 도구 결과가 OpenAI로 나가는 설정이라 **서버 `.env`(권한 600)에만** 둔다.
```bash
# 서버 ~/banking-agent/.env에 추가 (키 값은 채팅 · 깃에 올리지 않는다)
LLM_PROVIDER=openai
LLM_ALLOW_EXTERNAL=1          # 두 번째 잠금: 이게 없으면 켜지지 않는다
OPENAI_API_KEY=...
LLM_MODEL=gpt-6-luna
LLM_TEMPERATURE=omit          # 이 모델은 temperature를 받지 않는다
LLM_DAILY_CALL_LIMIT=100      # 하루 모델 호출 상한 (질문 1개 = 약 2회). 넘으면 모델을 부르지 않는다
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```
- 이미지가 `requirements-api.txt`(langchain-openai)를 설치해야 한다 (Dockerfile에 반영). 없으면 켜도 `ImportError`다.
- 확인: `curl http://<공개 IP>/health` → `provider: openai`, `model`, `llm_calls_today`, `llm_daily_limit`.
- **결과**: 약관 질문 7.7초, 계산 질문 4.5초(서버 밖에서 Caddy를 거쳐, 로컬 모델은 330초), 범위 밖 질문 0.1초(모델 호출 없음). 계산 결과 273,304원 일치. 사용량 카운터가 모델 호출 4회를 정확히 셌다.
- **끄기(로컬 모델로 복귀)**: `.env`에서 `LLM_PROVIDER` 줄을 지우고 `docker compose ... up -d`. 데모가 끝나면 `OPENAI_API_KEY` 줄도 지우고 키를 폐기하거나 교체한다.
- 임베딩과 검색은 계속 서버의 로컬 Ollama(bge-m3)가 하고, 모델에 들어가는 입력과 도구 결과만 OpenAI로 나간다 (전화번호 · 이메일 같은 모양은 보내기 전에 가린다).

## 아직 못 본 것
- 4 OCPU/24GB보다 큰 서버(예: 8코어)에서의 `/chat` 응답 시간
- 서버에서 상담 요청 저장(`data/` 쓰기)
