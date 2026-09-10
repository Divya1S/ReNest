import { vi } from "vitest";

/**
 * Route-table fetch mock for the Risk Intelligence pages. Each route is
 * [predicate(url, method), body | (init) => body]. Unmatched calls 404 so a
 * page can never silently rely on a real backend in unit tests.
 */
export function mockRiskFetch(routes) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const method = (init.method || "GET").toUpperCase();
    for (const [matches, handler] of routes) {
      if (matches(url, method)) {
        const body = typeof handler === "function" ? handler(init, url) : handler;
        return { ok: true, status: method === "POST" ? 201 : 200, json: async () => body };
      }
    }
    return { ok: false, status: 404, json: async () => ({ detail: `unmocked ${method} ${url}` }) };
  });
}

export const OVERVIEW = {
  window_hours: 24,
  generated_at: "2026-09-09T14:00:00Z",
  includes_simulation: false,
  simulated_rows: 0,
  totals: { transactions: 42, allowed: 35, reviewed: 4, blocked: 3, completed: 33, failed: 2, review_queue: 4 },
  rates: { block_rate: 0.0714, review_rate: 0.0952, average_risk_score: 21.4 },
  amounts: { blocked: "2910.00", held: "640.00", completed: "1210.55" },
  distribution: { low: 30, medium: 8, high: 4 },
  evaluation_latency: { count: 42, mean_ms: 0.02, p50_ms: 0.018, p95_ms: 0.04, p99_ms: 0.06, max_ms: 0.09 },
  series: [{ bucket: "2026-09-09T13:00", allow: 20, review: 2, block: 1 }, { bucket: "2026-09-09T14:00", allow: 15, review: 2, block: 2 }],
  policy: { sensitivity: 50, review_threshold: 40, block_threshold: 70 },
  model_version: "risk-rules-v1",
};

export const POLICY = {
  name: "default",
  sensitivity: 50,
  review_threshold: 40,
  block_threshold: 70,
  updated_at: null,
  updated_by: null,
  model_version: "risk-rules-v1",
  curve: [],
};

export const TXN = {
  public_id: "txn_01TESTAAAAAAAAAAAAAAAAAAAA",
  merchant_id: "mkt_textbooks",
  merchant_category: "books",
  amount: "420.00",
  currency: "USD",
  payment_method: "card",
  device_id: "unknown-phone",
  country: "US",
  status: "blocked",
  decision: "block",
  risk_score: 74,
  risk_band: "high",
  confidence: "0.83",
  model_version: "risk-rules-v1",
  evaluation_ms: "0.031",
  is_simulation: false,
  ground_truth_fraud: null,
  request_id: "req-123",
  reasons: ["amount_vs_history", "new_device", "ip_churn"],
  summary: "Blocked because of transaction amount is 11.05x above historical average and new device (score 74).",
  created_at: "2026-09-09T13:58:00Z",
};

