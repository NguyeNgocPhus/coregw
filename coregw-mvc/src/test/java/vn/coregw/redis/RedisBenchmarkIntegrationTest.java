package vn.coregw.redis;

import io.lettuce.core.RedisURI;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.concurrent.Executors;
import java.util.stream.IntStream;

import static org.junit.jupiter.api.Assertions.*;

@EnabledIfEnvironmentVariable(named = "REDIS_BENCH_TEST_URI", matches = ".+")
class RedisBenchmarkIntegrationTest {
    @Test
    void templateWorksConcurrentlyWithBothConnectionModes() {
        var uri = RedisURI.create(System.getenv("REDIS_BENCH_TEST_URI"));
        for (String mode : new String[]{"multiplexing", "pool"}) {
            new ApplicationContextRunner().withUserConfiguration(RedisConfiguration.class, RedisBenchmarkService.class, RedisMetricsConfiguration.class)
                    .withBean(io.micrometer.core.instrument.MeterRegistry.class, io.micrometer.core.instrument.simple.SimpleMeterRegistry::new)
                    .withInitializer(context -> context.getEnvironment().setActiveProfiles("redis-bench"))
                    .withPropertyValues("app.redis.enabled=true", "app.redis.mode=" + mode,
                            "app.redis.host=" + uri.getHost(), "app.redis.port=" + uri.getPort(),
                            "app.redis.pool.max-active=2", "app.redis.pool.max-idle=2",
                            "redis-bench.keys=16", "redis-bench.payload-bytes=128")
                    .run(context -> {
                        assertNull(context.getStartupFailure());
                        var factory = context.getBean(LettuceConnectionFactory.class);
                        var template = context.getBean(StringRedisTemplate.class);
                        assertEquals(mode.equals("multiplexing"), factory.getShareNativeConnection());
                        // Two simultaneous Spring connections share the native connection only in multiplexing mode.
                        try (var first = factory.getConnection(); var second = factory.getConnection()) {
                            first.ping(); second.ping();
                            if (mode.equals("multiplexing")) assertSame(first.getNativeConnection(), second.getNativeConnection());
                            else assertNotSame(first.getNativeConnection(), second.getNativeConnection());
                        }
                        var service = context.getBean(RedisBenchmarkService.class);
                        assertTrue(service.execute(0).valid(), "startup must seed GET keys");
                        template.delete("coregw:bench:read:0");
                        assertEquals("GET_MISSING", service.execute(0).error());
                        new RedisBenchmarkController(service).prepare();
                        assertTrue(service.execute(0).valid());
                        template.opsForValue().set("coregw:bench:read:0", "wrong");
                        assertEquals("GET_VALUE_MISMATCH", service.execute(0).error());
                        service.prepareData();
                        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
                            var futures = executor.invokeAll(IntStream.range(0, 64)
                                    .<java.util.concurrent.Callable<Boolean>>mapToObj(i -> () -> service.execute(i % 16).valid())
                                    .toList());
                            for (var future : futures) assertTrue(future.get());
                        }
                        assertEquals("x".repeat(128), template.opsForValue().get("coregw:bench:string:0"));
                        assertEquals("x".repeat(128), template.opsForHash().get("coregw:bench:hash:0", "value"));
                        var registry = context.getBean(io.micrometer.core.instrument.MeterRegistry.class);
                        assertEquals(1, registry.get("redis.mode.info").gauge().value());
                        assertTrue(registry.find("lettuce.command.completion").timers().stream()
                                .anyMatch(timer -> timer.count() > 0));
                        if (mode.equals("pool")) {
                            assertEquals(0, registry.get("redis.pool.active").gauge().value());
                            assertTrue(registry.get("redis.pool.borrowed").gauge().value() > 0);
                            assertTrue(registry.get("redis.pool.borrow.wait.mean.ms").gauge().value() >= 0);
                        } else assertNull(registry.find("redis.pool.active").gauge());
                        assertThrows(IllegalArgumentException.class, () -> service.execute(-1));
                        assertThrows(IllegalArgumentException.class, () -> service.execute(16));
                    });
        }
    }
}
