package vn.coregw;

import io.netty.channel.ChannelOption;
import org.springframework.context.annotation.*;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.http.HttpProtocol;
import reactor.netty.http.client.HttpClient;
import reactor.netty.resources.ConnectionProvider;

@Configuration(proxyBeanMethods = false)
public class HttpConfiguration {
    @Bean(destroyMethod = "dispose")
    ConnectionProvider coreConnections(GatewaySettings s) {
        return ConnectionProvider.builder("core").maxConnections(s.maxConnections())
                .pendingAcquireMaxCount(s.maxConnections()).pendingAcquireTimeout(s.acquireTimeout())
                .metrics(true).build();
    }
    @Bean
    WebClient coreClient(ConnectionProvider provider, GatewaySettings s) {
        var http = HttpClient.create(provider).protocol(HttpProtocol.HTTP11).keepAlive(true)
                .disableRetry(true).followRedirect(false)
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, Math.toIntExact(s.connectTimeout().toMillis()))
                .responseTimeout(s.responseTimeout());
        return WebClient.builder().clientConnector(new ReactorClientHttpConnector(http))
                .codecs(c -> c.defaultCodecs().maxInMemorySize(1024 * 1024)).build();
    }
}
