package vn.coregw.redis;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.data.redis.core.ReactiveRedisTemplate;
import java.time.Duration;
import java.util.UUID;
import reactor.core.publisher.Mono;

@Component
public class OrderRedisStore {
    private static final Logger log = LoggerFactory.getLogger(OrderRedisStore.class);
    private final ReactiveRedisTemplate<String, byte[]> template;
    private final Duration ttl;

    public OrderRedisStore(ObjectProvider<ReactiveRedisTemplate<String, byte[]>> templates,
            @Value("${gateway.order-redis.ttl:1h}") Duration ttl) {
        this.template = templates.getIfAvailable();
        if (ttl.isZero() || ttl.isNegative()) throw new IllegalArgumentException("Order Redis TTL must be positive");
        this.ttl = ttl;
    }

    // A fresh suffix prevents repeated client request IDs from overwriting earlier calls.
    public String key(String requestId) {
        return "coregw:orders:" + requestId + ":" + UUID.randomUUID();
    }

    public Mono<Void> save(String key, byte[] body) {
        if (template == null) return Mono.empty();
        return Mono.defer(() -> template.opsForValue().set(key, body == null ? new byte[0] : body, ttl))
                .onErrorResume(error -> {
                    log.warn("Order Redis write failed key={} exception={}", key, error.getClass().getSimpleName());
                    return Mono.empty();
                }).then();
    }
}
