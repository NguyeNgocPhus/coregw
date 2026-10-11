package vn.coregw;

import java.net.URI;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("gateway")
public record GatewaySettings(URI coreUrl, String corePath, int maxConnections,
        Duration acquireTimeout, Duration connectTimeout, Duration responseTimeout,
        boolean resilienceEnabled, int maxConcurrentCalls, int windowSize, int minimumCalls,
        float failureRate, Duration openWait, int halfOpenCalls, boolean exposeThreadModel) {
    public GatewaySettings {
        if (coreUrl == null || !("http".equals(coreUrl.getScheme()) || "https".equals(coreUrl.getScheme()))
                || coreUrl.getHost() == null || coreUrl.getRawQuery() != null || coreUrl.getFragment() != null)
            throw new IllegalArgumentException("gateway.core-url must be an absolute HTTP(S) URL without query/fragment");
        if (corePath == null || !corePath.startsWith("/") || corePath.startsWith("//"))
            throw new IllegalArgumentException("gateway.core-path must start with a single /");
        if (maxConnections < 1 || maxConcurrentCalls < 1) throw new IllegalArgumentException("Limits must be positive");
        for (Duration d : new Duration[]{acquireTimeout, connectTimeout, responseTimeout, openWait})
            if (d == null || d.isZero() || d.isNegative()) throw new IllegalArgumentException("Timeouts must be positive");
    }
    public URI coreEndpoint() {
        return URI.create(coreUrl.toString().replaceAll("/+$", "") + corePath);
    }
}
