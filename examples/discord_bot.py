from easierlit import (
    AppClosedError,
    EasierlitApp,
    EasierlitClient,
    EasierlitDiscordConfig,
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
            content=f"Discord echo: {incoming.content}",
            author="DiscordBot",
        )


if __name__ == "__main__":
    # Auth is enabled by default on EasierlitServer(auth=None).
    # Login with configured credentials before using the Discord bridge.
    client = EasierlitClient(run_func=run_func, worker_mode="thread")

    # Option A: explicit config token (highest priority).
    discord = EasierlitDiscordConfig(bot_token="your-discord-bot-token")

    # Option B: env fallback.
    # os.environ["DISCORD_BOT_TOKEN"] = "your-discord-bot-token"
    # discord = EasierlitDiscordConfig()

    server = EasierlitServer(client=client, discord=discord)
    server.serve()
