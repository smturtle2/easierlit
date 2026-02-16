# Easierlit 사용 가이드 (v0.4.0)

이 문서는 Easierlit v0.4.0의 상세 사용 레퍼런스입니다.
메서드 단위의 정확한 계약(시그니처/예외/실패모드)은 아래 API 레퍼런스를 우선 참고하세요.

- `docs/api-reference.en.md`
- `docs/api-reference.ko.md`

## 1. 범위와 버전

- 대상 버전: `0.4.0`
- 런타임 코어: Chainlit (`chainlit>=2.9,<3`)
- 현재 공개 API 기준만 다룹니다.

## 2. 아키텍처

Easierlit은 3개 구성 요소로 동작합니다.

- `EasierlitServer`: 메인 프로세스에서 Chainlit 시작
- `EasierlitClient`: 전역 thread 워커 1개에서 `run_func(app)` 실행
- `EasierlitApp`: 사용자 입력과 출력 명령을 연결하는 큐 브리지

상위 흐름:

1. `server.serve()`가 runtime bind 후 Chainlit 실행
2. Chainlit `on_message`가 입력을 `IncomingMessage`로 변환
3. 워커가 `app.recv()` 또는 `await app.arecv()`로 입력을 소비
4. 워커가 `app.*` API(message + thread CRUD)로 출력/저장

## 3. 표준 부트스트랩 패턴

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
server.serve()
```

참고:

- `serve()`는 블로킹입니다.
- `worker_mode`는 `"thread"`만 지원합니다.
- `run_func`는 sync/async 모두 지원하며 기본값 `run_func_mode="auto"`가 자동 판별합니다.

## 4. 공개 API 시그니처

이 섹션은 시그니처 요약입니다. 상세 메서드 계약은 `docs/api-reference.ko.md`를 참고하세요.

```python
EasierlitServer(
    client,
    host="127.0.0.1",
    port=8000,
    root_path="",
    auth=None,
    persistence=None,
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
EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/easierlit.db")
```

## 5. 서버 런타임 정책

Easierlit 서버는 다음 기본값을 강제합니다.

- Chainlit headless 모드 활성
- sidebar 기본 상태 `open`
- CoT 모드 `full` 강제
- `CHAINLIT_AUTH_COOKIE_NAME=easierlit_access_token`
- JWT secret 자동 관리(`.chainlit/jwt.secret`)
- `run_func` fail-fast: 워커 예외 시 서버 종료 트리거

## 6. 인증과 영속성

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
from easierlit import EasierlitPersistenceConfig, EasierlitServer

persistence = EasierlitPersistenceConfig(
    enabled=True,
    sqlite_path=".chainlit/easierlit.db",
)

server = EasierlitServer(client=client, persistence=persistence)
```

Thread History 표시 조건(Chainlit 정책):

- `requireLogin=True`
- `dataPersistence=True`

## 7. run_func 작성 패턴과 오류 처리

권장 구조:

1. sync `run_func`: `app.recv(timeout=...)` 기반 long-running loop
2. async `run_func`: `await app.arecv()` 기반 loop (필요 시 `await app.arecv(timeout=...)`)
3. timeout 사용 시 `TimeoutError`는 idle tick으로 처리
4. `AppClosedError`에서 루프 종료
5. 명령 단위 예외는 문맥을 붙여 로그 가독성 확보

`run_func`에서 처리되지 않은 예외가 발생하면:

- Easierlit가 traceback을 로그에 남김
- 서버 종료를 트리거함
- 종료 진행 중 입력 enqueue는 요약 메시지 방식으로 억제

## 8. App에서 Thread CRUD

`EasierlitApp` 메서드:

- `list_threads(first=20, cursor=None, search=None, user_identifier=None)`
- `get_thread(thread_id)`
- `get_history(thread_id) -> dict`
- `new_thread(name=None, metadata=None, tags=None) -> str`
- `update_thread(thread_id, name=None, metadata=None, tags=None)`
- `delete_thread(thread_id)`

동작 상세:

- Thread CRUD는 data layer가 필요합니다.
- `new_thread`는 고유한 thread id를 자동 생성하고 반환합니다.
- `update_thread`는 대상 thread가 이미 있을 때만 수정합니다.
- `get_history`은 thread 메타데이터와 `thread["steps"]` 순서 보존 `items` 단일 목록을 반환합니다.
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

Easierlit v0.4.0 매핑:

- `app.recv()` 입력은 사용자 메시지 흐름
- `app.arecv()` 입력도 동일한 사용자 메시지 흐름 계약을 따름
- `app.add_message()` 출력은 assistant 메시지 흐름
- `app.add_tool()/app.update_tool()` 출력은 tool-call 흐름이며 step name=`tool_name`
- `app.add_thought()/app.update_thought()` 출력은 tool-call 흐름이며 step name=`Reasoning` 고정

UI 옵션 참고(Chainlit): `ui.cot`는 `full`, `tool_call`, `hidden`을 지원합니다.

## 12. 트러블슈팅

`Cannot enqueue incoming message to a closed app`:

- 의미: 워커/app이 이미 닫혔고 대개 `run_func` 크래시 이후 상태
- 조치: 서버 traceback 원인 수정 후 재시작

`Data persistence is not enabled`:

- 의미: thread CRUD 또는 fallback에 data layer 필요
- 조치: persistence(기본값) 유지 또는 외부 data layer 구성

설정 변경 후 `Invalid authentication token`:

- 의미: 브라우저 토큰 stale 또는 secret mismatch
- 조치: 서버 재시작 후 재로그인(`easierlit_access_token` 사용)

SQLite `tags` 바인딩 이슈:

- Easierlit가 SQLite SQLAlchemyDataLayer에서 `tags`를 자동 정규화
- 문제가 계속되면 현재 프로젝트 코드가 실제로 import되는지 경로 확인

## 13. 예제

- `examples/minimal.py`
- `examples/custom_auth.py`
- `examples/thread_crud.py`
- `examples/thread_create_in_run_func.py`
- `examples/step_types.py`

## 14. 릴리스 체크리스트 (v0.4.0)

```bash
python3 -m py_compile examples/*.py
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

추가 확인:

- `pyproject.toml` version이 `0.4.0`
- 문서 링크 정상(`README.md`, `README.ko.md`, `docs/usage.en.md`, `docs/usage.ko.md`, `docs/api-reference.en.md`, `docs/api-reference.ko.md`)
