import time

from easierlit import (
    EasierlitApp,
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitPersistenceConfig,
    EasierlitServer,
)


HELP_TEXT = (
    "Commands:\n"
    "- /new [name]: create a new thread from on_message\n"
    "- /get <thread_id>: fetch thread metadata\n"
    "- anything else: echo in current thread\n"
    "- note: this example also creates one bootstrap thread inside run_func on startup"
)


def run_func(app: EasierlitApp):
    # Optional background worker: create one thread at startup.
    thread_id = app.new_thread(
        name="Created from run_func startup",
        metadata={"created_by": "run_func"},
        tags=["run-func-created"],
    )
    app.add_message(
        thread_id=thread_id,
        content="This thread was created from run_func during startup.",
        author="ThreadCreator",
        metadata={"kind": "thread-bootstrap"},
    )

    while not app.is_closed():
        time.sleep(0.2)



def on_message(app: EasierlitApp, incoming):
    text = incoming.content.strip()
    command_name = text.split(maxsplit=1)[0] if text else "(empty)"

    try:
        if text == "/help":
            app.add_message(
                thread_id=incoming.thread_id,
                content=HELP_TEXT,
                author="ThreadCreator",
            )
            return

        if text.startswith("/new"):
            raw_name = text[len("/new") :].strip()
            thread_name = raw_name or "Created from on_message"

            thread_id = app.new_thread(
                name=thread_name,
                metadata={
                    "created_by": "on_message",
                    "source_thread_id": incoming.thread_id,
                },
                tags=["on-message-created"],
            )
            app.add_message(
                thread_id=thread_id,
                content=(
                    "This thread was created from on_message.\n"
                    f"Source thread: {incoming.thread_id}"
                ),
                author="ThreadCreator",
                metadata={"kind": "thread-bootstrap"},
            )

            app.add_message(
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
            return

        if text.startswith("/get "):
            target_id = text[len("/get ") :].strip()
            if not target_id:
                app.add_message(
                    thread_id=incoming.thread_id,
                    content="Usage: /get <thread_id>",
                    author="ThreadCreator",
                )
                return

            try:
                thread = app.get_thread(target_id)
            except ValueError:
                app.add_message(
                    thread_id=incoming.thread_id,
                    content=f"Thread not found: {target_id}",
                    author="ThreadCreator",
                )
                return

            app.add_message(
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
            return

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}\n\nType /help for commands.",
            author="ThreadCreator",
        )
    except Exception as exc:
        raise RuntimeError(f"command '{command_name}' failed: {exc}") from exc


if __name__ == "__main__":
    client = EasierlitClient(on_message=on_message, run_funcs=[run_func], worker_mode="thread")
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
