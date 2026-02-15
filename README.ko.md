[English](README.md) | [한국어](README.ko.md)

# Easierlit

[![Version](https://img.shields.io/badge/version-0.1.0-2563eb)](pyproject.toml)
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
- `EasierlitClient`: 워커에서 `run_func(app)` 실행
- `EasierlitApp`: 입력/출력 큐 브리지
- 운영 기본값이 실용적입니다.
- headless 서버 실행
- sidebar 기본 상태 `open`
- JWT secret 자동관리 (`.chainlit/jwt.secret`)
- 전용 auth cookie (`easierlit_access_token`)
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
  -> app.send(...) / client.* CRUD
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

        app.send(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


client = EasierlitClient(run_func=run_func, worker_mode="thread")
server = EasierlitServer(client=client)
server.serve()  # blocking
```

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

메서드별 정확한 계약은 아래 문서를 우선 참고하세요.

- `docs/api-reference.ko.md`

각 공개 메서드의 파라미터 제약, 반환, 예외, 부작용, 동시성 주의점, 실패 대응을 정밀하게 다룹니다.

## 인증/영속성 기본값

- JWT secret: `.chainlit/jwt.secret` 자동관리
- 인증 cookie: `easierlit_access_token`
- 기본 persistence: `.chainlit/easierlit.db` (SQLite)
- SQLite 스키마 불일치 시 백업 후 재생성
- sidebar 기본 상태는 `open`으로 강제

Thread History 표시 조건(Chainlit 정책):

- `requireLogin=True`
- `dataPersistence=True`

Easierlit에서 일반적인 구성:

- `auth=EasierlitAuthConfig(...)` 설정
- persistence 기본값 유지

## Message / Thread 작업

Message API:

- `app.send(...)`
- `app.update_message(...)`
- `app.delete_message(...)`
- `client.add_message(...)`
- `client.update_message(...)`
- `client.delete_message(...)`

Thread API:

- `client.list_threads(...)`
- `client.get_thread(thread_id)`
- `client.update_thread(...)`
- `client.delete_thread(thread_id)`

동작 핵심:

- auth 설정 시 `client.update_thread(...)`는 소유자를 자동 귀속
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

Easierlit v0.1.0 공개 API는 메시지 중심이며,
전용 tool-call step 생성 API는 아직 제공하지 않습니다.

## 예제 맵

- `examples/minimal.py`: 기본 echo bot
- `examples/custom_auth.py`: 단일 계정 인증
- `examples/thread_crud.py`: thread list/get/update/delete
- `examples/thread_create_in_run_func.py`: `run_func`에서 thread 생성

## 문서 맵

- 메서드 API 계약(EN): `docs/api-reference.en.md`
- 메서드 API 계약(KO): `docs/api-reference.ko.md`
- 상세 사용 가이드(EN): `docs/usage.en.md`
- 상세 사용 가이드(KO): `docs/usage.ko.md`

## 마이그레이션 노트

과거 초안에서 제거된 API는 v0.1.0 공개 사용 범위에 포함되지 않습니다.
README 및 API 레퍼런스에 명시된 API만 사용하세요.
