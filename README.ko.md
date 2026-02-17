[English](README.md) | [한국어](README.ko.md)

# Easierlit

[![Python](https://img.shields.io/badge/python-3.10%2B-0ea5e9)](pyproject.toml)
[![Chainlit](https://img.shields.io/badge/chainlit-2.9%20to%203-10b981)](https://docs.chainlit.io)

Easierlit은 Chainlit 위에 얇게 올린 Python 중심 래퍼입니다.
Chainlit의 코어 기능은 유지하면서 워커 루프, 메시지 흐름, 인증, 영속성 보일러플레이트를 줄여줍니다.

## 빠른 링크

- 설치: [Install](#설치)
- 60초 시작: [Quick Start](#quick-start-60초)
- 메서드 계약 문서: [`docs/api-reference.ko.md`](docs/api-reference.ko.md)
- 상세 사용 가이드: [`docs/usage.ko.md`](docs/usage.ko.md)
- 영문 문서: [`README.md`](README.md), [`docs/api-reference.en.md`](docs/api-reference.en.md), [`docs/usage.en.md`](docs/usage.en.md)

## 왜 Easierlit인가

- 런타임 역할 분리가 명확합니다.
- `EasierlitServer`: 메인 프로세스에서 Chainlit 서버 실행
- `EasierlitClient`: 단일 thread 워커에서 `run_func(app)` 실행
- `EasierlitApp`: 입력/출력 큐 브리지
- 운영 기본값이 실용적입니다.
- headless 서버 실행
- sidebar 기본 상태 `open`
- JWT secret 자동관리 (`.chainlit/jwt.secret`)
- 범위 기반 auth cookie 기본값 (`easierlit_access_token_<hash>`)
- 워커 fail-fast 정책
- 영속성 동작이 현실적입니다.
- 기본 SQLite 부트스트랩 (`.chainlit/easierlit.db`)
- 스키마 호환성 복구
- thread CRUD의 SQLite `tags` 정규화

## 아키텍처 한눈에 보기

```text
User UI
  -> Chainlit callbacks (on_message / on_chat_start / ...)
  -> Easierlit runtime bridge
  -> EasierlitApp incoming queue
  -> worker의 run_func(app)
  -> app.* APIs (message + thread CRUD)
  -> runtime dispatcher
  -> realtime session OR data-layer fallback
```

## 설치

```bash
pip install easierlit
```

로컬 개발:

```bash
pip install -e ".[dev]"
```

## Quick Start (60초)

```python
from easierlit import AppClosedError, EasierlitClient, EasierlitServer


def run_func(app):
    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


client = EasierlitClient(run_func=run_func)
server = EasierlitServer(client=client)
server.serve()  # blocking
```

비동기 워커 패턴:

```python
from easierlit import AppClosedError, EasierlitClient, EasierlitServer


async def run_func(app):
    while True:
        try:
            incoming = await app.arecv()
        except AppClosedError:
            break

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


client = EasierlitClient(
    run_func=run_func,
    run_func_mode="auto",  # auto/sync/async
)
server = EasierlitServer(client=client)
server.serve()
```

## 공개 API

```python
EasierlitServer(
    client,
    host="127.0.0.1",
    port=8000,
    root_path="",
    auth=None,
    persistence=None,
    discord=None,
)

EasierlitClient(run_func, worker_mode="thread", run_func_mode="auto")

EasierlitApp.recv(timeout=None)
EasierlitApp.arecv(timeout=None)
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None) -> str
EasierlitApp.add_tool(thread_id, tool_name, content, metadata=None) -> str
EasierlitApp.add_thought(thread_id, content, metadata=None) -> str  # tool_name은 "Reasoning" 고정
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.update_tool(thread_id, message_id, tool_name, content, metadata=None)
EasierlitApp.update_thought(thread_id, message_id, content, metadata=None)  # tool_name은 "Reasoning" 고정
EasierlitApp.delete_message(thread_id, message_id)
EasierlitApp.list_threads(first=20, cursor=None, search=None, user_identifier=None)
EasierlitApp.get_thread(thread_id)
EasierlitApp.get_history(thread_id) -> dict
EasierlitApp.new_thread(name=None, metadata=None, tags=None) -> str
EasierlitApp.update_thread(thread_id, name=None, metadata=None, tags=None)
EasierlitApp.delete_thread(thread_id)
EasierlitApp.close()

EasierlitAuthConfig(username, password, identifier=None, metadata=None)
EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
    storage_provider=<auto S3StorageClient>,
)
EasierlitDiscordConfig(enabled=True, bot_token=None)
```

메서드별 정확한 계약은 아래 문서를 우선 참고하세요.

- `docs/api-reference.ko.md`

각 공개 메서드의 파라미터 제약, 반환, 예외, 부작용, 동시성 주의점, 실패 대응을 정밀하게 다룹니다.

## 인증/영속성 기본값

- JWT secret: `CHAINLIT_AUTH_SECRET`가 없을 때 `.chainlit/jwt.secret` 자동관리
- 인증 cookie: `CHAINLIT_AUTH_COOKIE_NAME`가 있으면 그대로 사용, 없으면 범위 기반 기본값 `easierlit_access_token_<hash>` 사용
- 종료 시 Easierlit이 `CHAINLIT_AUTH_COOKIE_NAME`/`CHAINLIT_AUTH_SECRET`를 이전 값으로 복원
- `auth=None`이면 기본 인증 자동 활성
- `auth=None`일 때 인증 자격증명 해석 순서:
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD` (둘 다 함께 설정 필요)
- 폴백 `admin` / `admin` (경고 로그 출력)
- 기본 persistence: `.chainlit/easierlit.db` (SQLite, thread/텍스트 step 저장)
- 기본 파일/이미지 저장소: `S3StorageClient`가 항상 기본 활성화
- 기본 S3 bucket: `EASIERLIT_S3_BUCKET` 또는 `BUCKET_NAME`, 미설정 시 `easierlit-default`
- SQLite 스키마 불일치 시 백업 후 재생성
- sidebar 기본 상태는 `open`으로 강제
- `serve()` 실행 중 Discord 연동은 기본 비활성입니다(`DISCORD_BOT_TOKEN`이 기존에 있어도 비활성).

Thread History 표시 조건(Chainlit 정책):

- `requireLogin=True`
- `dataPersistence=True`

Easierlit에서 일반적인 구성:

- `auth=None`, `persistence=None`으로 기본 인증/영속성 활성 사용
- 기본 계정을 쓰지 않으려면 `EASIERLIT_AUTH_USERNAME`/`EASIERLIT_AUTH_PASSWORD` 설정
- 기본 S3 bucket 이름을 바꾸려면 `EASIERLIT_S3_BUCKET`(또는 `BUCKET_NAME`) 설정
- 다른 저장소를 쓰려면 `persistence=EasierlitPersistenceConfig(storage_provider=...)` 전달
- 또는 `auth=EasierlitAuthConfig(...)`를 명시 전달

Discord 봇 구성:

- `discord=None`이면 Discord 연동 비활성
- `discord=EasierlitDiscordConfig(...)`를 전달하면 기본 활성
- 토큰 우선순위: `EasierlitDiscordConfig.bot_token` 우선, `DISCORD_BOT_TOKEN` 폴백
- Easierlit은 자체 Discord bridge로 동작하며 Chainlit Discord handler를 런타임에 monkeypatch하지 않음
- `serve()` 중에는 Chainlit의 `DISCORD_BOT_TOKEN` 경로를 비활성으로 유지하고, 종료 시 기존 env 값을 복원
- 활성화 상태에서 비어 있지 않은 토큰을 찾지 못하면 `serve()`가 `ValueError`를 발생

## Message / Thread 작업

Message API:

- `app.add_message(...)`
- `app.add_tool(...)`
- `app.add_thought(...)`
- `app.update_message(...)`
- `app.update_tool(...)`
- `app.update_thought(...)`
- `app.delete_message(...)`

Thread API:

- `app.list_threads(...)`
- `app.get_thread(thread_id)`
- `app.get_history(thread_id)`
- `app.new_thread(...)`
- `app.update_thread(...)`
- `app.delete_thread(thread_id)`

동작 핵심:

- `app.add_message(...)`는 생성된 `message_id`를 반환
- `app.add_tool(...)`은 도구 호출 step을 생성하며 도구명은 step author/name으로 표시됩니다.
- `app.add_thought(...)`는 동일한 도구 호출 경로를 사용하고 도구명은 `Reasoning`으로 고정됩니다.
- `app.get_history(...)`은 thread 메타데이터와 순서 보존 `items` 단일 목록을 반환합니다.
- `app.new_thread(...)`는 고유한 `thread_id`를 자동 생성하고 반환
- `app.update_thread(...)`는 기존 thread만 수정
- auth 설정 시 `app.new_thread(...)`/`app.update_thread(...)` 모두 소유자를 자동 귀속
- SQLite SQLAlchemyDataLayer 경로에서 thread `tags` 자동 정규화
- active websocket session이 없어도 내부 HTTP-context fallback으로 data-layer message CRUD 수행

## 워커 실패 정책

Easierlit은 워커 크래시에 대해 fail-fast 정책을 사용합니다.

- `run_func` 예외 발생 시 서버 종료 트리거
- 가능하면 UI에 요약 메시지 표시
- 전체 traceback은 서버 로그에 기록

## Chainlit Message vs Tool-call

Chainlit은 step type으로 메시지와 도구/실행을 구분합니다.

Message step:

- `user_message`
- `assistant_message`
- `system_message`

Tool/run 계열:

- `tool`, `run`, `llm`, `embedding`, `retrieval`, `rerank`, `undefined`

Easierlit 매핑:

- `app.add_message(...)` -> `assistant_message`
- `app.add_tool(...)` / `app.update_tool(...)` -> `tool`
- `app.add_thought(...)` / `app.update_thought(...)` -> `tool` (`Reasoning` 고정)
- `app.delete_message(...)`는 `message_id` 기준으로 message/tool/thought를 공통 삭제

## 예제 맵

- `examples/minimal.py`: 기본 echo bot
- `examples/custom_auth.py`: 단일 계정 인증
- `examples/discord_bot.py`: Discord 봇 설정과 토큰 우선순위
- `examples/thread_crud.py`: thread list/get/update/delete
- `examples/thread_create_in_run_func.py`: `run_func`에서 thread 생성
- `examples/step_types.py`: tool/thought step 생성/수정/삭제 예제

## 문서 맵

- 메서드 API 계약(EN): `docs/api-reference.en.md`
- 메서드 API 계약(KO): `docs/api-reference.ko.md`
- 상세 사용 가이드(EN): `docs/usage.en.md`
- 상세 사용 가이드(KO): `docs/usage.ko.md`

## 마이그레이션 노트

API 변경:

- `new_thread(thread_id=..., ...)` -> `thread_id = new_thread(...)`
- `send(...)` 제거
- 메시지 표준 API를 `add_message(...)`로 전환
- 도구/추론 API 추가: `add_tool(...)`, `add_thought(...)`, `update_tool(...)`, `update_thought(...)`
