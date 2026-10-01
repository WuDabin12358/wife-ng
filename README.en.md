# Wife NG

A Minecraft Java 1.16.5 client agent with DeepSeek planning, Fabric perception and task execution, and Baritone navigation.

[中文文档](README.md)

Player chat → model planning → one tool → in-game execution → verified result → next step.

Features include navigation, following, resource collection, crafting, smelting, container interactions, survival houses, creative blueprints, interior/site layout validation, pause/resume/cancel, and persistent world-specific planning memory.

## Build

Use JDK 21 and Python 3.10+ (tested with 3.12):

```sh
python scripts/fetch_dependencies.py
chmod +x gradlew
./gradlew build architectureChecks --no-daemon
python -m unittest discover -s tests -p 'test_*.py'
```

On Windows use `gradlew.bat`. Baritone is downloaded separately and checked against a pinned SHA-256.

## Run

Install a Java 8 Minecraft 1.16.5 client with Fabric Loader 0.19.3, Fabric API 0.42.0+1.16, the built Wife NG mod (not its sources JAR), and Baritone API Fabric 1.6.3.

Set `WIFE_NG_OWNER` to your player name, `WIFE_NG_BOT_NAME` to the bot's actual username, and `WIFE_NG_WORLD_ID` to a distinct world identifier. Set the same `WIFE_NG_TOKEN` in the client and planner environments; set `DEEPSEEK_API_KEY` for the planner. See [.env.example](.env.example) for reference; it is not automatically loaded.

Launch the client from the configured environment, join your world/server as the bot, then run `python WifeAgent.py`. Talk to the bot from your owner account: `wife，跟着我` (follow me), `wife，停止` (stop). The default prompt replies in Chinese. Use separate legitimate accounts when joining an online server together.

The HTTP bridge binds to loopback on port 8766. `GET /v1/capabilities` lists the actual tools. Privileged `server_command` is available only with `WIFE_NG_OP_MODE=true` and the account's server permissions.

Optional Windows HeadlessMC launchers require a separately installed HeadlessMC runtime and configured Minecraft instance; follow the Chinese README's setup instructions. Game files, JDKs, accounts, logs and worlds are excluded from this repository.

## Verification and contribution

CI builds the mod and runs offline Python and Java checks. These checks do not prove in-game task success. Verify deployments by JAR hash, client state, inventory/world changes and independent construction checks. See [testing](docs/TESTING.md), [architecture](docs/ARCHITECTURE.md), [contributing](CONTRIBUTING.md) and [security](SECURITY.md).

Original code is MIT licensed. Dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).

NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.
