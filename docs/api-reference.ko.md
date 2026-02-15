# Easierlit API 레퍼런스 (v0.1.0)

이 문서는 Easierlit v0.1.0의 메서드 단위 계약(contract)을 정의합니다.

## 1. 범위와 계약 표기 규칙

- 범위: 공개 API만 포함합니다.
- 제외: `_` 접두사의 private/internal 메서드.
- 현재 구현과 테스트 기준으로 동작을 고정합니다.

메서드별 계약 필드:

- `Signature`: 정확한 호출 형태.
- `Purpose`: 메서드 목적.
- `When to call / When not to call`: 사용/비사용 경계.
- `Parameters`: 타입, 기본값, 제약.
- `Returns`: 반환 타입과 의미.
- `Raises`: 직접 raise 및 주요 전파 예외.
- `Side effects`: 상태/환경 변화.
- `Concurrency/worker notes`: thread/process/event-loop 주의점.
- `Failure modes and fixes`: 대표 실패 원인과 복구 방법.
- `Examples`: 기본 1개 + 엣지/실패 1개.

## 2. EasierlitServer

### 2.1 `EasierlitServer.__init__`

- Signature

```python
EasierlitServer(
    client: EasierlitClient,
    host: str = "127.0.0.1",
    port: int = 8000,
    root_path: str = "",
    auth: EasierlitAuthConfig | None = None,
    persistence: EasierlitPersistenceConfig | None = None,
)
```

- Purpose
- 서버 객체를 만들고 런타임 설정을 보관합니다.

- When to call / When not to call
- 앱 부트스트랩 시 1회 생성합니다.
- `client=None` 또는 비정상 객체 전달은 피합니다.

- Parameters
- `client`: 필수 `EasierlitClient`.
- `host`: 바인딩 호스트.
- `port`: 바인딩 포트.
- `root_path`: reverse proxy 경로 프리픽스.
- `auth`: 단일 계정 인증 설정(선택).
- `persistence`: 영속성 설정(선택).

- Returns
- `None` (생성자).

- Raises
- host/port에 대한 직접 검증 예외는 생성자에서 발생하지 않습니다.
- auth 값이 잘못되면 `EasierlitAuthConfig` 생성 시 예외가 먼저 발생합니다.

- Side effects
- `persistence=None`이면 내부에서 `EasierlitPersistenceConfig()` 기본값을 사용합니다.

- Concurrency/worker notes
- 워커 시작 전 생성이 안전합니다.

- Failure modes and fixes
- auth 입력 오류: `EasierlitAuthConfig`를 먼저 검증하세요.

- Examples

```python
from easierlit import EasierlitClient, EasierlitServer

client = EasierlitClient(run_func=lambda app: None)
server = EasierlitServer(client=client, host="0.0.0.0", port=8000)
```

```python
# Edge: auth + persistence를 명시
from easierlit import EasierlitAuthConfig, EasierlitPersistenceConfig, EasierlitServer

server = EasierlitServer(
    client=client,
    auth=EasierlitAuthConfig(username="admin", password="admin"),
    persistence=EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/easierlit.db"),
)
```

### 2.2 `EasierlitServer.serve`

- Signature

```python
def serve(self) -> None
```

- Purpose
- runtime bind + worker 시작 + Chainlit 서버 실행(블로킹).

- When to call / When not to call
- 프로그램 엔트리포인트에서 1회 호출합니다.
- 같은 스레드에서 non-blocking 동작이 필요하면 직접 호출하지 않습니다.

- Parameters
- 없음.

- Returns
- Chainlit 종료 후 `None`.

- Raises
- `client.run(...)`에서 `WorkerAlreadyRunningError` 전파 가능.
- process 모드에서 `run_func` 비직렬화 가능 시 `TypeError` 전파 가능.
- 종료 시 `client.stop()`에서 `RunFuncExecutionError` 전파 가능.
- Chainlit/runtime 예외 전파 가능.

