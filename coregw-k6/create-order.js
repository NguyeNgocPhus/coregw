import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

function positive(name, fallback) {
  const value = Number(__ENV[name] || fallback);
  if (!Number.isFinite(value) || value <= 0) throw new Error(`${name} must be positive`);
  return value;
}
const base = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/+$/, '');
const path = __ENV.ORDER_PATH || '/api/orders';
const payload = __ENV.PAYLOAD_FILE ? open(__ENV.PAYLOAD_FILE) : (__ENV.PAYLOAD || open('./payload.json'));
JSON.parse(payload);
const workload = __ENV.WORKLOAD || 'baseline';
const executor = __ENV.EXECUTOR || 'vus';
if (!['baseline', 'resilience'].includes(workload)) throw new Error('WORKLOAD must be baseline or resilience');
if (!['vus', 'rate'].includes(executor)) throw new Error('EXECUTOR must be vus or rate');
const warmup = positive('WARMUP_SECONDS', 30);
const hold = positive('HOLD_SECONDS', 120);
const ramp = positive('RAMP_SECONDS', 10);
const levelText = executor === 'rate' ? (__ENV.LEVELS || '100,500,1000') : (__ENV.LEVELS || '50,200,1000');
const levels = levelText.split(',').map(Number);
if (levels.some(v => !Number.isInteger(v) || v < 1)) throw new Error('LEVELS must be positive integers');
const faultSeconds = positive('RESILIENCE_SECONDS', 150);
const runId = __ENV.RUN_ID || `manual-${Date.now()}`;
const mode = __ENV.MODE || 'manual';
const profile = __ENV.PROFILE || workload;
const repetition = __ENV.REPETITION || '1';
const stages = [];
for (let i = 0; i < levels.length; i++) {
  if (i) stages.push({ duration: `${ramp}s`, target: levels[i] });
  stages.push({ duration: `${hold}s`, target: levels[i] });
}
const preallocated = positive('PREALLOCATED_VUS', 1000);
const maxVus = positive('MAX_VUS', 3000);
if (maxVus < preallocated) throw new Error('MAX_VUS must be >= PREALLOCATED_VUS');
const warmupScenario = executor === 'rate' && workload === 'baseline'
  ? { executor: 'constant-arrival-rate', rate: levels[0], timeUnit: '1s', duration: `${warmup}s`,
      preAllocatedVUs: preallocated, maxVUs: maxVus, exec: 'warmupOrder', gracefulStop: '0s' }
  : { executor: 'constant-vus', vus: workload === 'resilience' ? positive('RESILIENCE_VUS', 600) : levels[0],
      duration: `${warmup}s`, exec: 'warmupOrder', gracefulStop: '0s' };
const thresholds = workload === 'baseline' ? { order_success_rate: [`rate>=${Number(__ENV.MIN_SUCCESS_RATE || 0.99)}`] } : {};
if (executor === 'rate' && workload === 'baseline') thresholds['dropped_iterations{scenario:measurement}'] = ['count>=0'];
for (const level of workload === 'baseline' ? levels.map(String) : ['healthy', 'core_5xx', 'core_timeout', 'disconnect', 'recovery']) {
  thresholds[`measured_order_duration{level:${level}}`] = ['p(99)>=0'];
  thresholds[`successful_orders{level:${level}}`] = ['count>=0'];
}
const measuredScenario = workload === 'resilience'
  ? { executor: 'constant-vus', vus: positive('RESILIENCE_VUS', 600), duration: `${faultSeconds}s` }
  : executor === 'rate'
    ? { executor: 'ramping-arrival-rate', startRate: levels[0], timeUnit: '1s', stages,
        preAllocatedVUs: preallocated, maxVUs: maxVus }
    : { executor: 'ramping-vus', startVUs: levels[0], stages, gracefulRampDown: '0s' };
export const options = {
  scenarios: {
    warmup: warmupScenario,
    measurement: { ...measuredScenario, startTime: `${warmup}s`, exec: 'measuredOrder', gracefulStop: '10s' },
  },
  tags: { mode, profile, run_id: runId, repetition },
  summaryTrendStats: ['min', 'avg', 'med', 'p(50)', 'p(95)', 'p(99)', 'max'],
  thresholds,
};
const successful = new Counter('successful_orders');
const successRate = new Rate('order_success_rate');
const latency = new Trend('measured_order_duration', true);
const successLatency = new Trend('successful_order_duration', true);
const timeouts = new Counter('order_timeouts');
const rejected = new Counter('gateway_503');
const transportErrors = new Counter('order_transport_errors');
const otherErrors = new Counter('order_other_errors');
const attempted = new Counter('measured_orders');

