package cn.wudabin.wifeng.http;

import cn.wudabin.wifeng.WifeNgClient;
import cn.wudabin.wifeng.perception.PerceptionService;
import cn.wudabin.wifeng.memory.ContainerMemoryService;
import cn.wudabin.wifeng.task.AgentTask;
import cn.wudabin.wifeng.task.TaskEngine;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Collection;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class ControlServer {
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();

    private final PerceptionService perception;
    private final TaskEngine tasks;
    private final ContainerMemoryService containerMemory;
    private final String token;
    private final int port;
    private HttpServer server;
    private ExecutorService executor;

    public ControlServer(PerceptionService perception, TaskEngine tasks, ContainerMemoryService containerMemory) {
        this.perception = perception;
        this.tasks = tasks;
        this.containerMemory = containerMemory;
        this.token = valueOrDefault(System.getenv("WIFE_NG_TOKEN"), "");
        this.port = parsePort(System.getenv("WIFE_NG_PORT"), 8766);
    }

    public void start() {
        try {
            server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), port), 0);
            server.createContext("/health", this::handleHealth);
            server.createContext("/v1/state", this::handleState);
            server.createContext("/v1/capabilities", this::handleCapabilities);
            server.createContext("/v1/tools", this::handleTools);
            server.createContext("/v1/tasks", this::handleTasks);
            server.createContext("/v1/memory/containers", this::handleContainerMemory);
            executor = Executors.newFixedThreadPool(2, runnable -> {
                Thread thread = new Thread(runnable, "wife-ng-http");
                thread.setDaemon(true);
                return thread;
            });
            server.setExecutor(executor);
            server.start();
        } catch (IOException error) {
            throw new IllegalStateException("Cannot bind Wife NG control API to 127.0.0.1:" + port, error);
        }
    }

    private void handleContainerMemory(HttpExchange exchange) throws IOException {
        if (!authorize(exchange)) return;
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
            sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Use GET");
            return;
        }
        send(exchange, 200, containerMemory.snapshot());
    }

    public String getAddress() {
        return "http://127.0.0.1:" + port;
    }

    private void handleHealth(HttpExchange exchange) throws IOException {
        if (!authorize(exchange)) return;
        JsonObject result = new JsonObject();
        result.addProperty("ok", true);
        result.addProperty("service", "wife-ng");
        result.addProperty("connected", perception.snapshot().get("connected").getAsBoolean());
        send(exchange, 200, result);
    }

    private void handleState(HttpExchange exchange) throws IOException {
        if (!authorize(exchange)) return;
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
            sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Use GET");
            return;
        }
        send(exchange, 200, perception.snapshot());
    }

    private void handleCapabilities(HttpExchange exchange) throws IOException {
        if (!authorize(exchange)) return;
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
            sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Use GET");
            return;
        }
        JsonObject result = new JsonObject();
        result.addProperty("protocol", "wife-ng-tools/1");
        JsonArray tools = new JsonArray();
        tools.add(tool("goto", "Path to a world position", "x:int,y:int,z:int,radius?:int"));
        tools.add(tool("follow", "Continuously follow a player", "player:string"));
        tools.add(tool("stop", "Preempt and stop the active action", ""));
        tools.add(tool("mine", "Mine a block type until an item count delta is observed",
                "block:string,amount?:int,collect_item?:string"));
        tools.add(tool("chop", "Collect any supported overworld or Nether logs", "amount?:int"));
        tools.add(tool("pickup", "Follow and collect dropped items", "item?:string,amount?:int"));
        tools.add(tool("deliver", "Approach a player and drop exact items at their feet",
                "player:string,item:string,amount?:int"));
        tools.add(tool("craft", "Craft an item using player crafting or a nearby table",
                "item:string,amount?:int"));
        tools.add(tool("smelt", "Use a nearby furnace and verify the output",
                "input:string,item:string,fuel?:string,amount?:int"));
        tools.add(tool("acquire", "Choose a built-in mining, logging, or crafting strategy",
                "item:string,amount?:int"));
        tools.add(tool("inspect_container", "Open and persist the contents of a nearby container",
                "x?:int,y?:int,z?:int"));
        tools.add(tool("equip", "Move an inventory item into the selected hotbar slot", "item:string"));
        tools.add(tool("use_item", "Use the held item or equip and use a named inventory item", "item?:string"));
        tools.add(tool("place_block", "Place an inventory block at an exact world position",
                "item:string,x:int,y:int,z:int"));
        tools.add(tool("interact_block", "Use or open a block at an exact world position",
                "x:int,y:int,z:int,face?:string,item?:string"));
        tools.add(tool("break_block_at", "Break the block at an exact world position",
                "x:int,y:int,z:int"));
        tools.add(tool("find_build_site", "Find a nearby flat, supported and clear building site",
                "width?:int,depth?:int,clearance?:int,radius?:int"));
        tools.add(tool("build_structure", "Plan and build a complete small house with resumable verified steps",
                "x?:int,y?:int,z?:int,width?:int,depth?:int,height?:int,search_radius?:int,"
                        + "floor_item?:string,wall_item?:string,roof_item?:string"));
        tools.add(tool("say", "Send one Minecraft chat message", "message:string"));
        if (tasks.isOpMode()) {
            tools.add(tool("server_command", "Execute one Minecraft server command as the OP bot",
                    "command:string"));
        }
        result.add("tools", tools);
        result.addProperty("task_controls", "GET,cancel,pause,resume");
        result.addProperty("op_mode", tasks.isOpMode());
        tools.add(tool("survey_region", "Lossless block states in an explicit loaded region; unknown chunks are marked",
                "from:[x,y,z],to:[x,y,z]; volume<=65536"));
        if (tasks.isOpMode()) {
            tools.add(tool("build_blueprint", "Creative-only bulk cuboid blueprint, ordered writes, resumable and fully verified",
                    "blueprint_id:string,operations:[{from:[x,y,z],to:[x,y,z],state:string}]"));
            tools.add(tool("verify_blueprint", "Compare every specified final cell to the blueprint, returning mismatch positions",
                    "blueprint_id:string,operations:[{from:[x,y,z],to:[x,y,z],state:string}]"));
        }
        result.addProperty("building_protocol", "creative-blueprints/1");
        send(exchange, 200, result);
    }

    private static JsonObject tool(String name, String description, String arguments) {
        JsonObject result = new JsonObject();
        result.addProperty("name", name);
        result.addProperty("description", description);
        result.addProperty("arguments", arguments);
        return result;
    }

    private void handleTools(HttpExchange exchange) throws IOException {
        if (!authorize(exchange)) return;
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Use POST");
            return;
        }
        try {
            JsonObject request = new JsonParser().parse(readBody(exchange)).getAsJsonObject();
            String tool = request.get("tool").getAsString();
            JsonObject arguments = request.has("arguments")
                    ? request.getAsJsonObject("arguments") : new JsonObject();
            AgentTask task = tasks.submit(tool, arguments);
            JsonObject response = new JsonObject();
            response.addProperty("accepted", true);
            response.add("task", GSON.toJsonTree(task));
            send(exchange, 202, response);
        } catch (Exception error) {
            sendError(exchange, 400, "INVALID_REQUEST", error.getMessage());
        }
    }

    private void handleTasks(HttpExchange exchange) throws IOException {
        if (!authorize(exchange)) return;
        String suffix = exchange.getRequestURI().getPath().substring("/v1/tasks".length());
        if (suffix.isEmpty() || "/".equals(suffix)) {
            if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Use GET");
                return;
            }
            JsonArray result = new JsonArray();
            Collection<AgentTask> all = tasks.all();
            for (AgentTask task : all) result.add(GSON.toJsonTree(task));
            send(exchange, 200, result);
            return;
        }

        String[] parts = suffix.substring(1).split("/");
        String id = parts[0];
        AgentTask task = tasks.get(id);
        if (task == null) {
            sendError(exchange, 404, "TASK_NOT_FOUND", id);
            return;
        }
        if (parts.length == 1 && "GET".equalsIgnoreCase(exchange.getRequestMethod())) {
            send(exchange, 200, GSON.toJsonTree(task).getAsJsonObject());
            return;
        }
        if (parts.length == 2 && "POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            boolean changed;
            switch (parts[1]) {
                case "cancel": changed = tasks.cancel(id); break;
                case "pause": changed = tasks.pause(id); break;
                case "resume": changed = tasks.resume(id); break;
                default:
                    sendError(exchange, 404, "UNKNOWN_TASK_ACTION", parts[1]);
                    return;
            }
            JsonObject response = new JsonObject();
            response.addProperty("changed", changed);
            response.add("task", GSON.toJsonTree(tasks.get(id)));
            send(exchange, changed ? 200 : 409, response);
            return;
        }
        sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Unsupported task route");
    }

    private boolean authorize(HttpExchange exchange) throws IOException {
        if (token.isEmpty()) return true;
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        if (("Bearer " + token).equals(authorization)) return true;
        sendError(exchange, 401, "UNAUTHORIZED", "Missing or invalid bearer token");
        return false;
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream input = exchange.getRequestBody(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int read;
            while ((read = input.read(buffer)) >= 0) output.write(buffer, 0, read);
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    private static void sendError(HttpExchange exchange, int status, String code, String message) throws IOException {
        JsonObject error = new JsonObject();
        error.addProperty("error", code);
        error.addProperty("message", message == null ? "" : message);
        send(exchange, status, error);
    }

    private static void send(HttpExchange exchange, int status, JsonObject body) throws IOException {
        sendJson(exchange, status, GSON.toJson(body));
    }

    private static void send(HttpExchange exchange, int status, JsonArray body) throws IOException {
        sendJson(exchange, status, GSON.toJson(body));
    }

    private static void sendJson(HttpExchange exchange, int status, String json) throws IOException {
        byte[] payload = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, payload.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(payload);
        }
    }

    private static int parsePort(String value, int fallback) {
        try {
            int parsed = Integer.parseInt(value);
            return parsed >= 1024 && parsed <= 65535 ? parsed : fallback;
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private static String valueOrDefault(String value, String fallback) {
        return value == null ? fallback : value.trim();
    }
}
