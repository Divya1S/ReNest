/**
 * Types and helpers for the Risk Intelligence pages.
 *
 * Everything the UI displays comes from the /api/risk endpoints. The only
 * calculation that lives here is the threshold curve, duplicated from the
 * backend so the sensitivity slider can preview thresholds before saving;
 * the server remains the authority on the decision.
 */

export type Decision = "allow" | "review" | "block";
export type TransactionStatus = "pending" | "approved" | "review" | "blocked" | "completed" | "failed";
export type RiskBand = "low" | "medium" | "high" | "unknown";

export interface RiskFactor {
  code: string;
  label: string;
  points: number;
  detail: Record<string, unknown>;
}

export interface RiskEvaluation {
  id: number;
  score: number;
  decision: Decision;
  confidence: string;
  model_version: string;
  sensitivity: number;
  thresholds: { sensitivity: number; review: number; block: number };
  evaluation_ms: string;
  explanation: string;
  factors: RiskFactor[];
  context_snapshot: Record<string, unknown>;
  created_at: string;
}

export interface PaymentSummary {
  public_id: string;
  merchant_id: string;
  merchant_category: string;
  amount: string;
  currency: string;
  payment_method: string;
  device_id: string;
  country: string;
  status: TransactionStatus;
  decision: Decision | "";
  risk_score: number | null;
  risk_band: RiskBand;
  confidence: string | null;
  model_version: string;
  evaluation_ms: string | null;
  is_simulation: boolean;
  ground_truth_fraud: boolean | null;
  request_id: string;
  reasons: string[];
  summary: string;
  created_at: string;
}

export interface PaymentEvent {
  event_id: string;
  sequence: number;
  event_type: string;
  schema_version: number;
  entity_type: string;
  entity_id: string;
  payload: Record<string, unknown>;
  request_id: string;
  occurred_at: string;
  status: "pending" | "processing" | "processed" | "failed" | "dead";
  attempts: number;
  processed_handlers: string[];
  last_error: string;
  next_attempt_at: string | null;
  processed_at: string | null;
}

