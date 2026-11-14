# SigNoz cho Redis benchmark

Luồng metrics: Actuator + Lettuce/Commons Pool → SigNoz Collector Prometheus receiver
→ ClickHouse → SigNoz. Redis exporter đọc INFO từ Redis → cùng collector.
Luồng traces/logs tùy chọn: OpenTelemetry Java agent → OTLP → SigNoz.
Không cần Prometheus server hoặc Grafana riêng. Service RedisTemplate không thay đổi.

## 1. Bật hệ sinh thái

Từ thư mục gốc repo:

```sh
./coregw-mvc/monitoring/signoz/setup.sh
```

Script dùng Foundry v0.3.0 chính thức để sinh Docker Compose từ `casting.yaml`,
khởi động SigNoz, ClickHouse, Keeper, PostgreSQL, collector và Redis exporter.
Pin SigNoz v0.146.0, collector v0.144.14, Java agent v2.32.0. Công cụ/JAR nằm ở
`.cache/signoz`, file triển khai sinh ra ở `pours/`, không cần commit binary.
Script không chạy k6. Redis hiện có được tái sử dụng, không flush DB.

- UI: http://localhost:3301 (8080 dành cho app).
- OTLP: localhost:4317 gRPC, localhost:4318 HTTP.
- Redis exporter: http://localhost:9121/metrics.
- App: http://localhost:8080/actuator/prometheus.

