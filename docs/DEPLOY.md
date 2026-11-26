# 서버 배포 순서 (Ubuntu 24.04 · Docker · Caddy)

서버 하나에 API · Ollama · Caddy(HTTPS · 요청 속도 제한)를 올리는 순서입니다. Oracle ARM(4 OCPU · 24GB) 기준이고 다른 서버도 같습니다.
**이 순서는 실제 서버에서 처음부터 끝까지 돌려 보지 못했습니다.** 맥에서는 각 부분(이미지 빌드, 색인 만들기, Caddy, 접근 키)을 따로 확인했고, 서버에서 처음 확인될 부분은 맨 아래에 적었습니다.

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
내 맥에서도 `http://<공개 IP>/health`가 열리는지 본다 (안 열리면 보안 목록이나 서버 방화벽 문제). `/chat`은 CPU라 수십 초 걸린다.
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

## 서버에서 처음 확인될 부분 (아직 못 본 것)
- `scripts/bootstrap.sh`의 실제 실행 (문법만 확인함)
- 컨테이너 안의 Ollama가 모델을 내려받는 `compose.yaml` 전체 흐름
- 도메인 인증서 자동 발급
- ARM 서버에서의 `/chat` 응답 시간 (맥의 에뮬레이션 수치와 다르다)