export const TXN_DETAIL = {
  ...TXN,
  ip_prefix: "10.20.0.0",
  processor_reference: "",
  failure_reason: "",
  metadata: {},
  manual_review: null,
  updated_at: "2026-09-09T13:58:00Z",
  evaluation: {
    id: 1,
    score: 74,
    decision: "block",
    confidence: "0.830",
    model_version: "risk-rules-v1",
    sensitivity: 50,
    thresholds: { sensitivity: 50, review: 40, block: 70 },
    evaluation_ms: "0.031",
    explanation: "RISK SCORE: 74 / 100\n+34 Transaction amount is 11.05x above historical average\n+21 New device\n+15 5 different networks in 24h\n+4 Transaction in the early hours (01:00-05:00 local)\nDECISION: BLOCK (review at 40, block at 70, sensitivity 50)\nDeclined. The customer is told the payment could not be completed; no funds move.",
    factors: [
      { code: "amount_vs_history", label: "Transaction amount is 11.05x above historical average", points: 34, detail: {} },
      { code: "new_device", label: "New device", points: 21, detail: {} },
      { code: "ip_churn", label: "5 different networks in 24h", points: 15, detail: {} },
      { code: "unusual_hour", label: "Transaction in the early hours (01:00-05:00 local)", points: 4, detail: {} },
    ],
    context_snapshot: { account_age_days: 400, successful_txn_count: 12, historical_avg_amount: "38.00", amount_ratio: "11.05", device_seen_count: 0, accounts_on_device_24h: 1, txn_count_1h: 0, txn_count_24h: 1, failed_txn_count_24h: 0, distinct_devices_24h: 3, distinct_ips_24h: 5, local_hour: 3, currency: "USD" },
    created_at: "2026-09-09T13:58:00Z",
  },
  events: [
    { event_id: "evt_1", sequence: 1, event_type: "payment.created", schema_version: 1, entity_type: "payment", entity_id: TXN.public_id, payload: {}, request_id: "req-123", occurred_at: "2026-09-09T13:58:00Z", status: "processed", attempts: 1, processed_handlers: [], last_error: "", next_attempt_at: null, processed_at: "2026-09-09T13:58:00Z" },
    { event_id: "evt_2", sequence: 2, event_type: "payment.risk_evaluated", schema_version: 1, entity_type: "payment", entity_id: TXN.public_id, payload: { summary: "Blocked because of new device (score 74)." }, request_id: "req-123", occurred_at: "2026-09-09T13:58:00Z", status: "processed", attempts: 1, processed_handlers: ["analytics"], last_error: "", next_attempt_at: null, processed_at: "2026-09-09T13:58:00Z" },
    { event_id: "evt_3", sequence: 3, event_type: "payment.blocked", schema_version: 1, entity_type: "payment", entity_id: TXN.public_id, payload: {}, request_id: "req-123", occurred_at: "2026-09-09T13:58:00Z", status: "processed", attempts: 1, processed_handlers: ["audit", "notify_customer"], last_error: "", next_attempt_at: null, processed_at: "2026-09-09T13:58:00Z" },
  ],
  audit: [
    { id: 1, actor_label: "user:1", action: "payment.evaluated", target_type: "payment", target_id: TXN.public_id, reason: "Blocked because of new device (score 74).", decision: "block", model_version: "risk-rules-v1", request_id: "req-123", metadata: {}, created_at: "2026-09-09T13:58:00Z" },
    { id: 2, actor_label: "service:risk-engine", action: "payment.blocked", target_type: "payment", target_id: TXN.public_id, reason: "Blocked because of new device (score 74).", decision: "block", model_version: "risk-rules-v1", request_id: "req-123", metadata: {}, created_at: "2026-09-09T13:58:01Z" },
  ],
};

export const SCENARIOS = {
  scenarios: [
    { key: "normal_customer", title: "Normal customer traffic", description: "Only legitimate customers.", fraud_share: 0 },
    { key: "account_takeover", title: "Account takeover", description: "Good accounts hijacked.", fraud_share: 0.25 },
  ],
  default_count: 1000,
  min_count: 50,
  max_count: 2000,
  model_version: "risk-rules-v1",
  current_sensitivity: 50,
  note: "Synthetic.",
};

