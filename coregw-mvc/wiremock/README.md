# Mock core orders bằng WireMock

Chạy từ thư mục gốc repository:

```sh
docker compose -p coregw-core -f coregw-mvc/compose.core.yml up -d
```

Một container phục vụ core `POST http://localhost:9090/orders`.
MVC chạy trên máy nhận `POST http://localhost:8080/api/orders` rồi gọi core.
Cấu hình mặc định MVC đã đúng: `CORE_URL=http://localhost:9090`, `CORE_PATH=/orders`.
Nếu IntelliJ đang override các biến này, đổi lại và restart MVC.
Nếu gateway chạy trong cùng Docker network, dùng `CORE_URL=http://core-mock:9090`.
Không chạy Java MockCore cùng lúc vì cùng dùng cổng 9090.

## Gọi qua gateway

```sh
curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' \
  -d '{"customerId":"customer-1","items":[{"productId":"product-001","quantity":1}],"amount":100000,"currency":"VND"}'
```

Mặc định trả 201, `orderId` UUID và `status=CREATED`, delay 200ms.
Thêm trường `mockScenario` vào JSON để chọn tình huống; gateway chuyển nguyên body:

| mockScenario | Core response |
| --- | --- |
| không có hoặc success | 201 sau 200ms |
| error | 500, CORE_ERROR |
| slow | 201 sau 5 giây; gateway mặc định timeout sau 3 giây |
| disconnect | reset kết nối |

```sh
curl -i http://localhost:8080/api/orders \
  -H 'Content-Type: application/json' -d '{"mockScenario":"error"}'
```

Nếu trả `503 CORE_CIRCUIT_OPEN`, circuit breaker của MVC đang mở do các lần gọi lỗi trước đó; ngừng tải lỗi, chờ ít nhất 10 giây rồi gọi success để kiểm tra phục hồi.

Các mapping không validate schema đơn hàng. Có thể thêm file mapping cho API khác trong cùng container.
Request journal đã tắt để tránh giữ request trong RAM khi chạy tải, async response giúp delay không chiếm worker.
Sau khi sửa mapping, restart container:

```sh
docker compose -p coregw-core -f coregw-mvc/compose.core.yml restart
```

## Chạy k6 nhẹ để kiểm tra trước

```sh
cd coregw-k6
mkdir -p results
BASE_URL=http://localhost:8080 LEVELS=10 WARMUP_SECONDS=2 HOLD_SECONDS=10 k6 run create-order.js
```

`dial tcp 127.0.0.1:8080: connect: can't assign requested address` phát sinh ở phía load generator khi mở TCP tới gateway, chưa tới core. Khi gặp dưới tải lớn, kiểm tra số kết nối/TIME_WAIT, giảm VUs hoặc arrival rate và xác nhận gateway còn hoạt động. Không dùng địa chỉ localhost của container k6 để gọi gateway trên máy: Docker Desktop dùng `BASE_URL=http://host.docker.internal:8080`.

Dừng mock:

```sh
docker compose -p coregw-core -f coregw-mvc/compose.core.yml down
```

Tham khảo: https://wiremock.org/docs/standalone/docker/ và https://wiremock.org/docs/simulating-faults/.
