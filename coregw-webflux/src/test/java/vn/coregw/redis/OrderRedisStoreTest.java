package vn.coregw.redis;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.data.redis.core.ReactiveRedisTemplate;
import org.springframework.data.redis.core.ReactiveValueOperations;
import java.time.Duration;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.mockito.Mockito.*;
import reactor.core.publisher.Mono;

@SuppressWarnings("unchecked")
class OrderRedisStoreTest {
    @Test
    void redisFailureDoesNotEscapeToTheCoreFlow() {
        ReactiveRedisTemplate<String, byte[]> template = mock(ReactiveRedisTemplate.class);
        ReactiveValueOperations<String, byte[]> values = mock(ReactiveValueOperations.class);
        ObjectProvider<ReactiveRedisTemplate<String, byte[]>> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(template);
        when(template.opsForValue()).thenReturn(values);
        byte[] body = new byte[]{1, 2, 3};
        when(values.set("test:request", body, Duration.ofHours(1)))
                .thenReturn(Mono.error(new DataAccessResourceFailureException("Redis unavailable")));
        var store = new OrderRedisStore(provider, Duration.ofHours(1));
        assertDoesNotThrow(() -> store.save("test:request", new byte[]{1, 2, 3}).block(Duration.ofSeconds(2)));
        verify(values).set("test:request", body, Duration.ofHours(1));
    }

    @Test
    void disabledRedisDoesNotRequireATemplate() {
        ObjectProvider<ReactiveRedisTemplate<String, byte[]>> provider = mock(ObjectProvider.class);
        var store = new OrderRedisStore(provider, Duration.ofHours(1));
        assertDoesNotThrow(() -> store.save("test:request", new byte[]{1, 2, 3}).block(Duration.ofSeconds(2)));
    }
}
