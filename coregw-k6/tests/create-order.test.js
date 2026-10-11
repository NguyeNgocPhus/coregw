import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../create-order.js', import.meta.url), 'utf8')
  .replace(/^import .*;$/gm, '').replace(/\bexport /g, '')
  + '\nglobalThis.api = {options, warmupOrder, measuredOrder, handleSummary};';
function script(env = {}) {
  const metrics = new Map();
  const responses = [];
  const requests = [];
  class Metric {
    constructor(name) { this.samples = []; metrics.set(name, this); }
    add(value, tags) { this.samples.push({value: Number(value), tags}); }
  }
  const exec = { vu: {idInTest: 1}, scenario: {name: 'measurement', iterationInTest: 0, startTime: Date.now()} };
  const context = vm.createContext({
    __ENV: env, exec, Counter: Metric, Rate: Metric, Trend: Metric,
    open: () => '{"amount":100}', check: (res, checks) => Object.values(checks).every(fn => fn(res)),
    http: { post: (url, body, options) => {
      requests.push({url, body, options}); exec.scenario.iterationInTest++;
      if (!responses.length) throw new Error('Missing mock HTTP response');
      return responses.shift();
    } },
  });
  vm.runInContext(source, context);
  return { ...context.api, metrics, requests, responses, exec };
}
function response(status, body = {orderId: 'o1'}, error_code = 0) {
  return {status, error_code, timings: {duration: 20}, json: () => body};
}
function count(s, name) { return s.metrics.get(name).samples.reduce((sum, x) => sum + x.value, 0); }

test('warmup excluded, only valid 201 counted as success, errors classified separately', () => {
  const s = script({BASE_URL: 'http://localhost:8080///'});
  s.responses.push(response(201)); s.warmupOrder();
  assert.equal(count(s, 'measured_orders'), 0);
  s.responses.push(response(201), response(503), response(504), response(500), response(201, {}), response(0, null, 1050), response(0));
  for (let i = 0; i < 7; i++) s.measuredOrder();
  assert.equal(count(s, 'measured_orders'), 7);
  assert.equal(count(s, 'successful_orders'), 1);
  assert.equal(s.metrics.get('successful_order_duration').samples.length, 1);
  assert.equal(count(s, 'gateway_503'), 1);
  assert.equal(count(s, 'order_timeouts'), 2);
  assert.equal(count(s, 'order_other_errors'), 2);
  assert.equal(count(s, 'order_transport_errors'), 1);
  assert.equal(s.requests[0].url, 'http://localhost:8080/api/orders');
  assert.equal(new Set(s.requests.map(r => r.options.headers['X-Request-ID'])).size, 8);
});

test('arrival-rate workload and warmup are configured independently', () => {
  const s = script({EXECUTOR: 'rate', LEVELS: '100,200', WARMUP_SECONDS: '5', HOLD_SECONDS: '10', RAMP_SECONDS: '2'});
  assert.equal(s.options.scenarios.warmup.executor, 'constant-arrival-rate');
  assert.equal(s.options.scenarios.measurement.executor, 'ramping-arrival-rate');
  assert.equal(s.options.scenarios.measurement.startTime, '5s');
  assert.equal(s.options.scenarios.measurement.stages.length, 3);
  assert.ok(s.options.thresholds['dropped_iterations{scenario:measurement}']);
});

test('resilience workload permits intentional failures without a success-rate threshold', () => {
  const s = script({WORKLOAD: 'resilience', RESILIENCE_VUS: '600'});
  assert.equal(s.options.scenarios.measurement.executor, 'constant-vus');
  assert.equal(s.options.scenarios.measurement.vus, 600);
  assert.equal(s.options.thresholds.order_success_rate, undefined);
});

test('summary throughput uses measurement seconds rather than including warmup', () => {
  const s = script({WARMUP_SECONDS: '30', RESULT_FILE: 'report.json'});
  const output = s.handleSummary({state: {testRunDurationMs: 40000}, metrics: {
    successful_orders: {values: {count: 20}}, measured_orders: {values: {count: 25}},
    order_success_rate: {values: {rate: .8}}, gateway_503: {values: {count: 5}},
    'dropped_iterations{scenario:measurement}': {values: {count: 2}},
    dropped_iterations: {values: {count: 5}},
  }});
  const report = JSON.parse(output['report.json']);
  assert.equal(report.successfulThroughput, 2);
  assert.equal(report.measuredSeconds, 10);
  assert.equal(report.successfulOrders, 20);
  assert.equal(report.rejected503, 5);
  assert.equal(report.droppedIterations, 2);
  assert.equal(report.totalDroppedIterationsIncludingWarmup, 5);
});

test('invalid workload configuration fails before generating load', () => {
  assert.throws(() => script({LEVELS: '0,20'}), /LEVELS/);
  assert.throws(() => script({EXECUTOR: 'unknown'}), /EXECUTOR/);
  assert.throws(() => script({MAX_VUS: '10', PREALLOCATED_VUS: '20'}), /MAX_VUS/);
});
