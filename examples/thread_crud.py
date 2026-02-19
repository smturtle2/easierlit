from easierlit import (
    EasierlitAuthConfig,
    EasierlitClient,
    EasierlitPersistenceConfig,
    EasierlitServer,
)


HELP_TEXT = (
    "Commands:\n"
    "- /threads: list recent threads for admin user\n"
    "- /rename <name>: rename current thread\n"
    "- /delete <thread_id>: delete a thread by id\n"
    "- anything else: echo"
)


def on_message(app, incoming):
    text = incoming.content.strip()

    if text == "/help":
        app.add_message(
            thread_id=incoming.thread_id,
            content=HELP_TEXT,
            author="ThreadBot",
        )
        return

    if text == "/threads":
        threads = app.list_threads(first=10, user_identifier="admin")
        if not threads.data:
            content = "No threads found."
        else:
            lines = ["Recent threads:"]
            for item in threads.data:
                lines.append(f"- {item['id']} | {item.get('name') or '(no name)'}")
            content = "\n".join(lines)

        app.add_message(
            thread_id=incoming.thread_id,
            content=content,
            author="ThreadBot",
        )
        return

    if text.startswith("/rename "):
        new_name = text[len("/rename ") :].strip()
        if not new_name:
            app.add_message(
                thread_id=incoming.thread_id,
                content="Usage: /rename <name>",
                author="ThreadBot",
            )
            return

        app.update_thread(incoming.thread_id, name=new_name)
        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Renamed current thread to: {new_name}",
            author="ThreadBot",
        )
        return

    if text.startswith("/delete "):
        thread_id = text[len("/delete ") :].strip()
        if not thread_id:
            app.add_message(
                thread_id=incoming.thread_id,
                content="Usage: /delete <thread_id>",
                author="ThreadBot",
            )
            return

        app.delete_thread(thread_id)
        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Deleted thread: {thread_id}",
            author="ThreadBot",
        )
        return

    app.add_message(
        thread_id=incoming.thread_id,
        content=(
            f"Thread: {incoming.thread_id}\n"
            f"Message: {incoming.content}\n\n"
            "Type /help for CRUD commands."
        ),
        author="ThreadBot",
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
