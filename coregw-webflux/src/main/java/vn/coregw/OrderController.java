package vn.coregw;

import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.reactor.bulkhead.operator.BulkheadOperator;
import io.github.resilience4j.reactor.circuitbreaker.operator.CircuitBreakerOperator;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

@RestController
public class OrderController {
    private final WebClient client;
    private final GatewaySettings settings;
    private final CircuitBreaker breaker;
    private final Bulkhead bulkhead;
    public OrderController(WebClient client, GatewaySettings settings, CircuitBreaker breaker, Bulkhead bulkhead) {
        this.client = client; this.settings = settings; this.breaker = breaker; this.bulkhead = bulkhead;
    }
    @PostMapping(value = "/api/orders", consumes = MediaType.APPLICATION_JSON_VALUE)
    public Mono<ResponseEntity<byte[]>> createOrder(@RequestBody byte[] body,
            @RequestHeader(value = "X-Request-ID", required = false) String suppliedId,
            @RequestHeader(HttpHeaders.CONTENT_TYPE) String contentType) {
        String id = GatewayResponses.requestId(suppliedId);
        boolean virtual = Thread.currentThread().isVirtual();
        Mono<ResponseEntity<byte[]>> call = Mono.defer(() -> client.post().uri(settings.coreEndpoint())
                .header(HttpHeaders.CONTENT_TYPE, contentType).header("X-Request-ID", id).bodyValue(body)
                .exchangeToMono(response -> response.toEntity(byte[].class))
                .map(response -> ResponseEntity.status(response.getStatusCode())
                        .headers(GatewayResponses.headers(response.getHeaders(), id)).body(response.getBody())));
        if (settings.resilienceEnabled()) call = call
                .transformDeferred(CircuitBreakerOperator.of(breaker))
                .transformDeferred(BulkheadOperator.of(bulkhead));
        return call.onErrorResume(error -> Mono.just(GatewayResponses.error(error, id)))
                .map(reply -> settings.exposeThreadModel() ? ResponseEntity.status(reply.getStatusCode())
                        .headers(reply.getHeaders()).header("X-Gateway-Thread-Virtual", Boolean.toString(virtual))
                        .body(reply.getBody()) : reply);
    }
}
