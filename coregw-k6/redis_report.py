"""Generate a standalone HTML comparison from k6 Redis JSON summaries (no CDN)."""
import html
import json
from pathlib import Path


def write_report(directory):
    directory = Path(directory)
    rows = []
    results = []
    for path in sorted(directory.glob('*.json')):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and 'successfulRequests' in data:
            results.append((path, data))
    for path, data in results:
        latency = data['latency']
        passed = data['successRate'] >= .99 and data['droppedIterations'] == 0
        cells = [path.stem, data['connections'], data['rate'], data['requests'],
                 data['errors'], data['droppedIterations'], f"{data['successRate'] * 100:.2f}%",
                 *[f"{latency.get(metric, 0):.3f}" for metric in ['avg', 'p(90)', 'p(99)']],
                 'PASS' if passed else 'FAIL']
        rows.append('<tr>' + ''.join(f'<td>{html.escape(str(cell))}</td>' for cell in cells) + '</tr>')
    headings = ['Run', 'Connections', 'Target req/s', 'Requests', 'Errors', 'Dropped',
                'Success %', 'Avg ms', 'p90 ms', 'p99 ms', 'Result']
    source_links = ' · '.join(f'<a href="{html.escape(path.name)}">{html.escape(path.stem)} JSON</a>' for path, _ in results)
    body = '''<!doctype html><html lang="vi"><meta charset="utf-8">
<title>Redis Lettuce — k6 report</title>
<style>body{font:16px system-ui;margin:40px;color:#17243a;background:#f5f7fb}h1{margin-bottom:8px}
.card{background:white;padding:24px;border-radius:12px;margin:20px 0;overflow:auto}
table{font-size:14px;border-collapse:collapse;width:100%;white-space:nowrap}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:right}
th:first-child,td:first-child{text-align:left}th{background:#e7eefb}p{line-height:1.6}a{color:#2257bd}</style>
<h1>Redis Lettuce: multiplexing và connection pool</h1>
<p>Mỗi request: <b>HTTP POST → GET → SET string → HSET → HTTP response</b>.</p>
<div class="card"><h2>Kết quả đo API</h2><table><thead><tr>'''
    body += ''.join(f'<th>{heading}</th>' for heading in headings)
    body += '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    body += '''<div class="card"><h2>Cách đọc</h2>
<p>p90: 90% request có thời gian phản hồi không vượt quá giá trị này; p99 tương tự cho 99% request.
Latency là thời gian HTTP do k6 đo, gồm cả 3 lệnh Redis, tính mọi response trong measurement.
Warm-up được loại khỏi bảng. Mỗi request thành công tương ứng 3 Redis commands; 50 req/s tương ứng khoảng 150 commands/s.</p>
<p>PASS: thành công ≥99% và không dropped iterations. Mỗi lượt đo 60 giây theo cấu hình mặc định; request hoàn thành trong drain tối đa 5 giây vẫn được tính.
Các percentile p50/p95/max và toàn bộ metric nằm trong JSON đính kèm. Report này đo ở tải đã chọn, không xác định TPS tối đa.
Chỉ so các hàng cùng số connection, payload, thread model và môi trường. Kết quả local chịu ảnh hưởng JVM warm-up,
Docker và các tiến trình trên cùng máy; chênh lệch nhỏ ở một lượt chưa chứng minh cấu hình nào tốt hơn.</p></div>'''
    if results:
        data = results[0][1]
        body += f'<p>Payload: {data["payloadBytes"]} bytes · Keys: {data["keys"]} · Warm-up: {data["warmup"]}s mỗi lượt.</p>'
    body += '<p>' + source_links + ' · <a href="environment.json">Environment</a></p></html>'
    (directory / 'report.html').write_text(body)
    return directory / 'report.html'


if __name__ == '__main__':
    import sys
    print(write_report(sys.argv[1]))
