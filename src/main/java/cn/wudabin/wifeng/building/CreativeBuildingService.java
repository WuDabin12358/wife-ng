package cn.wudabin.wifeng.building;

import cn.wudabin.wifeng.task.AgentTask;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.state.property.Property;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Creative+OP bulk construction. No command success claims without world verification. */
public final class CreativeBuildingService {
    public interface WorldAccess {
        boolean creative();
        String dimension();
        void command(String value);
        boolean loaded(BlockPos position);
        BlockState block(BlockPos position);
    }

    private static WorldAccess access(MinecraftClient client) {
        return new WorldAccess() {
            public boolean creative() { return client.player.isCreative(); }
            public String dimension() { return client.world.getRegistryKey().getValue().toString(); }
            public void command(String value) { client.player.sendChatMessage(value); }
            public boolean loaded(BlockPos p) { return client.world.isChunkLoaded(p); }
            public BlockState block(BlockPos p) { return client.world.getBlockState(p); }
        };
    }
    private static final class Spec {
        final String command;
        final Block block;
        final Map<Property<?>, Comparable<?>> properties = new LinkedHashMap<>();
        @SuppressWarnings({"rawtypes", "unchecked"})
        Spec(String text) {
            if (!text.matches("minecraft:[a-z0-9_]+(?:\\[[a-z0-9_=,]+\\])?"))
                throw new IllegalArgumentException("Invalid block state: " + text);
            int bracket = text.indexOf('[');
            Identifier id = new Identifier(bracket < 0 ? text : text.substring(0, bracket));
            if (!Registry.BLOCK.containsId(id)) throw new IllegalArgumentException("Not a 1.16.5 block: " + id);
            block = Registry.BLOCK.get(id);
            command = text;
            if (bracket >= 0) {
                for (String part : text.substring(bracket + 1, text.length() - 1).split(",")) {
                    String[] pair = part.split("=", -1);
                    if (pair.length != 2) throw new IllegalArgumentException("Invalid block property");
                    Property property = block.getStateManager().getProperty(pair[0]);
                    if (property == null || !property.parse(pair[1]).isPresent() || properties.containsKey(property))
                        throw new IllegalArgumentException("Invalid/duplicate state property: " + part);
                    properties.put(property, (Comparable)property.parse(pair[1]).get());
                }
            }
        }
        @SuppressWarnings({"rawtypes", "unchecked"})
        boolean matches(BlockState state) {
            if (state.getBlock() != block) return false;
            for (Map.Entry<Property<?>, Comparable<?>> e : properties.entrySet())
                if (!state.get((Property)e.getKey()).equals(e.getValue())) return false;
            return true;
        }
    }

    private static final class Cuboid {
        final BlockPos from, to;
        final Spec spec;
        Cuboid(BlockPos from, BlockPos to, Spec spec) { this.from = from; this.to = to; this.spec = spec; }
        BlockPos center() { return new BlockPos((from.getX()+to.getX())/2, Math.min(250,to.getY()+3), (from.getZ()+to.getZ())/2); }
        String command() { return "/fill " + xyz(from) + " " + xyz(to) + " " + spec.command + " replace"; }
        static String xyz(BlockPos p) { return p.getX()+" "+p.getY()+" "+p.getZ(); }
    }

    private final List<Cuboid> operations = new ArrayList<>();
    private final Map<Long, Spec> expected = new LinkedHashMap<>();
    private final List<List<Long>> verifyChunks = new ArrayList<>();
    private AgentTask active;
    private long waitingSince;
    private long sentAt;
    private int retries;
    private int verificationChunk;
    private int checked;
    private int mismatch;
    private JsonArray examples = new JsonArray();
    private boolean verifyOnly;
    private boolean inFinalVerification;
    private int phase = 0;

