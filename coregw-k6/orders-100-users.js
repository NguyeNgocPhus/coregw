import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    orders: {
      executor: 'constant-vus',
      vus: 100,
      duration: '120s',
      gracefulStop: '0s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>=0.99'],
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
