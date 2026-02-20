# Easierlit 사용 가이드

이 문서는 Easierlit의 상세 사용 레퍼런스입니다.
메서드 단위의 정확한 계약(시그니처/예외/실패모드)은 아래 API 레퍼런스를 우선 참고하세요.

- `docs/api-reference.en.md`
- `docs/api-reference.ko.md`

## 1. 범위

- 런타임 코어: Chainlit (`chainlit>=2.9.6,<3`)
- 현재 공개 API 기준만 다룹니다.

## 2. 아키텍처

Easierlit은 3개 구성 요소로 동작합니다.

- `EasierlitServer`: 메인 프로세스에서 Chainlit 시작
- `EasierlitClient`: 입력을 `on_message(app, incoming)` 워커로 디스패치
- `EasierlitApp`: message/thread CRUD 출력 브리지

상위 흐름:

1. `server.serve()`가 runtime bind 후 Chainlit 실행
2. Chainlit `on_message`가 입력을 `IncomingMessage`로 변환
3. runtime이 입력을 `client.on_message(app, incoming)` 워커로 전달
4. 핸들러가 `app.*` API(message + thread CRUD)로 출력/저장

## 3. 표준 부트스트랩 패턴

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
server.serve()
```

참고:

- `serve()`는 블로킹입니다.
- `worker_mode`는 `"thread"`만 지원합니다.
- `on_message`는 sync/async 모두 지원합니다.
- `run_funcs`는 선택적 백그라운드 워커로 사용할 수 있습니다.
- 기본값 `run_func_mode="auto"`가 각 함수의 실행 타입을 자동 판별합니다.
- async awaitable은 전용 이벤트 루프 runner에서 실행되어 메시지마다 `asyncio.run(...)` loop 생성/종료 오버헤드를 줄입니다.
- CPU-bound Python 코드는 여전히 GIL을 공유하므로, CPU를 완전히 분리하려면 프로세스 단위 offloading이 필요합니다.

## 4. 공개 API 시그니처

이 섹션은 시그니처 요약입니다. 상세 메서드 계약은 `docs/api-reference.ko.md`를 참고하세요.

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
EasierlitApp.add_message(thread_id, content, author="Assistant", metadata=None) -> str
EasierlitApp.add_tool(thread_id, tool_name, content, metadata=None) -> str
EasierlitApp.add_thought(thread_id, content, metadata=None) -> str  # tool_name은 "Reasoning" 고정
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.update_tool(thread_id, message_id, tool_name, content, metadata=None)
EasierlitApp.update_thought(thread_id, message_id, content, metadata=None)  # tool_name은 "Reasoning" 고정
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

## 5. 서버 런타임 정책

Easierlit 서버는 다음 기본값을 강제합니다.

- Chainlit headless 모드 활성
- sidebar 기본 상태 `open`
- CoT 모드 `full` 강제
- `CHAINLIT_AUTH_COOKIE_NAME`가 이미 있으면 유지, 없으면 `easierlit_access_token_<hash>`를 설정
- `CHAINLIT_AUTH_SECRET`가 32바이트 미만이면 해당 실행에서 안전한 시크릿으로 자동 대체하고, 미설정이면 `.chainlit/jwt.secret`를 자동 관리
- 종료 시 Easierlit이 `CHAINLIT_AUTH_COOKIE_NAME`/`CHAINLIT_AUTH_SECRET`를 이전 값으로 복원
- `UVICORN_WS_PROTOCOL`이 비어 있으면 `websockets-sansio`를 기본값으로 사용
- 워커 fail-fast: `run_func` 또는 `on_message` 예외 발생 시 서버 종료 트리거
- outgoing dispatcher는 thread-aware 병렬 lane(`max_outgoing_workers`, 기본 `4`)으로 동작
- outgoing 순서는 같은 `thread_id` 안에서만 보장되며 thread 간 전역 순서는 의도적으로 보장하지 않음
- `discord=EasierlitDiscordConfig(...)`를 전달하지 않으면 Discord bridge는 기본 비활성
- async awaitable 실행은 역할별로 분리됩니다.
- `run_func` awaitable은 전용 runner loop를 사용합니다.
- `on_message` awaitable은 `min(max_message_workers, 8)` 크기의 thread-aware runner pool을 사용합니다.
- 같은 `thread_id`는 동일한 message runner lane에 고정됩니다.

## 6. 인증, 영속성, Discord

생략 시 기본 동작:

- `auth=None`: 인증이 자동 활성화됩니다.
- 인증 자격증명 해석 순서:
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD` (둘 다 함께 설정 필요)
- 폴백 `admin` / `admin` (경고 로그 출력)
- `EASIERLIT_AUTH_USERNAME`/`EASIERLIT_AUTH_PASSWORD` 중 하나만 설정하면 `ValueError`가 발생합니다.
- `persistence=None`: 기본 SQLite 영속성(`.chainlit/easierlit.db`)이 활성화됩니다.
- 파일/이미지 저장소는 기본으로 항상 `LocalFileStorageClient`를 사용합니다.
- 기본 로컬 저장 경로는 `<CHAINLIT_APP_ROOT 또는 cwd>/public/easierlit`입니다.
- `LocalFileStorageClient(base_dir=...)`는 `~` 경로 확장을 지원합니다.
- 상대 `base_dir`는 `<CHAINLIT_APP_ROOT 또는 cwd>/public` 하위로 해석됩니다.
- `public` 밖 절대 `base_dir`도 직접 사용할 수 있습니다.
- 로컬 파일/이미지는 `/easierlit/local/{object_key}` 경로로 서빙됩니다.
- 로컬 파일/이미지 URL은 `CHAINLIT_PARENT_ROOT_PATH` + `CHAINLIT_ROOT_PATH`를 자동 반영합니다.