- Side effects
- 환경변수 설정:
- `CHAINLIT_HOST`
- `CHAINLIT_PORT`
- `CHAINLIT_ROOT_PATH`
- `CHAINLIT_AUTH_COOKIE_NAME=easierlit_access_token`
- `CHAINLIT_AUTH_SECRET` (`.chainlit/jwt.secret` 자동관리)
- Chainlit 강제 설정:
- `config.run.headless = True`
- `config.ui.default_sidebar_state = "open"`
- 워커 크래시 핸들러 등록(프로세스 종료 신호 트리거).

- Concurrency/worker notes
- 프로세스 라이프사이클을 소유하며 터미널을 점유합니다.

- Failure modes and fixes
- 워커 크래시: 서버 traceback 로그를 확인하고 `run_func`를 수정 후 재시작.
- 포트 충돌: `port` 변경.

- Examples

```python
server = EasierlitServer(client=client)
server.serve()  # blocking
```

```python
# Edge: fail-fast 정책
# run_func가 크래시하면 서버 로그를 남기고 종료됩니다.
```

## 3. EasierlitClient

### 3.1 `EasierlitClient.__init__`

- Signature

```python
EasierlitClient(
    run_func: Callable[[EasierlitApp], None],
    worker_mode: Literal["thread", "process"] = "thread",
)
```

- Purpose
- 앱 처리 함수와 워커 모드를 설정합니다.

- When to call / When not to call
- 런타임당 1개 client 생성이 일반적입니다.
- 지원하지 않는 `worker_mode`를 넘기지 않습니다.

- Parameters
- `run_func`: 워커가 실행할 함수.
- `worker_mode`: `"thread"` 또는 `"process"`.

- Returns
- `None` (생성자).

- Raises
- `worker_mode`가 허용값이 아니면 `ValueError`.

- Side effects
- 워커/에러 상태와 runtime 핸들을 초기화합니다.

- Concurrency/worker notes
- `process` 모드는 `run(...)` 시점에 `run_func` 직렬화 가능 여부가 필요합니다.

- Failure modes and fixes
- worker_mode 오타: 정확히 `thread` 또는 `process` 사용.

- Examples

```python
client = EasierlitClient(run_func=my_run_func, worker_mode="thread")
```

```python
# Edge/failure
try:
    EasierlitClient(run_func=my_run_func, worker_mode="async")
except ValueError:
    ...
```

### 3.2 `EasierlitClient.run`

- Signature

```python
def run(self, app: EasierlitApp) -> None
```

- Purpose
- 전역 워커 1개를 시작하고 `run_func(app)`를 실행합니다.

- When to call / When not to call
- 새로운 `EasierlitApp`와 함께 1회 호출합니다.
- 이미 워커가 실행 중이면 호출하지 않습니다.

- Parameters
- `app`: 통신 브리지 객체.

- Returns
- `None`.

- Raises
- 워커가 이미 실행 중이면 `WorkerAlreadyRunningError`.
- process 모드에서 `run_func` 직렬화 실패 시 `TypeError`.

- Side effects
- daemon thread/process 시작.
- 기존 워커 에러 상태 초기화.

- Concurrency/worker notes
- process 모드에서는 크래시 에러 모니터 스레드를 추가로 시작합니다.

- Failure modes and fixes
- 중복 실행: 먼저 `stop()` 호출.
- pickling 오류: nested closure/lambda 대신 top-level 함수 사용.

- Examples

```python
app = EasierlitApp()
client.run(app)
```

```python
# Edge/failure
client.run(app)
try:
    client.run(app)
except Exception as exc:
    print(type(exc).__name__)  # WorkerAlreadyRunningError
```

### 3.3 `EasierlitClient.stop`

- Signature

```python
def stop(self, timeout: float = 5.0) -> None
```

