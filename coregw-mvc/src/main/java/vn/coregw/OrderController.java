package vn.coregw;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;
import java.util.function.Supplier;

@RestController
public class OrderController {
    private final RestClient client;
    private final GatewaySettings settings;
    private final CircuitBreaker breaker;
    private final Bulkhead bulkhead;
    public OrderController(RestClient client, GatewaySettings settings, CircuitBreaker breaker, Bulkhead bulkhead) {
        this.client = client; this.settings = settings; this.breaker = breaker; this.bulkhead = bulkhead;
    }
    @PostMapping(value = "/api/orders", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<byte[]> createOrder(@RequestBody byte[] body,
            @RequestHeader(value = "X-Request-ID", required = false) String suppliedId,
            @RequestHeader(HttpHeaders.CONTENT_TYPE) String contentType) {
        String id = GatewayResponses.requestId(suppliedId);
        boolean virtual = Thread.currentThread().isVirtual();
        Supplier<ResponseEntity<byte[]>> call = () -> client.post().uri(settings.coreEndpoint())
                .header(HttpHeaders.CONTENT_TYPE, contentType).header("X-Request-ID", id).body(body)
                .exchange((request, response) -> ResponseEntity.status(response.getStatusCode())
                        .headers(GatewayResponses.headers(response.getHeaders(), id))
                        .body(response.getBody().readAllBytes()));
        if (settings.resilienceEnabled())
            call = Bulkhead.decorateSupplier(bulkhead, CircuitBreaker.decorateSupplier(breaker, call));
        ResponseEntity<byte[]> reply;
        try { reply = call.get(); }
        catch (Exception error) { reply = GatewayResponses.error(error, id); }
        if (settings.exposeThreadModel()) return ResponseEntity.status(reply.getStatusCode())
                .headers(reply.getHeaders()).header("X-Gateway-Thread-Virtual", Boolean.toString(virtual)).body(reply.getBody());
        return reply;
    }
}