    public void compile(JsonArray input) {
        operations.clear(); expected.clear(); verifyChunks.clear();
        if (input == null || input.size() == 0 || input.size() > 4096)
            throw new IllegalArgumentException("operations must contain 1..4096 cuboids");
        BlockPos min = null, max = null;
        long volume = 0;
        for (JsonElement entry : input) {
            JsonObject op = entry.getAsJsonObject();
            BlockPos a = RegionSnapshot.position(op.getAsJsonArray("from"));
            BlockPos b = RegionSnapshot.position(op.getAsJsonArray("to"));
            if (a.getX()>b.getX() || a.getY()>b.getY() || a.getZ()>b.getZ() || a.getY()<0 || b.getY()>255)
                throw new IllegalArgumentException("Invalid cuboid bounds");
            volume += (long)(b.getX()-a.getX()+1)*(b.getY()-a.getY()+1)*(b.getZ()-a.getZ()+1);
            if (volume > 524288) throw new IllegalArgumentException("Excessive write volume");
            min = min == null ? a : new BlockPos(Math.min(min.getX(),a.getX()),Math.min(min.getY(),a.getY()),Math.min(min.getZ(),a.getZ()));
            max = max == null ? b : new BlockPos(Math.max(max.getX(),b.getX()),Math.max(max.getY(),b.getY()),Math.max(max.getZ(),b.getZ()));
            if (max.getX()-min.getX()>=128 || max.getY()-min.getY()>=80 || max.getZ()-min.getZ()>=128
                    || (long)(max.getX()-min.getX()+1)*(max.getY()-min.getY()+1)*(max.getZ()-min.getZ()+1)>262144)
                throw new IllegalArgumentException("Design exceeds 128x80x128 / 262144 cells");
            Spec spec = new Spec(op.get("state").getAsString());
            if (spec.command.length()>140) throw new IllegalArgumentException("Block state is too long for a Minecraft chat command");
            for (BlockPos p : BlockPos.iterate(a,b)) expected.put(p.asLong(),spec);
            // <=4096 blocks per fill, never cross a chunk boundary horizontally.
            for (int x=a.getX();x<=b.getX();) {
                int tx=Math.min(b.getX(),(Math.floorDiv(x,16)+1)*16-1);
                for (int z=a.getZ();z<=b.getZ();) {
                    int tz=Math.min(b.getZ(),(Math.floorDiv(z,16)+1)*16-1);
                    for (int y=a.getY();y<=b.getY();y+=16)
                        operations.add(new Cuboid(new BlockPos(x,y,z),new BlockPos(tx,Math.min(y+15,b.getY()),tz),spec));
                    z=tz+1;
                }
                x=tx+1;
            }
            if (operations.size()>8192) throw new IllegalArgumentException("Too many split commands");
        }
        Map<Long,List<Long>> chunks = new LinkedHashMap<>();
        for (Long pos : expected.keySet()) {
            BlockPos p = BlockPos.fromLong(pos);
            long key=((long)Math.floorDiv(p.getX(),16)<<32) ^ (Math.floorDiv(p.getZ(),16)&0xffffffffL);
            if (!chunks.containsKey(key)) chunks.put(key,new ArrayList<>());
            chunks.get(key).add(pos);
        }
        verifyChunks.addAll(chunks.values());
    }

    public void start(MinecraftClient client, AgentTask task, boolean opMode, long tick) {
        start(access(client), task, opMode, tick);
    }

    public void start(WorldAccess world, AgentTask task, boolean opMode, long tick) {
        if (!opMode) throw new IllegalArgumentException("Creative blueprint tools require -CreativeBuild (OP mode)");
        if (!world.creative()) throw new IllegalArgumentException("Blueprint construction requires creative mode; survival tools remain available");
        compile(task.arguments.getAsJsonArray("operations"));
        active=task; waitingSince=tick; sentAt=0; retries=0; verificationChunk=0; checked=0; mismatch=0;
        examples=new JsonArray(); phase=0;
        verifyOnly="verify_blueprint".equals(task.tool); inFinalVerification=verifyOnly;
        if (task.stepIndex == null) task.stepIndex=0;
        if (task.stepIndex<0 || task.stepIndex>operations.size()) throw new IllegalArgumentException("Corrupt step index");
        task.totalSteps=operations.size();
        task.result.addProperty("blueprint_id",task.arguments.get("blueprint_id").getAsString());
        task.result.addProperty("world_id",RegionSnapshot.worldId());
        task.result.addProperty("dimension",world.dimension());
        if (!task.arguments.has("dimension") || !task.arguments.get("dimension").getAsString()
                .equals(world.dimension()))
            throw new IllegalArgumentException("Blueprint dimension does not match current world");
        task.result.addProperty("specified_cells",expected.size());
        task.progress=verifyOnly?"verifying_blueprint":"building_blueprint";
    }

