package cn.wudabin.wifeng.baritone;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.event.events.PathEvent;
import baritone.api.event.listener.AbstractGameEventListener;
import baritone.api.pathing.goals.GoalNear;
import net.minecraft.block.Block;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.Entity;
import net.minecraft.util.math.BlockPos;

import java.util.Locale;
import java.util.function.Predicate;

public final class BaritoneFacade {
    private volatile PathEvent lastPathEvent;
    private volatile long pathEventSequence;
    private boolean listenerRegistered;

    private IBaritone baritone() {
        IBaritone instance = BaritoneAPI.getProvider().getPrimaryBaritone();
        if (!listenerRegistered) {
            synchronized (this) {
                if (!listenerRegistered) {
                    instance.getGameEventHandler().registerEventListener(new AbstractGameEventListener() {
                        @Override
                        public void onPathEvent(PathEvent event) {
                            lastPathEvent = event;
                            pathEventSequence++;
                        }
                    });
                    listenerRegistered = true;
                }
            }
        }
        return instance;
    }

    public void goTo(int x, int y, int z, int radius) {
        baritone().getCustomGoalProcess().setGoalAndPath(
                new GoalNear(new BlockPos(x, y, z), Math.max(1, radius)));
    }

    public void followPlayer(String playerName) {
        final String expected = playerName.toLowerCase(Locale.ROOT);
        Predicate<Entity> filter = entity -> entity.getEntityName().toLowerCase(Locale.ROOT).equals(expected);
        baritone().getFollowProcess().follow(filter);
    }

    public void mine(String blockId, int amount) {
        baritone().getMineProcess().mineByName(Math.max(1, amount), blockId);
    }

    public void mineAny(int amount, String... blockIds) {
        baritone().getMineProcess().mineByName(Math.max(1, amount), blockIds);
    }

    public void chop(int amount) {
        mineAny(amount,
                "minecraft:oak_log", "minecraft:spruce_log", "minecraft:birch_log",
                "minecraft:jungle_log", "minecraft:acacia_log", "minecraft:dark_oak_log",
                "minecraft:crimson_stem", "minecraft:warped_stem");
    }

    public void getToBlock(Block block) {
        baritone().getGetToBlockProcess().getToBlock(block);
    }

    public boolean isBusy() {
        IBaritone instance = baritone();
        return instance.getPathingBehavior().isPathing()
                || instance.getFollowProcess().isActive()
                || instance.getMineProcess().isActive()
                || instance.getGetToBlockProcess().isActive()
                || instance.getBuilderProcess().isActive();
    }

    public PathStatus pathStatus() {
        PathEvent event = lastPathEvent;
        return new PathStatus(event == null ? "NONE" : event.name(), pathEventSequence);
    }

    public static final class PathStatus {
        public final String event;
        public final long sequence;

        private PathStatus(String event, long sequence) {
            this.event = event;
            this.sequence = sequence;
        }
    }

    public void stop() {
        IBaritone instance = baritone();
        instance.getPathingBehavior().cancelEverything();
        instance.getFollowProcess().cancel();
        instance.getMineProcess().cancel();
    }

    public boolean connected(MinecraftClient client) {
        return client.player != null && client.world != null;
    }
}
