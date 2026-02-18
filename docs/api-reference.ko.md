# Easierlit API 레퍼런스

## 1. 범위와 계약 표기

- 런타임 코어: Chainlit (`chainlit>=2.9.6,<3`)
- 본 문서는 현재 공개 API만 다룹니다.
- `EasierlitClient`는 thread 워커만 지원합니다(`worker_mode="thread"`).
- 워커 런타임의 주 API는 `EasierlitApp`이며, message/thread CRUD를 모두 제공합니다.

## 2. EasierlitServer

### 2.1 `EasierlitServer.__init__`

```python
EasierlitServer(
    client: EasierlitClient,
    host: str = "127.0.0.1",
    port: int = 8000,
    root_path: str = "",
    auth: EasierlitAuthConfig | None = None,
    persistence: EasierlitPersistenceConfig | None = None,
    discord: EasierlitDiscordConfig | None = None,
)
```

파라미터:

- `client`: 필수 `EasierlitClient` 인스턴스
- `host`, `port`, `root_path`: Chainlit 서버 바인딩/경로 설정
- `auth`: 인증 부트스트랩 설정. `None`이면 Easierlit이 아래 순서로 인증을 자동 활성화
- `EASIERLIT_AUTH_USERNAME` + `EASIERLIT_AUTH_PASSWORD`가 모두 있으면 해당 값 사용
- 둘 다 없으면 `admin` / `admin` 폴백 사용 (경고 로그 출력)
- `persistence`: 선택 영속성 설정. `None`이면 기본 SQLite 부트스트랩 정책 활성
- `persistence.storage_provider`: 파일/이미지 영속화를 위한 선택 로컬 storage client override. Easierlit은 `LocalFileStorageClient`를 요구합니다.
- `discord`: 선택 Discord 봇 설정 (기본은 비활성 정책)

### 2.2 `EasierlitServer.serve`

```python
serve() -> None
```

동작:

- runtime에 `client/app/auth/persistence`를 bind
- `client.run(app)`로 워커 시작
- Chainlit headless 실행
- sidebar 기본 상태를 `open`으로 강제
- CoT 모드를 `full`로 강제
- `CHAINLIT_AUTH_COOKIE_NAME`가 이미 있으면 유지, 없으면 결정적 범위 기반 cookie 이름 `easierlit_access_token_<hash>` 설정
- `CHAINLIT_AUTH_SECRET`가 32바이트 미만이면 해당 실행에서 안전한 시크릿으로 자동 대체하고, 미설정이면 `.chainlit/jwt.secret`에서 secret을 해석
- `UVICORN_WS_PROTOCOL`이 비어 있으면 `websockets-sansio`를 기본값으로 설정
- Discord 토큰 해석 순서: `bot_token` 우선, 없으면 `DISCORD_BOT_TOKEN` 폴백
- Chainlit Discord handler를 런타임 monkeypatch하지 않고 Easierlit 자체 Discord bridge를 사용
- `serve()` 동안 Chainlit의 `DISCORD_BOT_TOKEN` startup 경로를 비활성으로 유지하고 종료 시 기존 env 값을 복원
- 종료 시 `CHAINLIT_AUTH_COOKIE_NAME`/`CHAINLIT_AUTH_SECRET`도 기존 env 값으로 복원
- 종료 시 `client.stop()` 호출 후 runtime unbind
- 워커 크래시에 대해 fail-fast 정책 적용

전파 가능 예외:

- `client.run(...)` 경로의 `WorkerAlreadyRunningError`
- 종료 시 워커 크래시를 재전파하는 `RunFuncExecutionError`
- `EASIERLIT_AUTH_USERNAME`/`EASIERLIT_AUTH_PASSWORD` 중 하나만 설정된 경우 `ValueError`
- Discord 활성화 상태에서 토큰이 없을 때 `ValueError`

## 3. EasierlitClient

### 3.1 `EasierlitClient.__init__`

```python
EasierlitClient(
    run_func: Callable[[EasierlitApp], Any],
    worker_mode: Literal["thread"] = "thread",
    run_func_mode: Literal["auto", "sync", "async"] = "auto",
)
```

파라미터:

