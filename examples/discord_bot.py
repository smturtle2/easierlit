from easierlit import EasierlitClient, EasierlitDiscordConfig, EasierlitServer


def on_message(app, incoming):
    app.add_message(
        thread_id=incoming.thread_id,
        content=f"Discord echo: {incoming.content}",
        author="DiscordBot",
    )


if __name__ == "__main__":
    # Auth is enabled by default on EasierlitServer(auth=None).
    # Login with configured credentials before using the Discord bridge.
    client = EasierlitClient(on_message=on_message)

    # Option A: explicit config token (highest priority).
    discord = EasierlitDiscordConfig(bot_token="your-discord-bot-token")

    # Option B: env fallback.
    # os.environ["DISCORD_BOT_TOKEN"] = "your-discord-bot-token"
    # discord = EasierlitDiscordConfig()

    server = EasierlitServer(client=client, discord=discord)
    server.serve()
