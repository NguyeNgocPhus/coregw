package vn.coregw;

import com.sun.net.httpserver.HttpServer;
import io.github.resilience4j.bulkhead.Bulkhead;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.server.context.WebServerApplicationContext;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import static org.assertj.core.api.Assertions.*;

class GatewayIntegrationTest {
    @ParameterizedTest
    @ValueSource(strings = {"platform,resilient", "virtual,resilient", "platform,baseline", "virtual,baseline"})
    void forwardsAndProtectsCore(String profile) throws Exception {
        var calls = new AtomicInteger();
        var status = new AtomicInteger(201);
        var delay = new AtomicInteger();
        var disconnect = new AtomicBoolean();
        var receivedBody = new AtomicReference<String>();
        var receivedId = new AtomicReference<String>();
        var receivedType = new AtomicReference<String>();
        var gate = new AtomicReference<CountDownLatch>();
        var entered = new AtomicReference<CountDownLatch>();
        var executor = Executors.newVirtualThreadPerTaskExecutor();
        var core = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 128);
        core.setExecutor(executor);
        core.createContext("/orders", exchange -> {
            try (exchange) {
                calls.incrementAndGet();
                receivedBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                receivedId.set(exchange.getRequestHeaders().getFirst("X-Request-ID"));
                receivedType.set(exchange.getRequestHeaders().getFirst("Content-Type"));
                int replyStatus = status.get();
                var signal = entered.get(); if (signal != null) signal.countDown();
                var latch = gate.get(); if (latch != null) latch.await(2, TimeUnit.SECONDS);
                Thread.sleep(delay.get());
                if (disconnect.get()) return;
                var reply = (replyStatus == 201 ? "{\"orderId\":\"order-1\"}" : "{\"error\":\"core\"}")
                        .getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json; charset=UTF-8");
                exchange.sendResponseHeaders(replyStatus, reply.length);
                exchange.getResponseBody().write(reply);
            } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            catch (java.io.IOException ignored) { }
        });
        core.start();
        try (var app = new SpringApplicationBuilder(GatewayApplication.class).run(
                "--spring.profiles.active=" + profile, "--server.port=0", "--spring.main.banner-mode=off", "--app.redis.enabled=false",
                "--gateway.core-url=http://127.0.0.1:" + core.getAddress().getPort(),
                "--gateway.max-concurrent-calls=1", "--gateway.window-size=2", "--gateway.minimum-calls=2",
                "--gateway.half-open-calls=1", "--gateway.open-wait=200ms", "--gateway.response-timeout=500ms",
                "--gateway.expose-thread-model=true", "--logging.level.root=WARN")) {
            int port = ((WebServerApplicationContext) app).getWebServer().getPort();
            var client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();
            var uri = URI.create("http://localhost:" + port + "/api/orders");
            var request = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(5))
                    .header("Content-Type", "application/json; charset=UTF-8").header("X-Request-ID", "request-test")
                    .POST(HttpRequest.BodyPublishers.ofString("{\"customerId\":\"c1\",\"amount\":100}" )).build();
            var breaker = app.getBean(CircuitBreaker.class);
            var bulkhead = app.getBean(Bulkhead.class);
            boolean protectedMode = profile.contains("resilient");
            var response = client.send(request, HttpResponse.BodyHandlers.ofString());
            assertThat(response.statusCode()).isEqualTo(201);
            assertThat(response.body()).isEqualTo("{\"orderId\":\"order-1\"}");
            assertThat(receivedBody.get()).isEqualTo("{\"customerId\":\"c1\",\"amount\":100}");
            assertThat(receivedId.get()).isEqualTo("request-test");
            assertThat(receivedType.get()).isEqualTo("application/json; charset=UTF-8");
            assertThat(response.headers().firstValue("X-Request-ID")).contains("request-test");
            assertThat(org.springframework.http.MediaType.parseMediaType(response.headers().firstValue("Content-Type").orElseThrow()))
                    .isEqualTo(org.springframework.http.MediaType.parseMediaType("application/json; charset=UTF-8"));
            assertThat(response.headers().firstValue("X-Gateway-Thread-Virtual"))
                    .contains(Boolean.toString(profile.contains("virtual")));
            assertThat(calls.get()).isEqualTo(1);

