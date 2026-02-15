from easierlit import (
    AppClosedError,
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitPersistenceConfig,
    EasierlitServer,
    EasierlitApp,
)

def run_func(app: EasierlitApp):
    help_text = (
        "Commands:\n"
        "- /new [name]: create a new thread from run_func\n"
        "- /get <thread_id>: fetch thread metadata\n"
        "- anything else: echo in current thread\n"
        "- note: internal worker errors stop the server (see logs for traceback)"
    )

    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        text = incoming.content.strip()
        command_name = text.split(maxsplit=1)[0] if text else "(empty)"

        try:
            if text == "/help":
                app.send(
                    thread_id=incoming.thread_id,
                    content=help_text,
                    author="ThreadCreator",
                )
                continue

            if text.startswith("/new"):
                raw_name = text[len("/new") :].strip()
                thread_name = raw_name or "Created from run_func"

                # Public API only: create thread + add first message.
                # Easierlit normalizes this for SQLite-backed SQLAlchemyDataLayer.
                thread_id = app.new_thread(
                    name=thread_name,
                    metadata={
                        "created_by": "run_func",
                        "source_thread_id": incoming.thread_id,
                    },
                    tags=["run-func-created"],
                )
                app.send(
                    thread_id=thread_id,
                    content=(
                        "This thread was created inside run_func.\n"
                        f"Source thread: {incoming.thread_id}"
                    ),
                    author="ThreadCreator",
                    metadata={"kind": "thread-bootstrap"},
                )

                app.send(
                    thread_id=incoming.thread_id,
                    content=(
                        f"Created new thread.\n"
                        f"- id: {thread_id}\n"
                        f"- name: {thread_name}\n"
                        "- owner: auto-assigned to current login user\n"
                        f"Use /get {thread_id} to verify."
                    ),
                    author="ThreadCreator",
                )
                continue

            if text.startswith("/get "):
                target_id = text[len("/get ") :].strip()
                if not target_id:
                    app.send(
                        thread_id=incoming.thread_id,
                        content="Usage: /get <thread_id>",
                        author="ThreadCreator",
                    )
                    continue

                try:
                    thread = app.get_thread(target_id)
                except ValueError:
                    app.send(
                        thread_id=incoming.thread_id,
                        content=f"Thread not found: {target_id}",
                        author="ThreadCreator",
                    )
                    continue

                app.send(
                    thread_id=incoming.thread_id,
                    content=(
                        f"Thread lookup result\n"
                        f"- id: {thread.get('id')}\n"
                        f"- name: {thread.get('name')}\n"
                        f"- userId: {thread.get('userId')}\n"
                        f"- userIdentifier: {thread.get('userIdentifier')}\n"
                        f"- metadata: {thread.get('metadata')}"
                    ),
                    author="ThreadCreator",
                )
                continue

            app.send(
                thread_id=incoming.thread_id,
                content=(
                    f"Echo: {incoming.content}\n\nType /help for commands."
                ),
                author="ThreadCreator",
            )
        except Exception as exc:
            raise RuntimeError(f"command '{command_name}' failed: {exc}") from exc


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