- Purpose
- 워커 중지, 브리지 종료, 리소스 join, 워커 크래시 전파.

- When to call / When not to call
- 서버 종료 경로에서 호출합니다.
- `RunFuncExecutionError`를 무시하지 않는 것이 운영상 안전합니다.

- Parameters
- `timeout`: join 대기 시간.

- Returns
- `None`.

- Raises
- `run_func` 크래시가 있었으면 `RunFuncExecutionError`.

- Side effects
- app close 수행.
- thread/process join, 필요 시 process terminate.
- 내부 process/app 참조 정리.

- Concurrency/worker notes
- shutdown 경로에서 안전하지만 정책상 크래시 에러를 다시 올릴 수 있습니다.

- Failure modes and fixes
- stop에서 크래시가 올라오면 traceback 확인 후 `run_func` 원인 수정.

- Examples

```python
try:
    client.stop(timeout=5.0)
except Exception as exc:
    print(type(exc).__name__)
```

```python
# Edge: 짧은 timeout으로 빠른 정리 시도
client.stop(timeout=0.1)
```

### 3.4 `EasierlitClient.list_threads`

- Signature

```python
def list_threads(
    self,
    first: int = 20,
    cursor: str | None = None,
    search: str | None = None,
    user_identifier: str | None = None,
)
```

- Purpose
- Chainlit data layer에서 thread 목록을 페이지 조회합니다.

- When to call / When not to call
- persistence/data layer가 있을 때 호출합니다.
- 무영속 모드에서는 호출하지 않습니다.

- Parameters
- `first`: 페이지 크기.
- `cursor`: 페이지 커서.
- `search`: 검색어.
- `user_identifier`: 사용자 범위 필터(먼저 user id로 resolve).

- Returns
- Chainlit `PaginatedResponse`.
- SQLite SQLAlchemy 경로에서는 `tags` JSON 문자열을 가능한 경우 `list[str]`로 정규화합니다.

- Raises
- data layer가 없으면 `DataPersistenceNotEnabledError`.
- `user_identifier`가 존재하지 않으면 `ValueError`.

- Side effects
- 읽기 호출 외 부작용 없음.

- Concurrency/worker notes
- 내부 async를 runtime sync bridge로 실행합니다.

- Failure modes and fixes
- data layer 없음: 기본 persistence 유지 또는 외부 DB 구성.
- 사용자 미존재: identifier 확인 또는 필터 제거.

- Examples

```python
threads = client.list_threads(first=10)
for t in threads.data:
    print(t["id"], t.get("name"))
```

```python
# Edge/failure
try:
    client.list_threads(user_identifier="missing-user")
except ValueError as exc:
    print(exc)
```

### 3.5 `EasierlitClient.get_thread`

- Signature

```python
def get_thread(self, thread_id: str) -> dict
```

- Purpose
- thread id로 단건 조회합니다.

- When to call / When not to call
- thread 상세 조회 시 호출.
- persistence가 꺼져 있으면 호출하지 않습니다.

- Parameters
- `thread_id`: 대상 thread id.

- Returns
- thread dict.
- SQLite 경로에서 parse 가능한 JSON tags 문자열은 list로 정규화됩니다.

- Raises
- data layer가 없으면 `DataPersistenceNotEnabledError`.
- thread가 없으면 `ValueError`.

- Side effects
- 읽기 호출 외 부작용 없음.

- Concurrency/worker notes
- 워커 내에서 안전하게 호출 가능.

- Failure modes and fixes
- not found: id 및 로그인/소유권 컨텍스트 확인.

- Examples

```python
thread = client.get_thread("thread-1")
print(thread["id"], thread.get("tags"))
```

```python
# Edge/failure
try:
    client.get_thread("not-exists")
except ValueError:
    ...
```

### 3.6 `EasierlitClient.update_thread`

- Signature

```python
def update_thread(
    self,
    thread_id: str,
    name: str | None = None,
    metadata: dict | None = None,
    tags: list[str] | None = None,
) -> None
```

