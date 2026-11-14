package vn.coregw;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

@SpringBootApplication
@EnableConfigurationProperties(GatewaySettings.class)
public class GatewayWebFluxApplication {
    public static void main(String[] args) { SpringApplication.run(GatewayWebFluxApplication.class, args); }
}
