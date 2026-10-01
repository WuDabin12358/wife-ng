package cn.wudabin.wifeng.memory;

import cn.wudabin.wifeng.WifeNgClient;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.block.Block;
import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.GenericContainerScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

public final class ContainerMemoryService {
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
    private final Path file = FabricLoader.getInstance().getGameDir().resolve("wife-ng").resolve("containers.json");
    private final Map<String, JsonObject> memories = new LinkedHashMap<>();
    private int ticks;
    private String lastSignature = "";

    public ContainerMemoryService() {
        load();
    }

    public synchronized void tick(MinecraftClient client) {
        if (++ticks % 10 != 0 || client.player == null || client.world == null
                || !(client.player.currentScreenHandler instanceof GenericContainerScreenHandler)) return;
        GenericContainerScreenHandler handler = (GenericContainerScreenHandler) client.player.currentScreenHandler;
        BlockPos pos = nearestContainer(client, 6);
        if (pos == null) return;
        JsonObject memory = new JsonObject();
        memory.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
        memory.addProperty("x", pos.getX());
        memory.addProperty("y", pos.getY());
        memory.addProperty("z", pos.getZ());
        memory.addProperty("observed_at", Instant.now().toString());
        JsonArray items = new JsonArray();
        int containerSlots = handler.getRows() * 9;
        for (int index = 0; index < containerSlots; index++) {
            Slot slot = handler.getSlot(index);
            if (!slot.hasStack()) continue;
            ItemStack stack = slot.getStack();
            JsonObject item = new JsonObject();
            item.addProperty("slot", index);
            item.addProperty("item", Registry.ITEM.getId(stack.getItem()).toString());
            item.addProperty("count", stack.getCount());
            item.addProperty("name", stack.getName().getString());
            items.add(item);
        }
        memory.add("items", items);
        String key = memory.get("dimension").getAsString() + ":" + pos.getX() + ":" + pos.getY() + ":" + pos.getZ();
        String signature = key + ":" + items.toString();
        if (signature.equals(lastSignature)) return;
        lastSignature = signature;
        memories.put(key, memory);
        save();
    }

    public synchronized JsonArray snapshot() {
        JsonArray result = new JsonArray();
        for (JsonObject memory : memories.values()) {
            result.add(new JsonParser().parse(memory.toString()));
        }
        return result;
    }

    private void load() {
        try {
            if (!Files.exists(file)) return;
            JsonArray array = new JsonParser().parse(new String(Files.readAllBytes(file), StandardCharsets.UTF_8)).getAsJsonArray();
            for (JsonElement element : array) {
                JsonObject memory = element.getAsJsonObject();
                String key = memory.get("dimension").getAsString() + ":" + memory.get("x").getAsInt()
                        + ":" + memory.get("y").getAsInt() + ":" + memory.get("z").getAsInt();
                memories.put(key, memory);
            }
        } catch (Exception error) {
            WifeNgClient.LOGGER.warn("Could not load container memory", error);
        }
    }

    private void save() {
        try {
            Files.createDirectories(file.getParent());
            Path temporary = file.resolveSibling(file.getFileName().toString() + ".tmp");
            Files.write(temporary, GSON.toJson(snapshot()).getBytes(StandardCharsets.UTF_8));
            Files.move(temporary, file, StandardCopyOption.REPLACE_EXISTING);
        } catch (Exception error) {
            WifeNgClient.LOGGER.warn("Could not persist container memory", error);
        }
    }

    private static BlockPos nearestContainer(MinecraftClient client, int radius) {
        BlockPos origin = client.player.getBlockPos();
        BlockPos best = null;
        double bestDistance = Double.MAX_VALUE;
        for (BlockPos mutable : BlockPos.iterate(origin.add(-radius, -radius, -radius), origin.add(radius, radius, radius))) {
            Block block = client.world.getBlockState(mutable).getBlock();
            if (block != Blocks.CHEST && block != Blocks.TRAPPED_CHEST && block != Blocks.BARREL) continue;
            double dx = mutable.getX() - origin.getX();
            double dy = mutable.getY() - origin.getY();
            double dz = mutable.getZ() - origin.getZ();
            double distance = dx * dx + dy * dy + dz * dz;
            if (distance < bestDistance) {
                bestDistance = distance;
                best = mutable.toImmutable();
            }
        }
        return best;
    }
}
