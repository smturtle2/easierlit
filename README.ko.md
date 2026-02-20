[English](README.md) | [한국어](README.ko.md)

# Easierlit

[![Python](https://img.shields.io/badge/python-3.13%2B-0ea5e9)](pyproject.toml)
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
- `EasierlitClient`: `on_message(app, incoming)` 기반 메시지 디스패처
- `EasierlitApp`: 출력 명령(message/thread CRUD) 브리지
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
  -> EasierlitClient incoming dispatcher
  -> 메시지별 on_message(app, incoming) worker(thread)
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
from easierlit import EasierlitClient, EasierlitServer


def on_message(app, incoming):
    app.add_message(
        thread_id=incoming.thread_id,
        content=f"Echo: {incoming.content}",
        author="EchoBot",
    )


client = EasierlitClient(on_message=on_message)
server = EasierlitServer(client=client)
server.serve()  # blocking
```

선택적 백그라운드 run_func 패턴:

```python
import time

from easierlit import EasierlitClient, EasierlitServer


def on_message(app, incoming):
    app.add_message(incoming.thread_id, f"Echo: {incoming.content}", author="EchoBot")


def run_func(app):
    while not app.is_closed():
        # 선택적 백그라운드 워커; 입력 polling은 하지 않습니다.
        time.sleep(0.2)


client = EasierlitClient(
    on_message=on_message,
    run_funcs=[run_func],  # optional
    run_func_mode="auto",  # auto/sync/async
)
server = EasierlitServer(client=client)
server.serve()
```

이미지 element 예시(Markdown 없이):

```python
from chainlit.element import Image


image = Image(name="diagram.png", path="/absolute/path/diagram.png")
app.add_message(
    thread_id=incoming.thread_id,
    content="이미지 첨부",
    elements=[image],
)
```

외부(in-process) 입력 enqueue 예시:

```python
message_id = app.enqueue(
    thread_id="thread-external",
    content="hello from external integration",
    session_id="webhook-1",
    author="Webhook",
)
```

## 공개 API

```python
EasierlitServer(
    client,
    host="127.0.0.1",
    port=8000,
    root_path="",
    max_outgoing_workers=4,
    auth=None,
    persistence=None,
    discord=None,
)

EasierlitClient(
    on_message,
    run_funcs=None,
    worker_mode="thread",
    run_func_mode="auto",
    max_message_workers=64,
)

EasierlitApp.start_thread_task(thread_id)
EasierlitApp.end_thread_task(thread_id)
EasierlitApp.is_thread_task_running(thread_id) -> bool
EasierlitApp.enqueue(thread_id, content, session_id="external", author="User", message_id=None, metadata=None, elements=None, created_at=None) -> str
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None, elements=None) -> str
EasierlitApp.add_tool(thread_id, tool_name, content, metadata=None, elements=None) -> str
EasierlitApp.add_thought(thread_id, content, metadata=None, elements=None) -> str  # tool_name은 "Reasoning" 고정
EasierlitApp.send_to_discord(thread_id, content) -> bool
EasierlitApp.update_message(thread_id, message_id, content, metadata=None, elements=None)
EasierlitApp.update_tool(thread_id, message_id, tool_name, content, metadata=None, elements=None)
EasierlitApp.update_thought(thread_id, message_id, content, metadata=None, elements=None)  # tool_name은 "Reasoning" 고정
EasierlitApp.delete_message(thread_id, message_id)
EasierlitApp.list_threads(first=20, cursor=None, search=None, user_identifier=None)
EasierlitApp.get_thread(thread_id)
EasierlitApp.get_messages(thread_id) -> dict
EasierlitApp.new_thread(name=None, metadata=None, tags=None) -> str
EasierlitApp.update_thread(thread_id, name=None, metadata=None, tags=None)
EasierlitApp.delete_thread(thread_id)
EasierlitApp.reset_thread(thread_id)
EasierlitApp.close()

EasierlitAuthConfig(username, password, identifier=None, metadata=None)
EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
    storage_provider=<auto LocalFileStorageClient>,
)
EasierlitDiscordConfig(enabled=True, bot_token=None)
```

메서드별 정확한 계약은 아래 문서를 우선 참고하세요.

- `docs/api-reference.ko.md`

각 공개 메서드의 파라미터 제약, 반환, 예외, 부작용, 동시성 주의점, 실패 대응을 정밀하게 다룹니다.

## 인증/영속성 기본값

- JWT secret: `CHAINLIT_AUTH_SECRET`가 32바이트 미만이면 해당 실행에서 안전한 시크릿으로 자동 대체하고, 미설정이면 `.chainlit/jwt.secret`를 자동 관리
- 인증 cookie: `CHAINLIT_AUTH_COOKIE_NAME`가 있으면 그대로 사용, 없으면 범위 기반 기본값 `easierlit_access_token_<hash>` 사용
- 종료 시 Easierlit이 `CHAINLIT_AUTH_COOKIE_NAME`/`CHAINLIT_AUTH_SECRET`를 이전 값으로 복원
- `UVICORN_WS_PROTOCOL`이 비어 있으면 `websockets-sansio`를 기본값으로 사용
- `auth=None`이면 기본 인증 자동 활성
- `auth=None`일 때 인증 자격증명 해석 순서:
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD` (둘 다 함께 설정 필요)
- 폴백 `admin` / `admin` (경고 로그 출력)
- 기본 persistence: `.chainlit/easierlit.db` (SQLite, thread/텍스트 step 저장)
- 기본 파일/이미지 저장소: `LocalFileStorageClient`가 항상 기본 활성화
- 기본 로컬 저장 경로: `<CHAINLIT_APP_ROOT 또는 cwd>/public/easierlit`
- `LocalFileStorageClient(base_dir=...)`는 `~` 경로 확장을 지원합니다.
- 상대 `base_dir`는 `<CHAINLIT_APP_ROOT 또는 cwd>/public` 하위로 해석됩니다.
- `public` 밖 절대 `base_dir`도 직접 사용할 수 있습니다.
- 로컬 파일/이미지는 `/easierlit/local/{object_key}` 경로로 서빙됩니다.
- 로컬 파일/이미지 URL은 `CHAINLIT_PARENT_ROOT_PATH` + `CHAINLIT_ROOT_PATH` prefix를 함께 반영합니다.
- SQLite 스키마 불일치 시 백업 후 재생성
- sidebar 기본 상태는 `open`으로 강제
- `discord=EasierlitDiscordConfig(...)`를 전달하지 않으면 Discord bridge는 기본 비활성입니다.

