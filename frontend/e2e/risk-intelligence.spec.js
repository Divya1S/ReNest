import { test, expect } from "@playwright/test";

import { MOCK_USER, mockAuth, mockNotifications } from "./helpers/mock-api.js";

// ---------------------------------------------------------------------------
// Risk Intelligence: overview -> Fraud Lab run -> transaction detail, against
// a mocked API. Exercises routing, the lazy chunks and the page contracts.
// ---------------------------------------------------------------------------

const TXN_ID = "txn_01E2ETESTAAAAAAAAAAAAAAAA";
const RUN_ID = "sim_01E2ETESTBBBBBBBBBBBBBBBB";

const OVERVIEW = {
  window_hours: 24,
  generated_at: new Date().toISOString(),
  includes_simulation: false,
  simulated_rows: 0,
  totals: { transactions: 18, allowed: 14, reviewed: 2, blocked: 2, completed: 13, failed: 1, review_queue: 2 },
  rates: { block_rate: 0.1111, review_rate: 0.1111, average_risk_score: 24.5 },
  amounts: { blocked: "2820.00", held: "300.00", completed: "410.00" },
  distribution: { low: 13, medium: 3, high: 2 },
  evaluation_latency: { count: 18, mean_ms: 0.02, p50_ms: 0.02, p95_ms: 0.03, p99_ms: 0.04, max_ms: 0.05 },
  series: [{ bucket: "2026-09-09T14:00", allow: 14, review: 2, block: 2 }],
  policy: { sensitivity: 50, review_threshold: 40, block_threshold: 70 },
  model_version: "risk-rules-v1",
};

const TXN = {
  public_id: TXN_ID,
  merchant_id: "mkt_electronics",
  merchant_category: "electronics",
  amount: "2400.00",
  currency: "USD",
  payment_method: "card",
  device_id: "unknown",
  country: "US",
  status: "blocked",
  decision: "block",
  risk_score: 71,
  risk_band: "high",
  confidence: "0.79",
  model_version: "risk-rules-v1",
  evaluation_ms: "0.024",
  is_simulation: false,
  ground_truth_fraud: null,
  request_id: "e2e-req",
  reasons: ["extreme_amount", "very_new_account", "first_transaction_large"],
  summary: "Blocked because of exceptionally large amount for this marketplace (score 71).",
  created_at: new Date().toISOString(),
};

const TXN_DETAIL = {
  ...TXN,
  ip_prefix: "10.0.0.0",
  processor_reference: "",
  failure_reason: "",
  metadata: {},
  manual_review: null,
  updated_at: TXN.created_at,
  evaluation: {
    id: 1,
    score: 71,
    decision: "block",
    confidence: "0.790",
    model_version: "risk-rules-v1",
    sensitivity: 50,
    thresholds: { sensitivity: 50, review: 40, block: 70 },
    evaluation_ms: "0.024",
    explanation: "RISK SCORE: 71 / 100\n+35 Exceptionally large amount for this marketplace\n+18 Account created in the last 24 hours\n+18 Very large first transaction with no spending history\nDECISION: BLOCK (review at 40, block at 70, sensitivity 50)\nDeclined.",
    factors: [
      { code: "extreme_amount", label: "Exceptionally large amount for this marketplace", points: 35, detail: {} },
      { code: "very_new_account", label: "Account created in the last 24 hours", points: 18, detail: {} },
      { code: "first_transaction_large", label: "Very large first transaction with no spending history", points: 18, detail: {} },
    ],
    context_snapshot: { account_age_days: 0.1, successful_txn_count: 0, txn_count_1h: 0, txn_count_24h: 0 },
    created_at: TXN.created_at,
  },
  events: [
    { event_id: "evt_e2e_1", sequence: 1, event_type: "payment.created", schema_version: 1, entity_type: "payment", entity_id: TXN_ID, payload: {}, request_id: "e2e-req", occurred_at: TXN.created_at, status: "processed", attempts: 1, processed_handlers: [], last_error: "", next_attempt_at: null, processed_at: TXN.created_at },
    { event_id: "evt_e2e_2", sequence: 2, event_type: "payment.blocked", schema_version: 1, entity_type: "payment", entity_id: TXN_ID, payload: {}, request_id: "e2e-req", occurred_at: TXN.created_at, status: "processed", attempts: 1, processed_handlers: ["audit", "notify_customer"], last_error: "", next_attempt_at: null, processed_at: TXN.created_at },
  ],
  audit: [
    { id: 1, actor_label: "user:1", action: "payment.evaluated", target_type: "payment", target_id: TXN_ID, reason: TXN.summary, decision: "block", model_version: "risk-rules-v1", request_id: "e2e-req", metadata: {}, created_at: TXN.created_at },
  ],
};

