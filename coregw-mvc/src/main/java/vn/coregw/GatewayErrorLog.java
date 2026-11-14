package vn.coregw;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/** One sample per error category per five seconds, shared across request threads. */
final class GatewayErrorLog {
    private static final Logger log = LoggerFactory.getLogger(GatewayErrorLog.class);
    private static final ConcurrentHashMap<String, Window> windows = new ConcurrentHashMap<>();
    private static final long INTERVAL = 5_000_000_000L;

    private static final class Window {
        final AtomicLong next = new AtomicLong(System.nanoTime());
        final AtomicLong suppressed = new AtomicLong();
    }

    private static long sample(String category) {
        var window = windows.computeIfAbsent(category, key -> new Window());
        long now = System.nanoTime();
        long next = window.next.get();
        if (now - next >= 0 && window.next.compareAndSet(next, now + INTERVAL))
            return window.suppressed.getAndSet(0);
        window.suppressed.incrementAndGet();
        return -1;
    }

    static void failure(ResponseEntity<byte[]> reply, Throwable error, String id, String endpoint) {
        String body = new String(reply.getBody(), StandardCharsets.UTF_8);
        long suppressed = sample("gateway-" + reply.getStatusCode().value() + "-" + body);
        if (suppressed < 0) return;
        Throwable root = error;
        while (root.getCause() != null && root.getCause() != root) root = root.getCause();
        log.warn("Order failed requestId={} core={} status={} response={} exception={} cause={} message={} suppressedSinceLastLog={}",
                id, endpoint, reply.getStatusCode().value(), body, error.getClass().getSimpleName(),
                root.getClass().getSimpleName(), root.getMessage(), suppressed);
        log.debug("Order failure stack requestId={}", id, error);
    }

    static void upstream(int status, String id, String endpoint) {
        long suppressed = sample("upstream-" + status);
        if (suppressed >= 0)
            log.warn("Core returned error requestId={} core={} status={} suppressedSinceLastLog={}",
                    id, endpoint, status, suppressed);
    }
}
