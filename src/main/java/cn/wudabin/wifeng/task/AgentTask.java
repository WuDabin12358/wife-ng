package cn.wudabin.wifeng.task;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.time.Instant;
import java.util.UUID;

public final class AgentTask {
    public final String id;
    public final String tool;
    public final JsonObject arguments;
    public final String createdAt;
    public String updatedAt;
    public TaskState state;
    public String progress;
    public String failureCode;
    public String failureMessage;
    public boolean retryable;
    public int attempts;
    public long retryAtTick;
    public Integer baselineItemCount;
    public Integer baselineInventoryCount;
    /** Persisted high-level task output and resumable step counters. */
    public JsonObject result;
    public Integer stepIndex;
    public Integer totalSteps;

    public AgentTask(String tool, JsonObject arguments) {
        this.id = UUID.randomUUID().toString();
        this.tool = tool;
        this.arguments = arguments == null ? new JsonObject()
                : new JsonParser().parse(arguments.toString()).getAsJsonObject();
        this.createdAt = Instant.now().toString();
        this.updatedAt = createdAt;
        this.state = TaskState.QUEUED;
        this.progress = "waiting";
        this.failureCode = "";
        this.failureMessage = "";
        this.retryable = false;
        this.attempts = 0;
        this.retryAtTick = 0;
        this.baselineItemCount = null;
        this.baselineInventoryCount = null;
        this.result = new JsonObject();
        this.stepIndex = 0;
        this.totalSteps = 0;
    }

    public void transition(TaskState next, String detail) {
        state = next;
        progress = detail == null ? "" : detail;
        updatedAt = Instant.now().toString();
    }

    public boolean terminal() {
        return state == TaskState.SUCCEEDED || state == TaskState.FAILED || state == TaskState.CANCELLED;
    }
}
