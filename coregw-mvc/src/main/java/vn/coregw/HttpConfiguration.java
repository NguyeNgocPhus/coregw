package vn.coregw;

import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.core5.util.Timeout;
import org.springframework.context.annotation.*;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration(proxyBeanMethods = false)
public class HttpConfiguration {
    @Bean(destroyMethod = "close")
    CloseableHttpClient coreHttpClient(GatewaySettings s) {
        var pool = PoolingHttpClientConnectionManagerBuilder.create()
                .setMaxConnTotal(s.maxConnections()).setMaxConnPerRoute(s.maxConnections())
                .setDefaultConnectionConfig(ConnectionConfig.custom()
                        .setConnectTimeout(Timeout.ofMilliseconds(s.connectTimeout().toMillis()))
                        .setSocketTimeout(Timeout.ofMilliseconds(s.responseTimeout().toMillis())).build())
                .build();
        var request = RequestConfig.custom()
                .setConnectionRequestTimeout(Timeout.ofMilliseconds(s.acquireTimeout().toMillis()))
                .setResponseTimeout(Timeout.ofMilliseconds(s.responseTimeout().toMillis())).build();
        return HttpClients.custom().setConnectionManager(pool).setDefaultRequestConfig(request)
                .disableAutomaticRetries().disableRedirectHandling().disableCookieManagement().build();
    }
    @Bean
    RestClient coreClient(CloseableHttpClient client) {
        return RestClient.builder().requestFactory(new HttpComponentsClientHttpRequestFactory(client)).build();
    }
}
