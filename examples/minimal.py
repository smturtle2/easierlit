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


if __name__ == "__main__":
    # Minimal example: no auth callbacks configured.
    # (Chainlit policy: Thread History sidebar is shown only when
    # requireLogin=True and dataPersistence=True.)
    client = EasierlitClient(run_func=run_func, worker_mode="thread")
    server = EasierlitServer(client=client)
    server.serve()
