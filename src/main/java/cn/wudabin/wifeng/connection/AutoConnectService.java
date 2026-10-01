package cn.wudabin.wifeng.connection;

import cn.wudabin.wifeng.WifeNgClient;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ConnectScreen;
import net.minecraft.client.network.ServerInfo;

/** Connects a headless client to a configured server and retries after disconnects. */
public final class AutoConnectService {
    private static final long INITIAL_DELAY_TICKS = 40;
    private static final long RETRY_DELAY_TICKS = 1200;

    private final String host;
    private final int port;
    private long ticks;
    private long nextAttempt = INITIAL_DELAY_TICKS;
    private boolean connecting;

    public AutoConnectService() {
        String configured = System.getenv("WIFE_NG_SERVER");
        if (configured == null || configured.trim().isEmpty()) {
            host = "";
            port = 25565;
            return;
        }

        String value = configured.trim();
        int separator = value.lastIndexOf(':');
        if (separator > 0 && separator < value.length() - 1) {
            host = value.substring(0, separator);
            int parsedPort;
            try {
                parsedPort = Integer.parseInt(value.substring(separator + 1));
            } catch (NumberFormatException ignored) {
                parsedPort = 25565;
            }
            port = parsedPort;
        } else {
            host = value;
            port = 25565;
        }
    }

    public void tick(MinecraftClient client) {
        if (host.isEmpty()) {
            return;
        }
        ticks++;
        if (client.world != null && client.player != null) {
            connecting = false;
            nextAttempt = ticks + RETRY_DELAY_TICKS;
            return;
        }
        if (connecting && ticks < nextAttempt) {
            return;
        }

        // ConnectScreen has no durable completion callback in 1.16.5. If an
        // attempt never reaches a world, release the latch after the backoff.
        connecting = false;
        if (ticks < nextAttempt) {
            return;
        }

        connecting = true;
        nextAttempt = ticks + RETRY_DELAY_TICKS;
        WifeNgClient.LOGGER.info("Connecting to configured server {}:{}", host, port);
        ServerInfo server = new ServerInfo("wife-ng-auto", host + ":" + port, false);
        client.openScreen(new ConnectScreen(client.currentScreen, client, server));
    }
}
