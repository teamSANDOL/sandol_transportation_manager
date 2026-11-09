# sandol_transportation_manager

산돌이 교통정보 서비스다. 정류장 12곳의 실시간 버스 도착 정보를 GBIS(경기도 버스정보)에서 받아 준다.
FastAPI, PostgreSQL 15, Redis 7.4 를 docker compose 로 실행한다. PostgreSQL 은 정류장과 노선을, Redis 는 도착정보 cache, 키별 quota, rate limit 카운터를 둔다.

## 실행

```bash
cp .env.example .env   # POSTGRES_PASSWORD 와 DATA_GO_KR_KEY1 을 채운다
docker compose up --build
curl http://127.0.0.1:8000/health
```

컨테이너가 시작할 때 `python -m app.init_db` 가 `app/models.py` 로 테이블을 만들고, migration 으로 정류장과 노선 seed 만 넣는다.

## API 키

GBIS 는 공공데이터포털(data.go.kr)의 경기도 버스도착정보 API 로 받는다.

- 발급: 공공데이터포털에서 활용신청을 하고 인증키를 받는다. 일일 호출 한도는 키마다 10,000 이다.
- 설정: `.env` 의 `DATA_GO_KR_KEY1` ~ `KEY4` 에 넣는다. 비었거나 없는 번호는 건너뛴다.
- 사용: 1번부터 차례로 쓴다. 남은 quota 가 `BUS_QUOTA_THRESHOLD`(기본 10) 이하이면 다음 키로 넘어간다. 키가 모두 소진되면 cache 가 있을 때 stale 로, 없으면 503 으로 응답한다.
- 보관: 키는 `.env` 에만 둔다. `.env` 는 commit 과 이미지에 들어가지 않는다. 키 값은 로그, 응답, Redis 에 남기지 않고 번호만 남긴다.

## 개발

Python 3.11 과 uv 를 쓴다.

```bash
uv sync --locked
uv run pytest
uv run ruff check .
```

DB 테스트는 `TEST_DATABASE_URL` 이 있을 때만 돈다. 없으면 건너뛴다. 테스트가 이 DB 의 테이블을 지우고 다시 만들기 때문에 개발 DB 를 가리키지 않는다. 테스트는 실제 HTTP 전송 계층을 막아서 GBIS 로 나가지 못한다.

```bash
docker run -d --rm --name tm-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=tmtest -p 127.0.0.1:55432:5432 postgres:15-bookworm
TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55432/tmtest uv run pytest
```

CI(`.github/workflows/ci.yml`)는 PR 마다 ruff 와 pytest 를 DB 포함으로 돌린다.

## API

| 경로 | 내용 |
|---|---|
| `/stops`, `/stops/{n}` | 정류장 목록과 상세 |
| `/stops/{n}/arrivals` | 정류장의 도착 예정 |
| `/stops/{n}/routes/{route}/arrival` | 노선 하나의 도착 예정(항목조회) |
| `/stops/{n}/stations` | 노선도용 정류소 순서 |
| `/health` | 상태 확인 |

`DEV_FRONTEND_MODE=1` 일 때만 다음 경로를 등록한다. 운영에서는 0 으로 둔다.

| 경로 | 내용 |
|---|---|
| `/demo` | 테스트 화면. 왼쪽 사이드바에서 정류장을 고르면 오른쪽에 키오스크 화면이 뜬다 |
| `/demo/stops/{정류소 번호}` | 같은 화면에 그 정류장이 선택된 상태 |
| `/status` | 조회 방식과 키별 남은 quota. gateway 에 열지 않는다 |

`/demo` 화면은 열어 두는 동안 15초마다 API 를 부른다(탭이 숨겨지면 멈춘다). cache TTL(기본 40초)이 지날 때마다 GBIS 를 한 번 부르므로 쓰지 않을 때는 탭을 닫는다.

gateway 는 경로를 그대로 넘기므로 운영에 올리기 전에 `/<prefix>` 아래로 옮길지 정해야 한다. 아직 정하지 않았다.

## GBIS 호출

- GBIS 는 `app/gbis.py` 한 곳에서만 부른다. 입력 검증, rate limit, cache, 키별 quota 확인을 거친 뒤에만 나간다.
- 도착정보는 정류장별로 Redis 에 cache 한다. 같은 정류장의 동시 요청은 호출 한 번으로 합친다. GBIS 가 실패하면 만료된 cache 를 stale 로 준다.
- 기본값은 요청 시 조회(`BUS_FETCH_ON_DEMAND_ONLY=1`)다. 자동 조회를 켜려면 0 으로 바꾸고 `BUS_POLL_*` 를 정한다.
- 클라이언트 IP 당 분당 요청 수를 `BUS_RATE_LIMIT_PER_MINUTE`(기본 30)로 제한한다. IP 는 gateway 가 넣는 `X-Real-IP` 를 쓰므로 gateway 가 이 헤더를 `$remote_addr` 로 덮어써야 한다. 서비스 포트는 127.0.0.1 에만 열려 있다.

## 정류장 데이터

정류장 seed 는 `migrations/seed_stops.sql` 이다. 정류장 정보는 거의 바뀌지 않아 동기화 없이 SQL 로 관리한다. `0001` 은 한 번 적용되면 다시 돌지 않는다. 운영 DB 에서 정류장이나 노선을 바꿀 때는 볼륨을 지우지 말고 새 migration(`0002`)을 추가한다.
