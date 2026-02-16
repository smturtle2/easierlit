from __future__ import annotations

from easierlit import AppClosedError, EasierlitClient, EasierlitServer


def run_func(app):
    help_text = (
        "Commands:\n"
        "- /tool <name> <output>: create a tool step\n"
        "- /update_tool <output>: update latest tool step in this thread\n"
        "- /thought <output>: create a reasoning step (tool name is Reasoning)\n"
        "- /update_thought <output>: update latest reasoning step in this thread\n"
        "- /delete_last: delete latest tool/thought steps in this thread\n"
        "- /demo: run one full thought + tool sequence\n"
        "- /help: show this help\n"
    )

    latest_tool_by_thread: dict[str, tuple[str, str]] = {}
    latest_thought_by_thread: dict[str, str] = {}

    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        thread_id = incoming.thread_id
        text = incoming.content.strip()

        if text == "/help":
            app.add_message(thread_id=thread_id, content=help_text, author="StepTypes")
            continue

        if text.startswith("/tool "):
            payload = text[len("/tool ") :].strip()
            tool_name, sep, output = payload.partition(" ")
            if not sep:
                app.add_message(
                    thread_id=thread_id,
                    content="Usage: /tool <name> <output>",
                    author="StepTypes",
                )
                continue

            message_id = app.add_tool(
                thread_id=thread_id,
                tool_name=tool_name,
                content=output,
                metadata={"kind": "manual-tool"},
            )
            latest_tool_by_thread[thread_id] = (message_id, tool_name)
            app.add_message(
                thread_id=thread_id,
                content=f"Created tool step `{tool_name}` with id `{message_id}`.",
                author="StepTypes",
            )
            continue

        if text.startswith("/update_tool "):
            output = text[len("/update_tool ") :].strip()
            latest = latest_tool_by_thread.get(thread_id)
            if not latest:
                app.add_message(
                    thread_id=thread_id,
                    content="No tool step to update. Create one with /tool first.",
                    author="StepTypes",
                )
                continue
            if not output:
                app.add_message(
                    thread_id=thread_id,
                    content="Usage: /update_tool <output>",
                    author="StepTypes",
                )
                continue

            message_id, tool_name = latest
            app.update_tool(
                thread_id=thread_id,
                message_id=message_id,
                tool_name=tool_name,
                content=output,
                metadata={"kind": "manual-tool", "updated": True},
            )
            app.add_message(
                thread_id=thread_id,
                content=f"Updated tool step `{tool_name}` ({message_id}).",
                author="StepTypes",
            )
            continue

        if text.startswith("/thought "):
            output = text[len("/thought ") :].strip()
            if not output:
                app.add_message(
                    thread_id=thread_id,
                    content="Usage: /thought <output>",
                    author="StepTypes",
                )
                continue

            message_id = app.add_thought(
                thread_id=thread_id,
                content=output,
                metadata={"kind": "reasoning"},
            )
            latest_thought_by_thread[thread_id] = message_id
            app.add_message(
                thread_id=thread_id,
                content=f"Created reasoning step with id `{message_id}`.",
                author="StepTypes",
            )
            continue

        if text.startswith("/update_thought "):
            output = text[len("/update_thought ") :].strip()
            message_id = latest_thought_by_thread.get(thread_id)
            if not message_id:
                app.add_message(
                    thread_id=thread_id,
                    content="No reasoning step to update. Create one with /thought first.",
                    author="StepTypes",
                )
                continue
            if not output:
                app.add_message(
                    thread_id=thread_id,
                    content="Usage: /update_thought <output>",
                    author="StepTypes",
                )
                continue

            app.update_thought(
                thread_id=thread_id,
                message_id=message_id,
                content=output,
                metadata={"kind": "reasoning", "updated": True},
            )
            app.add_message(
                thread_id=thread_id,
                content=f"Updated reasoning step ({message_id}).",
                author="StepTypes",
            )
            continue

        if text == "/delete_last":
            deleted = []

            latest_tool = latest_tool_by_thread.pop(thread_id, None)
            if latest_tool:
                app.delete_message(thread_id=thread_id, message_id=latest_tool[0])
                deleted.append(f"tool:{latest_tool[0]}")

            latest_thought = latest_thought_by_thread.pop(thread_id, None)
            if latest_thought:
                app.delete_message(thread_id=thread_id, message_id=latest_thought)
                deleted.append(f"thought:{latest_thought}")

            if not deleted:
                content = "No tool/thought steps to delete."
            else:
                content = "Deleted step ids: " + ", ".join(deleted)

            app.add_message(thread_id=thread_id, content=content, author="StepTypes")
            continue

        if text == "/demo":
            thought_id = app.add_thought(
                thread_id=thread_id,
                content="Plan: search docs -> rank snippets -> summarize.",
                metadata={"phase": "plan"},
            )
            app.update_thought(
                thread_id=thread_id,
                message_id=thought_id,
                content="Plan done. Starting retrieval.",
                metadata={"phase": "execute"},
            )

            tool_id = app.add_tool(
                thread_id=thread_id,
                tool_name="DocsSearch",
                content='{"query":"chainlit cot full", "top_k": 3}',
                metadata={"phase": "execute"},
            )
            app.update_tool(
                thread_id=thread_id,
                message_id=tool_id,
                tool_name="DocsSearch",
                content='{"hits": 3, "best_match": "ui.cot=full"}',
                metadata={"phase": "complete"},
            )

            latest_thought_by_thread[thread_id] = thought_id
            latest_tool_by_thread[thread_id] = (tool_id, "DocsSearch")

            app.add_message(
                thread_id=thread_id,
                content=(
                    "Demo completed.\n"
                    f"- thought id: {thought_id}\n"
                    f"- tool id: {tool_id}\n"
                    "You can run /update_thought, /update_tool, or /delete_last."
                ),
                author="StepTypes",
            )
            continue

        app.add_message(
            thread_id=thread_id,
            content=(
                "Unknown command.\n"
                "Type /help for tool/thought examples or /demo for a full sequence."
            ),
            author="StepTypes",
        )


if __name__ == "__main__":
    client = EasierlitClient(run_func=run_func, worker_mode="thread")
    server = EasierlitServer(client=client)
    server.serve()
