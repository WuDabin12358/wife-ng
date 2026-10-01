package cn.wudabin.wifeng.task;

import cn.wudabin.wifeng.WifeNgClient;
import cn.wudabin.wifeng.baritone.BaritoneFacade;
import cn.wudabin.wifeng.building.CreativeBuildingService;
import cn.wudabin.wifeng.building.RegionSnapshot;
import cn.wudabin.wifeng.perception.PerceptionService;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.block.Block;
import net.minecraft.block.Blocks;
import net.minecraft.block.BlockState;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.AbstractClientPlayerEntity;
import net.minecraft.entity.ItemEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.item.BlockItem;
import net.minecraft.recipe.Recipe;
import net.minecraft.recipe.RecipeType;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import net.minecraft.screen.AbstractFurnaceScreenHandler;
import net.minecraft.screen.AbstractRecipeScreenHandler;
import net.minecraft.screen.GenericContainerScreenHandler;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Hand;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.registry.Registry;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Deque;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class TaskEngine {
    private static final long START_GRACE_TICKS = 40;
    private static final long RETRY_DELAY_TICKS = 60;
    private static final int DEFAULT_MAX_ATTEMPTS = 3;
    private static final String EXECUTOR_BUILD = "2026-10-01-creative-blueprints-v1";

    private enum PlacementAttempt {
        EQUIPPING,
        DISPATCHED,
        NO_SUPPORT,
        ITEM_MISSING
    }

    private static final class BuildStep {
        final BlockPos position;
        final String item;

        BuildStep(BlockPos position, String item) {
            this.position = position;
            this.item = item;
        }
    }

    private final Object lock = new Object();
    private final Map<String, AgentTask> tasks = new LinkedHashMap<>();
    private final Deque<String> queue = new ArrayDeque<>();
    private final BaritoneFacade baritone = new BaritoneFacade();
    private final CreativeBuildingService buildings = new CreativeBuildingService();
    private final PerceptionService perception;
    private final TaskJournal journal;
    private final boolean opMode;

    private AgentTask current;
    private long clientTicks;
    private long actionStartedTick;
    private int startingItemCount;
    private int startingInventoryCount;
    private long startingPathEventSequence;
    private long lastAutomationTick;
    private BlockPos activeBlockTarget;
    private long directBlockBrokenTick;
    private long pickupIdleTick;
    private double lastX;
    private double lastY;
    private double lastZ;
    private long lastMovedTick;

    public TaskEngine(PerceptionService perception) {
        this.perception = perception;
        this.journal = new TaskJournal(FabricLoader.getInstance().getGameDir().resolve("wife-ng")
                .resolve("worlds").resolve(RegionSnapshot.worldId()));
        this.opMode = readBooleanEnvironment("WIFE_NG_OP_MODE");
        restore();
        WifeNgClient.LOGGER.info("Task executor build {} loaded", EXECUTOR_BUILD);
    }

    public boolean isOpMode() {
        return opMode;
    }

    public AgentTask submit(String tool, JsonObject arguments) {
        String normalized = tool == null ? "" : tool.trim().toLowerCase(java.util.Locale.ROOT);
        AgentTask task = new AgentTask(normalized, arguments);
        synchronized (lock) {
            tasks.put(task.id, task);
            if ("stop".equals(normalized)) {
                queue.addFirst(task.id);
            } else if (current != null && "follow".equals(current.tool)
                    && !isImmediateTool(normalized)) {
                // A new physical action replaces continuous following. Put it
                // at the front so it cannot sit behind unrelated old work.
                queue.addFirst(task.id);
            } else {
                queue.addLast(task.id);
            }
            persist(task);
        }
        return task;
    }

    public AgentTask get(String id) {
        synchronized (lock) {
            return tasks.get(id);
        }
    }

    public Collection<AgentTask> all() {
        synchronized (lock) {
            return new ArrayList<>(tasks.values());
        }
    }

    public boolean cancel(String id) {
        synchronized (lock) {
            AgentTask task = tasks.get(id);
            if (task == null || task.terminal()) {
                return false;
            }
            task.transition(TaskState.CANCELLED, "cancelled_by_request");
            queue.remove(id);
            persist(task);
            return true;
        }
    }

    public boolean pause(String id) {
        synchronized (lock) {
            AgentTask task = tasks.get(id);
            if (task == null || task.terminal() || task.state == TaskState.PAUSED) {
                return false;
            }
            task.transition(TaskState.PAUSED, "paused_by_request");
            queue.remove(id);
            persist(task);
            return true;
        }
    }

    public boolean resume(String id) {
        synchronized (lock) {
            AgentTask task = tasks.get(id);
            if (task == null || task.state != TaskState.PAUSED) {
                return false;
            }
            task.transition(TaskState.QUEUED, "resumed");
            queue.addLast(id);
            persist(task);
            return true;
        }
    }

    public void tick(MinecraftClient client) {
        clientTicks++;
        synchronized (lock) {
            preemptForStopIfNeeded();
            preemptFollowForNewActionIfNeeded();
            if (current != null && (current.state == TaskState.CANCELLED || current.state == TaskState.PAUSED)) {
                baritone.stop();
                current = null;
            }
            processImmediateTasks(client);
            if (current == null) {
                current = takeNextQueued();
                if (current != null) {
                    start(client, current);
                }
            }
            if (current != null && current.state == TaskState.RUNNING) {
                AgentTask polling = current;
                try {
                    poll(client, polling);
                } catch (IllegalArgumentException error) {
                    fail(polling, "INVALID_ARGUMENT", error.getMessage(), false);
                } catch (Throwable error) {
                    WifeNgClient.LOGGER.error("Cannot poll task {}", polling.id, error);
                    if ("build_blueprint".equals(polling.tool) || "verify_blueprint".equals(polling.tool)) {
                        fail(polling, "BLUEPRINT_VERIFICATION_FAILED", error.toString(), false);
                    } else {
                        failOrRetry(polling, "EXECUTOR_ERROR", error.toString(), true);
                    }
                }
            }
        }
    }

    /**
     * Chat and OP commands are control-plane operations, not movement jobs.
     * Execute them without touching {@link #current} so a continuous follow
     * task cannot prevent conversation or command dispatch.
     */
    private void processImmediateTasks(MinecraftClient client) {
        Iterator<String> iterator = queue.iterator();
        while (iterator.hasNext()) {
            String id = iterator.next();
            AgentTask task = tasks.get(id);
            if (task == null || task.state != TaskState.QUEUED) {
                iterator.remove();
                continue;
            }
            if (!"say".equals(task.tool) && !"server_command".equals(task.tool)) {
                continue;
            }
            iterator.remove();
            executeImmediate(client, task);
        }
    }

    private void executeImmediate(MinecraftClient client, AgentTask task) {
        try {
            if (!baritone.connected(client)) {
                failImmediate(task, "NOT_CONNECTED", "Minecraft client is not connected", true);
                return;
            }
            task.attempts++;
            task.transition(TaskState.RUNNING, "dispatching");
            persist(task);
            if ("say".equals(task.tool)) {
                String message = requiredString(task.arguments, "message").trim();
                validateSingleLine(message, "message");
                client.player.sendChatMessage(message);
                succeedImmediate(task, "message_sent");
                return;
            }
            if (!opMode) {
                failImmediate(task, "OP_MODE_DISABLED",
                        "server_command requires WIFE_NG_OP_MODE=true", false);
                return;
            }
            String command = requiredString(task.arguments, "command").trim();
            validateSingleLine(command, "command");
            while (command.startsWith("/")) command = command.substring(1).trim();
            if (command.isEmpty()) {
                throw new IllegalArgumentException("command must not be empty");
            }
            client.player.sendChatMessage("/" + command);
            succeedImmediate(task, "server_command_sent");
        } catch (IllegalArgumentException error) {
            failImmediate(task, "INVALID_ARGUMENT", error.getMessage(), false);
        } catch (Throwable error) {
            WifeNgClient.LOGGER.error("Cannot execute immediate task {}", task.id, error);
            failImmediate(task, "EXECUTOR_ERROR", error.toString(), true);
        }
    }

    private static void validateSingleLine(String value, String name) {
        if (value.length() > 240) {
            throw new IllegalArgumentException(name + " must be at most 240 characters");
        }
        if (value.indexOf('\n') >= 0 || value.indexOf('\r') >= 0) {
            throw new IllegalArgumentException(name + " must be a single line");
        }
    }

    private void succeedImmediate(AgentTask task, String detail) {
        task.transition(TaskState.SUCCEEDED, detail);
        persist(task);
    }

    private void failImmediate(AgentTask task, String code, String message, boolean retryable) {
        task.failureCode = code;
        task.failureMessage = message == null ? "" : message;
        task.retryable = retryable;
        task.transition(TaskState.FAILED, "failed");
        persist(task);
    }

    private void preemptForStopIfNeeded() {
        String firstId = queue.peekFirst();
        AgentTask first = firstId == null ? null : tasks.get(firstId);
        if (first == null || !"stop".equals(first.tool)) {
            return;
        }
        if (current != null && !current.terminal()) {
            current.transition(TaskState.CANCELLED, "preempted_by_stop");
            persist(current);
            baritone.stop();
            current = null;
        }
    }

    private void preemptFollowForNewActionIfNeeded() {
        if (current == null || !"follow".equals(current.tool)) return;
        String firstId = queue.peekFirst();
        AgentTask first = firstId == null ? null : tasks.get(firstId);
        if (first == null || first.state != TaskState.QUEUED || isImmediateTool(first.tool)
                || "stop".equals(first.tool)) {
            return;
        }
        current.transition(TaskState.CANCELLED, "superseded_by_" + first.tool);
        persist(current);
        baritone.stop();
        current = null;
    }

    private static boolean isImmediateTool(String tool) {
        return "say".equals(tool) || "server_command".equals(tool);
    }

    private AgentTask takeNextQueued() {
        while (!queue.isEmpty()) {
            AgentTask candidate = tasks.get(queue.peekFirst());
            if (candidate != null && candidate.state == TaskState.QUEUED
                    && candidate.retryAtTick > clientTicks) {
                return null;
            }
            queue.removeFirst();
            if (candidate != null && candidate.state == TaskState.QUEUED) {
                return candidate;
            }
        }
        return null;
    }

    private void start(MinecraftClient client, AgentTask task) {
        if (!baritone.connected(client)) {
            fail(task, "NOT_CONNECTED", "Minecraft client is not connected", true);
            return;
        }
        try {
            task.attempts++;
            task.transition(TaskState.RUNNING, "starting");
            activeBlockTarget = null;
            directBlockBrokenTick = 0;
            pickupIdleTick = 0;
            actionStartedTick = clientTicks;
            lastX = client.player.getX();
            lastY = client.player.getY();
            lastZ = client.player.getZ();
            lastMovedTick = clientTicks;
            if (task.baselineItemCount == null) {
                task.baselineItemCount = inventoryCount(task.arguments, targetItem(task));
            }
            if (task.baselineInventoryCount == null) {
                task.baselineInventoryCount = totalInventoryCount();
            }
            startingItemCount = task.baselineItemCount;
            startingInventoryCount = task.baselineInventoryCount;
            startingPathEventSequence = baritone.pathStatus().sequence;
            task.failureCode = "";
            task.failureMessage = "";
            task.retryable = false;
            task.retryAtTick = 0;
            switch (task.tool) {
                case "goto":
                    baritone.goTo(requiredInt(task.arguments, "x"), requiredInt(task.arguments, "y"),
                            requiredInt(task.arguments, "z"), optionalInt(task.arguments, "radius", 1));
                    task.progress = "pathing";
                    break;
                case "follow":
                    baritone.followPlayer(requiredString(task.arguments, "player"));
                    task.progress = "following";
                    break;
                case "mine":
                    startBlockCollection(client, task,
                            startingItemCount + optionalInt(task.arguments, "amount", 1));
                    break;
                case "chop":
                    startBlockCollection(client, task,
                            startingItemCount + optionalInt(task.arguments, "amount", 1));
                    break;
                case "pickup":
                    task.progress = "collecting";
                    break;
                case "deliver":
                    requireInventory(task, requiredString(task.arguments, "item"),
                            optionalInt(task.arguments, "amount", 1));
                    baritone.followPlayer(requiredString(task.arguments, "player"));
                    task.progress = "approaching_recipient";
                    break;
                case "craft":
                    task.progress = "preparing_craft";
                    break;
                case "smelt":
                    requireInventory(task, requiredString(task.arguments, "input"),
                            optionalInt(task.arguments, "amount", 1));
                    requireInventory(task, optionalString(task.arguments, "fuel", "minecraft:coal"), 1);
                    task.progress = "finding_furnace";
                    break;
                case "acquire":
                    startAcquire(client, task);
                    break;
                case "inspect_container":
                    task.progress = "finding_container";
                    break;
                case "equip":
                    equipInventoryItem(client, requiredString(task.arguments, "item"));
                    succeed(task, "item_equipped");
                    break;
                case "use_item":
                    String useItem = optionalString(task.arguments, "item", "");
                    if (!useItem.isEmpty()) equipInventoryItem(client, useItem);
                    client.interactionManager.interactItem(client.player, client.world, Hand.MAIN_HAND);
                    client.player.swingHand(Hand.MAIN_HAND);
                    succeed(task, "item_use_dispatched");
                    break;
                case "place_block":
                    task.progress = "preparing_block_placement";
                    break;
                case "interact_block":
                    task.progress = "approaching_interaction_target";
                    break;
                case "break_block_at":
                    activeBlockTarget = requestedPosition(task.arguments);
                    task.progress = "approaching_break_target";
                    break;
                case "find_build_site":
                    initializeBuildSite(client, task);
                    break;
                case "survey_region":
                    task.result = RegionSnapshot.capture(client,
                            RegionSnapshot.position(task.arguments.getAsJsonArray("from")),
                            RegionSnapshot.position(task.arguments.getAsJsonArray("to")));
                    succeed(task, "region_captured");
                    break;
                case "build_blueprint":
                case "verify_blueprint":
                    baritone.stop();
                    buildings.start(client, task, opMode, clientTicks);
                    break;
                case "build_structure":
                    initializeStructure(client, task);
                    break;
                case "say":
                    String message = requiredString(task.arguments, "message").trim();
                    validateSingleLine(message, "message");
                    client.player.sendChatMessage(message);
                    succeed(task, "message_sent");
                    break;
                case "server_command":
                    // Normally dispatched by processImmediateTasks. Keep this
                    // branch defensive for tasks restored from older snapshots.
                    executeImmediate(client, task);
                    break;
                case "stop":
                    baritone.stop();
                    succeed(task, "all_actions_stopped");
                    break;
                default:
                    fail(task, "UNKNOWN_TOOL", "Unknown tool: " + task.tool, false);
                    break;
            }
            persist(task);
        } catch (IllegalArgumentException error) {
            fail(task, "INVALID_ARGUMENT", error.getMessage(), false);
        } catch (Throwable error) {
            WifeNgClient.LOGGER.error("Cannot start task {}", task.id, error);
            failOrRetry(task, "EXECUTOR_ERROR", error.toString(), true);
        }
    }

    private void poll(MinecraftClient client, AgentTask task) {
        if ("build_blueprint".equals(task.tool) || "verify_blueprint".equals(task.tool)) {
            if (client.player == null || client.world == null) {
                fail(task, "NOT_CONNECTED", "Disconnected during blueprint execution; resume after reconnect", true);
                return;
            }
            int before = task.stepIndex;
            if (buildings.tick(client, task, clientTicks)) {
                succeed(task, "blueprint_verified");
            } else if (task.stepIndex != before && task.stepIndex % 16 == 0) {
                persist(task);
            }
            return;
        }
        double px = client.player.getX();
        double py = client.player.getY();
        double pz = client.player.getZ();
        if (Math.abs(px - lastX) + Math.abs(py - lastY) + Math.abs(pz - lastZ) > 0.05) {
            lastX = px;
            lastY = py;
            lastZ = pz;
            lastMovedTick = clientTicks;
        }
        if ("follow".equals(task.tool)) {
            task.progress = "following";
            return;
        }
        if ("deliver".equals(task.tool)) {
            pollDeliver(client, task);
            return;
        }
        if ("craft".equals(task.tool)) {
            pollCraft(client, task, optionalInt(task.arguments, "amount", 1));
            return;
        }
        if ("smelt".equals(task.tool)) {
            pollSmelt(client, task);
            return;
        }
        if ("acquire".equals(task.tool)) {
            pollAcquire(client, task);
            return;
        }
        if ("inspect_container".equals(task.tool)) {
            pollInspectContainer(client, task);
            return;
        }
        if ("place_block".equals(task.tool)) {
            pollPlaceBlock(client, task);
            return;
        }
        if ("interact_block".equals(task.tool)) {
            pollInteractBlock(client, task);
            return;
        }
        if ("break_block_at".equals(task.tool)) {
            pollBreakBlockAt(client, task);
            return;
        }
        if ("build_structure".equals(task.tool)) {
            pollBuildStructure(client, task);
            return;
        }
        if ("pickup".equals(task.tool)) {
            pollPickup(client, task);
            return;
        }
        if ("goto".equals(task.tool) && reachedGoal(client, task.arguments)) {
            succeed(task, "destination_reached");
            return;
        }
        if (("mine".equals(task.tool) || "chop".equals(task.tool) || "pickup".equals(task.tool))
                && collectedEnough(task)) {
            baritone.stop();
            succeed(task, "requested_items_collected");
            return;
        }
        if (("mine".equals(task.tool) || "chop".equals(task.tool))
                && task.progress.startsWith("direct_")) {
            pollDirectBlockCollection(client, task);
            return;
        }
        if (baritone.isBusy() && clientTicks - lastMovedTick > 200) {
            failOrRetry(task, "PATH_STUCK",
                    "No movement for 10 seconds while an action was active", true);
            return;
        }
        BaritoneFacade.PathStatus path = baritone.pathStatus();
        if (path.sequence > startingPathEventSequence
                && ("CALC_FAILED".equals(path.event) || "NEXT_CALC_FAILED".equals(path.event))) {
            failOrRetry(task, "PATH_CALCULATION_FAILED",
                    "Baritone reported " + path.event + " before the task postcondition was reached", true);
            return;
        }
        long timeoutTicks = Math.max(20L, optionalInt(task.arguments, "timeout_seconds", defaultTimeoutSeconds(task)) * 20L);
        if (clientTicks - actionStartedTick > timeoutTicks) {
            failOrRetry(task, "ACTION_TIMEOUT",
                    "No verified completion within " + (timeoutTicks / 20L) + " seconds", true);
            return;
        }
        if (clientTicks - actionStartedTick > START_GRACE_TICKS && !baritone.isBusy()) {
            failOrRetry(task, "ACTION_ENDED_WITHOUT_POSTCONDITION",
                    "Baritone stopped before the task postcondition was observed", true);
        }
    }

    private void pollDeliver(MinecraftClient client, AgentTask task) {
        String item = requiredString(task.arguments, "item");
        int amount = optionalInt(task.arguments, "amount", 1);
        int delivered = startingItemCount - inventoryCount(task.arguments, item);
        if (delivered >= amount) {
            succeed(task, "items_dropped_at_recipient");
            return;
        }
        AbstractClientPlayerEntity recipient = findPlayer(client, requiredString(task.arguments, "player"));
        if (recipient == null) {
            if (timedOut(task, 90)) {
                failOrRetry(task, "PLAYER_NOT_VISIBLE", "Recipient is not visible in the current world", true);
            }
            return;
        }
        if (client.player.squaredDistanceTo(recipient) > 9.0D) {
            task.progress = "approaching_recipient";
            if (!baritone.isBusy()) baritone.followPlayer(recipient.getEntityName());
            if (timedOut(task, 120)) {
                failOrRetry(task, "RECIPIENT_UNREACHABLE", "Could not get close enough to the recipient", true);
            }
            return;
        }
        baritone.stop();
        task.progress = "dropping_items_" + delivered + "_of_" + amount;
        if (clientTicks - lastAutomationTick >= 4) {
            lastAutomationTick = clientTicks;
            if (!dropOne(client, item)) {
                fail(task, "ITEM_DISAPPEARED", "The requested item is no longer in the inventory", false);
            }
        }
    }

    private void pollCraft(MinecraftClient client, AgentTask task, int amount) {
        String item = requiredString(task.arguments, "item");
        int crafted = inventoryCount(task.arguments, item) - startingItemCount;
        if (crafted >= amount) {
            closeAutomationScreen(client);
            succeed(task, "crafted_requested_items");
            return;
        }
        Recipe<?> recipe = findRecipe(client, item, RecipeType.CRAFTING);
        if (recipe == null) {
            fail(task, "RECIPE_NOT_FOUND", "No crafting recipe outputs " + item, false);
            return;
        }
        ScreenHandler rawHandler = client.player.currentScreenHandler;
        if (rawHandler instanceof AbstractRecipeScreenHandler) {
            AbstractRecipeScreenHandler<?> handler = (AbstractRecipeScreenHandler<?>) rawHandler;
            if (recipe.fits(handler.getCraftingWidth(), handler.getCraftingHeight())) {
                task.progress = "crafting_" + crafted + "_of_" + amount;
                if (clientTicks - lastAutomationTick >= 8) {
                    lastAutomationTick = clientTicks;
                    Slot result = handler.getSlot(handler.getCraftingResultSlotIndex());
                    if (result.hasStack() && item.equals(itemId(result.getStack()))) {
                        client.interactionManager.clickSlot(handler.syncId, result.id, 0,
                                SlotActionType.QUICK_MOVE, client.player);
                    } else {
                        client.interactionManager.clickRecipe(handler.syncId, recipe, false);
                    }
                }
                if (timedOut(task, 120)) {
                    failOrRetry(task, "CRAFT_TIMEOUT", "Recipe could not be completed with the available ingredients", true);
                }
                return;
            }
        }
        BlockPos table = findNearestBlock(client, Blocks.CRAFTING_TABLE, 16);
        if (table == null) {
            if (inventoryCount(task.arguments, "minecraft:crafting_table") <= 0) {
                if (!craftPortableTable(client, task)) {
                    fail(task, "CRAFTING_TABLE_MATERIALS_MISSING",
                            "The recipe needs a crafting table and no table could be crafted from current materials", false);
                }
                if (timedOut(task, 120)) {
                    failOrRetry(task, "CRAFTING_TABLE_CRAFT_TIMEOUT",
                            "Could not craft a temporary crafting table from current materials", true);
                }
                return;
            }
            BlockPos placement = findSafePlacementNearPlayer(client);
            if (placement == null) {
                failOrRetry(task, "NO_SAFE_WORKSTATION_POSITION",
                        "No supported empty block near the player can hold a crafting table", true);
                return;
            }
            task.progress = "placing_temporary_crafting_table";
            if (clientTicks - lastAutomationTick >= 8) {
                lastAutomationTick = clientTicks;
                PlacementAttempt attempt = tryPlaceInventoryBlock(client,
                        "minecraft:crafting_table", placement);
                if (attempt == PlacementAttempt.NO_SUPPORT) {
                    failOrRetry(task, "NO_SAFE_WORKSTATION_POSITION",
                            "No solid adjacent face can support the temporary crafting table", true);
                }
            }
            if (timedOut(task, 120)) {
                failOrRetry(task, "CRAFTING_TABLE_PLACEMENT_TIMEOUT",
                        "Could not place the temporary crafting table", true);
            }
            return;
        }
        approachAndOpen(client, task, table, "crafting_table");
    }

    private void pollPlaceBlock(MinecraftClient client, AgentTask task) {
        String item = requiredString(task.arguments, "item");
        Block wanted = blockForItem(item);
        if (wanted == Blocks.AIR) {
            fail(task, "ITEM_NOT_PLACEABLE", item + " is not a placeable block item", false);
            return;
        }
        BlockPos target = requestedPosition(task.arguments);
        if (client.world.getBlockState(target).getBlock() == wanted) {
            succeed(task, "block_placed");
            return;
        }
        if (!client.world.getBlockState(target).getMaterial().isReplaceable()) {
            fail(task, "PLACEMENT_TARGET_OCCUPIED", "Target block is not replaceable at " + target, false);
            return;
        }
        if (client.player.squaredDistanceTo(Vec3d.ofCenter(target)) > 16.0D) {
            task.progress = "approaching_placement_target";
            if (!baritone.isBusy()) baritone.goTo(target.getX(), target.getY(), target.getZ(), 2);
            if (timedOut(task, 120)) {
                failOrRetry(task, "PLACEMENT_TARGET_UNREACHABLE", "Could not reach " + target, true);
            }
            return;
        }
        baritone.stop();
        task.progress = "placing_block";
        if (clientTicks - lastAutomationTick >= 8) {
            lastAutomationTick = clientTicks;
            PlacementAttempt attempt = tryPlaceInventoryBlock(client, item, target);
            if (attempt == PlacementAttempt.ITEM_MISSING) {
                fail(task, "ITEM_NOT_FOUND", "Inventory does not contain " + item, false);
            } else if (attempt == PlacementAttempt.NO_SUPPORT) {
                fail(task, "NO_PLACEMENT_SUPPORT",
                        "No solid adjacent face can support a block at " + target, false);
            }
        }
        if (timedOut(task, 60)) {
            failOrRetry(task, "BLOCK_PLACEMENT_TIMEOUT", "The block was not observed at " + target, true);
        }
    }

    private void initializeBuildSite(MinecraftClient client, AgentTask task) {
        int width = boundedDimension(task.arguments, "width", 5, 3, 15);
        int depth = boundedDimension(task.arguments, "depth", 5, 3, 15);
        int clearance = boundedDimension(task.arguments, "clearance", 5, 3, 10);
        int radius = boundedDimension(task.arguments, "radius", 20, 4, 32);
        BlockPos origin = findBuildSite(client, width, depth, clearance, radius);
        if (origin == null) {
            fail(task, "NO_FLAT_BUILD_SITE",
                    "No flat, supported and clear " + width + "x" + depth
                            + " site was found within " + radius + " blocks", false);
            return;
        }
        ensureTaskResult(task);
        writeOrigin(task.result, origin);
        task.result.addProperty("width", width);
        task.result.addProperty("depth", depth);
        task.result.addProperty("clearance", clearance);
        task.result.addProperty("distance", Math.sqrt(client.player.squaredDistanceTo(Vec3d.ofCenter(origin))));
        succeed(task, "build_site_found");
    }

    private void initializeStructure(MinecraftClient client, AgentTask task) {
        ensureTaskResult(task);
        if (task.result.has("initialized") && task.result.get("initialized").getAsBoolean()) {
            task.progress = "resuming_structure";
            return;
        }
        int width = boundedDimension(task.arguments, "width", 5, 3, 15);
        int depth = boundedDimension(task.arguments, "depth", 5, 3, 15);
        int height = boundedDimension(task.arguments, "height", 3, 2, 8);
        BlockPos origin;
        if (task.arguments.has("x") && task.arguments.has("y") && task.arguments.has("z")) {
            origin = requestedPosition(task.arguments);
            if (!isClearSupportedSite(client, origin, width, depth, height + 2)) {
                fail(task, "INVALID_BUILD_SITE",
                        "The requested origin is not a flat, supported and clear build site: " + origin, false);
                return;
            }
        } else {
            origin = findBuildSite(client, width, depth, height + 2,
                    boundedDimension(task.arguments, "search_radius", 20, 4, 32));
            if (origin == null) {
                fail(task, "NO_FLAT_BUILD_SITE",
                        "No suitable site was found for the requested structure", false);
                return;
            }
        }

        int floorNeeded = width * depth;
        int wallNeeded = (width * 2 + Math.max(0, depth - 2) * 2) * height - 2;
        int roofNeeded = width * depth;
        Map<String, Integer> remaining = placeableInventory(client);
        String floor = reserveBuildMaterial(remaining,
                optionalString(task.arguments, "floor_item", ""), floorNeeded,
                new String[]{"minecraft:cobblestone", "minecraft:stone", "minecraft:stone_bricks",
                        "minecraft:oak_planks", "minecraft:birch_planks"});
        String wall = reserveBuildMaterial(remaining,
                optionalString(task.arguments, "wall_item", ""), wallNeeded,
                new String[]{"minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
                        "minecraft:jungle_planks", "minecraft:acacia_planks", "minecraft:dark_oak_planks",
                        "minecraft:cobblestone"});
        String roof = reserveBuildMaterial(remaining,
                optionalString(task.arguments, "roof_item", ""), roofNeeded,
                new String[]{"minecraft:oak_planks", "minecraft:birch_planks", "minecraft:spruce_planks",
                        "minecraft:cobblestone"});
        if (floor.isEmpty() || wall.isEmpty() || roof.isEmpty()) {
            fail(task, "BUILD_MATERIALS_MISSING",
                    "Need one placeable stack for floor=" + floorNeeded + ", walls=" + wallNeeded
                            + ", roof=" + roofNeeded + "; inventory cannot satisfy this blueprint", false);
            return;
        }

        writeOrigin(task.result, origin);
        task.result.addProperty("width", width);
        task.result.addProperty("depth", depth);
        task.result.addProperty("height", height);
        task.result.addProperty("floor_item", floor);
        task.result.addProperty("wall_item", wall);
        task.result.addProperty("roof_item", roof);
        task.result.addProperty("initialized", true);
        task.stepIndex = 0;
        task.totalSteps = buildSteps(task).size();
        task.progress = "structure_planned_0_of_" + task.totalSteps;
        persist(task);
    }

    private void pollBuildStructure(MinecraftClient client, AgentTask task) {
        ensureTaskResult(task);
        if (!task.result.has("initialized")) {
            initializeStructure(client, task);
            return;
        }
        List<BuildStep> steps = buildSteps(task);
        int index = task.stepIndex == null ? 0 : task.stepIndex;
        task.totalSteps = steps.size();
        if (index >= steps.size()) {
            task.result.addProperty("placed_steps", steps.size());
            succeed(task, "structure_completed");
            return;
        }
        BuildStep step = steps.get(index);
        Block wanted = blockForItem(step.item);
        BlockState currentState = client.world.getBlockState(step.position);
        if (currentState.getBlock() == wanted) {
            task.stepIndex = index + 1;
            task.progress = "building_" + task.stepIndex + "_of_" + steps.size();
            persist(task);
            return;
        }
        if (!currentState.getMaterial().isReplaceable()) {
            fail(task, "BUILD_TARGET_OCCUPIED",
                    "Blueprint step " + index + " is occupied at " + step.position, false);
            return;
        }
        if (client.player.squaredDistanceTo(Vec3d.ofCenter(step.position)) > 16.0D) {
            task.progress = "approaching_build_step_" + index + "_of_" + steps.size();
            if (!baritone.isBusy()) {
                baritone.goTo(step.position.getX(), step.position.getY(), step.position.getZ(), 2);
            }
            if (clientTicks - lastMovedTick > 200) {
                failOrRetry(task, "BUILD_PATH_STUCK",
                        "No movement for 10 seconds while approaching " + step.position, true);
            }
            if (timedOut(task, 900)) {
                failOrRetry(task, "BUILD_TIMEOUT", "Structure did not finish within 15 minutes", true);
            }
            return;
        }
        baritone.stop();
        task.progress = "placing_build_step_" + index + "_of_" + steps.size();
        if (clientTicks - lastAutomationTick >= 8) {
            lastAutomationTick = clientTicks;
            PlacementAttempt attempt = tryPlaceInventoryBlock(client, step.item, step.position);
            if (attempt == PlacementAttempt.ITEM_MISSING) {
                fail(task, "BUILD_MATERIAL_DEPLETED",
                        "No " + step.item + " remains for blueprint step " + index, false);
            } else if (attempt == PlacementAttempt.NO_SUPPORT) {
                failOrRetry(task, "BUILD_STEP_UNSUPPORTED",
                        "Blueprint step has no placement support at " + step.position, true);
            }
        }
    }

    private static List<BuildStep> buildSteps(AgentTask task) {
        JsonObject plan = task.result;
        int ox = plan.get("x").getAsInt();
        int oy = plan.get("y").getAsInt();
        int oz = plan.get("z").getAsInt();
        int width = plan.get("width").getAsInt();
        int depth = plan.get("depth").getAsInt();
        int height = plan.get("height").getAsInt();
        String floor = plan.get("floor_item").getAsString();
        String wall = plan.get("wall_item").getAsString();
        String roof = plan.get("roof_item").getAsString();
        List<BuildStep> steps = new ArrayList<>();
        for (int x = 0; x < width; x++) {
            for (int z = 0; z < depth; z++) {
                steps.add(new BuildStep(new BlockPos(ox + x, oy, oz + z), floor));
            }
        }
        int doorX = width / 2;
        for (int y = 1; y <= height; y++) {
            for (int x = 0; x < width; x++) {
                if (!(x == doorX && y <= 2)) {
                    steps.add(new BuildStep(new BlockPos(ox + x, oy + y, oz), wall));
                }
                steps.add(new BuildStep(new BlockPos(ox + x, oy + y, oz + depth - 1), wall));
            }
            for (int z = 1; z < depth - 1; z++) {
                steps.add(new BuildStep(new BlockPos(ox, oy + y, oz + z), wall));
                steps.add(new BuildStep(new BlockPos(ox + width - 1, oy + y, oz + z), wall));
            }
        }
        for (int x = 0; x < width; x++) {
            for (int z = 0; z < depth; z++) {
                steps.add(new BuildStep(new BlockPos(ox + x, oy + height + 1, oz + z), roof));
            }
        }
        return steps;
    }

    private void pollInteractBlock(MinecraftClient client, AgentTask task) {
        BlockPos target = requestedPosition(task.arguments);
        if (client.world.getBlockState(target).isAir()) {
            fail(task, "INTERACTION_TARGET_MISSING", "No block exists at " + target, false);
            return;
        }
        if (client.player.squaredDistanceTo(Vec3d.ofCenter(target)) > 16.0D) {
            task.progress = "approaching_interaction_target";
            if (!baritone.isBusy()) baritone.goTo(target.getX(), target.getY(), target.getZ(), 2);
            if (timedOut(task, 120)) {
                failOrRetry(task, "INTERACTION_TARGET_UNREACHABLE", "Could not reach " + target, true);
            }
            return;
        }
        baritone.stop();
        String item = optionalString(task.arguments, "item", "");
        if (!item.isEmpty() && !item.equals(itemId(client.player.getMainHandStack()))) {
            int slot = findInventorySlot(client, item);
            if (slot < 0) {
                fail(task, "ITEM_NOT_FOUND", "Inventory does not contain " + item, false);
                return;
            }
            equipInventorySlot(client, slot);
            task.progress = "equipping_interaction_item";
            return;
        }
        Direction face = parseDirection(optionalString(task.arguments, "face", "up"));
        BlockHitResult hit = new BlockHitResult(hitPosition(target, face), face, target, false);
        client.interactionManager.interactBlock(client.player, client.world, Hand.MAIN_HAND, hit);
        client.player.swingHand(Hand.MAIN_HAND);
        succeed(task, "block_interaction_dispatched");
    }

    private void pollBreakBlockAt(MinecraftClient client, AgentTask task) {
        BlockPos target = requestedPosition(task.arguments);
        BlockState state = client.world.getBlockState(target);
        if (state.isAir()) {
            succeed(task, "target_block_broken");
            return;
        }
        if (client.player.getCameraPosVec(1.0F).squaredDistanceTo(Vec3d.ofCenter(target)) > 16.0D) {
            task.progress = "approaching_break_target";
            if (!baritone.isBusy()) baritone.goTo(target.getX(), target.getY(), target.getZ(), 2);
            if (timedOut(task, 120)) {
                failOrRetry(task, "BREAK_TARGET_UNREACHABLE", "Could not reach " + target, true);
            }
            return;
        }
        baritone.stop();
        selectBestInventoryTool(client, state);
        task.progress = "breaking_target_block";
        client.interactionManager.updateBlockBreakingProgress(target, Direction.UP);
        client.player.swingHand(Hand.MAIN_HAND);
        if (timedOut(task, 120)) {
            failOrRetry(task, "BLOCK_BREAK_TIMEOUT", "Block did not break at " + target, true);
        }
    }

    private void pollSmelt(MinecraftClient client, AgentTask task) {
        String output = requiredString(task.arguments, "item");
        int amount = optionalInt(task.arguments, "amount", 1);
        if (inventoryCount(task.arguments, output) - startingItemCount >= amount) {
            closeAutomationScreen(client);
            succeed(task, "smelted_requested_items");
            return;
        }
        if (client.player.currentScreenHandler instanceof AbstractFurnaceScreenHandler) {
            AbstractFurnaceScreenHandler handler = (AbstractFurnaceScreenHandler) client.player.currentScreenHandler;
            task.progress = "smelting";
            if (clientTicks - lastAutomationTick >= 8) {
                lastAutomationTick = clientTicks;
                Slot result = handler.getSlot(2);
                if (result.hasStack() && output.equals(itemId(result.getStack()))) {
                    client.interactionManager.clickSlot(handler.syncId, result.id, 0,
                            SlotActionType.QUICK_MOVE, client.player);
                } else if (!handler.getSlot(0).hasStack()) {
                    quickMovePlayerItem(client, handler, requiredString(task.arguments, "input"));
                } else if (!handler.getSlot(1).hasStack()) {
                    quickMovePlayerItem(client, handler,
                            optionalString(task.arguments, "fuel", "minecraft:coal"));
                }
            }
            if (timedOut(task, 360)) {
                failOrRetry(task, "SMELT_TIMEOUT", "The furnace did not produce the requested output", true);
            }
            return;
        }
        BlockPos furnace = findNearestBlock(client, Blocks.FURNACE, 16);
        if (furnace == null) {
            fail(task, "FURNACE_NOT_FOUND", "No furnace is visible within 16 blocks", false);
            return;
        }
        approachAndOpen(client, task, furnace, "furnace");
    }

    private void startAcquire(MinecraftClient client, AgentTask task) {
        String item = requiredString(task.arguments, "item");
        int wanted = optionalInt(task.arguments, "amount", 1);
        if (inventoryCount(task.arguments, item) >= wanted) {
            succeed(task, "already_in_inventory");
            return;
        }
        int missing = wanted - inventoryCount(task.arguments, item);
        if (isLog(item)) {
            startBlockCollection(client, task, wanted);
            return;
        }
        String block = acquisitionBlock(item);
        if (block != null) {
            startBlockCollection(client, task, wanted);
            return;
        }
        if (findRecipe(client, item, RecipeType.CRAFTING) != null) {
            task.progress = "acquire_crafting";
            return;
        }
        fail(task, "NO_ACQUISITION_STRATEGY", "No built-in acquisition strategy for " + item, false);
    }

    private void pollAcquire(MinecraftClient client, AgentTask task) {
        String item = requiredString(task.arguments, "item");
        int wanted = optionalInt(task.arguments, "amount", 1);
        int currentCount = inventoryCount(task.arguments, item);
        if (currentCount >= wanted) {
            baritone.stop();
            succeed(task, "resource_target_reached");
            return;
        }
        if (task.progress.startsWith("direct_")) {
            pollDirectBlockCollection(client, task);
            return;
        }
        if (task.progress.startsWith("acquire_crafting") || task.progress.startsWith("crafting_")) {
            pollCraft(client, task, wanted - startingItemCount);
            return;
        }
        if (clientTicks - actionStartedTick > START_GRACE_TICKS && !baritone.isBusy()) {
            failOrRetry(task, "ACQUISITION_ENDED_EARLY",
                    "Resource acquisition stopped before inventory reached " + wanted, true);
            return;
        }
        if (timedOut(task, 300)) {
            failOrRetry(task, "ACQUISITION_TIMEOUT", "Resource target was not reached", true);
        }
    }

    private void pollInspectContainer(MinecraftClient client, AgentTask task) {
        if (client.player.currentScreenHandler instanceof GenericContainerScreenHandler) {
            if (!task.progress.startsWith("reading_container")) {
                task.progress = "reading_container";
                lastAutomationTick = clientTicks;
                persist(task);
                return;
            }
            if (clientTicks - lastAutomationTick >= 15) {
                closeAutomationScreen(client);
                succeed(task, "container_contents_memorized");
            }
            return;
        }
        BlockPos requested = requestedBlockPos(task.arguments);
        BlockPos container = requested != null ? requested : findNearestContainer(client, 16);
        if (container == null) {
            fail(task, "CONTAINER_NOT_FOUND", "No chest, trapped chest, or barrel is visible within 16 blocks", false);
            return;
        }
        approachAndOpen(client, task, container, "container");
    }

    private void pollPickup(MinecraftClient client, AgentTask task) {
        if (collectedEnough(task)) {
            baritone.stop();
            succeed(task, "requested_items_collected");
            return;
        }
        String item = optionalString(task.arguments, "item", "");
        ItemEntity target = nearestItem(client, item);
        if (target == null) {
            task.progress = "searching_items";
            if (timedOut(task, 60)) {
                failOrRetry(task, "ITEM_NOT_FOUND",
                        "No matching dropped item is visible within 24 blocks", true);
            }
            return;
        }
        approachOrWaitItem(client, task, target, 90, "ITEM_UNREACHABLE", true);
    }

    private void pollDropCollection(MinecraftClient client, AgentTask task) {
        String drop = targetItem(task);
        boolean collected = drop.isEmpty()
                ? totalInventoryCount() > startingInventoryCount
                : inventoryCount(task.arguments, drop) > startingItemCount;
        if (collected) {
            task.progress = "direct_after_drop";
            return;
        }
        ItemEntity target = nearestItem(client, drop);
        if (target == null) {
            if (timedOut(task, 90)) {
                failOrRetry(task, "DROP_NOT_FOUND",
                        "The dropped item is not visible within 24 blocks", true);
            }
            return;
        }
        // This item was produced by the block we deliberately mined. Unlike a
        // free-standing pickup request, following it down is part of completing
        // the mining action and must not be rejected by the anti-pit heuristic.
        approachOrWaitItem(client, task, target, 90, "DROP_UNREACHABLE", false);
    }

    /**
     * Approaches a dropped item and waits in pickup range. Minecraft 1.16.5
     * picks items up within roughly one block, so the wait threshold is tight
     * and the approach goal is tightened from radius 1 to radius 0 once the
     * player stands next to the item's cell. Items more than a block below the
     * player (holes, ledges, lava edges) fail cleanly instead of being chased
     * into a trap.
     */
    private void approachOrWaitItem(MinecraftClient client, AgentTask task,
                                    ItemEntity item, int timeoutSeconds, String unreachableCode,
                                    boolean rejectDropsBelowPlayer) {
        double distance = client.player.distanceTo(item);
        double dy = Math.abs(client.player.getY() - item.getY());
        if (distance <= 1.2D && dy <= 0.8D) {
            baritone.stop();
            task.progress = "waiting_for_item_pickup";
            if (clientTicks - pickupIdleTick > 120) {
                failOrRetry(task, "PICKUP_STALLED",
                        "Item is in reach but was not collected", true);
            }
            return;
        }
        pickupIdleTick = clientTicks;
        BlockPos pos = item.getBlockPos();
        boolean tighten = !baritone.isBusy() && distance <= 2.4D;
        if (rejectDropsBelowPlayer && tighten && client.player.getY() - item.getY() > 0.5D) {
            failOrRetry(task, unreachableCode,
                    "Item at " + pos + " is out of reach below the player", true);
            return;
        }
        task.progress = "approaching_item_" + pos.getX() + "_" + pos.getY() + "_" + pos.getZ();
        if (!baritone.isBusy()) {
            baritone.goTo(pos.getX(), pos.getY(), pos.getZ(), tighten ? 0 : 1);
        }
        if (timedOut(task, timeoutSeconds)) {
            failOrRetry(task, unreachableCode, "Could not reach the item at " + pos, true);
        }
    }

    private static ItemEntity nearestItem(MinecraftClient client, String itemId) {
        if (client.player == null || client.world == null) {
            return null;
        }
        Box area = client.player.getBoundingBox().expand(24.0D);
        List<ItemEntity> items = client.world.getEntitiesByClass(ItemEntity.class, area, entity -> true);
        ItemEntity best = null;
        double bestDistance = Double.MAX_VALUE;
        for (ItemEntity entity : items) {
            if (!matchesItem(entity.getStack(), itemId)) {
                continue;
            }
            double distance = client.player.squaredDistanceTo(entity);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = entity;
            }
        }
        return best;
    }

    private static boolean matchesItem(ItemStack stack, String itemId) {
        if (itemId == null || itemId.isEmpty()) {
            return true;
        }
        String actual = Registry.ITEM.getId(stack.getItem()).toString();
        if (itemId.equals(actual)) {
            return true;
        }
        return "#minecraft:logs".equals(itemId) && isLog(actual);
    }

    private void startBlockCollection(MinecraftClient client, AgentTask task, int baritoneTargetCount) {
        activeBlockTarget = findNearestCollectionTarget(client, task, 16);
        if (activeBlockTarget != null) {
            task.progress = directApproachProgress(activeBlockTarget);
            WifeNgClient.LOGGER.info("direct target {} creative={} player={}",
                    activeBlockTarget, client.player.isCreative(), client.player.getPos());
            baritone.goTo(activeBlockTarget.getX(), activeBlockTarget.getY(), activeBlockTarget.getZ(), 2);
            return;
        }
        startBaritoneFallback(client, task, baritoneTargetCount);
    }

    private void startBaritoneFallback(MinecraftClient client, AgentTask task, int baritoneTargetCount) {
        activeBlockTarget = null;
        if ("chop".equals(task.tool)
                || ("acquire".equals(task.tool) && isLog(requiredString(task.arguments, "item")))) {
            baritone.chop(baritoneTargetCount);
            task.progress = "chopping";
        } else {
            String block = "mine".equals(task.tool)
                    ? requiredString(task.arguments, "block")
                    : acquisitionBlock(requiredString(task.arguments, "item"));
            WifeNgClient.LOGGER.info("no visible target within 16, falling back to baritone.mine({})", block);
            baritone.mine(block, baritoneTargetCount);
            task.progress = "mining";
        }
    }

    private void pollDirectBlockCollection(MinecraftClient client, AgentTask task) {
        if (activeBlockTarget == null) {
            if (task.progress.startsWith("direct_collecting_drop")) {
                pollDropCollection(client, task);
                return;
            }
            if (clientTicks - lastAutomationTick < 10) return;
            lastAutomationTick = clientTicks;
            activeBlockTarget = findNearestCollectionTarget(client, task, 16);
            if (activeBlockTarget == null) {
                failOrRetry(task, "VISIBLE_TARGET_EXHAUSTED",
                        "No matching block is visible after the previous block was collected", true);
                return;
            }
            task.progress = directApproachProgress(activeBlockTarget);
            baritone.goTo(activeBlockTarget.getX(), activeBlockTarget.getY(), activeBlockTarget.getZ(), 2);
            return;
        }

        BlockState state = client.world.getBlockState(activeBlockTarget);
        if (!matchesCollectionTarget(task, state.getBlock())) {
            WifeNgClient.LOGGER.info("direct target {} vanished (was broken), collecting drop", activeBlockTarget);
            activeBlockTarget = null;
            task.progress = "direct_collecting_drop";
            directBlockBrokenTick = clientTicks;
            lastAutomationTick = clientTicks;
            return;
        }

        double distance = client.player.getCameraPosVec(1.0F)
                .squaredDistanceTo(Vec3d.ofCenter(activeBlockTarget));
        if (distance > 16.0D) {
            task.progress = directApproachProgress(activeBlockTarget);
            if (!baritone.isBusy()) {
                baritone.goTo(activeBlockTarget.getX(), activeBlockTarget.getY(), activeBlockTarget.getZ(), 2);
            }
            if (timedOut(task, defaultTimeoutSeconds(task))) {
                failOrRetry(task, "BLOCK_UNREACHABLE", "Could not reach visible block at " + activeBlockTarget, true);
            }
            return;
        }

        baritone.stop();
        int toolSlot = selectBestHotbarTool(client, state);
        if (!client.player.inventory.getStack(toolSlot).getItem().isSuitableFor(state)) {
            // No hotbar tool can harvest this block quickly (e.g. an axe for
            // logs). Fall back to Baritone's own mining, which accepts slow
            // harvesting, instead of failing the whole task.
            WifeNgClient.LOGGER.info("no suitable tool for {}, falling back to baritone.mine", state.getBlock());
            startBaritoneFallback(client, task,
                    startingItemCount + optionalInt(task.arguments, "amount", 1));
            return;
        }
        boolean firstBreakTick = !task.progress.startsWith("direct_breaking_");
        task.progress = "direct_breaking_" + activeBlockTarget.getX() + "_"
                + activeBlockTarget.getY() + "_" + activeBlockTarget.getZ();
        if (firstBreakTick) {
            WifeNgClient.LOGGER.info("breaking {} eyeDist={} creative={} state={} held={}",
                    activeBlockTarget, Math.sqrt(distance), client.player.isCreative(), state.getBlock(),
                    client.player.getMainHandStack());
        }
        client.interactionManager.updateBlockBreakingProgress(activeBlockTarget, Direction.UP);
        client.player.swingHand(Hand.MAIN_HAND);
        if (timedOut(task, defaultTimeoutSeconds(task))) {
            failOrRetry(task, "BLOCK_BREAK_TIMEOUT", "Could not break visible block at " + activeBlockTarget, true);
        }
    }

    private static String directApproachProgress(BlockPos target) {
        return "direct_approaching_" + target.getX() + "_" + target.getY() + "_" + target.getZ();
    }

    private BlockPos findNearestCollectionTarget(MinecraftClient client, AgentTask task, int radius) {
        if (client.player == null || client.world == null) return null;
        BlockPos origin = client.player.getBlockPos();
        BlockPos best = null;
        double bestDistance = Double.MAX_VALUE;
        for (BlockPos candidate : BlockPos.iterate(origin.add(-radius, -radius, -radius),
                origin.add(radius, radius, radius))) {
            if (!matchesCollectionTarget(task, client.world.getBlockState(candidate).getBlock())) continue;
            double distance = candidate.getSquaredDistance(origin);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = candidate.toImmutable();
            }
        }
        return best;
    }

    private static boolean matchesCollectionTarget(AgentTask task, Block block) {
        String actual = Registry.BLOCK.getId(block).toString();
        if ("chop".equals(task.tool)) return isLog(actual);
        if ("mine".equals(task.tool)) return requiredString(task.arguments, "block").equals(actual);
        if ("acquire".equals(task.tool)) {
            String item = requiredString(task.arguments, "item");
            if (isLog(item)) return isLog(actual);
            String wantedBlock = acquisitionBlock(item);
            return wantedBlock != null && wantedBlock.equals(actual);
        }
        return false;
    }

    private static int selectBestHotbarTool(MinecraftClient client, BlockState state) {
        int bestSlot = client.player.inventory.selectedSlot;
        ItemStack bestStack = client.player.inventory.getStack(bestSlot);
        boolean bestSuitable = bestStack.getItem().isSuitableFor(state);
        float bestSpeed = bestStack.getMiningSpeedMultiplier(state);
        for (int slot = 0; slot < 9; slot++) {
            ItemStack stack = client.player.inventory.getStack(slot);
            boolean suitable = stack.getItem().isSuitableFor(state);
            float speed = stack.getMiningSpeedMultiplier(state);
            if (suitable != bestSuitable ? suitable : speed > bestSpeed) {
                bestSuitable = suitable;
                bestSpeed = speed;
                bestSlot = slot;
            }
        }
        client.player.inventory.selectedSlot = bestSlot;
        return bestSlot;
    }

    private static void selectBestInventoryTool(MinecraftClient client, BlockState state) {
        int bestSlot = client.player.inventory.selectedSlot;
        ItemStack bestStack = client.player.inventory.getStack(bestSlot);
        boolean bestSuitable = bestStack.getItem().isSuitableFor(state);
        float bestSpeed = bestStack.getMiningSpeedMultiplier(state);
        for (int slot = 0; slot < client.player.inventory.size(); slot++) {
            ItemStack stack = client.player.inventory.getStack(slot);
            boolean suitable = stack.getItem().isSuitableFor(state);
            float speed = stack.getMiningSpeedMultiplier(state);
            if (suitable != bestSuitable ? suitable : speed > bestSpeed) {
                bestSlot = slot;
                bestSuitable = suitable;
                bestSpeed = speed;
            }
        }
        equipInventorySlot(client, bestSlot);
    }

    private static void equipInventoryItem(MinecraftClient client, String item) {
        int slot = findInventorySlot(client, item);
        if (slot >= 0) {
            equipInventorySlot(client, slot);
            return;
        }
        throw new IllegalArgumentException("Inventory does not contain " + item);
    }

    private static int findInventorySlot(MinecraftClient client, String item) {
        if (client.player == null) return -1;
        for (int slot = 0; slot < client.player.inventory.size(); slot++) {
            ItemStack stack = client.player.inventory.getStack(slot);
            if (!stack.isEmpty() && item.equals(itemId(stack))) return slot;
        }
        return -1;
    }

    private static void equipInventorySlot(MinecraftClient client, int slot) {
        if (slot < 0 || slot >= client.player.inventory.size()) {
            throw new IllegalArgumentException("Inventory slot is out of range: " + slot);
        }
        if (slot < 9) {
            client.player.inventory.selectedSlot = slot;
            client.player.networkHandler.sendPacket(new UpdateSelectedSlotC2SPacket(slot));
        } else {
            // Never mutate PlayerInventory directly here. That changes only the
            // client's local copy, so the server can still see the old hotbar
            // item and use/place it. Send a real screen-handler SWAP operation.
            ScreenHandler handler = client.player.currentScreenHandler;
            Slot source = null;
            ItemStack sourceStack = client.player.inventory.getStack(slot);
            for (Slot screenSlot : handler.slots) {
                // Slot#index is private in 1.16.5. Inventories return their
                // stored ItemStack instance, so identity uniquely maps this
                // non-empty inventory stack into the active handler.
                if (screenSlot.inventory == client.player.inventory
                        && screenSlot.getStack() == sourceStack) {
                    source = screenSlot;
                    break;
                }
            }
            if (source == null) {
                throw new IllegalArgumentException("Cannot map inventory slot " + slot
                        + " into the current screen handler");
            }
            client.interactionManager.clickSlot(handler.syncId, source.id,
                    client.player.inventory.selectedSlot, SlotActionType.SWAP, client.player);
        }
    }

    private boolean craftPortableTable(MinecraftClient client, AgentTask task) {
        Recipe<?> tableRecipe = findRecipe(client, "minecraft:crafting_table", RecipeType.CRAFTING);
        if (tableRecipe == null) return false;
        ScreenHandler rawHandler = client.player.currentScreenHandler;
        if (!(rawHandler instanceof AbstractRecipeScreenHandler)) {
            closeAutomationScreen(client);
            task.progress = "opening_player_crafting_for_table";
            return true;
        }
        AbstractRecipeScreenHandler<?> handler = (AbstractRecipeScreenHandler<?>) rawHandler;
        if (!tableRecipe.fits(handler.getCraftingWidth(), handler.getCraftingHeight())) return false;
        task.progress = "crafting_temporary_crafting_table";
        if (clientTicks - lastAutomationTick >= 8) {
            lastAutomationTick = clientTicks;
            Slot result = handler.getSlot(handler.getCraftingResultSlotIndex());
            if (result.hasStack() && "minecraft:crafting_table".equals(itemId(result.getStack()))) {
                client.interactionManager.clickSlot(handler.syncId, result.id, 0,
                        SlotActionType.QUICK_MOVE, client.player);
            } else {
                client.interactionManager.clickRecipe(handler.syncId, tableRecipe, false);
            }
        }
        return true;
    }

    private static BlockPos findSafePlacementNearPlayer(MinecraftClient client) {
        BlockPos origin = client.player.getBlockPos();
        for (int radius = 1; radius <= 3; radius++) {
            for (int dx = -radius; dx <= radius; dx++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    if (Math.max(Math.abs(dx), Math.abs(dz)) != radius) continue;
                    for (int dy = 0; dy >= -2; dy--) {
                        BlockPos target = origin.add(dx, dy, dz);
                        BlockState targetState = client.world.getBlockState(target);
                        BlockState below = client.world.getBlockState(target.down());
                        if (targetState.getMaterial().isReplaceable()
                                && !below.getMaterial().isReplaceable()) {
                            return target.toImmutable();
                        }
                    }
                }
            }
        }
        return null;
    }

    private static PlacementAttempt tryPlaceInventoryBlock(MinecraftClient client, String item,
                                                            BlockPos target) {
        // Inventory slot changes and right-clicks must be separate protocol steps.
        // Otherwise the server can process the click before the selected-slot
        // packet and place whatever was previously held (for example dirt).
        if (!item.equals(itemId(client.player.getMainHandStack()))) {
            int slot = findInventorySlot(client, item);
            if (slot < 0) return PlacementAttempt.ITEM_MISSING;
            equipInventorySlot(client, slot);
            return PlacementAttempt.EQUIPPING;
        }
        for (Direction face : Direction.values()) {
            BlockPos support = target.offset(face.getOpposite());
            if (client.world.getBlockState(support).getMaterial().isReplaceable()) continue;
            BlockHitResult hit = new BlockHitResult(hitPosition(support, face), face, support, false);
            WifeNgClient.LOGGER.info("placing requested_item={} held_item={} target={} support={} face={}",
                    item, itemId(client.player.getMainHandStack()), target, support, face);
            client.interactionManager.interactBlock(client.player, client.world, Hand.MAIN_HAND, hit);
            client.player.swingHand(Hand.MAIN_HAND);
            return PlacementAttempt.DISPATCHED;
        }
        return PlacementAttempt.NO_SUPPORT;
    }

    private static Vec3d hitPosition(BlockPos block, Direction face) {
        return Vec3d.ofCenter(block).add(face.getOffsetX() * 0.5D,
                face.getOffsetY() * 0.5D, face.getOffsetZ() * 0.5D);
    }

    private static void ensureTaskResult(AgentTask task) {
        if (task.result == null) task.result = new JsonObject();
        if (task.stepIndex == null) task.stepIndex = 0;
        if (task.totalSteps == null) task.totalSteps = 0;
    }

    private static void writeOrigin(JsonObject result, BlockPos origin) {
        result.addProperty("x", origin.getX());
        result.addProperty("y", origin.getY());
        result.addProperty("z", origin.getZ());
    }

    private static int boundedDimension(JsonObject arguments, String name, int fallback, int min, int max) {
        int value = optionalInt(arguments, name, fallback);
        if (value < min || value > max) {
            throw new IllegalArgumentException(name + " must be between " + min + " and " + max);
        }
        return value;
    }

    private static Map<String, Integer> placeableInventory(MinecraftClient client) {
        Map<String, Integer> result = new LinkedHashMap<>();
        for (int slot = 0; slot < client.player.inventory.size(); slot++) {
            ItemStack stack = client.player.inventory.getStack(slot);
            if (stack.isEmpty() || !(stack.getItem() instanceof BlockItem)) continue;
            String id = itemId(stack);
            result.put(id, result.getOrDefault(id, 0) + stack.getCount());
        }
        return result;
    }

    private static String reserveBuildMaterial(Map<String, Integer> remaining, String explicit,
                                               int needed, String[] preferred) {
        if (!explicit.isEmpty()) {
            if (remaining.getOrDefault(explicit, 0) < needed) return "";
            remaining.put(explicit, remaining.get(explicit) - needed);
            return explicit;
        }
        for (String candidate : preferred) {
            if (remaining.getOrDefault(candidate, 0) >= needed) {
                remaining.put(candidate, remaining.get(candidate) - needed);
                return candidate;
            }
        }
        String best = "";
        int bestCount = -1;
        for (Map.Entry<String, Integer> entry : remaining.entrySet()) {
            if (entry.getValue() >= needed && entry.getValue() > bestCount) {
                best = entry.getKey();
                bestCount = entry.getValue();
            }
        }
        if (!best.isEmpty()) remaining.put(best, remaining.get(best) - needed);
        return best;
    }

    private static BlockPos findBuildSite(MinecraftClient client, int width, int depth,
                                          int clearance, int radius) {
        BlockPos player = client.player.getBlockPos();
        for (int ring = 0; ring <= radius; ring += 2) {
            for (int dx = -ring; dx <= ring; dx += 2) {
                for (int dz = -ring; dz <= ring; dz += 2) {
                    if (ring > 0 && Math.max(Math.abs(dx), Math.abs(dz)) != ring) continue;
                    int centerX = player.getX() + dx;
                    int centerZ = player.getZ() + dz;
                    int surface = findNaturalSurface(client, centerX, centerZ,
                            player.getY() + 8, Math.max(1, player.getY() - 32));
                    if (surface < 0) continue;
                    BlockPos origin = new BlockPos(centerX - width / 2, surface + 1,
                            centerZ - depth / 2);
                    if (isClearSupportedSite(client, origin, width, depth, clearance)) {
                        return origin;
                    }
                }
            }
        }
        return null;
    }

    private static boolean isClearSupportedSite(MinecraftClient client, BlockPos origin,
                                                int width, int depth, int clearance) {
        for (int x = 0; x < width; x++) {
            for (int z = 0; z < depth; z++) {
                BlockPos ground = origin.add(x, -1, z);
                BlockState groundState = client.world.getBlockState(ground);
                if (!isBuildGround(groundState)) return false;
                for (int y = 0; y < clearance; y++) {
                    BlockState state = client.world.getBlockState(origin.add(x, y, z));
                    if (!state.getMaterial().isReplaceable()) return false;
                }
            }
        }
        return true;
    }

    private static int findNaturalSurface(MinecraftClient client, int x, int z, int top, int bottom) {
        int safeTop = Math.min(254, top);
        for (int y = safeTop; y >= bottom; y--) {
            BlockState state = client.world.getBlockState(new BlockPos(x, y, z));
            if (isBuildGround(state)) return y;
        }
        return -1;
    }

    private static boolean isBuildGround(BlockState state) {
        if (state.getMaterial().isReplaceable() || !state.getFluidState().isEmpty()) return false;
        String id = Registry.BLOCK.getId(state.getBlock()).toString();
        return !(id.contains("leaves") || id.endsWith("_log") || id.endsWith("_wood")
                || id.contains("mushroom") || id.contains("cactus") || id.contains("bamboo")
                || id.contains("fence") || id.contains("wall") || id.contains("slab")
                || id.contains("stairs") || id.contains("chest") || id.contains("furnace")
                || id.contains("crafting_table"));
    }

    private static Direction parseDirection(String value) {
        switch (value.toLowerCase(java.util.Locale.ROOT)) {
            case "down": return Direction.DOWN;
            case "north": return Direction.NORTH;
            case "south": return Direction.SOUTH;
            case "west": return Direction.WEST;
            case "east": return Direction.EAST;
            case "up": return Direction.UP;
            default: throw new IllegalArgumentException("Unknown block face: " + value);
        }
    }

    private static BlockPos requestedPosition(JsonObject arguments) {
        return new BlockPos(requiredInt(arguments, "x"), requiredInt(arguments, "y"),
                requiredInt(arguments, "z"));
    }

    private static Block blockForItem(String item) {
        try {
            return Registry.BLOCK.get(new Identifier(item));
        } catch (Exception ignored) {
            return Blocks.AIR;
        }
    }

    private static int defaultTimeoutSeconds(AgentTask task) {
        switch (task.tool) {
            case "goto": return 120;
            case "pickup": return 60;
            case "deliver": return 120;
            case "craft": return 120;
            case "smelt": return 360;
            case "acquire": return 300;
            case "inspect_container": return 120;
            case "mine":
            case "chop": return 300;
            default: return 120;
        }
    }

    private boolean timedOut(AgentTask task, int fallbackSeconds) {
        int seconds = optionalInt(task.arguments, "timeout_seconds", fallbackSeconds);
        return clientTicks - actionStartedTick > Math.max(20L, seconds * 20L);
    }

    private void requireInventory(AgentTask task, String item, int amount) {
        int available = inventoryCount(task.arguments, item);
        if (available < Math.max(1, amount)) {
            throw new IllegalArgumentException("Need " + amount + " of " + item + ", but inventory has " + available);
        }
    }

    private static AbstractClientPlayerEntity findPlayer(MinecraftClient client, String name) {
        if (client.world == null) return null;
        for (AbstractClientPlayerEntity player : client.world.getPlayers()) {
            if (player.getEntityName().equalsIgnoreCase(name)) return player;
        }
        return null;
    }

    private static boolean dropOne(MinecraftClient client, String item) {
        if (client.player == null || client.interactionManager == null) return false;
        ScreenHandler handler = client.player.currentScreenHandler;
        for (Slot slot : handler.slots) {
            if (slot.inventory == client.player.inventory && slot.hasStack()
                    && item.equals(itemId(slot.getStack()))) {
                client.interactionManager.clickSlot(handler.syncId, slot.id, 0,
                        SlotActionType.THROW, client.player);
                return true;
            }
        }
        return false;
    }

    private static void quickMovePlayerItem(MinecraftClient client, ScreenHandler handler, String item) {
        for (Slot slot : handler.slots) {
            if (slot.inventory == client.player.inventory && slot.hasStack()
                    && item.equals(itemId(slot.getStack()))) {
                client.interactionManager.clickSlot(handler.syncId, slot.id, 0,
                        SlotActionType.QUICK_MOVE, client.player);
                return;
            }
        }
    }

    private static Recipe<?> findRecipe(MinecraftClient client, String outputItem, RecipeType<?> type) {
        if (client.world == null) return null;
        for (Recipe<?> recipe : client.world.getRecipeManager().values()) {
            if (recipe.getType() == type && outputItem.equals(itemId(recipe.getOutput()))) {
                return recipe;
            }
        }
        return null;
    }

    private static String itemId(ItemStack stack) {
        return Registry.ITEM.getId(stack.getItem()).toString();
    }

    private static BlockPos findNearestBlock(MinecraftClient client, Block wanted, int radius) {
        if (client.player == null || client.world == null) return null;
        BlockPos origin = client.player.getBlockPos();
        BlockPos best = null;
        double bestDistance = Double.MAX_VALUE;
        for (int y = -radius; y <= radius; y++) {
            for (int x = -radius; x <= radius; x++) {
                for (int z = -radius; z <= radius; z++) {
                    BlockPos pos = origin.add(x, y, z);
                    if (client.world.getBlockState(pos).getBlock() != wanted) continue;
                    double distance = pos.getSquaredDistance(origin);
                    if (distance < bestDistance) {
                        bestDistance = distance;
                        best = pos.toImmutable();
                    }
                }
            }
        }
        return best;
    }

    private static BlockPos findNearestContainer(MinecraftClient client, int radius) {
        if (client.player == null || client.world == null) return null;
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

    private static BlockPos requestedBlockPos(JsonObject arguments) {
        if (!arguments.has("x") && !arguments.has("y") && !arguments.has("z")) return null;
        if (!arguments.has("x") || !arguments.has("y") || !arguments.has("z")) {
            throw new IllegalArgumentException("Container coordinates require x, y, and z together");
        }
        return new BlockPos(arguments.get("x").getAsInt(), arguments.get("y").getAsInt(), arguments.get("z").getAsInt());
    }

    private void approachAndOpen(MinecraftClient client, AgentTask task, BlockPos pos, String name) {
        if (client.player.squaredDistanceTo(Vec3d.ofCenter(pos)) > 16.0D) {
            task.progress = "approaching_" + name;
            if (!baritone.isBusy()) baritone.goTo(pos.getX(), pos.getY(), pos.getZ(), 2);
            if (timedOut(task, 120)) {
                failOrRetry(task, "WORKSTATION_UNREACHABLE", "Could not reach " + name + " at " + pos, true);
            }
            return;
        }
        baritone.stop();
        task.progress = "opening_" + name;
        if (clientTicks - lastAutomationTick >= 10) {
            lastAutomationTick = clientTicks;
            BlockHitResult hit = new BlockHitResult(Vec3d.ofCenter(pos), Direction.UP, pos, false);
            client.interactionManager.interactBlock(client.player, client.world, Hand.MAIN_HAND, hit);
            client.player.swingHand(Hand.MAIN_HAND);
        }
    }

    private static void closeAutomationScreen(MinecraftClient client) {
        if (client.player != null && !(client.player.currentScreenHandler instanceof net.minecraft.screen.PlayerScreenHandler)) {
            client.player.closeHandledScreen();
        }
    }

    private static boolean isLog(String item) {
        return item.endsWith("_log") || item.endsWith("_stem");
    }

    private static String acquisitionBlock(String item) {
        switch (item) {
            case "minecraft:cobblestone": return "minecraft:stone";
            case "minecraft:coal": return "minecraft:coal_ore";
            case "minecraft:diamond": return "minecraft:diamond_ore";
            case "minecraft:redstone": return "minecraft:redstone_ore";
            case "minecraft:lapis_lazuli": return "minecraft:lapis_ore";
            case "minecraft:flint": return "minecraft:gravel";
            case "minecraft:clay_ball": return "minecraft:clay";
            default:
                Block block = Registry.BLOCK.get(new Identifier(item));
                return block == Blocks.AIR ? null : item;
        }
    }

    private boolean reachedGoal(MinecraftClient client, JsonObject arguments) {
        if (client.player == null) {
            return false;
        }
        // Baritone's GoalNear decides "reached" on block coordinates, not on
        // the player's exact position. Match that semantics exactly; otherwise
        // a player standing at the far edge of the radius block would be
        // reported as not reached even though Baritone already stopped.
        BlockPos goal = new BlockPos(requiredInt(arguments, "x"),
                requiredInt(arguments, "y"), requiredInt(arguments, "z"));
        BlockPos player = client.player.getBlockPos();
        int radius = optionalInt(arguments, "radius", 1);
        double dx = player.getX() - goal.getX();
        double dy = player.getY() - goal.getY();
        double dz = player.getZ() - goal.getZ();
        return dx * dx + dy * dy + dz * dz <= (double) radius * radius;
    }

    private boolean collectedEnough(AgentTask task) {
        String target = targetItem(task);
        if (target.isEmpty()) {
            return totalInventoryCount() - startingInventoryCount >= optionalInt(task.arguments, "amount", 1);
        }
        int wanted = optionalInt(task.arguments, "amount", 1);
        return inventoryCount(task.arguments, target) - startingItemCount >= wanted;
    }

    private int inventoryCount(JsonObject ignored, String itemId) {
        // Execution decisions must use the live client inventory. The perception
        // snapshot intentionally refreshes less often and can still list an item
        // for several ticks after crafting, dropping, or moving it.
        MinecraftClient client = MinecraftClient.getInstance();
        int total = 0;
        if (client.player == null) return total;
        for (int slot = 0; slot < client.player.inventory.size(); slot++) {
            ItemStack stack = client.player.inventory.getStack(slot);
            if (stack.isEmpty()) continue;
            String actual = itemId(stack);
            if (itemId.equals(actual) || ("#minecraft:logs".equals(itemId) && isLog(actual))) {
                total += stack.getCount();
            }
        }
        return total;
    }

    private int totalInventoryCount() {
        JsonArray inventory = perception.snapshot().getAsJsonArray("inventory");
        int total = 0;
        if (inventory == null) {
            return total;
        }
        for (JsonElement element : inventory) {
            total += element.getAsJsonObject().get("count").getAsInt();
        }
        return total;
    }

    private static String targetItem(AgentTask task) {
        if ("chop".equals(task.tool)) {
            return "#minecraft:logs";
        }
        if ("mine".equals(task.tool)) {
            String block = requiredString(task.arguments, "block");
            return normalizeCollectItem(block,
                    optionalString(task.arguments, "collect_item", defaultCollectItem(block)));
        }
        if ("pickup".equals(task.tool)) {
            return optionalString(task.arguments, "item", "");
        }
        if ("deliver".equals(task.tool) || "craft".equals(task.tool)
                || "smelt".equals(task.tool) || "acquire".equals(task.tool)) {
            return requiredString(task.arguments, "item");
        }
        return "";
    }

    private static String defaultCollectItem(String block) {
        switch (block) {
            case "minecraft:stone": return "minecraft:cobblestone";
            case "minecraft:coal_ore": return "minecraft:coal";
            case "minecraft:diamond_ore": return "minecraft:diamond";
            case "minecraft:emerald_ore": return "minecraft:emerald";
            case "minecraft:redstone_ore": return "minecraft:redstone";
            case "minecraft:lapis_ore": return "minecraft:lapis_lazuli";
            case "minecraft:clay": return "minecraft:clay_ball";
            default: return block;
        }
    }

    private static String normalizeCollectItem(String block, String requested) {
        // DeepSeek may know modern Minecraft drops. Wife NG is deliberately
        // pinned to 1.16.5, where raw iron/gold/copper do not exist yet.
        if ("minecraft:iron_ore".equals(block) && "minecraft:raw_iron".equals(requested)) {
            return "minecraft:iron_ore";
        }
        if ("minecraft:gold_ore".equals(block) && "minecraft:raw_gold".equals(requested)) {
            return "minecraft:gold_ore";
        }
        try {
            Identifier id = new Identifier(requested);
            if (Registry.ITEM.getId(Registry.ITEM.get(id)).equals(id)) return requested;
        } catch (Exception ignored) {
            // Fall through to the known 1.16.5 drop mapping.
        }
        return defaultCollectItem(block);
    }

    private void succeed(AgentTask task, String detail) {
        task.transition(TaskState.SUCCEEDED, detail);
        persist(task);
        current = null;
    }

    private void fail(AgentTask task, String code, String message, boolean retryable) {
        // A terminal task must never leave a Baritone process running in the
        // background; otherwise a later item/entity can wake the stale action.
        baritone.stop();
        task.failureCode = code;
        task.failureMessage = message == null ? "" : message;
        task.retryable = retryable;
        task.transition(TaskState.FAILED, "failed");
        persist(task);
        current = null;
    }

    private void failOrRetry(AgentTask task, String code, String message, boolean retryable) {
        int maxAttempts = optionalInt(task.arguments, "max_attempts", DEFAULT_MAX_ATTEMPTS);
        if (retryable && task.attempts < Math.max(1, maxAttempts)) {
            baritone.stop();
            task.failureCode = code;
            task.failureMessage = message == null ? "" : message;
            task.retryable = true;
            task.retryAtTick = clientTicks + RETRY_DELAY_TICKS;
            task.transition(TaskState.QUEUED, "retry_wait_" + task.attempts + "_of_" + maxAttempts);
            queue.addFirst(task.id);
            persist(task);
            current = null;
            return;
        }
        fail(task, code, message, retryable);
    }

    private void restore() {
        List<AgentTask> restored = journal.load();
        synchronized (lock) {
            List<AgentTask> recoveredTransitions = new ArrayList<>();
            for (AgentTask task : restored) {
                if (!task.terminal()) {
                    task.transition(TaskState.PAUSED, "recovered_after_restart");
                    recoveredTransitions.add(task);
                }
                tasks.put(task.id, task);
            }
            // Loading the snapshot is not enough: persist the recovery
            // transition after every task has been reinserted so the rewritten
            // snapshot remains complete and the event stream records why the
            // task is paused. A second crash must not leave RUNNING on disk.
            for (AgentTask recovered : recoveredTransitions) {
                persist(recovered);
            }
        }
    }

    private void persist(AgentTask changed) {
        journal.record(changed, tasks.values());
    }

    private static int requiredInt(JsonObject object, String name) {
        if (!object.has(name)) {
            throw new IllegalArgumentException("Missing integer argument: " + name);
        }
        return object.get(name).getAsInt();
    }

    private static int optionalInt(JsonObject object, String name, int fallback) {
        return object.has(name) ? object.get(name).getAsInt() : fallback;
    }

    private static String requiredString(JsonObject object, String name) {
        if (!object.has(name) || object.get(name).getAsString().trim().isEmpty()) {
            throw new IllegalArgumentException("Missing string argument: " + name);
        }
        return object.get(name).getAsString().trim();
    }

    private static String optionalString(JsonObject object, String name, String fallback) {
        return object.has(name) ? object.get(name).getAsString().trim() : fallback;
    }

    private static boolean readBooleanEnvironment(String name) {
        String value = System.getenv(name);
        return value != null && ("1".equals(value.trim())
                || "true".equalsIgnoreCase(value.trim())
                || "yes".equalsIgnoreCase(value.trim())
                || "on".equalsIgnoreCase(value.trim()));
    }
}
