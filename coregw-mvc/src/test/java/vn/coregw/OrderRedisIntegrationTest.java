package vn.coregw;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.server.context.WebServerApplicationContext;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.serializer.RedisSerializer;
import java.net.*;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import static org.assertj.core.api.Assertions.*;

@EnabledIfEnvironmentVariable(named = "ORDER_REDIS_TEST", matches = "true")
class OrderRedisIntegrationTest {
    @Test
    void storesExactBodiesWithTtlAndUniqueKeysIncludingCoreErrors() throws Exception {
        String id = "redis-test-" + UUID.randomUUID();
        String pattern = "coregw:orders:" + id + ":*";
        byte[] body = "{\"customer\":\"Nguyễn\",\"amount\":100}".getBytes(StandardCharsets.UTF_8);
        byte[] reply = "{\"orderId\":\"order-1\"}".getBytes(StandardCharsets.UTF_8);
        var calls = new AtomicInteger();
        var status = new AtomicInteger(201);
        var core = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 16);
        core.createContext("/orders", exchange -> {
            try (exchange) {
                calls.incrementAndGet();
                exchange.getRequestBody().readAllBytes();
                exchange.getResponseHeaders().set("Content-Type", "application/json");
                exchange.sendResponseHeaders(status.get(), reply.length);
                exchange.getResponseBody().write(reply);
            }
        });
        core.start();
        try (var app = new SpringApplicationBuilder(GatewayApplication.class).run(
                "--spring.profiles.active=baseline", "--server.port=0", "--spring.main.banner-mode=off",
                "--app.redis.enabled=true", "--app.redis.host=127.0.0.1", "--app.redis.port=6379",
                "--gateway.order-redis.ttl=60s", "--logging.level.root=WARN",
                "--gateway.core-url=http://127.0.0.1:" + core.getAddress().getPort())) {
            var redis = new RedisTemplate<String, byte[]>();
            redis.setConnectionFactory(app.getBean(LettuceConnectionFactory.class));
            redis.setKeySerializer(RedisSerializer.string());
            redis.setValueSerializer(RedisSerializer.byteArray());
            redis.afterPropertiesSet();
            try {
                int port = ((WebServerApplicationContext) app).getWebServer().getPort();
                var client = HttpClient.newHttpClient();
                var request = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/api/orders"))
                        .timeout(Duration.ofSeconds(10)).header("Content-Type", "application/json")
                        .header("X-Request-ID", id).POST(HttpRequest.BodyPublishers.ofByteArray(body)).build();
                for (int code : new int[]{201, 400, 500}) {
                    status.set(code);
                    var response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
                    assertThat(response.statusCode()).isEqualTo(code);
                    assertThat(response.body()).isEqualTo(reply);
                }
                assertThat(calls.get()).isEqualTo(3);
                var keys = redis.keys(pattern);
                assertThat(keys).hasSize(6);
                for (String key : keys) {
                    assertThat(redis.opsForValue().get(key)).isEqualTo(key.endsWith(":request") ? body : reply);
                    assertThat(redis.getExpire(key)).isBetween(1L, 60L);
                }
                core.stop(0);
                assertThat(client.send(request, HttpResponse.BodyHandlers.ofByteArray()).statusCode()).isEqualTo(502);
                // A connection failure has no core response to store.
                assertThat(redis.keys(pattern + ":request")).hasSize(4);
                assertThat(redis.keys(pattern + ":response")).hasSize(3);
            } finally {
                var keys = redis.keys(pattern);
                if (!keys.isEmpty()) redis.delete(keys);
            }
        } finally {
            core.stop(0);
        }
    }
}