- `run_func`: 사용자 워커 엔트리 함수
- `worker_mode`: `"thread"`만 허용
- `run_func_mode`:
- `"auto"`: 반환값 기준으로 sync/async 자동 처리
- `"sync"`: awaitable 반환 금지
- `"async"`: awaitable 반환 필수

예외:

- `worker_mode`/`run_func_mode`가 유효하지 않으면 `ValueError`

### 3.2 `EasierlitClient.run`

```python
run(app: EasierlitApp) -> None
```

동작:

- daemon thread 워커 1개 시작
- 워커에서 `run_func(app)` 실행
- 처리되지 않은 워커 예외 traceback 저장 후 app 종료

예외:

- 이미 워커가 살아있으면 `WorkerAlreadyRunningError`

### 3.3 `EasierlitClient.stop`

```python
stop(timeout: float = 5.0) -> None
```

동작:

- app 종료
- 워커 스레드 join (`timeout`)
- 워커 실패가 있으면 `RunFuncExecutionError`로 재전파

### 3.4 `EasierlitClient.set_worker_crash_handler` (고급)

```python
set_worker_crash_handler(handler: Callable[[str], None] | None) -> None
```

- 워커 크래시 시 traceback 문자열을 전달받는 콜백 등록/해제

### 3.5 `EasierlitClient.peek_worker_error` (고급)

```python
peek_worker_error() -> str | None
```

- 마지막 워커 traceback 문자열 조회

## 4. EasierlitApp

### 4.1 `EasierlitApp.recv`

```python
recv(timeout: float | None = None) -> IncomingMessage
```

동작:

- incoming 메시지를 blocking 수신
- timeout 초과 시 `TimeoutError`
- app이 닫힌 상태면 `AppClosedError`

### 4.2 `EasierlitApp.arecv`

```python
arecv(timeout: float | None = None) -> IncomingMessage
```

- `recv`의 async 버전
- timeout/close 동작은 동일

### 4.3 `EasierlitApp.add_message`

