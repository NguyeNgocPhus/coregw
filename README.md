# Coregw

Hai gateway Spring nhận HTTP REST, gọi HTTP sang core và chờ response. Mỗi module có đúng một API nghiệp vụ: `POST /api/orders`. Project `coregw-k6` dùng chung một script để đo ba chế độ và mock core độc lập.

| Chế độ | Server | HTTP client | Spring profiles |
| --- | --- | --- | --- |
| WebFlux | Reactor Netty | WebClient / Reactor Netty | `baseline` hoặc `resilient` |
| MVC platform | Tomcat, tối đa 200 worker threads | RestClient / Apache HttpClient | `platform,baseline` hoặc `platform,resilient` |
| MVC virtual | Tomcat, virtual threads | RestClient / Apache HttpClient | `virtual,baseline` hoặc `virtual,resilient` |

Spring Boot **4.0.8**, Resilience4j **2.4.0**, Java target **21**, Maven Wrapper **3.9.16**. Yêu cầu JDK 21+; khi so sánh, dùng cùng JDK, heap và tài nguyên. K6 cần cài riêng; JavaScript k6 không chạy bằng Node. Node chỉ dùng để kiểm tra logic script, không có npm dependency.

## Build và kiểm thử

```sh
./mvnw clean package
node --test coregw-k6/tests/*.test.js
```

Integration test mở HTTP server thật trên cổng ngẫu nhiên cho WebFlux baseline/resilient và MVC platform/virtual × baseline/resilient. Test kiểm tra forward payload, request ID, content type, status/body, core 4xx/5xx, circuit breaker phục hồi, bulkhead rejection/release, timeout, ngắt kết nối, core không hoạt động, không retry và loại thread thực tế xử lý request.

## Chạy nhanh

Terminal 1: mock core (không dùng database):

```sh
MOCK_DELAY_MS=200 java coregw-k6/mock/MockCore.java
```

Terminal 2: chọn **một** gateway:

```sh
java -Xms512m -Xmx512m -jar coregw-webflux/target/coregw-webflux-0.0.1-SNAPSHOT.jar --spring.profiles.active=baseline
# Hoặc MVC platform:
java -Xms512m -Xmx512m -jar coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar --spring.profiles.active=platform,baseline
# Hoặc MVC virtual:
java -Xms512m -Xmx512m -jar coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar --spring.profiles.active=virtual,baseline
```

Terminal 3:

```sh
curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' -H 'X-Request-ID: order-test-1' \
  --data '{"customerId":"customer-1","items":[{"productId":"p1","quantity":1}],"amount":100000,"currency":"VND"}'
```

Mock trả `201` và JSON `{"orderId":"...","status":"CREATED"}`. Gateway forward nguyên byte payload, status và body; không tạo hoặc lưu đơn hàng. Gateway forward Content-Type và X-Request-ID, tự tạo request ID nếu client chưa gửi. Không forward các header khác. Core URL là cấu hình tin cậy, không lấy từ request client.

Đổi sang core hiện có:

```sh
CORE_URL=http://localhost:9091 CORE_PATH=/your-core/create-order \
java -jar coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar --spring.profiles.active=virtual,resilient
```

`CORE_URL` có thể chứa base path; `CORE_PATH` được nối vào URL đó. Không cần sửa project core.

## Resilience và cấu hình

`resilient` bật circuit breaker + SemaphoreBulkhead; `baseline` bỏ qua cả hai decorators. Registry/metrics vẫn tồn tại trong baseline nhưng không ghi nhận lời gọi. Mặc định WebFlux là `resilient`, MVC là `platform,resilient`. Chỉ chọn một trong baseline/resilient và một trong platform/virtual.

Luồng bảo vệ: **Bulkhead → CircuitBreaker → HTTP call + đọc body**. WebFlux dùng Reactor operators, MVC dùng synchronous decorators; không có `@Async`, thread pool bulkhead hay TimeLimiter. Retry tự động và redirect được tắt ở cả hai HTTP clients. Một create order chỉ được gọi core một lần.

| Cấu hình | Mặc định | Biến môi trường |
| --- | --- | --- |
| Gateway port | 8080 | `PORT` |
| Core base URL / path | http://localhost:9090 / /orders | `CORE_URL`, `CORE_PATH` |
| Pool total và mỗi route | 500 | `CORE_MAX_CONNECTIONS` |
| Pool acquisition timeout | 100ms | `CORE_ACQUIRE_TIMEOUT` |
| Connect timeout | 1s | `CORE_CONNECT_TIMEOUT` |
| Response timeout | 3s | `CORE_RESPONSE_TIMEOUT` |
| Bulkhead concurrent calls | 500 | `CORE_MAX_CONCURRENT_CALLS` |

