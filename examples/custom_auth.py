from easierlit import (
    AppClosedError,
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitApp,
    EasierlitPersistenceConfig,
    EasierlitServer,
)


def run_func(app: EasierlitApp):
    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Authenticated echo: {incoming.content}",
            author="SecureBot",
        )


if __name__ == "__main__":
    client = EasierlitClient(run_func=run_func, worker_mode="thread")
    auth = EasierlitAuthConfig(
        username="admin",
        password="admin",
        identifier="admin",
        metadata={"role": "admin"},
    )
    persistence = EasierlitPersistenceConfig(
        enabled=True,
        sqlite_path=".chainlit/easierlit.db",
    )
    server = EasierlitServer(client=client, auth=auth, persistence=persistence)
    server.serve()
