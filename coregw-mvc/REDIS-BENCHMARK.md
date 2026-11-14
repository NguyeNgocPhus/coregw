# RedisTemplate + Lettuce trong MVC

## Code cần đọc

- `RedisSettings.java`: các tham số Redis từ YAML.
- `RedisConfiguration.java`: tạo `LettuceConnectionFactory` và bean `StringRedisTemplate`.
- `RedisBenchmarkService.java`: dùng template, mỗi API thực hiện GET → SET → HSET.
- `RedisBenchmarkController.java`: `POST /api/bench/redis?key=0`.

`StringRedisTemplate` là `RedisTemplate<String, String>` với string serializers cho
key, value, hash key và hash value. Khi dùng ở service khác, chỉ cần inject:

```java
private final StringRedisTemplate redisTemplate;

public MyService(StringRedisTemplate redisTemplate) {
    this.redisTemplate = redisTemplate;
}
// redisTemplate.opsForValue().get("key");
// redisTemplate.opsForValue().set("key", "value");
// redisTemplate.opsForHash().put("hash", "field", "value");
```

Config có thể dùng độc lập với benchmark: bật `app.redis.enabled=true`.
Profile `redis-bench` chỉ bật API benchmark và nạp dữ liệu GET ban đầu.
Không còn Future, Semaphore, round-robin hoặc tự borrow/return connection trong service.

## Chọn multiplexing hoặc pool

Các tham số có sẵn trong `src/main/resources/application.yml`:

```yaml
app:
  redis:
    enabled: true
    host: localhost
    port: 6379
    database: 0
    username: ""
    password: ""
    ssl: false
    mode: multiplexing  # đổi thành pool rồi khởi động lại
    command-timeout: 1s
    connect-timeout: 1s
    pool:
      max-active: 8
      max-idle: 8
      min-idle: 0
      max-wait: 1s
```

- `multiplexing`: `shareNativeConnection=true`, các GET/SET/HSET dùng chung một
  native connection. Không tạo connection pool.
- `pool`: dùng `LettucePoolingClientConfiguration` và `shareNativeConnection=false`;
  mỗi thao tác RedisTemplate mượn/trả connection tự động. GET, SET, HSET trong một
  API có thể dùng connection khác nhau, không giữ một connection cho cả request.

Template là singleton dùng chung giữa các service. Không chia sẻ thủ công đối tượng
`RedisConnection` giữa các thread. Ba lệnh ở API không phải transaction. Timeout áp
dụng theo từng command; pool còn có thời gian chờ riêng `max-wait`, không phải deadline
chung cho cả HTTP request. Bật TLS bằng `ssl: true` nếu Redis server yêu cầu.

## Tự chạy app và k6

Từ thư mục gốc, chuẩn bị Redis và build:

```sh
docker compose -f coregw-mvc/compose.redis.yml up -d
./mvnw -pl coregw-mvc -am package
mkdir -p coregw-k6/results
```

Terminal 1 — bật app dùng multiplexing:

```sh
java -jar coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=virtual,baseline,redis-bench \
  --app.redis.mode=multiplexing
```

Terminal 2 — gọi thử API rồi tự chạy k6:

```sh
curl -X POST 'http://localhost:8080/api/bench/redis?key=0'
k6 run -e MODE=multiplexing -e CONNECTIONS=1 -e RATE=50 \
  -e WARMUP_SECONDS=10 -e HOLD_SECONDS=60 \
  -e RESULT_FILE=coregw-k6/results/multiplexing.json \
  -e REPORT_FILE=coregw-k6/results/multiplexing.html coregw-k6/redis.js
```

Dừng Java bằng Ctrl+C, bật lại dùng pool:

```sh
java -jar coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar \
  --spring.profiles.active=virtual,baseline,redis-bench \
  --app.redis.mode=pool \
  --app.redis.pool.max-active=8 --app.redis.pool.max-idle=8
```

Sau đó tự chạy k6:

```sh
k6 run -e MODE=pool -e CONNECTIONS=8 -e RATE=50 \
  -e WARMUP_SECONDS=10 -e HOLD_SECONDS=60 \
  -e RESULT_FILE=coregw-k6/results/pool.json \
  -e REPORT_FILE=coregw-k6/results/pool.html coregw-k6/redis.js
```

Mở file HTML vừa sinh để xem p90, p99, average, errors, dropped iterations. Không cần runner Python.
MODE/CONNECTIONS ở k6 chỉ là nhãn report; cấu hình thật nằm trong Java/YAML.
Muốn so cùng một connection, đặt pool max-active=1 và max-idle=1, CONNECTIONS=1.
50 HTTP req/s tương ứng khoảng 150 Redis commands/s. Script loại warm-up khỏi
measurement. Tải này chưa xác định throughput tối đa; tăng thời gian/lặp lại nếu
muốn kết luận về chênh lệch nhỏ. Không dùng kết quả của bản Lettuce trực tiếp cũ
để kết luận về bản RedisTemplate này.

## Dữ liệu benchmark và tests

Profile `redis-bench` nạp `redis-bench.keys=10000` string có sẵn để GET, mỗi value
`redis-bench.payload-bytes=1024` byte. Dùng Redis riêng: chỉ ghi prefix
`coregw:bench:*`, không flush database. Không chạy các benchmark khác payload đồng thời.
Nếu đổi keys/payload ở Java, đổi KEYS/PAYLOAD_BYTES ở k6 tương ứng.

```sh
REDIS_BENCH_TEST_URI=redis://localhost:6379 ./mvnw -pl coregw-mvc -am test
docker compose -f coregw-mvc/compose.redis.yml down
```

Test kiểm tra cả hai mode: native connection được share đúng cấu hình, concurrent
GET/SET/HSET chạy được và dữ liệu string/hash được ghi thật. Không có biến môi trường
thì integration test Redis được skip. Gateway `/api/orders` giữ nguyên.

K6 `setup()` gọi `POST /api/bench/redis/prepare` để nạp lại dữ liệu GET trước khi
kiểm tra API và phát tải, kể cả khi Redis đã restart sau app. Bước này không vào
measurement. Endpoint prepare chỉ có trong profile benchmark; không gọi khi đang
đo tải. API báo `GET_MISSING` hoặc `GET_VALUE_MISMATCH` nếu dữ liệu đọc không đúng.

## Giữ lịch sử report

Mỗi lần `k6 run redis.js` tự thêm timestamp giờ Việt Nam và mã ngẫu nhiên vào tên
HTML/JSON, ví dụ `redis-report-20261021-093000-123-ab12cd34.html`.
Cặp HTML/JSON cùng lượt có chung Run ID; thời gian và Run ID cũng nằm trong report.
K6 in đường dẫn `reportFile` và `resultFile` khi kết thúc để mở lại dễ dàng.

Ngay cả khi đặt `REPORT_FILE=pool.html` hoặc `RESULT_FILE=pool.json`, script vẫn thêm
Run ID trước đuôi file, không ghi đè tên cố định. File được lưu ở thư mục hiện tại
hoặc thư mục đã chỉ định (thư mục đó cần tồn tại). Report cũ được giữ nguyên.

## Theo dõi app và Redis bằng SigNoz

Xem [cài đặt SigNoz, dashboard, metrics và traces](monitoring/signoz/README.md).
K6 tiếp tục chạy trực tiếp như trước; không cần đổi API hoặc RedisTemplate.
