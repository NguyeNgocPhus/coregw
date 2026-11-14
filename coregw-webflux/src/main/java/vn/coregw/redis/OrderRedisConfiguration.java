package vn.coregw.redis;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.SocketOptions;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.convert.DurationStyle;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.serializer.RedisSerializationContext;
import org.springframework.data.redis.serializer.RedisSerializer;
import org.springframework.data.redis.core.ReactiveRedisTemplate;

@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(name = "app.redis.enabled", havingValue = "true", matchIfMissing = true)
public class OrderRedisConfiguration {
    @Bean
    LettuceConnectionFactory orderRedisConnectionFactory(Environment env) {
        var server = new RedisStandaloneConfiguration(
                env.getProperty("app.redis.host", "localhost"),
                env.getProperty("app.redis.port", Integer.class, 6379));
        server.setDatabase(env.getProperty("app.redis.database", Integer.class, 0));
        String username = env.getProperty("app.redis.username", "");
        String password = env.getProperty("app.redis.password", "");
        if (!username.isBlank()) server.setUsername(username);
        if (!password.isEmpty()) server.setPassword(password);
        var config = LettuceClientConfiguration.builder()
                .commandTimeout(DurationStyle.detectAndParse(
                        env.getProperty("app.redis.command-timeout", "1s")))
                .clientOptions(ClientOptions.builder().socketOptions(SocketOptions.builder()
                        .connectTimeout(DurationStyle.detectAndParse(
                                env.getProperty("app.redis.connect-timeout", "1s"))).build()).build());
        if (env.getProperty("app.redis.ssl", Boolean.class, false)) config.useSsl();
        return new LettuceConnectionFactory(server, config.build());
    }
    @Bean
    ReactiveRedisTemplate<String, byte[]> orderRedisTemplate(
            LettuceConnectionFactory factory) {
        var context = RedisSerializationContext.<String, byte[]>newSerializationContext(RedisSerializer.string())
                .value(RedisSerializer.byteArray()).build();
        return new ReactiveRedisTemplate<>(factory, context);
    }
}