- Purpose
- thread 메타데이터를 생성/갱신(upsert)합니다.

- When to call / When not to call
- rename/create/update 용도로 호출합니다.
- persistence 없이 호출하지 않습니다.

- Parameters
- `thread_id`: 대상 thread id.
- `name`: 표시 이름.
- `metadata`: 메타데이터.
- `tags`: 태그 목록.

- Returns
- `None`.

- Raises
- data layer가 없으면 `DataPersistenceNotEnabledError`.
- backend 예외 전파 가능.

- Side effects
- auth 설정 시 소유자 user를 자동 조회/생성하여 `user_id` 저장.
- SQLite SQLAlchemyDataLayer에서는 `tags`를 JSON 문자열로 직렬화 후 저장.

- Concurrency/worker notes
- owner resolve 중 `get_user` / `create_user` 호출이 발생할 수 있습니다.

- Failure modes and fixes
- SQLite tags 바인딩 오류: `tags`는 list로 넘기고 Easierlit 정규화에 맡깁니다.
- 소유권 미귀속: 서버 auth 설정 확인.

- Examples

```python
client.update_thread(
    thread_id="thread-1",
    name="Renamed",
    metadata={"source": "run_func"},
    tags=["demo", "active"],
)
```

```python
# Edge: id만으로 upsert 호출
client.update_thread(thread_id="new-thread-id")
```

### 3.7 `EasierlitClient.delete_thread`

- Signature

```python
def delete_thread(self, thread_id: str) -> None
```

- Purpose
- thread id 기준으로 영속 데이터에서 삭제합니다.

- When to call / When not to call
- 명시적 정리/삭제 플로우에서 호출.
- persistence 없이 호출하지 않습니다.

- Parameters
- `thread_id`: 대상 thread id.

- Returns
- `None`.

- Raises
- data layer가 없으면 `DataPersistenceNotEnabledError`.
- backend 예외 전파 가능.

- Side effects
- thread 영속 레코드 삭제.

- Concurrency/worker notes
- run worker 명령 처리에서 사용 가능.

- Failure modes and fixes
- ACL/backend 제약으로 실패 시 auth 및 backend 정책 확인.

- Examples

```python
client.delete_thread("thread-1")
```

```python
# Edge/failure
try:
    client.delete_thread("missing")
except Exception as exc:
    print(exc)
```

### 3.8 `EasierlitClient.add_message`

- Signature

```python
def add_message(
    self,
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
) -> str
```

- Purpose
- assistant 메시지 step을 생성하고 message id를 반환합니다.

- When to call / When not to call
- `run_func` 내부에서 assistant 출력 저장에 호출.
- active session도 data layer도 없는 thread에는 호출하지 않습니다.

- Parameters
- `thread_id`: 대상 thread.
- `content`: 메시지 본문.
- `author`: 작성자 표시.
- `metadata`: 추가 메타데이터.

- Returns
- 생성된 UUID `message_id` 문자열.

- Raises
- active session/data layer 모두 없으면 `ThreadSessionNotActiveError`.
- Chainlit 이벤트 루프에서 sync wait 시 `RuntimeError` 가능.
- backend/runtime 예외 전파 가능.

- Side effects
- session 활성 시 realtime 전송.
- session 비활성 시 fallback `create_step(type="assistant_message")` 영속 저장.

- Concurrency/worker notes
- `run_coroutine_sync`로 runtime loop에 동기 대기합니다.

- Failure modes and fixes
- session/data layer 부재: persistence 활성화 또는 활성 thread에서만 호출.
- 이벤트 루프 오사용: Chainlit callback 루프가 아닌 worker 문맥에서 호출.

- Examples

```python
msg_id = client.add_message(
    thread_id=incoming.thread_id,
    content="Hello from worker",
    author="Bot",
)
```

