package cn.wudabin.wifeng.task;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import cn.wudabin.wifeng.WifeNgClient;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.reflect.Type;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;

public final class TaskJournal {
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().setPrettyPrinting().create();
    private static final Type TASK_LIST = new TypeToken<List<AgentTask>>() { }.getType();

    private final Path directory;
    private final Path snapshotFile;
    private final Path eventFile;

    public TaskJournal(Path directory) {
        this.directory = directory;
        this.snapshotFile = directory.resolve("tasks.json");
        this.eventFile = directory.resolve("task-events.jsonl");
    }

    public synchronized List<AgentTask> load() {
        if (!Files.exists(snapshotFile)) {
            return Collections.emptyList();
        }
        try (BufferedReader reader = Files.newBufferedReader(snapshotFile, StandardCharsets.UTF_8)) {
            List<AgentTask> tasks = GSON.fromJson(reader, TASK_LIST);
            return tasks == null ? Collections.emptyList() : tasks;
        } catch (Exception error) {
            WifeNgClient.LOGGER.error("Cannot load persisted task state", error);
            return Collections.emptyList();
        }
    }

    public synchronized void record(AgentTask task, Collection<AgentTask> allTasks) {
        try {
            Files.createDirectories(directory);
            appendEvent(task);
            writeSnapshot(allTasks);
        } catch (IOException error) {
            WifeNgClient.LOGGER.error("Cannot persist task {}", task.id, error);
        }
    }

    private void appendEvent(AgentTask task) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(eventFile, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
            com.google.gson.JsonObject event = new com.google.gson.JsonObject();
            event.addProperty("recorded_at", Instant.now().toString());
            com.google.gson.JsonObject taskEvent = GSON.toJsonTree(task).getAsJsonObject();
            // Immutable blueprint cuboids and exact survey rows remain in the
            // snapshot/artifact cache; do not duplicate them in every step event.
            if ("build_blueprint".equals(task.tool) || "verify_blueprint".equals(task.tool)) {
                taskEvent.remove("arguments");
                taskEvent.addProperty("arguments_in", "tasks.json");
            } else if ("survey_region".equals(task.tool) && taskEvent.has("result")) {
                taskEvent.getAsJsonObject("result").remove("rows");
                taskEvent.addProperty("exact_rows_in", "tasks.json");
            }
            event.add("task", taskEvent);
            writer.write(new GsonBuilder().disableHtmlEscaping().create().toJson(event));
            writer.newLine();
        }
    }

    private void writeSnapshot(Collection<AgentTask> allTasks) throws IOException {
        Path temporary = directory.resolve("tasks.json.tmp");
        List<AgentTask> ordered = new ArrayList<>(allTasks);
        try (BufferedWriter writer = Files.newBufferedWriter(temporary, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING)) {
            GSON.toJson(ordered, TASK_LIST, writer);
        }
        try {
            Files.move(temporary, snapshotFile, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException unsupportedAtomicMove) {
            Files.move(temporary, snapshotFile, StandardCopyOption.REPLACE_EXISTING);
        }
    }
}
