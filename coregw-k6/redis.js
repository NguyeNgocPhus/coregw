import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

// One iteration = one HTTP request = GET + SET + HSET in the MVC API.
const rate = Number(__ENV.RATE || 100);
const warmup = Number(__ENV.WARMUP_SECONDS || 10);
const seconds = Number(__ENV.HOLD_SECONDS || 180);
const keys = Number(__ENV.KEYS || 10000);
for (const n of [rate, warmup, seconds, keys]) {
  if (!Number.isInteger(n) || n < 1) throw new Error('rate, durations and keys must be positive integers');
}
const success = new Rate('redis_success');
const latency = new Trend('redis_latency', true);
const successLatency = new Trend('redis_success_latency', true);
const completed = new Counter('redis_completed');
const failed = new Counter('redis_errors');
const scenario = { executor: 'constant-arrival-rate', rate, timeUnit: '1s',
  preAllocatedVUs: 10, maxVUs: 100, gracefulStop: '5s' };
export const options = {
  scenarios: {
    warmup: { ...scenario, duration: `${warmup}s`, tags: { phase: 'warmup' } },
    measurement: { ...scenario, startTime: `${warmup + 6}s`, duration: `${seconds}s`,
      tags: { phase: 'measurement' } },
  },
  summaryTrendStats: ['avg', 'min', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds: { redis_success: ['rate>=0.99'], 'dropped_iterations{phase:measurement}': ['count==0'] },
};
// Check wiring before generating load; do not spend a full run benchmarking a 404.
export function setup() {
  const base = `${__ENV.BASE_URL || 'http://localhost:8080'}/api/bench/redis`;
  // Seed GET keys outside the measured run. Redis may have been reset since app startup.
  const preparation = http.post(`${base}/prepare`, null, { timeout: '60s' });
  if (preparation.status !== 200) {
    throw new Error(`Redis prepare failed: HTTP ${preparation.status}; ${preparation.body?.slice(0, 500)}. Restart the app with profile redis-bench and the latest build.`);
  }
  const url = `${base}?key=0`;
  const response = http.post(url, null, { timeout: '3s' });
  let valid = false;
  try {
    valid = response.status === 200 && response.json('valid') === true && response.json('commands') === 3;
  } catch (_) { /* Report the actual status and body below. */ }
  if (!valid) {
    const hint = response.status === 404
      ? ' Enable Spring profile redis-bench in the Java app, then restart.' : '';
    throw new Error(`Redis API preflight failed: HTTP ${response.status}; ${response.body?.slice(0, 500)}.${hint}`);
  }
}

export default function () {
  const key = Math.floor(Math.random() * keys);
  const response = http.post(`${__ENV.BASE_URL || 'http://localhost:8080'}/api/bench/redis?key=${key}`,
    null, { timeout: '3s' });
  if (exec.scenario.name !== 'measurement') return;
  let valid = false;
  try {
    valid = response.status === 200 && response.json('valid') === true && response.json('commands') === 3;
  } catch (_) { /* Mark non-JSON responses as failed requests. */ }
  success.add(valid);
  latency.add(response.timings.duration);
  if (valid) { completed.add(1); successLatency.add(response.timings.duration); }
  else failed.add(1, { status: String(response.status) });
}
export function handleSummary(data) {
  // Generate once here so HTML and JSON from the same run share an ID.
  const createdAt = new Date(Date.now() + 7 * 60 * 60 * 1000).toISOString().replace('Z', '+07:00');
  const runId = createdAt.slice(0, 23).replace(/[-:]/g, '').replace('T', '-').replace('.', '-')
    + '-' + Math.random().toString(36).slice(2, 10);
  const resultFile = uniqueFilename(__ENV.RESULT_FILE || 'redis-result.json', runId);
  const reportFile = uniqueFilename(__ENV.REPORT_FILE || 'redis-report.html', runId);
  const count = data.metrics.redis_completed?.values.count || 0;
  const errors = data.metrics.redis_errors?.values.count || 0;
  const result = {
    runId, createdAt,
    mode: __ENV.MODE || 'manual', connections: Number(__ENV.CONNECTIONS || 1),
    rate, seconds, warmup, payloadBytes: Number(__ENV.PAYLOAD_BYTES || 1024), keys,
    requests: count + errors, successfulRequests: count, errors,
    // Completions include graceful drain (up to 5s); denominator is the scheduled load window.
    successfulRequestsPerLoadSecond: count / seconds,
    successRate: data.metrics.redis_success?.values.rate || 0,
    latency: data.metrics.redis_latency?.values || {},
    successfulLatency: data.metrics.redis_success_latency?.values || {},
    droppedIterations: data.metrics['dropped_iterations{phase:measurement}']?.values.count || 0,
    raw: data,
  };
  return { [resultFile]: JSON.stringify(result, null, 2),
    [reportFile]: htmlReport(result),
    stdout: JSON.stringify({ reportFile, resultFile, mode: result.mode, requests: result.requests,
      successRate: result.successRate, latency: result.latency }, null, 2) + '\n' };
}


// Keep the requested directory/extension, append a unique ID even for custom names.
function uniqueFilename(path, runId) {
  const dot = path.lastIndexOf('.');
  const slash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return dot > slash ? `${path.slice(0, dot)}-${runId}${path.slice(dot)}` : `${path}-${runId}`;
}

function htmlReport(result) {
  const escape = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const metrics = [
    ['Run ID', result.runId], ['Thời gian (UTC+07:00)', result.createdAt],
    ['Mode (nhãn)', result.mode], ['Connections (nhãn)', result.connections],
    ['Target HTTP req/s', result.rate], ['Measurement (s)', result.seconds],
    ['Requests', result.requests], ['Success %', (result.successRate * 100).toFixed(2)],
    ['Errors', result.errors], ['Dropped iterations', result.droppedIterations],
    ...['avg', 'p(50)', 'p(90)', 'p(95)', 'p(99)', 'max'].map(metric =>
      [metric + ' (ms)', (result.latency[metric] || 0).toFixed(3)]),
  ];
  return `<!doctype html><html lang="vi"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Redis k6 report</title>
    <style>body{font:16px system-ui;max-width:800px;margin:32px auto;padding:16px;background:#f5f7fb;color:#17243a}
    table{width:100%;border-collapse:collapse;background:white}td{padding:10px 16px;border-bottom:1px solid #ddd}
    td:last-child{text-align:right;font-weight:600}p{line-height:1.6}</style>
    <h1>Redis API — k6 report</h1><p>POST /api/bench/redis → GET → SET string → HSET.</p>
    <table>${metrics.map(([name, value]) => `<tr><td>${escape(name)}</td><td>${escape(value)}</td></tr>`).join('')}</table>
    <p>Warm-up ${result.warmup}s được loại khỏi metrics. Latency là thời gian HTTP của mọi request trong measurement.
    p90 và p99 là percentile 90% và 99%. Một HTTP request thành công thực hiện 3 Redis commands.
    Mode/Connections là nhãn bạn cung cấp; cấu hình thực tế nằm ở ứng dụng Java.
    Kết quả đo tại tải đã chọn, chưa xác định TPS tối đa.</p></html>`;
}
