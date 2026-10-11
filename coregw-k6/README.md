# Coregw k6

Project tải riêng dùng cùng script `create-order.js` cho WebFlux, MVC platform, MVC virtual và core trực tiếp. Cần **k6**, **JDK 21+**, **Python 3.9+**. Không có npm dependency; `npm test` chỉ chạy unit test logic metrics bằng Node.

Cài k6 theo [tài liệu Grafana](https://grafana.com/docs/k6/latest/set-up/install-k6/). Với macOS có Homebrew: `brew install k6`. Script đã kiểm tra với k6 2.3.0.

## Chạy manual

Khởi động core/gateway theo README gốc, rồi từ thư mục `coregw-k6`:

```sh
mkdir -p results
BASE_URL=http://localhost:8080 MODE=webflux PROFILE=baseline \
  k6 run create-order.js

# Arrival rate: tăng từ 100 lên 500 rồi 1.000 request/giây.
BASE_URL=http://localhost:8080 MODE=mvc-virtual PROFILE=baseline \
  EXECUTOR=rate LEVELS=100,500,1000 k6 run create-order.js

# Core trực tiếp: mock vẫn dùng /orders.
BASE_URL=http://localhost:9090 ORDER_PATH=/orders MODE=direct PROFILE=baseline \
  k6 run create-order.js
```

`PROFILE` là nhãn kết quả k6, không đổi profile Spring của server đang chạy.

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `BASE_URL` | http://localhost:8080 | Gateway hoặc core |
| `ORDER_PATH` | /api/orders | Path create order |
| `PAYLOAD_FILE` / `PAYLOAD` | payload.json | JSON từ file hoặc chuỗi; file ưu tiên nếu có cả hai |
| `WORKLOAD` | baseline | baseline hoặc resilience |
| `EXECUTOR` | vus | vus hoặc rate; resilience dùng constant-vus |
| `LEVELS` | 50,200,1000 (vus); 100,500,1000 (rate) | Mức concurrency hoặc request/giây |
| `WARMUP_SECONDS` | 30 | Warm-up riêng, không vào custom metrics đo |
| `HOLD_SECONDS` / `RAMP_SECONDS` | 120 / 10 | Thời gian giữ mỗi mức / tăng tải giữa các mức |
| `PREALLOCATED_VUS` / `MAX_VUS` | 1000 / 3000 | VU allocation cho arrival rate |
| `REQUEST_TIMEOUT` | 10s | Timeout phía k6 |
| `MIN_SUCCESS_RATE` | 0.99 | Threshold chỉ cho baseline |
| `RESILIENCE_VUS` / `RESILIENCE_SECONDS` | 600 / 150 | Tải và tổng thời gian đo lỗi/phục hồi |
| `MODE`, `PROFILE`, `REPETITION`, `RUN_ID` | manual, workload, 1, timestamp | Nhãn kết quả |
| `RESULT_FILE` | results/<mode>-<profile>-<repeat>-<run>.json | File summary; thư mục phải tồn tại |

Mỗi request có X-Request-ID riêng gồm run, scenario, VU và iteration. JSON `payload.json` chỉ là dữ liệu benchmark, gateway không áp schema nghiệp vụ. Nếu core thật có response khác `201` với `orderId` dạng string, điều chỉnh validation trước khi đo.

## Runner matrix

Chạy từ thư mục gốc sau `./mvnw package`:

```sh
python3 coregw-k6/run-benchmarks.py --suite baseline --executor vus
python3 coregw-k6/run-benchmarks.py --suite baseline --executor rate

# Một phần matrix:
python3 coregw-k6/run-benchmarks.py --modes mvc-platform mvc-virtual \
  --delays 200 --repetitions 3 --levels 50,200,1000

# Smoke test ngắn cho toàn bộ chế độ:
python3 coregw-k6/run-benchmarks.py --delays 5 --repetitions 1 \
  --levels 2,3 --warmup 1 --hold 2 --ramp 1
```

Runner chạy một gateway tại một thời điểm; thứ tự mặc định direct → webflux → mvc-platform → mvc-virtual. Mỗi lượt khởi động JVM/mock mới để reset circuit/pool/state. Các executable có thể chỉ định bằng `--java`, `--javac`, `--k6`; cùng Java và heap `--heap 512m` áp dụng cho tất cả gateway. Có thể thêm Spring config bằng `--gateway-arg=--gateway.max-concurrent-calls=100`; cùng argument áp dụng cho mọi gateway.

Thêm `--samples` để xuất JSONL raw metrics (file lớn). Mặc định chỉ xuất summary theo mức tải, logs, snapshot JVM/resilience metrics và mock stats. `failures.json` ghi các lượt có k6 exit code khác 0, gồm lỗi threshold. Runner vẫn chạy tiếp các lượt còn lại, rồi thoát lỗi nếu có lượt thất bại.

## Core chậm/lỗi/phục hồi

```sh
python3 coregw-k6/run-benchmarks.py --suite resilience \
  --modes webflux mvc-platform mvc-virtual --repetitions 1
```

Sau warm-up 30s, giữ 600 VUs và chạy năm phase, mặc định 30s/phase:

1. Core khỏe: 201, delay 200ms; tải này có thể vượt bulkhead 500.
2. Core lỗi: 500, delay 200ms; circuit breaker mở.
3. Core chậm: 201, delay 4s, vượt gateway response timeout 3s.
4. Core ngắt kết nối.
5. Core phục hồi: 201, delay 200ms; circuit chuyển half-open rồi closed.

Circuit giữ state xuyên suốt các phase nên một phần request phase sau có thể bị circuit từ chối trước khi core được gọi. Muốn quan sát timeout nhiều hơn, chạy bài timeout riêng với `--gateway.open-wait` nhỏ hoặc reset gateway. Integration test xác minh từng cơ chế độc lập.

Runner cập nhật file điều khiển mock bằng atomic replace; mock đọc mỗi 100ms. Nhãn phase k6 tính theo thời gian scenario, còn runner tính từ lúc khởi động k6, nên ranh giới phase xấp xỉ (có overhead khởi động). File properties cho biết trạng thái cuối; logs/samples hỗ trợ kiểm tra chuyển phase.

## Mock core độc lập

```sh
MOCK_PORT=9090 MOCK_DELAY_MS=200 MOCK_STATUS=201 \
  java coregw-k6/mock/MockCore.java
```

Mock dùng JDK HttpServer với virtual threads, nhận duy nhất POST /orders, không lưu dữ liệu. Đọc request tối đa 1MB. Các biến bổ sung: `MOCK_DISCONNECT=true`, `MOCK_CONTROL_FILE=/tmp/mock.properties`, `MOCK_STATS_FILE=/tmp/mock-stats.json`.

File control là Java properties:

```properties
delayMs=4000
status=201
disconnect=false
```

Mock trả status cấu hình với JSON; 201 có orderId UUID. Stats gồm tổng calls, active và peakActive, cập nhật mỗi giây và khi dừng. Một timeout ở gateway không đảm bảo core đã hủy xử lý đơn hàng; đây là lý do create order không retry.

## Đọc kết quả

- `successfulThroughput`: số create order thành công / thời gian đo, có tính thời gian drain sau tải.
- `successfulLatency`: p50/p95/p99 của **201 có orderId hợp lệ**, loại bỏ lỗi trả nhanh.
- `latency`: mọi request trong measurement, loại warm-up.
- `timeout`, `rejected503`, `transportErrors`, `otherErrors`: nhóm lỗi riêng. 503 có thể do gateway hoặc core; xem body/logs để phân biệt.
- `droppedIterations`: request k6 không phát được trong measurement ở bài arrival rate; tổng gồm warm-up nằm ở trường riêng.
- `byLevel`: latency và số thành công từng mức tải/phase. Ramp có trong tổng measurement nhưng không trong các mức giữ tải.
- `raw`: toàn bộ k6 summary; các metric HTTP mặc định vẫn gồm warm-up.

Healthy baseline mặc định yêu cầu success rate ≥99%. Resilience có lỗi chủ động nên không áp threshold này. VUs là số client đồng thời, không phải target RPS; arrival rate phù hợp hơn khi so cùng offered load. Đừng kết luận từ smoke test vài giây hoặc từ tổng RPS khi phần lớn request nhận 503.