```python
add_message(
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

동작:

- `add_message` outgoing command를 큐에 적재
- `elements`로 전달된 Chainlit element 객체(이미지/파일 등)를 runtime으로 전달
- 생성된 `message_id` 반환
- 실제 반영은 runtime dispatcher에서 수행

### 4.4 `EasierlitApp.add_tool`

```python
add_tool(
    thread_id: str,
    tool_name: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

- `add_tool` outgoing command를 큐에 적재
- `tool_name`은 step `name`(UI author 표기)으로 저장
- `elements`로 전달된 Chainlit element 객체를 runtime으로 전달

### 4.5 `EasierlitApp.add_thought`

```python
add_thought(
    thread_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

- `tool_name="Reasoning"` 고정값으로 `add_tool(...)`을 호출하는 래퍼

### 4.6 `EasierlitApp.update_message`

```python
update_message(
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> None
```

- `update_message` command를 큐에 적재
- `elements`로 전달된 Chainlit element 객체를 runtime으로 전달

### 4.7 `EasierlitApp.update_tool`

```python
update_tool(
    thread_id: str,
    message_id: str,
    tool_name: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> None
```

- `update_tool` command를 큐에 적재
- `tool_name`은 step `name`(UI author 표기)으로 저장
- `elements`로 전달된 Chainlit element 객체를 runtime으로 전달

### 4.8 `EasierlitApp.update_thought`

```python
update_thought(
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> None
```

- `tool_name="Reasoning"` 고정값으로 `update_tool(...)`을 호출하는 래퍼

### 4.9 `EasierlitApp.delete_message`

```python
delete_message(thread_id: str, message_id: str) -> None
```

- `delete` command를 큐에 적재

메시지 command 적용 모델:

1. runtime이 thread의 활성 websocket session을 우선 탐색
2. session이 있으면 realtime context로 즉시 반영
3. session이 없고 data layer가 있으면 HTTP context 초기화 후 persistence fallback 반영
4. 둘 다 없으면 command 적용 시 `ThreadSessionNotActiveError`

### 4.10 `EasierlitApp.list_threads`

```python
list_threads(
    first: int = 20,
    cursor: str | None = None,
    search: str | None = None,
    user_identifier: str | None = None,
)
```

동작:

- data layer에서 thread 목록 조회
- `user_identifier` 지정 시 user 조회 후 user id 기준 필터 적용
- SQLite SQLAlchemyDataLayer 경로에서 `tags`를 문자열(JSON) -> 리스트로 정규화

예외:

- data layer 미설정 시 `DataPersistenceNotEnabledError`
- `user_identifier` 미존재 시 `ValueError`

### 4.11 `EasierlitApp.get_thread`

```python
get_thread(thread_id: str) -> dict
```

- thread dict 반환
- SQLite `tags` 형식 정규화
- 미존재 thread면 `ValueError`

### 4.12 `EasierlitApp.get_messages`

```python
get_messages(thread_id: str) -> dict
```

동작:

- `get_thread(thread_id)`로 대상 thread를 조회
- `thread["steps"]` 원래 순서를 그대로 유지
- dict 형태 step 중 `user_message`, `assistant_message`, `system_message`, `tool` 타입만 유지
- `thread["elements"]`를 `forId` 별칭(`forId`, `for_id`, `stepId`, `step_id`) 기준으로 각 message에 매핑
- 반환되는 각 element에 `has_source`와 `source` 메타데이터를 추가
- `source.kind` 값: `url`, `path`, `bytes`, `objectKey`, `chainlitKey`
- `url`이 비어 있고 `objectKey`가 있으면 data-layer storage provider를 통해 URL 복구를 시도
- 반환 형식:
- `thread`: `steps`를 제외한 thread 메타데이터
- `messages`: `elements`를 포함한 메시지/도구 step 순서 보존 단일 목록

### 4.13 `EasierlitApp.new_thread`

```python
new_thread(
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> str
```

동작:

- UUID4로 `thread_id`를 내부 생성
- 생성된 id가 이미 존재하면 최대 16회 재시도
- 생성된 `thread_id`를 반환
- auth 설정 시 owner user를 자동 조회/생성해 `user_id`로 저장
- SQLite SQLAlchemyDataLayer에서는 `tags`를 JSON 문자열로 저장

예외:

- 16회 재시도 후에도 고유 id를 확보하지 못하면 `RuntimeError`

### 4.14 `EasierlitApp.update_thread`

```python
update_thread(
    thread_id: str,
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> None
```

동작:

- 대상 thread가 이미 있을 때만 수정
- auth 설정 시 owner user를 자동 조회/생성해 `user_id`로 저장
- SQLite SQLAlchemyDataLayer에서는 `tags`를 JSON 문자열로 저장

예외:

- thread가 없으면 `ValueError`

### 4.15 `EasierlitApp.delete_thread`

```python
delete_thread(thread_id: str) -> None
```

- data layer를 통해 thread 삭제

### 4.16 `EasierlitApp.close`

```python
close() -> None
```

동작:

- app를 closed 상태로 전환
- 대기 중 `recv/arecv`를 해제
- dispatcher 종료를 위한 `close` command 큐 적재

### 4.17 `EasierlitApp.is_closed`

```python
is_closed() -> bool
```

- app closed 상태 반환

## 5. 설정 및 데이터 모델

### 5.1 `EasierlitAuthConfig`

```python
EasierlitAuthConfig(
    username: str,
    password: str,
    identifier: str | None = None,
    metadata: dict[str, Any] | None = None,
)
```

- `username`, `password`는 빈 문자열 불가
- `EasierlitServer(auth=None)` 경로에서는 이 설정이 환경변수/기본값으로 자동 생성됨

### 5.2 `EasierlitPersistenceConfig`

```python
EasierlitPersistenceConfig(
    enabled: bool = True,
    sqlite_path: str = ".chainlit/easierlit.db",
    storage_provider: BaseStorageClient | Any = <auto LocalFileStorageClient>,
)
```

- `storage_provider`는 `SQLAlchemyDataLayer(storage_provider=...)`로 전달됩니다.
- 기본 `storage_provider`는 `LocalFileStorageClient`입니다.
- 기본 로컬 저장 경로는 `<CHAINLIT_APP_ROOT 또는 cwd>/public/easierlit`입니다.
- `LocalFileStorageClient(base_dir=...)`는 반드시 `<CHAINLIT_APP_ROOT 또는 cwd>/public` 하위여야 하며, 아니면 `ValueError`를 발생시킵니다.
- 생성되는 로컬 파일/이미지 URL은 `CHAINLIT_PARENT_ROOT_PATH`와 `CHAINLIT_ROOT_PATH`를 함께 반영합니다.
- `enabled=True`에서는 유효한 `LocalFileStorageClient`가 필수이며, `None` 또는 비-local provider는 설정 오류를 발생시킵니다.
- 기본 persistence 경로에서는 startup에 local storage upload/read/delete preflight를 수행합니다.

### 5.3 `EasierlitDiscordConfig`

```python
EasierlitDiscordConfig(
    enabled: bool = True,
    bot_token: str | None = None,
)
```

동작:

- `EasierlitServer(...)`에서 `discord=None`이면 `serve()` 동안 Discord 비활성
- `discord=EasierlitDiscordConfig(...)`를 전달하면 기본 활성
- `enabled=False`: Easierlit Discord bridge를 시작하지 않음
- `enabled=True`: Discord 토큰 우선순위는 `bot_token`(비어 있지 않은 경우) 우선, `DISCORD_BOT_TOKEN` 폴백
- `serve()` 동안 Chainlit의 `DISCORD_BOT_TOKEN` startup 경로를 비활성으로 유지하고 종료 후 기존 env 값을 복원
- 활성화 상태에서 비어 있지 않은 토큰이 없으면 `ValueError`

### 5.4 `IncomingMessage`

```python
IncomingMessage(
    thread_id: str,
    session_id: str,
    message_id: str,
    content: str,
    author: str,
    created_at: str | None = None,
    metadata: dict | None = None,
)
```

### 5.5 `OutgoingCommand`

```python
OutgoingCommand(
    command: Literal[
        "add_message",
        "add_tool",
        "update_message",
        "update_tool",
        "delete",
        "close",
    ],
    thread_id: str | None = None,
    message_id: str | None = None,
    content: str | None = None,
    author: str = "Assistant",
    metadata: dict | None = None,
)
```

## 6. 예외 매트릭스 + 트러블슈팅

| 예외 | 대표 트리거 | 대응 |
|---|---|---|
| `AppClosedError` | app 종료 후 `recv()` 호출, 종료 후 enqueue 시도 | 워커 루프 종료 처리 |
| `WorkerAlreadyRunningError` | 실행 중인 워커에서 `client.run()` 재호출 | 먼저 `client.stop()` |
| `RunFuncExecutionError` | `run_func` 미처리 예외 | traceback 확인 후 run_func 로직 수정 |
| `DataPersistenceNotEnabledError` | data layer 없는 상태에서 thread CRUD 호출 | persistence/data layer 설정 |
| `ThreadSessionNotActiveError` | session/data layer 모두 없는 상태에서 메시지 command 적용 | 활성 session 유지 또는 persistence 설정 |
| `RuntimeError` | `new_thread()`가 재시도 후에도 고유 id 할당 실패 | id 생성/충돌 상황 점검 후 재시도 |
| `ValueError` | 잘못된 worker mode/run_func mode, user/thread 미존재 | 입력/식별자 검증 |

## 7. Chainlit Message vs Tool-call 매핑

- `app.recv/arecv` 입력은 user-message 흐름
- `app.add_message` 출력은 assistant-message 흐름
- `app.add_tool/update_tool` 출력은 tool-call 흐름이며 step name은 `tool_name` 사용
- `app.add_thought/update_thought` 출력은 tool-call 흐름이며 step name은 `Reasoning` 고정

## 8. Method-to-Example 인덱스

| 메서드 그룹 | 예제 |
|---|---|
| `EasierlitClient.run`, `stop` | `examples/minimal.py` |
| `EasierlitApp.list_threads`, `get_thread`, `get_messages`, `new_thread`, `update_thread`, `delete_thread` | `examples/thread_crud.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.add_message`, `update_message`, `delete_message` | `examples/minimal.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.add_tool`, `add_thought`, `update_tool`, `update_thought` | `examples/step_types.py` |
| 인증/영속성 설정 | `examples/custom_auth.py` |
| Discord 설정 | `examples/discord_bot.py` |
