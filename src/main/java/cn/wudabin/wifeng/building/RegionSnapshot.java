package cn.wudabin.wifeng.building;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.state.property.Property;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

/** Lossless world-space rows. Unloaded is never represented as air. Main thread only. */
public final class RegionSnapshot {
    private RegionSnapshot() { }

    @SuppressWarnings({"rawtypes", "unchecked"})
    public static String stateName(BlockState state) {
        Map<String, String> properties = new TreeMap<>();
        for (Map.Entry<Property<?>, Comparable<?>> e : state.getEntries().entrySet()) {
            properties.put(e.getKey().getName(), ((Property)e.getKey()).name(e.getValue()));
        }
        StringBuilder result = new StringBuilder(Registry.BLOCK.getId(state.getBlock()).toString());
        if (!properties.isEmpty()) {
            result.append('[');
            for (Map.Entry<String, String> e : properties.entrySet()) {
                if (result.charAt(result.length() - 1) != '[') result.append(',');
                result.append(e.getKey()).append('=').append(e.getValue());
            }
            result.append(']');
        }
        return result.toString();
    }

    public static JsonObject capture(MinecraftClient client, BlockPos min, BlockPos max) {
        Map<BlockState, String> names = new java.util.HashMap<>();
        JsonObject result = encode(min, max, p -> client.world.isChunkLoaded(p)
                ? names.computeIfAbsent(client.world.getBlockState(p), RegionSnapshot::stateName) : "__unloaded__");
        result.addProperty("dimension", client.world.getRegistryKey().getValue().toString());
        return result;
    }

    public static JsonObject encode(BlockPos min, BlockPos max, java.util.function.Function<BlockPos, String> source) {
        long volume = (long)(max.getX() - min.getX() + 1) * (max.getY() - min.getY() + 1)
                * (max.getZ() - min.getZ() + 1);
        if (min.getX() > max.getX() || min.getY() > max.getY() || min.getZ() > max.getZ()
                || min.getY() < 0 || max.getY() > 255 || volume <= 0 || volume > 65536) {
            throw new IllegalArgumentException("Survey requires ordered bounds, y=0..255, volume<=65536");
        }
        Map<String, Integer> palette = new LinkedHashMap<>();
        JsonArray rows = new JsonArray();
        int unknown = 0;
        String previousLayer = null;
        JsonArray previousRows = null;
        for (int y = min.getY(); y <= max.getY(); y++) {
            JsonArray layer = new JsonArray();
            JsonObject lastRow = null;
            for (int z = min.getZ(); z <= max.getZ(); z++) {
                JsonArray runs = new JsonArray();
                int runStart = min.getX();
                String previous = null;
                for (int x = min.getX(); x <= max.getX() + 1; x++) {
                    String name = null;
                    if (x <= max.getX()) {
                        BlockPos p = new BlockPos(x, y, z);
                        name = source.apply(p);
                        if ("__unloaded__".equals(name)) unknown++;
                    }
                    if (previous != null && !previous.equals(name)) {
                        if (!palette.containsKey(previous)) palette.put(previous, palette.size());
                        JsonArray run = new JsonArray();
                        run.add(runStart); run.add(x - 1); run.add(palette.get(previous));
                        runs.add(run);
                        runStart = x;
                    }
                    previous = name;
                }
                JsonObject row = new JsonObject();
                row.addProperty("y", y); row.addProperty("y_to", y);
                row.addProperty("z", z); row.addProperty("z_to", z); row.add("runs", runs);
                if (lastRow != null && lastRow.getAsJsonArray("runs").equals(runs)) {
                    lastRow.addProperty("z_to", z);
                } else {
                    layer.add(row); lastRow = row;
                }
            }
            StringBuilder signature = new StringBuilder();
            for (int n=0;n<layer.size();n++) {
                JsonObject row=layer.get(n).getAsJsonObject();
                signature.append(row.get("z")).append('/').append(row.get("z_to")).append(':').append(row.get("runs"));
            }
            if (signature.toString().equals(previousLayer)) {
                for (int n=0;n<previousRows.size();n++) previousRows.get(n).getAsJsonObject().addProperty("y_to",y);
            } else {
                for (int n=0;n<layer.size();n++) rows.add(layer.get(n));
                previousLayer=signature.toString(); previousRows=layer;
            }
        }
        JsonArray states = new JsonArray();
        for (String name : palette.keySet()) states.add(name);
        JsonObject result = new JsonObject();
        result.addProperty("encoding", "world-x-runs-v2");
        result.addProperty("legend", "Each band covers inclusive y..y_to and z..z_to, runs:[x_start,x_end_inclusive,palette_index]. Same x runs repeat over this y/z rectangle. Every cell including air is covered. __unloaded__ is unknown.");
        result.addProperty("captured_at", Instant.now().toString());
        result.addProperty("world_id", worldId());
        result.addProperty("complete", unknown == 0);
        result.addProperty("unknown_cells", unknown);
        result.addProperty("cells", volume);
        result.add("from", coordinates(min)); result.add("to", coordinates(max));
        result.add("palette", states); result.add("rows", rows);
        return result;
    }

    public static String worldId() {
        String value = System.getenv("WIFE_NG_WORLD_ID");
        return value == null || value.isEmpty() ? "legacy" : value.replaceAll("[^a-zA-Z0-9_.-]", "_");
    }

    public static JsonArray coordinates(BlockPos p) {
        JsonArray a = new JsonArray(); a.add(p.getX()); a.add(p.getY()); a.add(p.getZ()); return a;
    }

    public static BlockPos position(JsonArray values) {
        if (values == null || values.size() != 3) throw new IllegalArgumentException("Expected [x,y,z]");
        for (int i = 0; i < 3; i++) {
            double value = values.get(i).getAsDouble();
            if (value != Math.rint(value) || Math.abs(value) > 29999900)
                throw new IllegalArgumentException("Invalid world integer coordinate");
        }
        return new BlockPos(values.get(0).getAsInt(), values.get(1).getAsInt(), values.get(2).getAsInt());
    }
}
