/**
 * k6 load test — SSE connection scalability
 * Target: 500 persistent connections without exhausting the ASGI worker pool
 *
 * k6 does not natively support SSE (text/event-stream).  We open an HTTP GET
 * and keep it alive for up to 30s per VU, checking that the response stream
 * starts correctly.  The real signal is worker CPU / memory on the server side.
 *
 * Run:
 *   k6 run --env BASE_URL=http://localhost:8000 \
 *          --env SESSION_COOKIE=<cookie> \
 *          sse_connections.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";

const errorRate = new Rate("errors");

export const options = {
  scenarios: {
    ramp_up: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 500 },   // ramp to 500 concurrent VUs
        { duration: "60s", target: 500 },   // hold
        { duration: "10s", target: 0 },
      ],
    },
  },
  thresholds: {
    errors: ["rate<0.02"],
    // Connection establishment (not full stream) should be fast
    http_req_duration: ["p(95)<3000"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SESSION = __ENV.SESSION_COOKIE || "";

export default function () {
  const params = {
    headers: {
      Accept: "text/event-stream",
      Cookie: `sessionid=${SESSION}`,
      "Cache-Control": "no-cache",
    },
    timeout: "35s",
  };

  // GET /api/events — the SSE endpoint streams for up to 300s server-side
  // We only hold each VU open for ~30s to simulate realistic client behaviour
  const res = http.get(`${BASE_URL}/api/events`, params);

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "content-type is event-stream": (r) =>
      (r.headers["Content-Type"] || "").includes("text/event-stream"),
    "not empty": (r) => r.body && r.body.length > 0,
  });

  errorRate.add(!ok);

  // k6 closes the connection when the script advances; realistic hold time
  sleep(30);
}