export interface AuditEntry {
  id: number;
  actor_label: string;
  action: string;
  target_type: string;
  target_id: string;
  reason: string;
  decision: string;
  model_version: string;
  request_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface PaymentDetail extends PaymentSummary {
  ip_prefix: string;
  processor_reference: string;
  failure_reason: string;
  metadata: Record<string, unknown>;
  manual_review: { by: number; outcome: string; note: string } | null;
  evaluation: RiskEvaluation | null;
  events: PaymentEvent[];
  audit: AuditEntry[];
  updated_at: string;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface LatencySummary {
  count: number;
  mean_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  max_ms: number | null;
}

export interface Overview {
  window_hours: number;
  generated_at: string;
  includes_simulation: boolean;
  simulated_rows: number;
  totals: { transactions: number; allowed: number; reviewed: number; blocked: number; completed: number; failed: number; review_queue: number };
  rates: { block_rate: number | null; review_rate: number | null; average_risk_score: number | null };
  amounts: { blocked: string; held: string; completed: string };
  distribution: { low: number; medium: number; high: number };
  evaluation_latency: LatencySummary;
  series: { bucket: string; allow: number; review: number; block: number }[];
  policy: { sensitivity: number; review_threshold: number; block_threshold: number };
  model_version: string;
}

export interface Policy {
  name: string;
  sensitivity: number;
  review_threshold: number;
  block_threshold: number;
  updated_at: string | null;
  updated_by: string | null;
  model_version: string;
  curve: { sensitivity: number; review: number; block: number }[];
}

export interface SweepPoint {
  sensitivity: number;
  review_threshold: number;
  block_threshold: number;
  blocked: number;
  reviewed: number;
  detection_rate: number | null;
  friction_rate: number | null;
}

export interface SimulationMetrics {
  analyzed: number;
  allowed: number;
  reviewed: number;
  blocked: number;
  labelled_fraud: number;
  labelled_legitimate: number;
  fraud_blocked: number;
  fraud_reviewed: number;
  fraud_missed: number;
  legitimate_blocked: number;
  legitimate_reviewed: number;
  precision_blocked: number | null;
  recall_blocked: number | null;
  precision_flagged: number | null;
  recall_flagged: number | null;
  false_positive_rate: number | null;
  protected_amount: string;
  held_amount: string;
  friction_amount: string;
  risk_bands: { low: number; medium: number; high: number };
  top_factors_fraud: { code: string; count: number }[];
  top_factors_legitimate: { code: string; count: number }[];
  latency: LatencySummary;
  thresholds: { sensitivity: number; review: number; block: number };
  model_version: string;
  sweep: SweepPoint[];
  seed: number;
  scenario: string;
  duration_ms?: number;
}

export interface SimulationRun {
  public_id: string;
  scenario: string;
  scenario_title: string;
  sensitivity: number;
  seed: number;
  transaction_count: number;
  model_version: string;
  metrics: SimulationMetrics;
  duration_ms: string;
  created_at: string;
}

export interface Scenario {
  key: string;
  title: string;
  description: string;
  fraud_share: number;
}

export interface Investigation {
  public_id: string;
  question: string;
  intent: string;
  queries: { name: string; params: Record<string, unknown> }[];
  results: Record<string, unknown>;
  answer: string;
  facts: string[];
  inferences: string[];
  evidence: string[];
  mode: "deterministic" | "llm";
  model_version: string;
  latency_ms: string;
  created_at: string;
}

export interface AgentPolicy {
  public_id: string;
  name: string;
  daily_limit: string;
  transaction_limit: string;
  requires_approval_above: string;
  allowed_categories: string[];
  blocked_merchants: string[];
  currency: string;
  active: boolean;
  spent_today: string;
  created_at: string;
  updated_at: string;
}

export interface AgentReason {
  code: string;
  outcome: "pass" | "review" | "block";
  text: string;
  [key: string]: unknown;
}

export interface AgentAttempt {
  id: number;
  amount: string;
  currency: string;
  category: string;
  merchant_id: string;
  description: string;
  decision: Decision;
  reasons: AgentReason[];
  risk_score: number | null;
  counts_toward_spend: boolean;
  created_at: string;
}

export interface AgentVerdict {
  attempt: AgentAttempt;
  decision: Decision;
  reasons: AgentReason[];
  risk_score: number | null;
  counts_toward_spend: boolean;
  spent_today: string;
  remaining_today: string;
}

export interface SystemHealth {
  status: "ok" | "degraded";
  generated_at: string;
  model_version: string;
  processor: string;
  investigator_mode: string;
  investigator_setting: string;
  policy: { sensitivity: number; review_threshold: number; block_threshold: number };
  counters: Record<string, number>;
  error_rate: number;
  latency: Record<string, LatencySummary>;
  database: {
    transactions_24h: number;
    simulated_transactions_24h: number;
    evaluations_24h: number;
    evaluation_latency_24h: LatencySummary;
  };
  events: {
    pending: number;
    failed: number;
    dead: number;
    processed_24h: number;
    total: number;
    oldest_unprocessed_age_s: number | null;
    handlers: Record<string, string[]>;
  };
  idempotency: { active_keys: number; in_progress: number };
  notes: string[];
}

// Mirrors risk.engine.scoring.thresholds_for; used only for slider previews.
export function thresholdsFor(sensitivity: number): { review: number; block: number } {
  const s = Math.max(0, Math.min(100, Math.round(sensitivity)));
  return { review: Math.round(60 - 0.4 * s), block: Math.round(90 - 0.4 * s) };
}

export const DECISION_LABEL: Record<string, string> = {
  allow: "Allowed",
  review: "Review",
  block: "Blocked",
};

export const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  approved: "Approved",
  review: "Under review",
  blocked: "Blocked",
  completed: "Completed",
  failed: "Failed",
};

export const EVENT_LABEL: Record<string, string> = {
  "payment.created": "Payment created",
  "payment.risk_evaluated": "Risk evaluated",
  "payment.approved": "Approved",
  "payment.review_required": "Review required",
  "payment.blocked": "Blocked",
  "payment.completed": "Completed",
  "payment.failed": "Failed",
  "payment.reviewed": "Manually reviewed",
  "risk.policy_updated": "Policy updated",
  "agent.decision": "Agent decision",
};

export function bandFor(score: number | null | undefined): RiskBand {
  if (score === null || score === undefined) return "unknown";
  if (score >= 60) return "high";
  if (score >= 30) return "medium";
  return "low";
}

export function formatMoney(amount: string | number | null | undefined, currency = "USD"): string {
  const value = Number(amount ?? 0);
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 2 }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatMs(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "n/a";
  const number = Number(value);
  if (Number.isNaN(number)) return "n/a";
  if (number < 1) return `${number.toFixed(3)} ms`;
  if (number < 100) return `${number.toFixed(1)} ms`;
  return `${Math.round(number)} ms`;
}

export function humanCode(code: string): string {
  return code.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
