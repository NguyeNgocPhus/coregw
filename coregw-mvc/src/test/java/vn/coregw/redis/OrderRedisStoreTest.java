package vn.coregw.redis;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import java.time.Duration;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.mockito.Mockito.*;


@SuppressWarnings("unchecked")
class OrderRedisStoreTest {
    @Test
    void redisFailureDoesNotEscapeToTheCoreFlow() {
        RedisTemplate<String, byte[]> template = mock(RedisTemplate.class);
        ValueOperations<String, byte[]> values = mock(ValueOperations.class);
        ObjectProvider<RedisTemplate<String, byte[]>> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(template);
        when(template.opsForValue()).thenReturn(values);
        byte[] body = new byte[]{1, 2, 3};
        doThrow(new DataAccessResourceFailureException("Redis unavailable"))
                .when(values).set("test:request", body, Duration.ofHours(1));
        var store = new OrderRedisStore(provider, Duration.ofHours(1));
        assertDoesNotThrow(() -> store.save("test:request", new byte[]{1, 2, 3}));
        verify(values).set("test:request", body, Duration.ofHours(1));
    }

    @Test
    void disabledRedisDoesNotRequireATemplate() {
        ObjectProvider<RedisTemplate<String, byte[]>> provider = mock(ObjectProvider.class);
        var store = new OrderRedisStore(provider, Duration.ofHours(1));
        assertDoesNotThrow(() -> store.save("test:request", new byte[]{1, 2, 3}));
    }
}
