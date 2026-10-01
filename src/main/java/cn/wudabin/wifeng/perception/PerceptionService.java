package cn.wudabin.wifeng.perception;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import cn.wudabin.wifeng.building.RegionSnapshot;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.ItemEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.registry.Registry;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

public final class PerceptionService {
    private static final int BASIC_INTERVAL_TICKS = 10;
    private static final int WORLD_INTERVAL_TICKS = 40;
    private static final int BLOCK_RADIUS = 8;
    private static final int ENTITY_RADIUS = 16;

    private final AtomicReference<JsonObject> latest = new AtomicReference<>(disconnectedSnapshot());
    private final ChatEventStore chatEvents;
    private int ticks;
    private JsonArray cachedBlocks = new JsonArray();
    private JsonArray cachedEntities = new JsonArray();
    private JsonObject cachedLayout = new JsonObject();

    public PerceptionService(ChatEventStore chatEvents) {
        this.chatEvents = chatEvents;
    }

    public void tick(MinecraftClient client) {
        ticks++;
        if (ticks % BASIC_INTERVAL_TICKS != 0) {
            return;
        }

        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        if (player == null || world == null) {
            cachedBlocks = new JsonArray(); cachedEntities = new JsonArray(); cachedLayout = new JsonObject();
            latest.set(disconnectedSnapshot());
            return;
        }

        if (ticks % WORLD_INTERVAL_TICKS == 0) {
            cachedBlocks = scanBlocks(world, player.getBlockPos());
            cachedEntities = scanEntities(world, player);
            BlockPos center = player.getBlockPos();
            cachedLayout = RegionSnapshot.capture(client,
                    new BlockPos(center.getX()-4, Math.max(0,center.getY()-4), center.getZ()-4),
                    new BlockPos(center.getX()+4, Math.min(255,center.getY()+4), center.getZ()+4));
        }

        JsonObject root = new JsonObject();
        root.addProperty("captured_at", Instant.now().toString());
        root.addProperty("connected", true);
        root.addProperty("world_id", RegionSnapshot.worldId());
        root.addProperty("dimension", world.getRegistryKey().getValue().toString());
        root.add("self", self(player));
        root.add("inventory", inventory(player));
        root.add("nearby_blocks", copyArray(cachedBlocks));
        root.add("nearby_entities", copyArray(cachedEntities));
        root.add("local_layout", new JsonParser().parse(cachedLayout.toString()));
        root.add("recent_chat", chatEvents.snapshot());
        latest.set(root);
    }

    public JsonObject snapshot() {
        return new JsonParser().parse(latest.get().toString()).getAsJsonObject();
    }

    private static JsonObject self(ClientPlayerEntity player) {
        JsonObject result = new JsonObject();
        result.addProperty("name", player.getEntityName());
        result.addProperty("creative", player.isCreative());
        result.addProperty("x", player.getX());
        result.addProperty("y", player.getY());
        result.addProperty("z", player.getZ());
        result.addProperty("yaw", player.yaw);
        result.addProperty("pitch", player.pitch);
        result.addProperty("health", player.getHealth());
        result.addProperty("max_health", player.getMaxHealth());
        result.addProperty("food", player.getHungerManager().getFoodLevel());
        result.addProperty("air", player.getAir());
        result.addProperty("on_ground", player.isOnGround());
        result.addProperty("experience_level", player.experienceLevel);
        return result;
    }

    private static JsonArray inventory(ClientPlayerEntity player) {
        JsonArray result = new JsonArray();
        for (int slot = 0; slot < player.inventory.size(); slot++) {
            ItemStack stack = player.inventory.getStack(slot);
            if (stack.isEmpty()) {
                continue;
            }
            JsonObject item = new JsonObject();
            item.addProperty("slot", slot);
            item.addProperty("item", Registry.ITEM.getId(stack.getItem()).toString());
            item.addProperty("count", stack.getCount());
            item.addProperty("damage", stack.getDamage());
            item.addProperty("max_damage", stack.getMaxDamage());
            item.addProperty("name", stack.getName().getString());
            result.add(item);
        }
        return result;
    }

    private static JsonArray scanBlocks(ClientWorld world, BlockPos center) {
        JsonArray result = new JsonArray();
        BlockPos min = center.add(-BLOCK_RADIUS, -BLOCK_RADIUS, -BLOCK_RADIUS);
        BlockPos max = center.add(BLOCK_RADIUS, BLOCK_RADIUS, BLOCK_RADIUS);
        for (BlockPos pos : BlockPos.iterate(min, max)) {
            BlockState state = world.getBlockState(pos);
            if (state.isAir()) {
                continue;
            }
            JsonObject block = new JsonObject();
            block.addProperty("block", Registry.BLOCK.getId(state.getBlock()).toString());
            block.addProperty("state", RegionSnapshot.stateName(state));
            block.addProperty("x", pos.getX());
            block.addProperty("y", pos.getY());
            block.addProperty("z", pos.getZ());
            result.add(block);
        }
        return result;
    }

    private static JsonArray scanEntities(ClientWorld world, ClientPlayerEntity player) {
        JsonArray result = new JsonArray();
        Box area = player.getBoundingBox().expand(ENTITY_RADIUS);
        List<Entity> entities = world.getEntitiesByClass(Entity.class, area, entity -> entity != player);
        for (Entity entity : entities) {
            JsonObject value = new JsonObject();
            value.addProperty("id", entity.getEntityId());
            value.addProperty("type", Registry.ENTITY_TYPE.getId(entity.getType()).toString());
            value.addProperty("name", entity.getName().getString());
            value.addProperty("x", entity.getX());
            value.addProperty("y", entity.getY());
            value.addProperty("z", entity.getZ());
            value.addProperty("distance", player.distanceTo(entity));
            if (entity instanceof ItemEntity) {
                ItemStack stack = ((ItemEntity) entity).getStack();
                value.addProperty("item", Registry.ITEM.getId(stack.getItem()).toString());
                value.addProperty("count", stack.getCount());
            }
            result.add(value);
        }
        return result;
    }

    private static JsonObject disconnectedSnapshot() {
        JsonObject result = new JsonObject();
        result.addProperty("captured_at", Instant.now().toString());
        result.addProperty("connected", false);
        result.add("inventory", new JsonArray());
        result.add("nearby_blocks", new JsonArray());
        result.add("nearby_entities", new JsonArray());
        result.add("recent_chat", new JsonArray());
        return result;
    }

    private static JsonArray copyArray(JsonArray source) {
        return new JsonParser().parse(source.toString()).getAsJsonArray();
    }
}
