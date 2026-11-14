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
import org.springframework.data.redis.connection.lettuce.LettucePoolingClientConfiguration;
import org.apache.commons.pool2.impl.GenericObjectPoolConfig;
import org.springframework.data.redis.serializer.RedisSerializer;
import org.springframework.data.redis.core.RedisTemplate;

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
        String mode = env.getProperty("app.redis.mode", "pool");
        LettuceClientConfiguration.LettuceClientConfigurationBuilder builder;
        if (mode.equals("pool")) {
            var pool = new GenericObjectPoolConfig<io.lettuce.core.api.StatefulConnection<?,?>>();
            pool.setMaxTotal(env.getProperty("app.redis.pool.max-active", Integer.class, 8));
            pool.setMaxIdle(env.getProperty("app.redis.pool.max-idle", Integer.class, 8));
            pool.setMinIdle(env.getProperty("app.redis.pool.min-idle", Integer.class, 0));
            pool.setMaxWait(DurationStyle.detectAndParse(
                    env.getProperty("app.redis.pool.max-wait", "1s")));
            builder = LettucePoolingClientConfiguration.builder().poolConfig(pool);
        } else if (mode.equals("multiplexing")) {
            builder = LettuceClientConfiguration.builder();
        } else {
            throw new IllegalArgumentException("app.redis.mode must be pool or multiplexing");
        }
        var config = builder
                .commandTimeout(DurationStyle.detectAndParse(
                        env.getProperty("app.redis.command-timeout", "1s")))
                .clientOptions(ClientOptions.builder().socketOptions(SocketOptions.builder()
                        .connectTimeout(DurationStyle.detectAndParse(
                                env.getProperty("app.redis.connect-timeout", "1s"))).build()).build());
        if (env.getProperty("app.redis.ssl", Boolean.class, false)) config.useSsl();
        var factory = new LettuceConnectionFactory(server, config.build());
        factory.setShareNativeConnection(!mode.equals("pool"));
        return factory;
    }
    @Bean
    RedisTemplate<String, byte[]> orderRedisTemplate(
            LettuceConnectionFactory factory) {
        var template = new RedisTemplate<String, byte[]>();
        template.setConnectionFactory(factory);
        template.setKeySerializer(RedisSerializer.string());
        template.setValueSerializer(RedisSerializer.byteArray());
        return template;
    }
}