```python
# Edge/failure
try:
    client.add_message("thread-x", "hello")
except Exception as exc:
    print(type(exc).__name__)
```

### 3.9 `EasierlitClient.update_message`

- Signature

```python
def update_message(
    self,
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
) -> None
```

- Purpose
- 기존 message/step 내용을 갱신합니다.

- When to call / When not to call
- 기존 `message_id`를 알고 있을 때 호출.
- backend upsert를 원치 않으면 미존재 id 호출을 피합니다.

- Parameters
- `thread_id`, `message_id`, `content`, `metadata`.

- Returns
- `None`.

- Raises
- active session/data layer 모두 없으면 `ThreadSessionNotActiveError`.
- sync wait/event-loop 오사용 시 `RuntimeError` 가능.
- backend/runtime 예외 전파 가능.

- Side effects
- realtime update 또는 fallback `update_step` 수행.

- Concurrency/worker notes
- `add_message`와 동일한 sync bridge 동작.

- Failure modes and fixes
- 미존재 message id 동작은 backend 의존적이므로 앱 레벨 검증 권장.

- Examples

```python
client.update_message(thread_id="thread-1", message_id=msg_id, content="Updated")
```

```python
# Edge/failure
try:
    client.update_message("thread-1", "missing-id", "Updated")
except Exception as exc:
    print(exc)
```

### 3.10 `EasierlitClient.delete_message`

- Signature

```python
def delete_message(self, thread_id: str, message_id: str) -> None
```

- Purpose
- message/step를 id로 삭제합니다.

- When to call / When not to call
- 메시지 삭제가 필요할 때 호출.
- strict 동작이 필요하면 미존재 id 호출을 피합니다.

- Parameters
- `thread_id`, `message_id`.

- Returns
- `None`.

- Raises
- active session/data layer 모두 없으면 `ThreadSessionNotActiveError`.
- sync wait/event-loop 오사용 시 `RuntimeError` 가능.
- backend/runtime 예외 전파 가능.

- Side effects
- realtime remove 또는 fallback `delete_step` 수행.

- Concurrency/worker notes
- 다른 message CRUD와 동일한 sync bridge 동작.

- Failure modes and fixes
- backend가 미존재 step id를 무시할 수 있으므로 필요한 경우 사전 검증.

- Examples

```python
client.delete_message(thread_id="thread-1", message_id=msg_id)
```

```python
# Edge/failure
try:
    client.delete_message("thread-1", "missing-id")
except Exception as exc:
    print(exc)
```

### 3.11 `EasierlitClient.set_worker_crash_handler` (Advanced)

- Signature

```python
def set_worker_crash_handler(self, handler: Callable[[str], None] | None) -> None
```

- Purpose
- 워커 크래시 traceback을 수신하는 콜백을 등록/해제합니다.

- When to call / When not to call
- 운영/관측 고급 시나리오에서 호출.
- `run_func` 내부 예외 처리를 대체하는 용도로 쓰지 않습니다.

- Parameters
- `handler`: traceback 문자열을 받는 콜백, 또는 해제를 위한 `None`.

- Returns
- `None`.

- Raises
- 직접 예외는 거의 없습니다.

- Side effects
- 기존 핸들러를 원자적으로 교체합니다.

- Concurrency/worker notes
- 내부 lock으로 보호됩니다.
- 핸들러는 에러 경로에서 실행되므로 빠르고 안전해야 합니다.

- Failure modes and fixes
- 핸들러 내부 예외는 운영 리스크이므로 최소 로직만 유지하세요.

- Examples

```python
def on_crash(tb: str) -> None:
    print("Worker crashed:", tb.splitlines()[-1])

client.set_worker_crash_handler(on_crash)
```

```python
# Edge: 핸들러 해제
client.set_worker_crash_handler(None)
```

### 3.12 `EasierlitClient.peek_worker_error` (Advanced)

- Signature

