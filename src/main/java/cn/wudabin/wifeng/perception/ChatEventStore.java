package cn.wudabin.wifeng.perception;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.network.MessageType;
import net.minecraft.network.packet.s2c.play.GameMessageS2CPacket;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class ChatEventStore {
    private static final int MAX_EVENTS = 100;
    private static final Pattern PUBLIC_CHAT = Pattern.compile("^<([^>]+)>\\s*(.*)$", Pattern.DOTALL);
    private final Deque<JsonObject> events = new ArrayDeque<>();

    public synchronized void record(GameMessageS2CPacket packet) {
        String text = packet.getMessage().getString();
        JsonObject event = new JsonObject();
        event.addProperty("captured_at", Instant.now().toString());
        event.addProperty("text", text);
        MessageType location = packet.getLocation();
        event.addProperty("kind", location == null ? "unknown" : location.name().toLowerCase(java.util.Locale.ROOT));
        UUID sender = packet.getSender();
        if (sender != null) event.addProperty("sender_uuid", sender.toString());

        Matcher matcher = PUBLIC_CHAT.matcher(text);
        if (matcher.matches()) {
            event.addProperty("player", matcher.group(1));
            event.addProperty("message", matcher.group(2));
        }

        events.addLast(event);
        while (events.size() > MAX_EVENTS) events.removeFirst();
    }

    public synchronized JsonArray snapshot() {
        JsonArray result = new JsonArray();
        for (JsonObject event : events) {
            result.add(new com.google.gson.JsonParser().parse(event.toString()));
        }
        return result;
    }
}
