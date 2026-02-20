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
    max_outgoing_workers: int = 4,
    auth: EasierlitAuthConfig | None = None,
    persistence: EasierlitPersistenceConfig | None = None,
    discord: EasierlitDiscordConfig | None = None,
)
```

파라미터:

- `client`: 필수 `EasierlitClient` 인스턴스
- `host`, `port`, `root_path`: Chainlit 서버 바인딩/경로 설정
- `max_outgoing_workers`: outgoing dispatcher lane 개수. `1` 이상이어야 하며 기본값은 `4`
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
- runtime outgoing dispatcher lane 수를 `max_outgoing_workers`로 설정
- `client.run(app)`로 워커 시작
- Chainlit headless 실행
- sidebar 기본 상태를 `open`으로 강제
- CoT 모드를 `full`로 강제
- `CHAINLIT_AUTH_COOKIE_NAME`가 이미 있으면 유지, 없으면 결정적 범위 기반 cookie 이름 `easierlit_access_token_<hash>` 설정
- `CHAINLIT_AUTH_SECRET`가 32바이트 미만이면 해당 실행에서 안전한 시크릿으로 자동 대체하고, 미설정이면 `.chainlit/jwt.secret`에서 secret을 해석
- `UVICORN_WS_PROTOCOL`이 비어 있으면 `websockets-sansio`를 기본값으로 설정
- Discord 토큰 해석 순서: `bot_token` 우선, 없으면 `DISCORD_BOT_TOKEN` 폴백
- Chainlit Discord handler를 런타임 monkeypatch하지 않고 Easierlit 자체 Discord bridge를 사용
- `serve()` 동안 `DISCORD_BOT_TOKEN`을 비우지 않으며 env 값은 그대로 유지
- 종료 시 `CHAINLIT_AUTH_COOKIE_NAME`/`CHAINLIT_AUTH_SECRET`도 기존 env 값으로 복원
- 종료 시 `client.stop()` 호출 후 runtime unbind
- 워커 크래시에 대해 fail-fast 정책 적용

전파 가능 예외:

- `client.run(...)` 경로의 `WorkerAlreadyRunningError`
- 종료 시 워커 크래시를 재전파하는 `RunFuncExecutionError`
- `EASIERLIT_AUTH_USERNAME`/`EASIERLIT_AUTH_PASSWORD` 중 하나만 설정된 경우 `ValueError`
- `max_outgoing_workers < 1`인 경우 `ValueError`
- Discord 활성화 상태에서 토큰이 없을 때 `ValueError`

## 3. EasierlitClient

### 3.1 `EasierlitClient.__init__`

```python
EasierlitClient(
    on_message: Callable[[EasierlitApp, IncomingMessage], Any],
    run_funcs: list[Callable[[EasierlitApp], Any]] | None = None,
    worker_mode: Literal["thread"] = "thread",
    run_func_mode: Literal["auto", "sync", "async"] = "auto",
    max_message_workers: int = 64,
)
```

파라미터:

- `on_message`: 필수 입력 메시지 핸들러(sync/async 지원)
- `run_funcs`: 선택적 백그라운드 워커 엔트리 함수 리스트
- `worker_mode`: `"thread"`만 허용
- `run_func_mode`:
- `"auto"`: 반환값 기준으로 sync/async 자동 처리
- `"sync"`: awaitable 반환 금지
- `"async"`: awaitable 반환 필수
- `max_message_workers`: on_message 전역 동시 실행 상한

예외:

- `worker_mode`/`run_func_mode`/`run_funcs`/`max_message_workers`가 비정상이면 `ValueError`
- `on_message`가 callable이 아니면 `TypeError`
- `run_funcs` 항목 중 callable이 아닌 값이 있으면 `TypeError`

### 3.2 `EasierlitClient.run`

```python
run(app: EasierlitApp) -> None
```

동작:

- `dispatch_incoming(...)` 기반 입력 디스패치 활성화
- 입력마다 daemon thread 1개로 on_message 실행
- 같은 `thread_id`는 직렬, 다른 `thread_id`는 `max_message_workers` 범위 내 병렬 실행
- async awaitable 실행은 역할별로 분리됨
- `run_func` awaitable은 전용 내부 이벤트 루프 runner 1개를 사용
- `on_message` awaitable은 `min(max_message_workers, 8)` 크기의 thread-aware runner pool을 사용
- 같은 `thread_id`는 동일한 `on_message` runner lane으로 라우팅
- CPU-bound Python 핸들러는 여전히 GIL 경쟁이 있으므로, 이번 최적화는 awaitable/I/O 중심 경로에서 효과가 큼
- `run_func`마다 daemon thread 워커 1개씩 시작
- 동일한 app 인스턴스로 각 `run_func(app)` 실행
- 처리되지 않은 워커 예외 traceback 저장 후 app 종료(fail-fast)
- 동작 변경(Breaking): 처리되지 않은 `on_message` 예외도 `run_func`와 동일하게 fail-fast 처리
- 특정 `run_func`의 정상 종료는 다른 워커를 중지시키지 않음

예외:

- 이미 워커가 살아있으면 `WorkerAlreadyRunningError`

### 3.3 `EasierlitClient.stop`

```python
stop(timeout: float = 5.0) -> None
```

동작:

- app 종료
- 신규 incoming 스케줄링 중단 + pending incoming 버퍼 제거
- in-flight 메시지 워커는 기다리지 않음
- 모든 워커 스레드 join (`timeout` 각각 적용)
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

### 4.1 `EasierlitApp.enqueue`

```python
enqueue(
    thread_id: str,
    content: str,
    session_id: str = "external",
    author: str = "User",
    message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    elements: list[Any] | None = None,
    created_at: str | None = None,
) -> str
```

동작:

- 즉시 UI/data layer 반영을 위해 `step_type="user_message"`인 `add_message` outgoing command를 먼저 큐에 적재
- `IncomingMessage`를 생성해 runtime/client on_message 워커로 디스패치
- 적재된 `message_id` 반환
- `message_id` 생략 시 UUID 기반 자동 생성
- `thread_id`/`session_id`/`author`가 공백 문자열이면 `ValueError`
- `message_id`를 명시했는데 공백 문자열이면 `ValueError`
- app이 이미 닫혀 있으면 `AppClosedError`

### 4.2 `EasierlitApp.add_message`

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
- 이 호출은 Discord로 자동 전송하지 않음
- 실제 반영은 runtime dispatcher에서 수행
- runtime dispatcher는 같은 `thread_id` 내 outgoing 순서는 보장하지만 thread 간 전역 outgoing 순서는 보장하지 않음

### 4.3 `EasierlitApp.add_tool`

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

### 4.6 `EasierlitApp.add_thought`

```python
add_thought(
    thread_id: str,
    content: str,
    metadata: dict | None = None,
    elements: list[Any] | None = None,
) -> str
```

- `tool_name="Reasoning"` 고정값으로 `add_tool(...)`을 호출하는 래퍼

### 4.6.1 `EasierlitApp.send_to_discord`

```python
send_to_discord(
    thread_id: str,
    content: str,
    elements: list[Any] | None = None,
) -> bool
```

- `thread_id`에 매핑된 Discord channel로만 내용을 전송
- `elements`를 전달하면 해석 가능한 항목을 Discord 첨부파일로 전송
- 전송 성공 시 `True`, channel 미등록/전송 실패 시 `False` 반환
- `thread_id`가 공백이거나, `content`/`elements`가 모두 비어 있으면 `ValueError`
- Chainlit step/data-layer 레코드는 생성/수정하지 않음

### 4.6.2 `EasierlitApp.is_discord_thread`

```python
is_discord_thread(thread_id: str) -> bool
```

- thread가 Discord 유입으로 판별되면 `True` 반환
- 판별 순서:
- 활성 세션의 runtime Discord channel 매핑
- 저장된 thread metadata marker (`easierlit_discord_owner_id`, `client_type="discord"`, `clientType="discord"`)
- marker가 없거나 data layer를 사용할 수 없으면 `False` 반환
- `thread_id`가 공백이면 `ValueError`

### 4.7 `EasierlitApp.update_message`

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

### 4.8 `EasierlitApp.update_tool`

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

### 4.9 `EasierlitApp.update_thought`

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

### 4.10 `EasierlitApp.delete_message`

```python
delete_message(thread_id: str, message_id: str) -> None
```

- `delete` command를 큐에 적재

메시지 command 적용 모델:

1. runtime이 thread의 활성 websocket session을 우선 탐색
2. session이 있으면 realtime context로 즉시 반영
3. session이 없고 data layer가 있으면 HTTP context 초기화 후 persistence fallback 반영
4. 둘 다 없으면 command 적용 시 `ThreadSessionNotActiveError`

### 4.11 `EasierlitApp.list_threads`

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

### 4.12 `EasierlitApp.get_thread`

```python
get_thread(thread_id: str) -> dict
```

- thread dict 반환
- SQLite `tags` 형식 정규화
- 미존재 thread면 `ValueError`

### 4.13 `EasierlitApp.get_messages`

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

### 4.14 `EasierlitApp.new_thread`

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

### 4.15 `EasierlitApp.update_thread`

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

### 4.16 `EasierlitApp.delete_thread`

```python
delete_thread(thread_id: str) -> None
```

- data layer를 통해 thread 삭제

### 4.17 `EasierlitApp.reset_thread`

```python
reset_thread(thread_id: str) -> None
```

동작:

- `get_thread(thread_id)`로 대상 thread 존재를 확인
- 기존 step id를 수집해 runtime 경로(realtime + data-layer fallback)로 `delete` command를 즉시 적용
- thread를 삭제한 뒤 동일한 `thread_id`로 재생성
- 재생성 시 `name`만 복원하고 `metadata`/`tags`는 초기화

예외:

- data layer 미설정 시 `DataPersistenceNotEnabledError`
- thread가 없으면 `ValueError`

### 4.18 `EasierlitApp.close`

```python
close() -> None
```

동작:

- app를 closed 상태로 전환
- dispatcher 종료를 위한 `close` command 큐 적재

### 4.19 `EasierlitApp.is_closed`

```python
is_closed() -> bool
```

- app closed 상태 반환

### 4.20 Discord Typing API

```python
discord_typing_open(thread_id: str) -> bool
discord_typing_close(thread_id: str) -> bool
```

동작:

- `discord_typing_open(...)`는 Discord 매핑 thread의 typing indicator를 시작
- `discord_typing_close(...)`는 Discord 매핑 thread의 typing indicator를 종료
- 두 메서드 모두 성공 시 `True`, Discord 매핑/typing sender 부재 시 `False` 반환
- 두 메서드 모두 `thread_id`는 비어 있지 않은 문자열이어야 하며 공백 문자열은 `ValueError`
- typing 상태는 `on_message` 실행 구간에서 자동 관리되지 않고 명시적으로 제어됨
- 공개 `lock/unlock` 메서드는 제공하지 않음

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
- `LocalFileStorageClient(base_dir=...)`는 `~` 경로 확장을 지원합니다.
- 상대 `base_dir`는 `<CHAINLIT_APP_ROOT 또는 cwd>/public` 하위로 해석됩니다.
- `public` 밖 절대 `base_dir`도 직접 사용할 수 있습니다.
- 로컬 파일/이미지는 `/easierlit/local/{object_key}` 경로로 서빙됩니다.
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
- `serve()` 동안 Easierlit은 `DISCORD_BOT_TOKEN`을 비우지 않음
- 활성화 상태에서 비어 있지 않은 토큰이 없으면 `ValueError`

### 5.4 `IncomingMessage`

```python
IncomingMessage(
    thread_id: str,
    session_id: str,
    message_id: str,
    content: str,
    elements: list[Any] = [],
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
| `AppClosedError` | app 종료 후 incoming dispatch 또는 enqueue 시도 | 처리 중단 후 필요 시 서버 재시작 |
| `WorkerAlreadyRunningError` | 실행 중인 워커에서 `client.run()` 재호출 | 먼저 `client.stop()` |
| `RunFuncExecutionError` | 워커 미처리 예외 | traceback 확인 후 `run_func`/`on_message` 로직 수정 |
| `DataPersistenceNotEnabledError` | data layer 없는 상태에서 thread CRUD 호출 | persistence/data layer 설정 |
| `ThreadSessionNotActiveError` | session/data layer 모두 없는 상태에서 메시지 command 적용 | 활성 session 유지 또는 persistence 설정 |
| `RuntimeError` | `new_thread()`가 재시도 후에도 고유 id 할당 실패 | id 생성/충돌 상황 점검 후 재시도 |
| `ValueError` | 잘못된 worker mode/run_func mode/run_funcs, user/thread 미존재, enqueue 입력값 오류 | 입력/식별자 검증 |

## 7. Chainlit Message vs Tool-call 매핑

- `on_message(..., incoming)` 입력은 user-message 흐름
- `app.enqueue(...)`는 입력을 `user_message`로 미러링하고 on_message로 디스패치
- `app.discord_typing_open/discord_typing_close`는 Discord typing indicator를 제어
- `app.add_message` 출력은 assistant-message 흐름
- `app.add_tool/update_tool` 출력은 tool-call 흐름이며 step name은 `tool_name` 사용
- `app.add_thought/update_thought` 출력은 tool-call 흐름이며 step name은 `Reasoning` 고정
- `app.send_to_discord`는 Discord 전용 출력 경로이며 step 저장이 없음
- `app.is_discord_thread`는 Discord 유입 marker를 점검

## 8. Method-to-Example 인덱스

| 메서드 그룹 | 예제 |
|---|---|
| `EasierlitClient.run`, `stop` | `examples/minimal.py` |
| `EasierlitApp.list_threads`, `get_thread`, `get_messages`, `new_thread`, `update_thread`, `delete_thread`, `reset_thread` | `examples/thread_crud.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.discord_typing_open`, `discord_typing_close` | 전용 예제 없음 (런타임/수동 제어 API) |
| `EasierlitApp.enqueue` | 외부 입력을 `user_message`로 미러링하고 `on_message`로 디스패치하는 in-process 연동 |
| `EasierlitApp.add_message`, `update_message`, `delete_message` | `examples/minimal.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitApp.add_tool`, `add_thought`, `update_tool`, `update_thought` | `examples/step_types.py` |
| `EasierlitApp.send_to_discord` | `examples/discord_bot.py` |
| `EasierlitApp.is_discord_thread` | 전용 예제 없음 (runtime/data-layer marker 점검 API) |
| 인증/영속성 설정 | `examples/custom_auth.py` |
| Discord 설정 | `examples/discord_bot.py` |
