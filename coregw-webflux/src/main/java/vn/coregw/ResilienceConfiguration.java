package vn.coregw;

import io.github.resilience4j.bulkhead.*;
import io.github.resilience4j.circuitbreaker.*;
import io.github.resilience4j.micrometer.tagged.*;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.ResponseEntity;
import java.time.Duration;

@Configuration(proxyBeanMethods = false)
public class ResilienceConfiguration {
    @Bean
    CircuitBreakerRegistry circuitBreakerRegistry(GatewaySettings s, MeterRegistry meters) {
        var config = CircuitBreakerConfig.custom().slidingWindowSize(s.windowSize())
                .minimumNumberOfCalls(s.minimumCalls()).failureRateThreshold(s.failureRate())
                .waitDurationInOpenState(s.openWait()).permittedNumberOfCallsInHalfOpenState(s.halfOpenCalls())
                .recordResult(result -> result instanceof ResponseEntity<?> r && r.getStatusCode().is5xxServerError())
                .build();
        var registry = CircuitBreakerRegistry.of(config);
        TaggedCircuitBreakerMetrics.ofCircuitBreakerRegistry(registry).bindTo(meters);
        return registry;
    }
    @Bean
    BulkheadRegistry bulkheadRegistry(GatewaySettings s, MeterRegistry meters) {
        var registry = BulkheadRegistry.of(BulkheadConfig.custom()
                .maxConcurrentCalls(s.maxConcurrentCalls()).maxWaitDuration(Duration.ZERO).build());
        TaggedBulkheadMetrics.ofBulkheadRegistry(registry).bindTo(meters);
        return registry;
    }
    @Bean CircuitBreaker coreCircuitBreaker(CircuitBreakerRegistry r) { return r.circuitBreaker("core"); }
    @Bean Bulkhead coreBulkhead(BulkheadRegistry r) { return r.bulkhead("core"); }
}
