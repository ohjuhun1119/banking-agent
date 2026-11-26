# 서버 배포 순서 (Ubuntu 24.04 · Docker · Caddy)

서버 하나에 API · Ollama · Caddy(HTTPS · 요청 속도 제한)를 올리는 순서입니다. Oracle ARM(4 OCPU · 24GB) 기준이고 다른 서버도 같습니다.
이 순서는 Oracle ARM 서버(2 OCPU/12GB)에서 실제로 따라 해 보았고, 결과와 아직 못 본 것은 맨 아래에 적었습니다.

## 0. 준비물
- 서버의 공개 IP, SSH 개인 키 (아래 예는 `~/.ssh/oracle_banking`, 기본 사용자 `ubuntu`)
- 내 맥의 `rag/chunks/chunks.jsonl` (약관 청크, 깃에 없는 파일)
- (선택) HTTPS용 도메인. 없으면 HTTP로 먼저 확인하고 나중에 붙인다.
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
도메인의 A 레코드를 공개 IP로 맞추고 `.env`의 `SITE_ADDRESS`를 그 도메인으로 바꾼 뒤 `docker compose -f compose.yaml -f compose.prod.yaml up -d`를 다시 실행한다. Caddy가 인증서를 자동으로 받는다 (80 · 443이 열려 있어야 한다).

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

## 아직 못 본 것
- 도메인 인증서 자동 발급
- 4 OCPU/24GB보다 큰 서버(예: 8코어)에서의 `/chat` 응답 시간
- 서버에서 상담 요청 저장(`data/` 쓰기)
