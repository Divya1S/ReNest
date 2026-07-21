/**
 * k6 load test — Reservation creation
 * Target: p95 < 500ms at 20 RPS sustained for 60s
 *
 * Run:
 *   k6 run --env BASE_URL=http://localhost:8000 \
 *          --env SESSION_COOKIE=<cookie> \
 *          --env CSRF_TOKEN=<token> \
 *          --env LISTING_ID=<id> \
 *          reservation_create.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const errorRate = new Rate("errors");
const latency = new Trend("reservation_latency", true);

export const options = {
  scenarios: {
    sustained: {
      executor: "constant-arrival-rate",
      rate: 20,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 20,
      maxVUs: 60,
    },
  },
  thresholds: {
    reservation_latency: ["p(95)<500"],
    errors: ["rate<0.05"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SESSION = __ENV.SESSION_COOKIE || "";
const CSRF = __ENV.CSRF_TOKEN || "";
const LISTING_ID = __ENV.LISTING_ID || "1";

export default function () {
  const headers = {
    "Content-Type": "application/json",
    "X-CSRFToken": CSRF,
    Cookie: `sessionid=${SESSION}; csrftoken=${CSRF}`,
  };

  const payload = JSON.stringify({
    listing: parseInt(LISTING_ID, 10),
    pickup_note: "k6 load test",
  });

  const res = http.post(`${BASE_URL}/api/reservations/`, payload, { headers });

  // 201 = created, 409 = already reserved (both valid under load)
  const ok = check(res, {
    "2xx or 409": (r) => r.status === 201 || r.status === 409,
    "not 5xx": (r) => r.status < 500,
  });

  errorRate.add(!ok);
  latency.add(res.timings.duration);

  sleep(0.2);
}