const RUN = {
  public_id: RUN_ID,
  scenario: "card_testing",
  scenario_title: "Card testing",
  sensitivity: 50,
  seed: 3,
  transaction_count: 1000,
  model_version: "risk-rules-v1",
  duration_ms: "210.000",
  created_at: new Date().toISOString(),
  metrics: {
    analyzed: 1000, allowed: 690, reviewed: 60, blocked: 250,
    labelled_fraud: 300, labelled_legitimate: 700,
    fraud_blocked: 245, fraud_reviewed: 40, fraud_missed: 15,
    legitimate_blocked: 5, legitimate_reviewed: 20,
    precision_blocked: 0.98, recall_blocked: 0.8167, precision_flagged: 0.9194, recall_flagged: 0.95, false_positive_rate: 0.0357,
    protected_amount: "610.00", held_amount: "90.00", friction_amount: "800.00",
    risk_bands: { low: 640, medium: 110, high: 250 },
    top_factors_fraud: [{ code: "card_testing", count: 290 }, { code: "velocity_1h", count: 300 }],
    top_factors_legitimate: [{ code: "new_device", count: 60 }],
    latency: { count: 1000, mean_ms: 0.006, p50_ms: 0.005, p95_ms: 0.009, p99_ms: 0.012, max_ms: 0.03 },
    thresholds: { sensitivity: 50, review: 40, block: 70 },
    model_version: "risk-rules-v1",
    sweep: [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((s) => ({ sensitivity: s, review_threshold: 60 - 0.4 * s, block_threshold: 90 - 0.4 * s, blocked: 100 + s, reviewed: 60, detection_rate: Math.min(1, 0.6 + s / 250), friction_rate: s / 400 })),
    seed: 3,
    scenario: "card_testing",
    duration_ms: 210,
  },
};

async function mockRisk(page) {
  await page.route("**/api/risk/overview**", (route) => route.fulfill({ json: OVERVIEW }));
  await page.route("**/api/risk/policy", (route) => route.fulfill({ json: { name: "default", sensitivity: 50, review_threshold: 40, block_threshold: 70, updated_at: null, updated_by: null, model_version: "risk-rules-v1", curve: [] } }));
  await page.route("**/api/risk/feed**", (route) => route.fulfill({ json: { results: [TXN], generated_at: new Date().toISOString() } }));
  await page.route("**/api/risk/scenarios", (route) => route.fulfill({ json: { scenarios: [{ key: "card_testing", title: "Card testing", description: "Bursts of tiny charges.", fraud_share: 0.3 }], default_count: 1000, min_count: 50, max_count: 2000, model_version: "risk-rules-v1", current_sensitivity: 50, note: "" } }));
  await page.route("**/api/risk/simulations**", (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 201, json: RUN });
    return route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } });
  });
  await page.route(`**/api/risk/payments/${TXN_ID}`, (route) => route.fulfill({ json: TXN_DETAIL }));
}

test.describe("Risk Intelligence", () => {
  test("overview, Fraud Lab run and transaction investigation", async ({ page }) => {
    await mockAuth(page, MOCK_USER);
    await mockNotifications(page);
    await mockRisk(page);

    await page.goto("/risk");
    await expect(page.getByRole("heading", { name: "Risk Overview" })).toBeVisible();
    await expect(page.getByText("Review queue")).toBeVisible();
    await expect(page.getByText("11.1% of traffic", { exact: false })).toBeVisible();
    await expect(page.getByRole("slider", { name: /sensitivity/i })).toBeDisabled();

    await page.getByRole("navigation", { name: "Risk Intelligence sections" }).getByRole("link", { name: "Fraud Lab" }).click();
    await expect(page.getByRole("heading", { name: "Fraud Lab" })).toBeVisible();
    await page.getByRole("button", { name: "Run scenario" }).click();
    await expect(page.getByText("Precision (flagged)")).toBeVisible();
    await expect(page.getByText("91.9%")).toBeVisible();
    await expect(page.getByText("Detection versus friction")).toBeVisible();

    await page.goto("/risk");
    await page.getByRole("link", { name: new RegExp(TXN_ID.slice(0, 14)) }).click();
    await expect(page.getByText("RISK SCORE: 71 / 100", { exact: false })).toBeVisible();
    await expect(page.getByText("Exceptionally large amount for this marketplace").first()).toBeVisible();
    await expect(page.getByText("Audit trail")).toBeVisible();
    await expect(page.getByText("service:risk-engine").or(page.getByText("user:1"))).toBeVisible();
  });
});
