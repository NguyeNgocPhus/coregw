import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Standalone Java 21+ core simulator; no Spring, database, or Maven dependency. */
public class MockCore {
    record Behavior(int delayMs, int status, boolean disconnect) {
        Behavior {
            if (delayMs < 0 || status < 200 || status > 599 || status == 204 || status == 304)
                throw new IllegalArgumentException("Invalid mock delay/status (204/304 have no body)");
        }
    }
    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(env("MOCK_PORT", "9090"));
        var behavior = new AtomicReference<>(new Behavior(Integer.parseInt(env("MOCK_DELAY_MS", "200")),
                Integer.parseInt(env("MOCK_STATUS", "201")), Boolean.parseBoolean(env("MOCK_DISCONNECT", "false"))));
        String control = env("MOCK_CONTROL_FILE", "");
        String stats = env("MOCK_STATS_FILE", "");
        var calls = new AtomicLong();
        var active = new AtomicInteger();
        var peak = new AtomicInteger();
        var workers = Executors.newVirtualThreadPerTaskExecutor();
        var monitor = Executors.newSingleThreadScheduledExecutor();
        var server = HttpServer.create(new InetSocketAddress(port), 2048);
        server.setExecutor(workers);
        server.createContext("/orders", exchange -> {
            try (exchange) {
                if (!exchange.getRequestURI().getPath().equals("/orders")) { exchange.sendResponseHeaders(404, -1); return; }
                if (!exchange.getRequestMethod().equals("POST")) { exchange.sendResponseHeaders(405, -1); return; }
                calls.incrementAndGet();
                peak.accumulateAndGet(active.incrementAndGet(), Math::max);
                try {
                    byte[] input = exchange.getRequestBody().readNBytes(1024 * 1024 + 1);
                    if (input.length > 1024 * 1024) { exchange.sendResponseHeaders(413, -1); return; }
                    var b = behavior.get();
                    if (b.delayMs() > 0) Thread.sleep(b.delayMs());
                    if (b.disconnect()) return;
                    String id = exchange.getRequestHeaders().getFirst("X-Request-ID");
                    if (id != null) exchange.getResponseHeaders().set("X-Request-ID", id);
                    exchange.getResponseHeaders().set("Content-Type", "application/json");
                    byte[] reply = (b.status() == 201
                            ? "{\"orderId\":\"" + UUID.randomUUID() + "\",\"status\":\"CREATED\"}"
                            : "{\"code\":\"MOCK_CORE_ERROR\"}").getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(b.status(), reply.length);
                    exchange.getResponseBody().write(reply);
                } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
                catch (java.io.IOException ignored) { /* Client may time out or disconnect. */ }
                finally { active.decrementAndGet(); }
            }
        });
        Runnable writeStats = () -> {
            if (!stats.isBlank()) try {
                var destination = Path.of(stats);
                var temporary = Path.of(stats + ".tmp");
                Files.writeString(temporary, "{\"calls\":" + calls.get() + ",\"active\":" + active.get()
                        + ",\"peakActive\":" + peak.get() + "}");
                Files.move(temporary, destination, StandardCopyOption.REPLACE_EXISTING);
            } catch (Exception e) { System.err.println("Stats: " + e.getMessage()); }
        };
        monitor.scheduleWithFixedDelay(() -> {
            if (!control.isBlank() && Files.exists(Path.of(control))) try {
                var p = new Properties();
                try (var in = Files.newInputStream(Path.of(control))) { p.load(in); }
                var current = behavior.get();
                behavior.set(new Behavior(Integer.parseInt(p.getProperty("delayMs", "" + current.delayMs())),
                        Integer.parseInt(p.getProperty("status", "" + current.status())),
                        Boolean.parseBoolean(p.getProperty("disconnect", "" + current.disconnect()))));
            } catch (Exception e) { System.err.println("Control: " + e.getMessage()); }
        }, 0, 100, TimeUnit.MILLISECONDS);
        monitor.scheduleWithFixedDelay(writeStats, 0, 1, TimeUnit.SECONDS);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop(0); monitor.shutdownNow(); workers.shutdownNow(); writeStats.run();
        }));
        server.start();
        System.out.println("Mock core: POST http://localhost:" + port + "/orders; " + behavior.get());
        new CountDownLatch(1).await();
    }
    static String env(String name, String fallback) { return System.getenv().getOrDefault(name, fallback); }
}
