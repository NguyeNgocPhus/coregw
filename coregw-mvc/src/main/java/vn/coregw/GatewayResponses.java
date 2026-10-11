package vn.coregw;

import io.github.resilience4j.bulkhead.BulkheadFullException;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import org.springframework.http.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

public final class GatewayResponses {
    private GatewayResponses() {}
    public static String requestId(String value) {
        return value == null || value.isBlank() ? UUID.randomUUID().toString() : value;
    }
    public static HttpHeaders headers(HttpHeaders upstream, String requestId) {
        var headers = new HttpHeaders();
        String type = upstream.getFirst(HttpHeaders.CONTENT_TYPE);
        if (type != null) headers.set(HttpHeaders.CONTENT_TYPE, type);
        headers.set("X-Request-ID", requestId);
        return headers;
    }
    public static ResponseEntity<byte[]> error(Throwable error, String requestId) {
        int status = 502;
        String code = "CORE_CONNECTION_ERROR";
        for (Throwable t = error; t != null; t = t.getCause()) {
            if (t instanceof BulkheadFullException) { status = 503; code = "CORE_BULKHEAD_FULL"; break; }
            if (t instanceof CallNotPermittedException) { status = 503; code = "CORE_CIRCUIT_OPEN"; break; }
            // Covers JDK, Apache HttpClient and Netty timeout exceptions without stack-specific dependencies.
            if (t instanceof java.util.concurrent.TimeoutException || t.getClass().getSimpleName().contains("Timeout")) {
                status = 504; code = "CORE_TIMEOUT"; break;
            }
        }
        return ResponseEntity.status(status).contentType(MediaType.APPLICATION_JSON)
                .header("X-Request-ID", requestId)
                .body(("{\"code\":\"" + code + "\"}").getBytes(StandardCharsets.UTF_8));
    }
}