            breaker.reset(); status.set(400);
            for (int i = 0; i < 2; i++) assertThat(client.send(request, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(400);
            assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
            assertThat(breaker.getMetrics().getNumberOfFailedCalls()).isZero();

            breaker.reset(); status.set(500);
            for (int i = 0; i < 2; i++) {
                var error = client.send(request, HttpResponse.BodyHandlers.ofString());
                assertThat(error.statusCode()).isEqualTo(500);
                assertThat(error.body()).isEqualTo("{\"error\":\"core\"}");
            }
            if (protectedMode) {
                assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.OPEN);
                int before = calls.get();
                var rejected = client.send(request, HttpResponse.BodyHandlers.ofString());
                assertThat(rejected.statusCode()).isEqualTo(503);
                assertThat(rejected.body()).contains("CORE_CIRCUIT_OPEN");
                assertThat(calls.get()).isEqualTo(before);
                status.set(201); Thread.sleep(250);
                assertThat(client.send(request, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(201);
                assertThat(breaker.getState()).isEqualTo(CircuitBreaker.State.CLOSED);
            } else assertThat(breaker.getMetrics().getNumberOfBufferedCalls()).isZero();

            breaker.reset(); status.set(201);
            var occupied = new CountDownLatch(1); var release = new CountDownLatch(1);
            entered.set(occupied); gate.set(release);
            var first = client.sendAsync(request, HttpResponse.BodyHandlers.ofString());
            assertThat(occupied.await(3, TimeUnit.SECONDS)).isTrue();
            if (protectedMode) {
                int before = calls.get();
                var rejected = client.send(request, HttpResponse.BodyHandlers.ofString());
                assertThat(rejected.statusCode()).isEqualTo(503);
                assertThat(rejected.body()).contains("CORE_BULKHEAD_FULL");
                assertThat(calls.get()).isEqualTo(before);
                assertThat(breaker.getMetrics().getNumberOfFailedCalls()).isZero();
            }
            release.countDown();
            assertThat(first.get(3, TimeUnit.SECONDS).statusCode()).isEqualTo(201);
            entered.set(null); gate.set(null);
            assertThat(bulkhead.getMetrics().getAvailableConcurrentCalls()).isEqualTo(1);

            breaker.reset(); delay.set(900);
            int beforeTimeout = calls.get();
            var timeout = client.send(request, HttpResponse.BodyHandlers.ofString());
            assertThat(timeout.statusCode()).isEqualTo(504);
            assertThat(timeout.body()).contains("CORE_TIMEOUT");
            assertThat(calls.get()).isEqualTo(beforeTimeout + 1);
            assertThat(bulkhead.getMetrics().getAvailableConcurrentCalls()).isEqualTo(1);
            delay.set(0); breaker.reset(); disconnect.set(true);
            int beforeDisconnect = calls.get();
            var broken = client.send(request, HttpResponse.BodyHandlers.ofString());
            assertThat(broken.statusCode()).isEqualTo(502);
            assertThat(calls.get()).isEqualTo(beforeDisconnect + 1);
            assertThat(bulkhead.getMetrics().getAvailableConcurrentCalls()).isEqualTo(1);
            disconnect.set(false); breaker.reset();
            assertThat(client.send(request, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(201);
            core.stop(0); breaker.reset();
            assertThat(client.send(request, HttpResponse.BodyHandlers.ofString()).statusCode()).isEqualTo(502);
            assertThat(bulkhead.getMetrics().getAvailableConcurrentCalls()).isEqualTo(1);
        } finally { core.stop(0); executor.shutdownNow(); }
    }
}
