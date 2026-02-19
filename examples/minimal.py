from easierlit import EasierlitClient, EasierlitPersistenceConfig, EasierlitServer


def on_message(app, incoming):
    app.add_message(
        thread_id=incoming.thread_id,
        content=f"Echo: {incoming.content}",
        author="EchoBot",
    )


if __name__ == "__main__":
    # Minimal example: auth/persistence are enabled by default.
    # Override credentials via EASIERLIT_AUTH_USERNAME/PASSWORD or auth=...
    client = EasierlitClient(on_message=on_message)
    persistence = EasierlitPersistenceConfig(local_storage_dir="~/.easierlit/minimal_example")
    server = EasierlitServer(client=client, persistence=persistence)
    server.serve()
