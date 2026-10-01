package cn.wudabin.wifeng;

import cn.wudabin.wifeng.http.ControlServer;
import cn.wudabin.wifeng.connection.AutoConnectService;
import cn.wudabin.wifeng.perception.ChatEventStore;
import cn.wudabin.wifeng.perception.PerceptionService;
import cn.wudabin.wifeng.memory.ContainerMemoryService;
import cn.wudabin.wifeng.task.TaskEngine;
import net.fabricmc.api.ClientModInitializer;
import net.minecraft.client.MinecraftClient;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

public final class WifeNgClient implements ClientModInitializer {
    public static final String MOD_ID = "wife_ng";
    public static final Logger LOGGER = LogManager.getLogger(MOD_ID);
    public static final ChatEventStore CHAT_EVENTS = new ChatEventStore();

    private PerceptionService perception;
    private TaskEngine tasks;
    private ControlServer server;
    private AutoConnectService autoConnect;
    private ContainerMemoryService containerMemory;
    private static WifeNgClient instance;

    @Override
    public void onInitializeClient() {
        instance = this;
        MinecraftClient client = MinecraftClient.getInstance();
        perception = new PerceptionService(CHAT_EVENTS);
        tasks = new TaskEngine(perception);
        autoConnect = new AutoConnectService();
        containerMemory = new ContainerMemoryService();
        server = new ControlServer(perception, tasks, containerMemory);
        server.start();

        LOGGER.info("Wife NG client initialized; control API is listening on {}", server.getAddress());
    }

    public static void onEndClientTick(MinecraftClient client) {
        WifeNgClient current = instance;
        if (current != null) {
            current.tick(client);
        }
    }

    private void tick(MinecraftClient client) {
        try {
            autoConnect.tick(client);
            perception.tick(client);
            containerMemory.tick(client);
            tasks.tick(client);
        } catch (Throwable error) {
            LOGGER.error("Unhandled Wife NG tick error", error);
        }
    }
}
