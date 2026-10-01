package cn.wudabin.wifeng.building;

import com.google.gson.*;
import net.minecraft.Bootstrap;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.state.property.Property;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import cn.wudabin.wifeng.task.AgentTask;
import net.minecraft.command.argument.BlockArgumentParser;
import com.mojang.brigadier.StringReader;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

/** Offline checks against the real 1.16.5 registry, without launching Minecraft. */
public final class BuildingChecks {
    private static final class MemoryWorld implements CreativeBuildingService.WorldAccess {
        final Map<Long,BlockState> blocks=new java.util.HashMap<>();
        boolean allowed=true;
        public boolean creative() { return true; }
        public String dimension() { return "minecraft:overworld"; }
        public boolean loaded(BlockPos p) { return true; }
        public BlockState block(BlockPos p) { return blocks.getOrDefault(p.asLong(),net.minecraft.block.Blocks.AIR.getDefaultState()); }
        public void command(String value) {
            if(!value.startsWith("/fill ") || !allowed) return;
            String[] parts=value.split(" ");
            BlockPos a=new BlockPos(Integer.parseInt(parts[1]),Integer.parseInt(parts[2]),Integer.parseInt(parts[3]));
            BlockPos b=new BlockPos(Integer.parseInt(parts[4]),Integer.parseInt(parts[5]),Integer.parseInt(parts[6]));
            try {
                BlockState state=new BlockArgumentParser(new StringReader(parts[7]),false).parse(false).getBlockState();
                for(BlockPos p:BlockPos.iterate(a,b)) blocks.put(p.asLong(),state);
            } catch(Exception e) { throw new IllegalArgumentException(e); }
        }
    }
    private static AgentTask job(String tool,JsonArray input) {
        JsonObject args=new JsonObject(); args.addProperty("blueprint_id","offline-test");
        args.addProperty("dimension","minecraft:overworld"); args.add("operations",input);
        return new AgentTask(tool,args);
    }
    private static void run(CreativeBuildingService service,MemoryWorld world,AgentTask task) {
        service.start(world,task,true,0);
        int deadline=5000+task.totalSteps*400;
        for(int tick=1;tick<deadline;tick++) if(service.tick(world,task,tick)) return;
        throw new AssertionError("Simulated executor never completed; progress="+task.progress);
    }
    private static int count;
    private static void check(boolean value,String message) { if(!value) throw new AssertionError(message); count++; }
    private static JsonArray ops(String value) { return new JsonParser().parse(value).getAsJsonArray(); }
    private static Object field(Object target,String name) throws Exception {
        Field f=target.getClass().getDeclaredField(name); f.setAccessible(true); return f.get(target);
    }
    private static boolean matches(Object spec,BlockState state) throws Exception {
        Method m=spec.getClass().getDeclaredMethod("matches",BlockState.class); m.setAccessible(true); return (Boolean)m.invoke(spec,state);
    }
    @SuppressWarnings({"rawtypes","unchecked"})
    public static void main(String[] args) throws Exception {
        Bootstrap.initialize();
        JsonObject catalog=new JsonObject();
        for(Block block:Registry.BLOCK) {
            JsonObject properties=new JsonObject();
            for(Property property:block.getStateManager().getProperties()) {
                JsonArray values=new JsonArray();
                for(Object value:property.getValues()) values.add(property.name((Comparable)value));
                properties.add(property.getName(),values);
            }
            catalog.add(Registry.BLOCK.getId(block).toString(),properties);
        }
        Files.write(Paths.get(args[0]), new GsonBuilder().setPrettyPrinting().create().toJson(catalog).getBytes(StandardCharsets.UTF_8));
        CreativeBuildingService service=new CreativeBuildingService();
        service.compile(ops("[{from:[-1,4,-1],to:[16,4,16],state:'minecraft:stone'},{from:[0,4,0],to:[0,4,0],state:'minecraft:air'}]"));
        Map<Long,Object> expected=(Map)field(service,"expected");
        check(expected.size()==324,"Exact target coverage across negative and positive chunks");
        check(matches(expected.get(new BlockPos(0,4,0).asLong()),Registry.BLOCK.get(new net.minecraft.util.Identifier("minecraft:air")).getDefaultState()),"Later air overrides earlier solid");
        check(!matches(expected.get(new BlockPos(0,4,0).asLong()),Registry.BLOCK.get(new net.minecraft.util.Identifier("minecraft:stone")).getDefaultState()),"Mismatch must not count as verified");
        for(Object op:(List)field(service,"operations")) {
            BlockPos a=(BlockPos)field(op,"from"), b=(BlockPos)field(op,"to");
            check(Math.floorDiv(a.getX(),16)==Math.floorDiv(b.getX(),16) && Math.floorDiv(a.getZ(),16)==Math.floorDiv(b.getZ(),16),"Each fill stays in one chunk");
            check((b.getX()-a.getX()+1)*(b.getY()-a.getY()+1)*(b.getZ()-a.getZ()+1)<=4096,"Fill volume cap");
        }
        for(String invalid:new String[]{"minecraft:cherry_planks","minecraft:oak_stairs[facing=up]","minecraft:stone[watery=true]","minecraft:stone\\nkill"}) {
            boolean rejected=false;
            try { service.compile(ops("[{from:[0,4,0],to:[0,4,0],state:'"+invalid+"'}]")); } catch(IllegalArgumentException e) { rejected=true; }
            check(rejected,"Registry validation rejects "+invalid);
        }
        JsonObject snapshot=RegionSnapshot.encode(new BlockPos(-1,3,2),new BlockPos(2,3,2),
                p -> p.getX()==-1?"minecraft:stone":p.getX()==2?"__unloaded__":"minecraft:air");
        check(!snapshot.get("complete").getAsBoolean() && snapshot.get("unknown_cells").getAsInt()==1,"Unknown is explicit");
        JsonArray runs=snapshot.getAsJsonArray("rows").get(0).getAsJsonObject().getAsJsonArray("runs");
        check(runs.size()==3,"Lossless run segmentation");
        check(runs.get(1).getAsJsonArray().get(0).getAsInt()==0 && runs.get(1).getAsJsonArray().get(1).getAsInt()==1,"Inclusive coordinates preserved");
        check(RegionSnapshot.stateName(Registry.BLOCK.get(new net.minecraft.util.Identifier("minecraft:oak_stairs")).getDefaultState()).contains("facing=north"),"Property values serialize canonically");
        JsonObject flat=RegionSnapshot.encode(new BlockPos(0,4,0),new BlockPos(15,10,15),p->"minecraft:air");
        JsonObject band=flat.getAsJsonArray("rows").get(0).getAsJsonObject();
        check(flat.getAsJsonArray("rows").size()==1 && band.get("y_to").getAsInt()==10 && band.get("z_to").getAsInt()==15,"Lossless repeated planes compress to one band");
        JsonArray target=ops("[{from:[-1,4,-1],to:[16,5,16],state:'minecraft:stone'},{from:[0,4,0],to:[1,5,1],state:'minecraft:air'}]");
        MemoryWorld world=new MemoryWorld();
        AgentTask build=job("build_blueprint",target);
        run(new CreativeBuildingService(),world,build);
        check(build.result.get("verified").getAsBoolean() && build.result.get("checked_cells").getAsInt()==648,"Scheduler verifies the full overlapping final target");
        world.blocks.put(new BlockPos(2,4,2).asLong(),net.minecraft.block.Blocks.AIR.getDefaultState());
        AgentTask verify=job("verify_blueprint",target);
        run(new CreativeBuildingService(),world,verify);
        check(!verify.result.get("verified").getAsBoolean() && verify.result.get("mismatch_count").getAsInt()==1,"Final verification detects one externally changed cell");
        MemoryWorld denied=new MemoryWorld(); denied.allowed=false;
        boolean rejected=false;
        try { run(new CreativeBuildingService(),denied,job("build_blueprint",target)); } catch(IllegalStateException e) { rejected=true; }
        check(rejected,"An acknowledged command without a world change never succeeds");
        CreativeBuildingService first=new CreativeBuildingService();
        MemoryWorld resumedWorld=new MemoryWorld(); AgentTask resumed=job("build_blueprint",target);
        first.start(resumedWorld,resumed,true,0);
        for(int tick=1;tick<100;tick++) { first.tick(resumedWorld,resumed,tick); if(resumed.stepIndex>0) break; }
        check(resumed.stepIndex>0 && resumed.stepIndex<resumed.totalSteps,"Pause fixture has partially completed steps");
        run(new CreativeBuildingService(),resumedWorld,resumed);
        check(resumed.result.get("verified").getAsBoolean(),"A new executor resumes saved progress and checks all final cells");
        if(args.length>1) {
            JsonObject saved=new JsonParser().parse(new String(Files.readAllBytes(Paths.get(args[1])),StandardCharsets.UTF_8)).getAsJsonObject();
            JsonArray modelOperations=saved.getAsJsonObject("compiled").getAsJsonArray("operations");
            service.compile(modelOperations);
            int targetCells=((Map)field(service,"expected")).size();
            MemoryWorld modelWorld=new MemoryWorld();
            AgentTask modelBuild=job("build_blueprint",modelOperations);
            run(new CreativeBuildingService(),modelWorld,modelBuild);
            check(modelBuild.result.get("verified").getAsBoolean() && modelBuild.result.get("checked_cells").getAsInt()==targetCells,"Real model blueprint completes the simulated build and checks every specified cell");
            AgentTask modelVerify=job("verify_blueprint",modelOperations);
            run(new CreativeBuildingService(),modelWorld,modelVerify);
            check(modelVerify.result.get("verified").getAsBoolean(),"Independent verification of the real model blueprint passes");
            System.out.println("Real model blueprint simulated and validated against Minecraft registry; target cells="+targetCells+"; block physics require a real server");
        }
        System.out.println("OFFLINE ARCHITECTURE CHECKS PASSED: "+count+"; catalog blocks="+catalog.size()+"; no game/server started");
    }
}
