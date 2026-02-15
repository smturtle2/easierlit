# Easierlit

Easierlit은 Chainlit 위에 얇게 올린 래퍼로, Python 중심으로 챗 앱을 만들기 쉽게 구성되어 있습니다.

- `EasierlitServer`: 메인 프로세스에서 Chainlit 서버 실행
- `EasierlitClient`: 워커에서 사용자 로직(`run_func`) 실행
- `EasierlitApp`: 사용자 입력/출력 명령 브리지

이 문서는 **Easierlit v0.1.0** 기준입니다.

PyPI 기본 README는 영어(`README.md`)를 사용하고, 이 파일은 한국어 문서입니다.

## 설치

```bash
pip install easierlit
```

로컬 개발 설치:

```bash
pip install -e .
```

## 60초 빠른 시작

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

        app.send(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


client = EasierlitClient(run_func=run_func, worker_mode="thread")
server = EasierlitServer(client=client)
server.serve()
```

## 핵심 개념

- `run_func(app)`가 메인 처리 루프입니다.
- `app.recv()`로 사용자 메시지를 받습니다.
- `app.send()` 계열 API로 어시스턴트 출력을 보냅니다.
- `server.serve()`는 블로킹이며 headless Chainlit을 시작합니다.

라이프사이클 요약:

`server.serve()` -> Chainlit callbacks -> 워커의 `app.recv()` -> `app.send()` / `client.*` CRUD

## 공개 API (v0.1.0)

```python
EasierlitServer(
    client,
    host="127.0.0.1",
    port=8000,
    root_path="",
    auth=None,
    persistence=None,
)

EasierlitClient(run_func, worker_mode="thread")

EasierlitApp.recv(timeout=None)
EasierlitApp.send(thread_id, content, author="Assistant", metadata=None)
EasierlitApp.update_message(thread_id, message_id, content, metadata=None)
EasierlitApp.delete_message(thread_id, message_id)
EasierlitApp.close()

EasierlitAuthConfig(username, password, identifier=None, metadata=None)
EasierlitPersistenceConfig(enabled=True, sqlite_path=".chainlit/easierlit.db")
```

## 인증/영속성 기본값

- JWT secret은 `.chainlit/jwt.secret`에 자동 관리됩니다.
- 인증 쿠키 이름은 `easierlit_access_token`으로 고정됩니다.
- 기본 영속성(persistence)은 `.chainlit/easierlit.db` SQLite입니다.
- SQLite 스키마가 호환되지 않으면 백업 후 자동 재생성됩니다.
- 사이드바 기본 상태는 `open`으로 강제됩니다.

## Thread History 표시 조건

Chainlit 정책상 Thread History는 아래 두 조건이 모두 참일 때 표시됩니다.

- `requireLogin=True`
- `dataPersistence=True`

Easierlit에서 일반적으로는 다음을 의미합니다.

- `auth=EasierlitAuthConfig(...)` 설정
- 기본 persistence 활성 유지

## Message CRUD / Thread CRUD

Message API:

- `app.send(...)`
- `app.update_message(...)`
- `app.delete_message(...)`
- `client.add_message(...)`
- `client.update_message(...)`
- `client.delete_message(...)`

Thread API (data layer 기반):

- `client.list_threads(...)`
- `client.get_thread(thread_id)`
- `client.update_thread(...)`
- `client.delete_thread(thread_id)`

중요 동작:

- auth 설정 시 `client.update_thread(...)`는 auth 사용자 소유자로 자동 귀속됩니다.
- SQLite SQLAlchemyDataLayer에서는 thread `tags`를 자동 직렬화/역직렬화합니다.
- 활성 websocket session이 없을 때도 Easierlit가 내부 HTTP context를 초기화해 data layer fallback을 수행합니다.

## 워커 실패 정책 (fail-fast)

- `run_func`에서 예외 발생 시 서버 종료를 즉시 트리거합니다.
- 가능하면 UI에 짧은 요약 메시지를 보냅니다.
- 전체 traceback은 서버 로그에 출력됩니다.

## Chainlit의 Message vs Tool Call 구분

Chainlit은 step type으로 메시지와 도구/실행을 구분합니다.

메시지 타입:

- `user_message`
- `assistant_message`
- `system_message`

도구/실행 타입 예시:

- `tool`
- `run`
- `llm`
- `retrieval`
- `embedding`
- `rerank`

Easierlit v0.1.0 기준:

- `app.recv()`는 사용자 메시지 흐름을 소비합니다.
- `app.send()` / `client.add_message()`는 assistant message 흐름을 생성합니다.
- Easierlit 공개 API에는 전용 tool-call step 생성 API가 아직 없습니다.

UI 표시 관련(Chainlit): `ui.cot`는 `full`, `tool_call`, `hidden`을 지원합니다.

## 예제 맵

- `examples/minimal.py`: 기본 echo bot
- `examples/custom_auth.py`: 단일 계정 인증 설정
- `examples/thread_crud.py`: thread list/get/update/delete
- `examples/thread_create_in_run_func.py`: `run_func`에서 새 thread 생성

## 문서 링크

- API 레퍼런스(EN, 메서드 계약): `docs/api-reference.en.md`
- API 레퍼런스(KO): `docs/api-reference.ko.md`
- 상세 가이드(EN): `docs/usage.en.md`
- 한국어 개요: `README.ko.md`
- 상세 가이드(KO): `docs/usage.ko.md`

메서드별 정확한 계약(파라미터/반환/예외/실패모드)은 API 레퍼런스를 우선 참고하세요.

## 마이그레이션 노트

구버전 초안의 제거된 API는 v0.1.0 공개 사용법에 포함되지 않습니다.
위에 명시된 API만 사용하세요.