Môi trường local bật [impersonation mode](https://signoz.io/docs/manage/administrator-guide/configuration/impersonation-mode/):
mở UI trực tiếp, không cần username/password. Mọi request dùng quyền root admin.
Cấu hình nằm trong `casting.yaml`, UI vẫn chỉ bind loopback. Root user tự tạo khi
khởi động trong organization `coregw-local`; dashboard và dữ liệu cũ được giữ nguyên.
Script provision cũng tự nhận diện chế độ này, không cần đăng nhập.
Để bật lại đăng nhập, tắt `SIGNOZ_IDENTN_IMPERSONATION_ENABLED`, bật lại
`SIGNOZ_IDENTN_TOKENIZER_ENABLED` và `SIGNOZ_IDENTN_APIKEY_ENABLED`, rồi chạy `setup.sh`.
Dashboard đã import sẵn trên máy hiện tại. Trên máy mới, vào **Dashboards → New Dashboard →
Import JSON**, import `dashboard.json` (JVM/HTTP) và `redis-dashboard.json`
(Lettuce/pool/Redis). Hai dashboard dùng schema v6 và PromQL; xem
[DASHBOARDS.md](DASHBOARDS.md) để biết bộ lọc, metric mapping và cách cập nhật tự động.
Các series app được phân biệt bằng nhãn `redis_mode=pool|multiplexing`.

Metrics bắt đầu có sau khi **build và restart app** để nhận cấu hình metrics mới.
Không cần agent nếu chỉ xem metrics. Collector scrape mỗi 5s, rate window 1 phút;
chờ ít nhất 30–60s trước khi đọc rate/percentile. Histogram percentile là ước lượng.
Nếu app chạy cổng khác, sửa target trong `casting.yaml`, chạy lại setup.

## 2. Bật traces/logs khi chạy IntelliJ (tùy chọn)

VM options (đường dẫn tuyệt đối, không đặt vào Program arguments):

```text
-javaagent:/Users/nguyen/Documents/java/coregw/.cache/signoz/opentelemetry-javaagent.jar
-Dotel.javaagent.configuration-file=/Users/nguyen/Documents/java/coregw/coregw-mvc/monitoring/signoz/javaagent.properties
-Dotel.resource.attributes=redis.mode=pool,benchmark.run=pool-01
```

Đổi `redis.mode` và `benchmark.run` cho phù hợp lần chạy; cấu hình Redis thực tế vẫn
là `app.redis.mode` trong YAML/Program arguments. `redis.mode` ở agent chỉ là nhãn.
Agent tự instrument HTTP và Lettuce; xem **Services / Traces**, lọc service.name
`coregw-mvc`, redis.mode và benchmark.run để xem các span GET/SET/HSET dưới request.
Kiểm tra **Logs** cho logback logs. Trace sampling mặc định 10%; không thấy một
request cụ thể không có nghĩa request đó chưa chạy. GET/SET/HSET spans không bao
gồm đầy đủ thời gian chờ mượn pool, cần xem thêm pool metrics.

Với terminal:

```sh
java -javaagent:.cache/signoz/opentelemetry-javaagent.jar \
  -Dotel.javaagent.configuration-file=coregw-mvc/monitoring/signoz/javaagent.properties \
  -Dotel.resource.attributes=redis.mode=pool,benchmark.run=pool-01 \
  -jar coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=virtual,baseline,redis-bench --app.redis.mode=pool
```

Giữ nguyên cấu hình agent/sampling và JVM khi so hai mode. Thêm agent có overhead;
không so lượt bật agent với lượt tắt agent.

## 3. Tự chạy k6 và so sánh

Giữ cách chạy cũ: `k6 run redis.js` trong `coregw-k6`.
Chạy app mode pool, đo một lượt, ghi lại giờ bắt đầu/kết thúc. Restart app mode
multiplexing và đo lượt khác, cùng rate/payload/thread model/heap/agent. Chọn time
range tương ứng từng lượt trong SigNoz, **loại startup + prepare + warm-up**.
Ở app các đường biểu diễn có nhãn mode; metrics Redis bao gồm mọi client và chỉ
phân biệt hai lượt bằng thời gian. Không chạy hai mode cùng lúc trên cùng Redis.

| Cần so | Metric/panel | Ý nghĩa |
|---|---|---|
| Hiệu năng API | HTTP p90/p99, req/s, errors/s | K6 đo end-to-end; app đo phía server |
| Chi phí client | process CPU, heap, allocation/s, GC pause, live threads | Mode nào tốn tài nguyên hơn ở cùng TPS |
| Chờ pool | redis_pool_active/idle/waiters, borrow wait | Có hết connection/chờ acquire không |
| Connection churn | redis_pool_creations/destroyed/borrowed | Có liên tục tạo connection hoặc borrow nhiều không |
| Redis command latency | lettuce_command_completion_seconds | Client/network latency của GET/SET/HSET, ngoài pool wait |
| Chi phí Redis | CPU cores, memory/RSS, network | Redis có bão hòa hay tiết kiệm tài nguyên hơn không |
| Redis connections | redis_connected_clients | Pool thường giữ nhiều TCP connections; gồm exporter/client khác |
| Redis execution | commands/s, command execution mean | Thời gian thực thi trong Redis; không phải p99 |
| Dữ liệu/lỗi | hits/misses, evictions, rejected connections | Phát hiện GET thiếu dữ liệu, memory pressure hoặc từ chối connection |

Pool gauges chỉ xuất hiện ở mode pool; multiplexing không có pool nên không có
series. Gauge scrape 5s có thể không thấy borrow/wait ngắn. Commons Pool wait mean
là trung bình cửa sổ mẫu gần đây, max tính từ lúc tạo pool; đơn vị integer ms nên
chờ dưới 1ms có thể hiện 0. Đây **không phải p99**. Redis blocked_clients là lệnh
blocking phía Redis, không phải request đang đợi Java pool.

Ở tải thấp, latency có thể tương đương. Kết luận hữu ích là mode nào đạt cùng TPS
và SLO với ít CPU/GC/connections hơn; tăng tải từng bước nếu muốn tìm điểm bão hòa.
Không dùng tốc độ trả lỗi nhanh làm thành tích. Dashboard có Scrape health, tất cả
phải up=1 khi đo. Docker/ClickHouse trên cùng máy cũng cạnh tranh CPU với benchmark;
đo nghiêm túc nên tách hệ thống monitoring và load generator ra máy khác.

## 4. Lưu và xem lại

Dữ liệu SigNoz nằm trong named Docker volumes, tồn tại qua restart/`docker compose down`.
Retention do SigNoz quản lý; chỉnh retention trong SigNoz nếu cần lịch sử dài hơn.
Report k6 HTML/JSON vẫn độc lập theo từng lượt như trước. Không dùng `down -v` khi
muốn giữ dữ liệu theo dõi.

```sh
# Dừng SigNoz, giữ dữ liệu
docker compose -f coregw-mvc/monitoring/signoz/pours/deployment/compose.yaml down
# Dừng exporter riêng; giữ Redis đang chạy
docker compose -f coregw-mvc/compose.redis.yml --profile monitoring stop redis-exporter
```

Troubleshooting: kiểm tra app `/actuator/prometheus` có `redis_mode_info`,
`lettuce_command_completion_seconds_bucket`; mode pool có `redis_pool_active`.
Xem `docker compose -f .../compose.yaml logs --tail=100 ingester` nếu chưa thấy metrics.

Nguồn: [SigNoz Docker/Foundry](https://signoz.io/docs/install/docker/),
[Java agent](https://signoz.io/docs/instrumentation/java/opentelemetry-java/),
[Lettuce metrics](https://redis.github.io/lettuce/advanced-usage/observability/).

Lưu ý metric names trong SigNoz: Prometheus receiver chuyển histogram/summary thành
`http_server_requests_seconds.bucket/.count/.sum`, tương tự Lettuce và GC. Dashboard
đã dùng selector `__name__` chính xác và được kiểm tra query thực tế. `job` được đưa
vào resource `service.name`; panel Scrape health dùng tất cả `up` cùng `redis_up`.