인증 설정 예시:

```python
from easierlit import EasierlitAuthConfig, EasierlitServer

auth = EasierlitAuthConfig(
    username="admin",
    password="admin",
    identifier="admin",
    metadata={"role": "admin"},
)

server = EasierlitServer(client=client, auth=auth)
```

영속성 설정 예시:

```python
from easierlit import EasierlitPersistenceConfig, EasierlitServer, LocalFileStorageClient

persistence = EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
    storage_provider=LocalFileStorageClient(...),  # 선택 override. LocalFileStorageClient만 허용됩니다.
)

server = EasierlitServer(client=client, persistence=persistence)
```

Discord 설정 예시:

```python
import os

from easierlit import EasierlitDiscordConfig, EasierlitServer

# config token이 환경변수 token보다 우선합니다.
discord = EasierlitDiscordConfig(
    bot_token=os.environ.get("MY_DISCORD_TOKEN"),
)

server = EasierlitServer(client=client, discord=discord)
```

Discord 토큰 해석 정책:

- `discord=None`이면 Discord 비활성
- `discord=EasierlitDiscordConfig(...)`를 전달하면 기본 활성
- `EasierlitDiscordConfig.bot_token`이 비어 있지 않으면 우선 사용
- config token이 없으면 `DISCORD_BOT_TOKEN`을 폴백으로 사용
- Discord 활성화 상태에서 유효 토큰이 없으면 `serve()`가 `ValueError` 발생
- Easierlit은 자체 Discord bridge로 동작하며 Chainlit Discord handler를 런타임에 monkeypatch하지 않음
- `serve()` 동안 Easierlit은 `DISCORD_BOT_TOKEN`을 비우지 않으며 env 값은 그대로 유지

Thread History 표시 조건(Chainlit 정책):

- `requireLogin=True`
- `dataPersistence=True`

## 7. on_message 작성 패턴과 오류 처리

권장 구조:

1. 입력 처리의 기본 엔트리로 `on_message(app, incoming)`를 사용합니다.
2. 핸들러는 대화 간 병렬 실행을 고려해 thread-safe하게 작성합니다.
3. 출력/저장은 `app.*` API로만 수행합니다.
4. 명령 단위 예외는 문맥을 붙여 로그 가독성 확보

`on_message` 또는 백그라운드 `run_func`에서 처리되지 않은 예외가 발생하면:

- Easierlit가 traceback을 로그에 남김
- 서버 종료를 트리거함
- 종료 진행 중 입력 dispatch는 요약 메시지 방식으로 억제
- 동작 변경(Breaking): `on_message`는 더 이상 내부 안내 메시지를 보내고 계속 진행하지 않음

외부 in-process 입력:

- `app.enqueue(...)`는 입력을 `user_message`로 UI/data layer에 반영하고 `on_message`로 디스패치합니다.
- 같은 프로세스에서 동작하는 webhook/내부 연동 코드에 적합합니다.

Thread 작업 상태 API:

- `start_thread_task(thread_id)`
- `end_thread_task(thread_id)`
- `is_thread_task_running(thread_id) -> bool`

동작:

- `start_thread_task(...)`는 특정 thread를 작업 중(UI indicator) 상태로 표시합니다.
- `end_thread_task(...)`는 작업 중(UI indicator) 상태를 해제합니다.
- Easierlit이 각 `on_message` 실행 구간의 task state를 자동 관리합니다.
- 공개 `lock/unlock` 메서드는 제공하지 않습니다.

## 8. App에서 Thread CRUD

`EasierlitApp` 메서드:

- `list_threads(first=20, cursor=None, search=None, user_identifier=None)`
- `get_thread(thread_id)`
- `get_messages(thread_id) -> dict`
- `new_thread(name=None, metadata=None, tags=None) -> str`
- `update_thread(thread_id, name=None, metadata=None, tags=None)`
- `delete_thread(thread_id)`
- `reset_thread(thread_id)`

동작 상세:

