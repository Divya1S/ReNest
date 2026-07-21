/**
 * k6 load test — Browse listings
 * Target: p95 < 200ms at 100 RPS sustained for 60s
 *
 * Run:
 *   k6 run --env BASE_URL=http://localhost:8000 browse_listings.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const errorRate = new Rate("errors");
const latency = new Trend("browse_latency", true);

export const options = {
  scenarios: {
    sustained: {
      executor: "constant-arrival-rate",
      rate: 100,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    browse_latency: ["p(95)<200"],
    errors: ["rate<0.01"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// Pre-baked session cookie — run `make load-session` to generate one
// and set SESSION_COOKIE env var, or leave blank for unauthenticated browse.
const SESSION = __ENV.SESSION_COOKIE || "";

export default function () {
  const params = {
    headers: {
      Cookie: SESSION ? `sessionid=${SESSION}` : "",
    },
  };

  const res = http.get(`${BASE_URL}/api/listings/?page=1`, params);

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "has results": (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.results);
      } catch {
        return false;
      }
    },
  });

  errorRate.add(!ok);
  latency.add(res.timings.duration);

  sleep(0.1);
}