Thread History 표시 조건(Chainlit 정책):

- `requireLogin=True`
- `dataPersistence=True`

Easierlit에서 일반적인 구성:

- `auth=None`, `persistence=None`으로 기본 인증/영속성 활성 사용
- 기본 계정을 쓰지 않으려면 `EASIERLIT_AUTH_USERNAME`/`EASIERLIT_AUTH_PASSWORD` 설정
- 로컬 저장소 경로/동작을 바꾸려면 `persistence=EasierlitPersistenceConfig(storage_provider=LocalFileStorageClient(...))` 전달
- 또는 `auth=EasierlitAuthConfig(...)`를 명시 전달

Discord 봇 구성:

- `discord=None`이면 Discord 연동 비활성
- `discord=EasierlitDiscordConfig(...)`를 전달하면 기본 활성
- 토큰 우선순위: `EasierlitDiscordConfig.bot_token` 우선, `DISCORD_BOT_TOKEN` 폴백
- Discord 응답은 명시적으로 `app.send_to_discord(...)`를 호출해 전송
- Discord 유입 thread는 Thread History 노출 안정성을 위해 runtime auth 사용자로 우선 귀속
- Easierlit은 자체 Discord bridge로 동작하며 Chainlit Discord handler를 런타임에 monkeypatch하지 않음
- `serve()` 중에도 Easierlit은 `DISCORD_BOT_TOKEN`을 비우지 않으며, env 값은 그대로 유지됨
- 활성화 상태에서 비어 있지 않은 토큰을 찾지 못하면 `serve()`가 `ValueError`를 발생

## Message / Thread 작업

Message API:

- `app.add_message(...)`
- `app.add_tool(...)`
- `app.add_thought(...)`
- `app.send_to_discord(...)`
- `app.update_message(...)`
- `app.update_tool(...)`
- `app.update_thought(...)`
- `app.delete_message(...)`