Cả hai dùng HTTP/1.1 và keep-alive. Response timeout là timeout chờ/đọc của HTTP client, không phải deadline tổng thể cho toàn bộ request. Reactor giới hạn pending acquire ở 500; Apache chờ trên các caller và dùng acquisition timeout. Payload benchmark nhỏ, cả hai buffer body; đây chưa phải streaming proxy.

Circuit breaker: cửa sổ 100 calls, tối thiểu 50 calls, failure rate 50%, open 10s, half-open 10 calls. HTTP 5xx được ghi nhận thất bại nhưng vẫn forward nguyên response. HTTP 4xx không tính lỗi core. Bulkhead rejection nằm ngoài circuit breaker. Không có fallback thành công giả.

| Lỗi tại gateway | HTTP | JSON code |
| --- | --- | --- |
| Bulkhead đầy | 503 | `CORE_BULKHEAD_FULL` |
| Circuit mở | 503 | `CORE_CIRCUIT_OPEN` |
| Timeout, kể cả chờ pool | 504 | `CORE_TIMEOUT` |
| Lỗi kết nối / response bị ngắt | 502 | `CORE_CONNECTION_ERROR` |

Các response HTTP core gửi về giữ nguyên, kể cả core 503/504; mã lỗi JSON giúp phân biệt với lỗi do gateway tạo.

Để xem thread xử lý request, thêm `--gateway.expose-thread-model=true`; response sẽ có `X-Gateway-Thread-Virtual`. Tùy chọn này tắt mặc định khi benchmark. Spring dùng virtual threads ở server MVC, không chuyển lời gọi sang pool phụ.

## Benchmark

Xem [hướng dẫn k6](coregw-k6/README.md) cho các lệnh manual, matrix và resilience.

Smoke test toàn bộ runner sau khi có k6:

```sh
python3 coregw-k6/run-benchmarks.py --modes direct webflux mvc-platform mvc-virtual \
  --delays 5 --repetitions 1 --levels 2,3 --warmup 1 --hold 2 --ramp 1
```

Bộ baseline đầy đủ, đo core trực tiếp trước rồi ba chế độ gateway:

```sh
python3 coregw-k6/run-benchmarks.py --suite baseline --executor vus
python3 coregw-k6/run-benchmarks.py --suite baseline --executor rate
```

Runner tự khởi động/dừng mock và từng gateway, dùng cùng executable Java và heap 512m. Kết quả JSON/log nằm trong `coregw-k6/results/<timestamp>/`. Không cần khởi động server riêng khi dùng runner; các cổng 8080/9090 phải trống. Một matrix mặc định có 36 lượt, mất khoảng 4 giờ với executor VUs; smoke test ngắn chỉ xác nhận wiring, không dùng để kết luận hiệu năng.

Đánh giá successful throughput và successful p95/p99 cùng timeout/rejection, không dùng tốc độ trả lỗi để kết luận gateway nhanh. WebFlux/MVC là so sánh cả server/client stack; hai chế độ MVC chỉ khác mô hình thread. Khi tải lớn, tách máy k6, gateway và mock/core để tránh CPU/memory của chúng cạnh tranh trên cùng máy. Kiểm tra dropped iterations và đo core trực tiếp để xác định bottleneck phía load generator/core.

## Metrics

```sh
curl http://localhost:8080/actuator/health
curl http://localhost:8080/actuator/metrics
curl http://localhost:8080/actuator/metrics/jvm.threads.live
curl http://localhost:8080/actuator/metrics/jvm.memory.used
curl http://localhost:8080/actuator/metrics/process.cpu.usage
curl http://localhost:8080/actuator/metrics/http.server.requests
curl http://localhost:8080/actuator/metrics/resilience4j.circuitbreaker.calls
curl http://localhost:8080/actuator/metrics/resilience4j.bulkhead.available.concurrent.calls
curl http://localhost:8080/actuator/prometheus
```

Runner lưu snapshot metrics cuối mỗi lượt. Để theo dõi cả lúc tải tăng và phục hồi, scrape Prometheus mỗi 1–5 giây hoặc dùng JFR (`-XX:StartFlightRecording=filename=recording.jfr,settings=profile`) với cùng cấu hình ở cả ba chế độ. Resilience metrics được bind trực tiếp bằng resilience4j-micrometer; không cần Spring AOP.

Đây là môi trường benchmark local, chưa thêm authentication, persistence hoặc xử lý nghiệp vụ đơn hàng.
