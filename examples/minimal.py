from easierlit import AppClosedError, EasierlitClient, EasierlitServer


def run_func(app):
    while True:
        try:
            incoming = app.recv(timeout=1.0)
        except TimeoutError:
            continue
        except AppClosedError:
            break

        app.add_message(
            thread_id=incoming.thread_id,
            content=f"Echo: {incoming.content}",
            author="EchoBot",
        )


if __name__ == "__main__":
    # Minimal example: auth/persistence are enabled by default.
    # Override credentials via EASIERLIT_AUTH_USERNAME/PASSWORD or auth=...
    client = EasierlitClient(run_func=run_func, worker_mode="thread")
    server = EasierlitServer(client=client)
    server.serve()
