import { ArrowLeft } from "lucide-react";
import React, { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { DecisionPill, FactorList, Panel, ScoreBar, SimulationTag, StateBlock, StatusText } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime } from "../lib/formatters";
import { EVENT_LABEL, formatMoney, formatMs, humanCode } from "../lib/risk";

const EVENT_TONES = {
  processed: "bg-[color:var(--color-teal)]",
  pending: "bg-[#d9a441]",
  processing: "bg-[#d9a441]",
  failed: "bg-[#d9a441]",
  dead: "bg-[color:var(--color-urgent)]",
};

function ContextSummary({ snapshot }) {
  if (!snapshot) return null;
  const rows = [
    ["Account age", `${Number(snapshot.account_age_days ?? 0).toFixed(1)} days`],
    ["Successful history", `${snapshot.successful_txn_count ?? 0} payments`],
    ["Historical average", snapshot.historical_avg_amount ? formatMoney(snapshot.historical_avg_amount, snapshot.currency) : "none"],
    ["Amount ratio", snapshot.amount_ratio ? `${snapshot.amount_ratio}x` : "n/a"],
    ["Device seen before", `${snapshot.device_seen_count ?? 0} times`],
    ["Accounts on device (24h)", snapshot.accounts_on_device_24h ?? 1],
    ["Transactions (1h / 24h)", `${snapshot.txn_count_1h ?? 0} / ${snapshot.txn_count_24h ?? 0}`],
    ["Failed attempts (24h)", snapshot.failed_txn_count_24h ?? 0],
    ["Distinct devices / networks (24h)", `${snapshot.distinct_devices_24h ?? 0} / ${snapshot.distinct_ips_24h ?? 0}`],
    ["Local hour", snapshot.local_hour ?? "unknown"],
  ];
  return (
    <dl className="grid gap-x-6 gap-y-2 text-[13px] sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3 border-b border-black/5 py-1.5 dark:border-white/10">
          <dt className="text-[color:var(--text-muted)]">{label}</dt>
          <dd className="font-semibold tabular-nums text-[color:var(--color-ink)] dark:text-white">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function ReviewActions({ txn, onUpdated }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function resolve(approve) {
    setBusy(true);
    try {
      const updated = await apiFetch(`/risk/payments/${txn.public_id}/review`, { method: "POST", body: { approve, note } });
      toast.success(approve ? "Approved and sent to the processor." : "Rejected.");
      onUpdated(updated);
    } catch (error) {
      toast.error(error.message || "Review failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Manual review" description="This payment is held. Approving captures it through the sandbox processor; rejecting blocks it. Both are audited.">
      <label htmlFor="review-note" className="field-label">Reviewer note</label>
      <textarea id="review-note" className="field min-h-[80px]" value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} />
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="primary-button" disabled={busy} onClick={() => resolve(true)}>Approve</button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => resolve(false)}>Reject</button>
      </div>
    </Panel>
  );
}

export default function RiskTransactionDetailPage() {
  const { publicId } = useParams();
  usePageTitle(`Transaction ${publicId?.slice(0, 12) ?? ""}`);
  const { user } = useAuth();
  const detail = useApi(`/risk/payments/${publicId}`);
  const txn = detail.data;
  const evaluation = txn?.evaluation;

  return (
    <RiskShell
      title={txn ? formatMoney(txn.amount, txn.currency) : "Transaction"}
      subtitle={txn ? `${txn.public_id} · ${txn.merchant_id} · ${txn.payment_method}` : undefined}
      actions={
        <Link to="/risk/transactions" className="secondary-button">
          <ArrowLeft size={14} aria-hidden="true" /> Explorer
        </Link>
      }
    >
      <StateBlock loading={detail.loading && !txn} error={detail.error} onRetry={detail.refetch} skeleton="h-72">
        {txn ? (
          <>
            <PageSection className="grid gap-6 lg:grid-cols-3">
              <Panel className="lg:col-span-2" title="Decision">
                <div className="flex flex-wrap items-center gap-3">
                  <DecisionPill decision={txn.decision} />
                  <StatusText status={txn.status} />
                  {txn.is_simulation ? <SimulationTag groundTruth={txn.ground_truth_fraud} /> : null}
                  {txn.manual_review ? (
                    <span className="text-[12px] text-[color:var(--text-muted)]">manually {txn.manual_review.outcome}{txn.manual_review.note ? `: ${txn.manual_review.note}` : ""}</span>
                  ) : null}
                </div>
                <div className="mt-5 flex items-end gap-4">
                  <p className="text-[56px] font-black leading-none text-[color:var(--color-ink)] dark:text-white">{txn.risk_score ?? "–"}</p>
                  <div className="flex-1 pb-2">
                    <ScoreBar score={txn.risk_score} thresholds={evaluation?.thresholds} />
                  </div>
                </div>
                <dl className="mt-4 grid gap-2 text-[13px] sm:grid-cols-3">
                  <div><dt className="text-[color:var(--text-muted)]">Confidence</dt><dd className="font-semibold">{txn.confidence ?? "n/a"} <span className="font-normal text-[color:var(--text-muted)]">(heuristic)</span></dd></div>
                  <div><dt className="text-[color:var(--text-muted)]">Model</dt><dd className="font-semibold">{txn.model_version || "n/a"}</dd></div>
                  <div><dt className="text-[color:var(--text-muted)]">Evaluation time</dt><dd className="font-semibold">{formatMs(txn.evaluation_ms)}</dd></div>
                  <div><dt className="text-[color:var(--text-muted)]">Request id</dt><dd className="break-all font-mono text-[12px]">{txn.request_id || "n/a"}</dd></div>
                  <div><dt className="text-[color:var(--text-muted)]">Processor reference</dt><dd className="break-all font-mono text-[12px]">{txn.processor_reference || "none"}</dd></div>
                  <div><dt className="text-[color:var(--text-muted)]">Failure</dt><dd className="text-[12px]">{txn.failure_reason || "none"}</dd></div>
                </dl>
                {evaluation ? (
                  <pre className="mt-5 overflow-x-auto rounded-[16px] bg-[color:var(--color-night)] p-4 text-[12.5px] leading-6 text-white/90">
                    {evaluation.explanation}
                  </pre>
                ) : null}
              </Panel>
              <Panel title="Risk factors" description="Signed points, strongest first. The score is their sum, clamped to 0 to 100.">
                <FactorList factors={evaluation?.factors} />
              </Panel>
            </PageSection>

            {txn.status === "review" && user?.is_staff ? (
              <PageSection className="mt-6">
                <ReviewActions txn={txn} onUpdated={(updated) => detail.setData(updated)} />
              </PageSection>
            ) : null}

            <PageSection className="mt-6 grid gap-6 lg:grid-cols-2">
              <Panel title="Signals the engine saw" description="The frozen context stored with the evaluation, so the decision can be replayed.">
                <ContextSummary snapshot={evaluation?.context_snapshot} />
              </Panel>
              <Panel title="Event timeline" description="Append-only domain events, in sequence.">
                <ol className="relative ml-2 border-l border-black/10 pl-5 dark:border-white/15">
                  {txn.events.map((event) => (
                    <li key={event.event_id} className="relative pb-4 last:pb-0">
                      <span className={`absolute -left-[26px] top-1.5 h-3 w-3 rounded-full ${EVENT_TONES[event.status] || EVENT_TONES.pending}`} aria-hidden="true" />
                      <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                        {EVENT_LABEL[event.event_type] || event.event_type}
                        <span className="ml-2 text-[11px] font-normal uppercase tracking-[0.1em] text-[color:var(--text-muted)]">{event.status}{event.attempts > 1 ? ` · ${event.attempts} attempts` : ""}</span>
                      </p>
                      <p className="text-[12px] text-[color:var(--text-muted)]">
                        {formatDateTime(event.occurred_at)} · {event.event_id} · v{event.schema_version}
                        {event.processed_handlers?.length ? ` · handlers: ${event.processed_handlers.join(", ")}` : ""}
                      </p>
                      {event.payload?.summary ? <p className="mt-1 text-[13px]">{String(event.payload.summary)}</p> : null}
                      {event.last_error ? <p className="mt-1 text-[12px] text-[color:var(--color-urgent)]">{event.last_error}</p> : null}
                    </li>
                  ))}
                </ol>
              </Panel>
            </PageSection>

            <PageSection className="mt-6">
              <Panel title="Audit trail" description="Who did what, when and why. Records cannot be edited or deleted.">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-left text-[13px]">
                    <thead>
                      <tr className="text-[11px] uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
                        <th scope="col" className="pb-2 pr-3 font-semibold">When</th>
                        <th scope="col" className="pb-2 pr-3 font-semibold">Who</th>
                        <th scope="col" className="pb-2 pr-3 font-semibold">What</th>
                        <th scope="col" className="pb-2 pr-3 font-semibold">Why</th>
                        <th scope="col" className="pb-2 font-semibold">Decision</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-black/5 dark:divide-white/10">
                      {txn.audit.map((entry) => (
                        <tr key={entry.id} className="align-top">
                          <td className="py-2 pr-3 whitespace-nowrap text-[color:var(--text-muted)]">{formatDateTime(entry.created_at)}</td>
                          <td className="py-2 pr-3 font-mono text-[12px]">{entry.actor_label}</td>
                          <td className="py-2 pr-3 font-semibold">{humanCode(entry.action.replace(".", "_"))}</td>
                          <td className="py-2 pr-3">{entry.reason}</td>
                          <td className="py-2">{entry.decision ? <DecisionPill decision={entry.decision} /> : "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </PageSection>
          </>
        ) : null}
      </StateBlock>
    </RiskShell>
  );
}