export const SIM_RUN = {
  public_id: "sim_01TESTBBBBBBBBBBBBBBBBBBBB",
  scenario: "account_takeover",
  scenario_title: "Account takeover",
  sensitivity: 50,
  seed: 7,
  transaction_count: 1000,
  model_version: "risk-rules-v1",
  duration_ms: "180.500",
  created_at: "2026-09-09T14:00:00Z",
  metrics: {
    analyzed: 1000, allowed: 700, reviewed: 120, blocked: 180,
    labelled_fraud: 250, labelled_legitimate: 750,
    fraud_blocked: 170, fraud_reviewed: 70, fraud_missed: 10,
    legitimate_blocked: 10, legitimate_reviewed: 50,
    precision_blocked: 0.9444, recall_blocked: 0.68, precision_flagged: 0.8, recall_flagged: 0.96, false_positive_rate: 0.08,
    protected_amount: "48210.00", held_amount: "9100.00", friction_amount: "2100.00",
    risk_bands: { low: 650, medium: 170, high: 180 },
    top_factors_fraud: [{ code: "amount_vs_history", count: 240 }, { code: "new_device", count: 250 }],
    top_factors_legitimate: [{ code: "new_device", count: 70 }],
    latency: { count: 1000, mean_ms: 0.006, p50_ms: 0.005, p95_ms: 0.009, p99_ms: 0.012, max_ms: 0.03 },
    thresholds: { sensitivity: 50, review: 40, block: 70 },
    model_version: "risk-rules-v1",
    sweep: [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((s) => ({ sensitivity: s, review_threshold: 60 - 0.4 * s, block_threshold: 90 - 0.4 * s, blocked: s * 2, reviewed: 100, detection_rate: Math.min(1, 0.5 + s / 200), friction_rate: s / 500 })),
    seed: 7,
    scenario: "account_takeover",
    duration_ms: 180.5,
  },
};

export const AGENT = {
  public_id: "agent_01TESTCCCCCCCCCCCCCCCCCCCC",
  name: "Dorm restock agent",
  daily_limit: "150.00",
  transaction_limit: "60.00",
  requires_approval_above: "40.00",
  allowed_categories: ["supplies", "food"],
  blocked_merchants: ["mkt_tickets"],
  currency: "USD",
  active: true,
  spent_today: "18.50",
  created_at: "2026-09-09T12:00:00Z",
  updated_at: "2026-09-09T12:00:00Z",
};

export const AGENT_VERDICT = {
  attempt: { id: 9, amount: "75.00", currency: "USD", category: "electronics", merchant_id: "mkt_electronics", description: "Headphones", decision: "block", reasons: [], risk_score: null, counts_toward_spend: false, created_at: "2026-09-09T14:01:00Z" },
  decision: "block",
  reasons: [
    { code: "category_not_allowed", outcome: "block", text: "Category 'electronics' is not in the allowed list (supplies, food)." },
    { code: "transaction_limit", outcome: "block", text: "Transaction exceeds agent transaction limit of USD 60.00." },
  ],
  risk_score: null,
  counts_toward_spend: false,
  spent_today: "18.50",
  remaining_today: "131.50",
};

export const INVESTIGATION = {
  public_id: "inv_01TESTDDDDDDDDDDDDDDDDDDDD",
  question: "Why did blocked transactions increase today?",
  intent: "blocked_trend",
  queries: [{ name: "decision_counts", params: { window_hours: 24 } }, { name: "top_factors", params: { window_hours: 24, decision: "block" } }],
  results: {},
  answer: "In the last 24 hours: 42 transactions, 3 blocked, 4 held for review, 35 allowed. Blocks are up 200% versus the previous period (1).",
  facts: ["In the last 24 hours: 42 transactions, 3 blocked, 4 held for review, 35 allowed.", "Blocks are up 200% versus the previous period (1)."],
  inferences: ["New device combined with above-average amounts is the signature of account takeover."],
  evidence: [TXN.public_id],
  mode: "deterministic",
  model_version: "investigator-templates-v1",
  latency_ms: "4.200",
  created_at: "2026-09-09T14:02:00Z",
};

export const HEALTH = {
  status: "ok",
  generated_at: "2026-09-09T14:00:00Z",
  model_version: "risk-rules-v1",
  processor: "sandbox",
  investigator_mode: "deterministic",
  investigator_setting: "deterministic",
  policy: { sensitivity: 50, review_threshold: 40, block_threshold: 70 },
  counters: { requests_total: 120, errors_total: 0, payments_created_total: 42, decisions_allow_total: 35, decisions_review_total: 4, decisions_block_total: 3, payments_completed_total: 33, payments_failed_total: 2, events_emitted_total: 160, events_processed_total: 160, events_dead_lettered_total: 0, idempotent_replays_total: 1, idempotency_conflicts_total: 0, simulations_total: 2, investigations_total: 1 },
  error_rate: 0,
  latency: { risk_evaluation_ms: { count: 42, mean_ms: 0.02, p50_ms: 0.018, p95_ms: 0.04, p99_ms: 0.06, max_ms: 0.09 }, payment_create_ms: { count: 42, mean_ms: 12.1, p50_ms: 11.2, p95_ms: 18.4, p99_ms: 22.0, max_ms: 25.5 }, request_ms: { count: 120, mean_ms: 9.5, p50_ms: 8.1, p95_ms: 20.2, p99_ms: 31.0, max_ms: 40.0 }, event_dispatch_ms: { count: 42, mean_ms: 3.1, p50_ms: 2.8, p95_ms: 5.0, p99_ms: 6.0, max_ms: 7.0 }, simulation_ms: { count: 2, mean_ms: 180.5, p50_ms: 180.5, p95_ms: 190.0, p99_ms: 190.0, max_ms: 190.0 } },
  database: { transactions_24h: 42, simulated_transactions_24h: 2000, evaluations_24h: 2042, evaluation_latency_24h: { count: 2042, mean_ms: 0.01, p50_ms: 0.008, p95_ms: 0.02, p99_ms: 0.04, max_ms: 0.09 } },
  events: { pending: 0, failed: 0, dead: 0, processed_24h: 160, total: 160, oldest_unprocessed_age_s: null, handlers: { "payment.blocked": ["audit", "notify_customer"] } },
  idempotency: { active_keys: 3, in_progress: 0 },
  notes: ["Counters live in the cache."],
};