function phaseTag() {
  if (workload === 'resilience') {
    const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
    const length = faultSeconds / 5;
    return ['healthy', 'core_5xx', 'core_timeout', 'disconnect', 'recovery'][Math.min(4, Math.floor(elapsed / length))];
  }
  let elapsed = (Date.now() - exec.scenario.startTime) / 1000;
  for (let i = 0; i < levels.length; i++) {
    if (i) { if (elapsed < ramp) return 'ramp'; elapsed -= ramp; }
    if (elapsed < hold) return String(levels[i]);
    elapsed -= hold;
  }
  return String(levels[levels.length - 1]);
}
function send(measured) {
  const tags = { phase: measured ? 'measurement' : 'warmup', level: measured ? phaseTag() : 'warmup' };
  const id = `${runId}-${exec.scenario.name}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
  const res = http.post(`${base}${path}`, payload, {
    headers: { 'Content-Type': 'application/json', 'X-Request-ID': id },
    timeout: __ENV.REQUEST_TIMEOUT || '10s', tags,
    redirects: 0,
  });
  if (!measured) return;
  let valid = false;
  if (res.status === 201) {
    try { const body = res.json(); valid = typeof body.orderId === 'string' && body.orderId.length > 0; } catch (_) {}
    check(res, { '201 contains orderId': () => valid }, tags);
  }
  attempted.add(1, tags);
  successful.add(valid ? 1 : 0, tags);
  successRate.add(valid, tags);
  latency.add(res.timings.duration, tags);
  if (valid) successLatency.add(res.timings.duration, tags);
  const timeout = res.status === 504 || (res.status === 0 && res.error_code === 1050);
  timeouts.add(timeout ? 1 : 0, tags);
  rejected.add(res.status === 503 ? 1 : 0, tags);
  transportErrors.add(res.status === 0 && !timeout ? 1 : 0, tags);
  otherErrors.add(!valid && !timeout && res.status !== 503 && res.status !== 0 ? 1 : 0, tags);
}
export function warmupOrder() { send(false); }
export function measuredOrder() { send(true); }
export function handleSummary(data) {
  const count = name => data.metrics[name] ? data.metrics[name].values.count : 0;
  const measuredSeconds = Math.max(0.001, data.state.testRunDurationMs / 1000 - warmup);
  const report = {
    runId, mode, profile, repetition, workload, executor, levels,
    coreDelayMs: __ENV.CORE_DELAY_MS || null,
    measuredSeconds, successfulThroughput: count('successful_orders') / measuredSeconds,
    measuredOrders: count('measured_orders'), successfulOrders: count('successful_orders'),
    timeout: count('order_timeouts'), rejected503: count('gateway_503'),
    transportErrors: count('order_transport_errors'), otherErrors: count('order_other_errors'),
    droppedIterations: count('dropped_iterations{scenario:measurement}'),
    totalDroppedIterationsIncludingWarmup: count('dropped_iterations'),
    // Custom trends exclude warmup; successful trend also excludes fast failures/rejections.
    latency: data.metrics.measured_order_duration?.values || {},
    successfulLatency: data.metrics.successful_order_duration?.values || {},
    successRate: data.metrics.order_success_rate?.values.rate || 0,
    raw: data,
  };
  report.byLevel = {};
  for (const level of workload === 'baseline' ? levels.map(String) : ['healthy', 'core_5xx', 'core_timeout', 'disconnect', 'recovery']) {
    report.byLevel[level] = {
      successfulOrders: count(`successful_orders{level:${level}}`),
      latency: data.metrics[`measured_order_duration{level:${level}}`]?.values || {},
    };
  }
  const file = __ENV.RESULT_FILE || `results/${mode}-${profile}-${repetition}-${runId}.json`;
  return { [file]: JSON.stringify(report, null, 2), stdout: JSON.stringify({ ...report, raw: undefined }, null, 2) + '\n' };
}
