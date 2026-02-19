from easierlit import (
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitPersistenceConfig,
    EasierlitServer,
)


def on_message(app, incoming):
    app.add_message(
        thread_id=incoming.thread_id,
        content=f"Authenticated echo: {incoming.content}",
        author="SecureBot",
    )


if __name__ == "__main__":
    client = EasierlitClient(on_message=on_message)
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