- Thread CRUD는 data layer가 필요합니다.
- `new_thread`는 고유한 thread id를 자동 생성하고 반환합니다.
- `update_thread`는 대상 thread가 이미 있을 때만 수정합니다.
- `reset_thread`는 thread 메시지를 전부 삭제하고 동일한 thread id로 재생성하며 `name`만 복원합니다.
- `delete_thread`/`reset_thread`는 해당 thread 작업 상태를 자동 해제합니다.
- `get_messages`는 thread 메타데이터와 순서 보존 `messages` 단일 목록을 반환합니다.
- `get_messages`는 `user_message`/`assistant_message`/`system_message`/`tool` step 타입만 포함합니다.
- `get_messages`는 `thread["elements"]`를 `forId` 별칭(`forId`, `for_id`, `stepId`, `step_id`) 기준으로 매핑해 각 message에 `elements`를 포함합니다.
- `get_messages`는 각 element에 `has_source`와 `source`(`url`/`path`/`bytes`/`objectKey`/`chainlitKey`)를 추가합니다.
- auth 설정 시 `new_thread`/`update_thread` 모두 소유자 user를 자동 조회/생성 후 `user_id`로 저장합니다.
- SQLite SQLAlchemyDataLayer에서는 `tags` list를 저장 시 JSON 직렬화하고 조회 시 list로 정규화합니다.

## 9. Message CRUD와 fallback

메시지 메서드:

- `app.add_message(...)`, `app.update_message(...)`, `app.delete_message(...)`
- `app.add_tool(...)`, `app.add_thought(...)`, `app.update_tool(...)`, `app.update_thought(...)`

실행 모델:

1. thread에 활성 websocket session이 있으면 realtime context로 반영
2. session이 비활성이고 data layer가 있으면 persistence fallback 수행
3. fallback 전에 내부 HTTP Chainlit context를 초기화
4. session/data layer 모두 없으면 queued command 적용 시 `ThreadSessionNotActiveError` 발생
5. outgoing command는 `thread_id` lane 단위로 병렬 처리되며 같은 thread 순서는 유지되고 thread 간 완료 순서는 달라질 수 있음

## 10. run_func에서 새 thread 생성

참고 예제: `examples/thread_create_in_run_func.py`

패턴:

1. `thread_id = app.new_thread(...)` 호출
2. 반환된 `thread_id`로 후속 메시지 대상 지정
3. `app.add_message(...)`로 bootstrap assistant message 저장
4. 현재 thread로 생성 결과를 안내

auth 설정 시 생성 thread는 auth 사용자 소유자로 자동 귀속됩니다.

## 11. Message vs Tool Call (Chainlit)

Chainlit은 step type으로 메시지와 도구/실행을 구분합니다.

메시지 타입:

- `user_message`
- `assistant_message`
- `system_message`

도구/실행 타입:

- `tool`, `run`, `llm`, `embedding`, `retrieval`, `rerank`, `undefined`

Easierlit 매핑:

- `on_message(..., incoming)` 입력은 사용자 메시지 흐름
- `app.add_message()` 출력은 assistant 메시지 흐름
- `app.add_tool()/app.update_tool()` 출력은 tool-call 흐름이며 step name=`tool_name`
- `app.add_thought()/app.update_thought()` 출력은 tool-call 흐름이며 step name=`Reasoning` 고정

UI 옵션 참고(Chainlit): `ui.cot`는 `full`, `tool_call`, `hidden`을 지원합니다.

## 12. 트러블슈팅

`Cannot dispatch incoming message to a closed app`:

- 의미: 워커/app이 이미 닫혔고 대개 `run_func` 크래시 이후 상태
- 조치: 서버 traceback 원인 수정 후 재시작

`Data persistence is not enabled`:

- 의미: thread CRUD 또는 fallback에 data layer 필요
- 조치: persistence(기본값) 유지 또는 외부 data layer 구성

설정 변경 후 `Invalid authentication token`:

- 의미: 브라우저 토큰 stale 또는 secret mismatch
- 조치: 서버 재시작 후 재로그인(`CHAINLIT_AUTH_COOKIE_NAME`는 사용자 지정값이거나 `easierlit_access_token_<hash>`일 수 있음)

SQLite `tags` 바인딩 이슈:

- Easierlit가 SQLite SQLAlchemyDataLayer에서 `tags`를 자동 정규화
- 문제가 계속되면 현재 프로젝트 코드가 실제로 import되는지 경로 확인

## 13. 예제

- `examples/minimal.py`
- `examples/custom_auth.py`
- `examples/discord_bot.py`
- `examples/thread_crud.py`
- `examples/thread_create_in_run_func.py`
- `examples/step_types.py`

## 14. 릴리스 체크리스트

```bash
python3 -m py_compile examples/*.py
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

추가 확인:

- `pyproject.toml` version이 릴리스 태그와 일치
- 문서 링크 정상(`README.md`, `README.ko.md`, `docs/usage.en.md`, `docs/usage.ko.md`, `docs/api-reference.en.md`, `docs/api-reference.ko.md`)