```python
def peek_worker_error(self) -> str | None
```

- Purpose
- 최근 워커 traceback 캐시를 소비 없이 조회합니다.

- When to call / When not to call
- 진단/종료 요약 로그 용도로 호출.
- `None`을 완전한 정상 보장으로 해석하지 않습니다.

- Parameters
- 없음.

- Returns
- traceback 문자열 또는 `None`.

- Raises
- 직접 예외는 거의 없습니다.

- Side effects
- 없음.

- Concurrency/worker notes
- lock으로 보호된 읽기입니다.

- Failure modes and fixes
- 타이밍상 `None`이어도 이후 `stop()`에서 에러가 전파될 수 있으므로 최종 상태는 `stop()` 기준으로 판단.

- Examples

```python
err = client.peek_worker_error()
if err:
    print(err)
```

```python
# Edge: 정상 종료 후
assert client.peek_worker_error() is None
```

## 4. EasierlitApp

### 4.1 `EasierlitApp.recv`

- Signature

```python
def recv(self, timeout: float | None = None) -> IncomingMessage
```

- Purpose
- inbound 큐에서 다음 사용자 메시지를 꺼냅니다.

- When to call / When not to call
- worker loop에서 호출.
- app이 닫힌 뒤에는 호출하지 않습니다.

- Parameters
- `timeout`: 대기 시간(초), `None`이면 무기한 블록.

- Returns
- `IncomingMessage`.

- Raises
- 타임아웃 시 `TimeoutError`.
- 종료 상태/종료 sentinel 수신 시 `AppClosedError`.

- Side effects
- inbound 큐에서 항목 1개 소비.

- Concurrency/worker notes
- process-safe 큐 사용.

- Failure modes and fixes
- 빈번한 timeout: idle tick으로 간주하고 루프 지속.
- closed app: 루프를 종료하도록 처리.

- Examples

```python
try:
    incoming = app.recv(timeout=1.0)
except TimeoutError:
    pass
```

```python
# Edge/failure
try:
    app.recv(timeout=0.1)
except Exception as exc:
    print(type(exc).__name__)  # TimeoutError or AppClosedError
```

### 4.2 `EasierlitApp.send`

- Signature

```python
def send(
    self,
    thread_id: str,
    content: str,
    author: str = "Assistant",
    metadata: dict | None = None,
) -> str
```

- Purpose
- send 명령을 큐에 적재하고 message id를 반환합니다.

- When to call / When not to call
- `run_func`에서 assistant 출력을 보낼 때 호출.
- `close()` 이후에는 호출하지 않습니다.

- Parameters
- `thread_id`, `content`, `author`, `metadata`.

- Returns
- 생성된 `message_id` 문자열.

- Raises
- app 종료 상태면 `AppClosedError`.
- payload 직렬화 불가면 `TypeError`.

- Side effects
- `OutgoingCommand(command="send")`를 outgoing 큐에 적재.

- Concurrency/worker notes
- process-safe 큐 경로.

- Failure modes and fixes
- pickle 오류: metadata를 JSON 유사 타입으로 유지.

- Examples

```python
msg_id = app.send(thread_id=incoming.thread_id, content="hello", author="Bot")
```

```python
# Edge/failure: 직렬화 불가능 metadata
try:
    app.send("thread-1", "x", metadata={"bad": lambda x: x})
except TypeError:
    ...
```

### 4.3 `EasierlitApp.update_message`

- Signature

```python
def update_message(
    self,
    thread_id: str,
    message_id: str,
    content: str,
    metadata: dict | None = None,
) -> None
```

- Purpose
- update 명령을 큐에 적재합니다.

- When to call / When not to call
- 기존 message id를 수정할 때 호출.
- `close()` 이후에는 호출하지 않습니다.

- Parameters
- `thread_id`, `message_id`, `content`, `metadata`.

- Returns
- `None`.