    /** Returns true only on completion; failures are explicit and never retried blindly. */
    public boolean tick(MinecraftClient client, AgentTask task, long tick) {
        return tick(access(client), task, tick);
    }

    public boolean tick(WorldAccess world, AgentTask task, long tick) {
        if (task != active) throw new IllegalStateException("No active blueprint");
        if (!world.creative()) throw new IllegalArgumentException("Creative mode was lost");
        if (!task.result.get("dimension").getAsString().equals(world.dimension()))
            throw new IllegalArgumentException("Dimension changed during construction");
        if (!inFinalVerification && task.stepIndex>=operations.size()) {
            inFinalVerification=true; phase=0; waitingSince=tick;
        }
        if (inFinalVerification) return verify(world,task,tick);
        Cuboid op=operations.get(task.stepIndex);
        if (phase==0) {
            world.command("/tp @s " + Cuboid.xyz(op.center()));
            phase=1; waitingSince=tick; return false;
        }
        if (!ready(world,op.from,op.to) || tick-waitingSince<10) {
            timeout(tick, "Chunks did not load before fill"); return false;
        }
        if (phase==1) {
            world.command(op.command()); sentAt=tick; phase=2; return false;
        }
        if (tick-sentAt<10) return false;
        boolean matches=true;
        for (BlockPos p : BlockPos.iterate(op.from,op.to)) {
            if (!op.spec.matches(world.block(p))) { matches=false; break; }
        }
        if (!matches) {
            if (tick-sentAt<80) return false;
            if (++retries>2) throw new IllegalStateException("Fill verification failed at step " + task.stepIndex + "; check OP permission, state physics and command errors");
            phase=1; return false;
        }
        task.stepIndex++; retries=0; phase=0; waitingSince=tick;
        task.progress="built_verified_steps_"+task.stepIndex+"_of_"+task.totalSteps;
        task.result.addProperty("verified_steps",task.stepIndex);
        return false;
    }

    private boolean verify(WorldAccess world, AgentTask task, long tick) {
        if (verificationChunk>=verifyChunks.size()) {
            task.result.addProperty("checked_cells",checked);
            task.result.addProperty("mismatch_count",mismatch);
            task.result.add("mismatches",examples);
            task.result.addProperty("verification_complete",true);
            task.result.addProperty("verified",mismatch==0);
            if (mismatch>0 && !verifyOnly) throw new IllegalStateException("Final blueprint differs at " + mismatch + " cells; inspect mismatches and repair the design");
            return true;
        }
        List<Long> positions=verifyChunks.get(verificationChunk);
        BlockPos first=BlockPos.fromLong(positions.get(0));
        if (phase==0) {
            world.command("/tp @s " + first.getX()+" "+Math.min(250,first.getY()+20)+" "+first.getZ());
            phase=1; waitingSince=tick; return false;
        }
        if (tick-waitingSince<20 || !world.loaded(first)) {
            timeout(tick,"Verification chunk did not load"); return false;
        }
        for (Long encoded : positions) {
            BlockPos p=BlockPos.fromLong(encoded);
            BlockState actual=world.block(p);
            checked++;
            if (!expected.get(encoded).matches(actual)) {
                mismatch++;
                if (examples.size()<32) {
                    JsonObject example=new JsonObject(); example.add("at",RegionSnapshot.coordinates(p));
                    example.addProperty("expected",expected.get(encoded).command);
                    example.addProperty("actual",RegionSnapshot.stateName(actual)); examples.add(example);
                }
            }
        }
        verificationChunk++; phase=0; waitingSince=tick;
        task.progress="verifying_final_cells_"+checked+"_of_"+expected.size();
        return false;
    }

    private void timeout(long tick,String message) {
        if (tick-waitingSince>400) throw new IllegalStateException(message);
    }
    private static boolean ready(WorldAccess world,BlockPos a,BlockPos b) {
        return world.loaded(a) && world.loaded(b);
    }
}