Thread API:

- `app.list_threads(...)`
- `app.get_thread(thread_id)`
- `app.get_messages(thread_id)`
- `app.new_thread(...)`
- `app.update_thread(...)`
- `app.delete_thread(thread_id)`
- `app.reset_thread(thread_id)`

Thread 작업 상태 API:

- `app.start_thread_task(thread_id)`
- `app.end_thread_task(thread_id)`
- `app.is_thread_task_running(thread_id)`

동작 핵심:

- `app.add_message(...)`는 생성된 `message_id`를 반환
- `app.enqueue(...)`는 입력을 `user_message`로 UI/data layer에 반영하고 `on_message`로 디스패치
- `app.add_tool(...)`은 도구 호출 step을 생성하며 도구명은 step author/name으로 표시됩니다.
- `app.add_thought(...)`는 동일한 도구 호출 경로를 사용하고 도구명은 `Reasoning`으로 고정됩니다.
- `app.add_message(...)`/`app.add_tool(...)`/`app.add_thought(...)`는 Discord로 자동 전송되지 않습니다.
- `app.send_to_discord(...)`는 Discord에만 전송하고 `True/False`를 반환합니다.
- `app.start_thread_task(...)`는 특정 thread를 작업 중(UI indicator) 상태로 표시합니다.
- `app.end_thread_task(...)`는 해당 thread의 작업 중(UI indicator) 상태를 해제합니다.
- `app.is_thread_task_running(...)`는 thread 작업 중 상태를 반환합니다.
- Easierlit은 각 `on_message` 실행 구간에서 thread 작업 상태를 자동으로 관리합니다.
- async awaitable 실행은 역할별로 분리됩니다.
- `run_func` awaitable은 전용 runner loop에서 실행됩니다.
- `on_message` awaitable은 `min(max_message_workers, 8)` 크기의 thread-aware runner pool에서 실행됩니다.
- 같은 `thread_id`는 동일한 `on_message` runner lane에 고정됩니다.
- runtime outgoing dispatcher는 thread-aware 병렬 lane을 사용합니다. 같은 `thread_id`의 순서는 유지되지만 thread 간 전역 outgoing 순서는 보장하지 않습니다.
- CPU-bound Python 핸들러는 여전히 GIL을 공유하므로, CPU 격리가 필요하면 프로세스 단위 offloading이 필요합니다.
- `app.get_messages(...)`은 thread 메타데이터와 순서 보존 `messages` 단일 목록을 반환합니다.
- `app.get_messages(...)`은 `user_message`/`assistant_message`/`system_message`/`tool`만 포함하고 run 계열 step은 제외합니다.
- `app.get_messages(...)`은 `thread["elements"]`를 `forId` 별칭(`forId`/`for_id`/`stepId`/`step_id`) 기준으로 각 message에 매핑합니다.
- `app.get_messages(...)`은 이미지/파일 source 추적을 위해 `elements[*].has_source`와 `elements[*].source`(`url`/`path`/`bytes`/`objectKey`/`chainlitKey`)를 추가합니다.
- `app.new_thread(...)`는 고유한 `thread_id`를 자동 생성하고 반환
- `app.update_thread(...)`는 기존 thread만 수정
- `app.delete_thread(...)`/`app.reset_thread(...)`는 해당 thread 작업 상태를 자동 해제
- auth 설정 시 `app.new_thread(...)`/`app.update_thread(...)` 모두 소유자를 자동 귀속
- SQLite SQLAlchemyDataLayer 경로에서 thread `tags` 자동 정규화
- active websocket session이 없어도 내부 HTTP-context fallback으로 data-layer message CRUD 수행
- 공개 `lock/unlock` API는 제공하지 않습니다.

## 워커 실패 정책

Easierlit은 워커 크래시에 대해 fail-fast 정책을 사용합니다.

- 어떤 `run_func` 또는 `on_message`에서든 예외 발생 시 서버 종료 트리거
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
- `app.send_to_discord(...)`는 step 저장 없이 Discord 응답만 전송
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
- 동작 변경(Breaking): `on_message` 예외는 이제 `run_func`와 동일하게 fail-fast로 처리되며, 내부 안내 메시지를 띄우고 계속 진행하지 않습니다.