- Raises
- `AppClosedError`, `TypeError` (`send`와 동일 제약).

- Side effects
- `OutgoingCommand(command="update")` 큐 적재.

- Concurrency/worker notes
- process-safe 큐 경로.

- Failure modes and fixes
- 미존재 id 처리 결과는 downstream backend 정책 의존.

- Examples

```python
app.update_message("thread-1", msg_id, "new content")
```

```python
# Edge/failure
app.close()
try:
    app.update_message("thread-1", msg_id, "x")
except AppClosedError:
    ...
```

### 4.4 `EasierlitApp.delete_message`

- Signature

```python
def delete_message(self, thread_id: str, message_id: str) -> None
```

- Purpose
- delete 명령을 큐에 적재합니다.

- When to call / When not to call
- 메시지 제거 시 호출.
- `close()` 이후에는 호출하지 않습니다.

- Parameters
- `thread_id`, `message_id`.

- Returns
- `None`.

- Raises
- `AppClosedError`, `TypeError`.

- Side effects
- `OutgoingCommand(command="delete")` 큐 적재.

- Concurrency/worker notes
- process-safe 큐 경로.

- Failure modes and fixes
- 미존재 id 처리 결과는 backend 정책 의존.

- Examples

```python
app.delete_message("thread-1", msg_id)
```

```python
# Edge/failure
app.close()
try:
    app.delete_message("thread-1", msg_id)
except AppClosedError:
    ...
```

### 4.5 `EasierlitApp.close`

- Signature

```python
def close(self) -> None
```

- Purpose
- 브리지를 종료하고 dispatcher/recv 루프 종료 신호를 보냅니다.

- When to call / When not to call
- 정상 종료 경로에서 호출.
- 여러 번 호출해도 안전합니다.

- Parameters
- 없음.

- Returns
- `None`.

- Raises
- 의도된 공개 예외 없음.

- Side effects
- closed 플래그 설정.
- inbound sentinel `None` 적재.
- outbound `OutgoingCommand(command="close")` 적재.

- Concurrency/worker notes
- idempotent 종료 신호.

- Failure modes and fixes
- 루프가 계속 돌면 `AppClosedError` 또는 close command 처리 여부를 확인.

- Examples

```python
app.close()
```

```python
# Edge: idempotent
app.close()
app.close()
```

### 4.6 `EasierlitApp.is_closed`

- Signature

```python
def is_closed(self) -> bool
```

- Purpose
- 브리지 종료 상태를 확인합니다.

- When to call / When not to call
- 루프/폴링 조건에서 호출.

- Parameters
- 없음.

- Returns
- 종료 시 `True`, 아니면 `False`.

- Raises
- 없음.

- Side effects
- 없음.

- Concurrency/worker notes
- multiprocessing event 상태 읽기.

- Failure modes and fixes
- 없음.

- Examples

```python
if app.is_closed():
    return
```

```python
# Edge
app.close()
assert app.is_closed() is True
```

## 5. 설정/데이터 모델

### 5.1 `EasierlitAuthConfig`

- Signature

```python
EasierlitAuthConfig(
    username: str,
    password: str,
    identifier: str | None = None,
    metadata: dict[str, Any] | None = None,
)
```

- Contract
- `username`, `password`는 공백-only/빈 문자열 불가.
- `identifier=None`이면 런타임에서 `username`을 identifier로 사용.
- `metadata=None`이면 인증 콜백/런타임 처리 시 `{}`로 사용.

- Raises
- username/password가 비어있거나 공백-only면 `ValueError`.
- 제거된 키워드 인자를 전달하면 `TypeError`.

### 5.2 `EasierlitPersistenceConfig`

- Signature

```python
EasierlitPersistenceConfig(
    enabled: bool = True,
    sqlite_path: str = ".chainlit/easierlit.db",
)
```

- Contract
- 기본 SQLite data layer bootstrap 허용 여부 제어.
- 외부 data layer env/config가 이미 있으면 Easierlit가 덮어쓰지 않음.

