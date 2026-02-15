from easierlit import (
    AppClosedError,
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitPersistenceConfig,
    EasierlitServer,
)

def run_func(app):
    help_text = (
        "Commands:\n"
        "- /threads: list recent threads for admin user\n"
        "- /rename <name>: rename current thread\n"
        "- /delete <thread_id>: delete a thread by id\n"
        "- anything else: echo"
    )

    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        text = incoming.content.strip()

        if text == "/help":
            app.send(
                thread_id=incoming.thread_id,
                content=help_text,
                author="ThreadBot",
            )
            continue

        if text == "/threads":
            threads = app.list_threads(first=10, user_identifier="admin")
            if not threads.data:
                content = "No threads found."
            else:
                lines = ["Recent threads:"]
                for item in threads.data:
                    lines.append(f"- {item['id']} | {item.get('name') or '(no name)'}")
                content = "\n".join(lines)

            app.send(
                thread_id=incoming.thread_id,
                content=content,
                author="ThreadBot",
            )
            continue

        if text.startswith("/rename "):
            new_name = text[len("/rename ") :].strip()
            if not new_name:
                app.send(
                    thread_id=incoming.thread_id,
                    content="Usage: /rename <name>",
                    author="ThreadBot",
                )
                continue

            app.update_thread(incoming.thread_id, name=new_name)
            app.send(
                thread_id=incoming.thread_id,
                content=f"Renamed current thread to: {new_name}",
                author="ThreadBot",
            )
            continue

        if text.startswith("/delete "):
            thread_id = text[len("/delete ") :].strip()
            if not thread_id:
                app.send(
                    thread_id=incoming.thread_id,
                    content="Usage: /delete <thread_id>",
                    author="ThreadBot",
                )
                continue

            app.delete_thread(thread_id)
            app.send(
                thread_id=incoming.thread_id,
                content=f"Deleted thread: {thread_id}",
                author="ThreadBot",
            )
            continue

        app.send(
            thread_id=incoming.thread_id,
            content=(
                f"Thread: {incoming.thread_id}\n"
                f"Message: {incoming.content}\n\n"
                "Type /help for CRUD commands."
            ),
            author="ThreadBot",
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
