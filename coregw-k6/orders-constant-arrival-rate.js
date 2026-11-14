import http from 'k6/http';
import { check } from 'k6';

function positiveInteger(name, fallback) {
  const value = Number(__ENV[name] || fallback);
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

const rate = positiveInteger('RATE', 1000);
const preAllocatedVUs = positiveInteger('PREALLOCATED_VUS', 200);
const maxVUs = positiveInteger('MAX_VUS', Math.max(preAllocatedVUs, 1000));

if (maxVUs < preAllocatedVUs) {
  throw new Error('MAX_VUS must be >= PREALLOCATED_VUS');
}

export const options = {
  scenarios: {
    orders: {
      executor: 'constant-arrival-rate',
      // One iteration sends one request, so RATE is the target requests/second.
      rate,
      timeUnit: '1s',
      duration: __ENV.DURATION || '120s',
      preAllocatedVUs,
      maxVUs,
      // Allow the last requests to finish within the HTTP timeout.
      gracefulStop: '10s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>=0.99'],
    dropped_iterations: ['count==0'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

const baseUrl = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/+$/, '');
const payload = open('./payload.json');

export default function () {
  const response = http.post(`${baseUrl}/api/orders`, payload, {
    headers: { 'Content-Type': 'application/json' },
    timeout: '10s',
    redirects: 0,
  });

  check(response, {
    '201 with orderId': (res) => {
      if (res.status !== 201) return false;
      try {
        const body = res.json();
        return typeof body.orderId === 'string' && body.orderId.length > 0;
      } catch (_) {
        return false;
      }
    },
  });
}