### 5.3 `IncomingMessage`

- Signature

```python
IncomingMessage(
    thread_id: str,
    session_id: str,
    message_id: str,
    content: str,
    author: str,
    created_at: str | None = None,
    metadata: dict[str, Any] = {},
)
```

- Contract
- Chainlit callback 파이프라인에서 생성되고 `app.recv()` 소비 대상으로 사용.

### 5.4 `OutgoingCommand`

- Signature

```python
OutgoingCommand(
    command: Literal["send", "update", "delete", "close"],
    thread_id: str | None = None,
    message_id: str | None = None,
    content: str | None = None,
    author: str = "Assistant",
    metadata: dict[str, Any] = {},
)
```

- Contract
- app -> runtime dispatch를 위한 내부 명령 envelope.
- 공개 export되지만 일반 사용자 직접 생성 대상은 아님.

## 6. 예외 매트릭스 + 트러블슈팅 맵

| Exception | 대표 트리거 | 1차 대응 |
| --- | --- | --- |
| `ValueError` | `worker_mode` 오류, 사용자 미존재, thread 미존재, auth 필드 오류 | 입력값/identifier/thread id 검증 |
| `WorkerAlreadyRunningError` | 워커 실행 중 `client.run()` 재호출 | 먼저 `client.stop()` 수행 |
| `RunFuncExecutionError` | 워커 크래시가 `stop()`에서 전파 | `peek_worker_error()`/traceback 확인 후 `run_func` 수정 |
| `DataPersistenceNotEnabledError` | data layer 없이 Thread CRUD 호출 | persistence 활성화 또는 외부 data layer 구성 |
| `ThreadSessionNotActiveError` | active session/data layer 모두 없이 Message CRUD | 활성 session 사용 또는 persistence fallback 활성화 |
| `AppClosedError` | close 이후 `EasierlitApp` 사용 | 루프 종료 후 라이프사이클 재시작 |
| `TimeoutError` | `app.recv(timeout=...)` 시간 만료 | idle tick으로 처리하고 루프 지속 |
| `TypeError` | process 직렬화 실패(`run_func`/payload) | top-level 함수 + JSON 유사 payload 사용 |
| `RuntimeError` | Chainlit 이벤트 루프에서 sync wait 시도 | worker 문맥에서 client message 메서드 호출 |

## 7. Chainlit Message vs Tool-call 매핑

Chainlit step 분류:

- message step: `user_message`, `assistant_message`, `system_message`
- tool/run 계열: `tool`, `run`, `llm`, `embedding`, `retrieval`, `rerank`, `undefined`

Easierlit v0.1.0 매핑:

- `app.recv()`는 user-message 흐름을 소비.
- `app.send()` / `client.add_message()`는 assistant-message 흐름을 생성.
- tool-call step 전용 공개 API는 제공하지 않음.

UI 참고: Chainlit `ui.cot`는 `full`, `tool_call`, `hidden` 지원.

## 8. Method-to-Example 인덱스

| Method | Primary Example |
| --- | --- |
| `EasierlitServer.__init__`, `serve` | `examples/minimal.py`, `examples/custom_auth.py` |
| `EasierlitClient.run`, `stop` | `examples/minimal.py` |
| `EasierlitClient.list_threads`, `get_thread`, `update_thread`, `delete_thread` | `examples/thread_crud.py`, `examples/thread_create_in_run_func.py` |
| `EasierlitClient.add_message`, `update_message`, `delete_message` | `examples/thread_create_in_run_func.py` |
| `EasierlitApp.recv`, `send` | `examples/minimal.py` |
| `EasierlitApp.update_message`, `delete_message` | `tests/test_app_queue.py` |
| `set_worker_crash_handler`, `peek_worker_error` | `tests/test_client_worker.py`, `src/easierlit/server.py` |
