import { RefreshCw } from "lucide-react";
import React, { useEffect } from "react";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { MetricTile, Panel, StateBlock } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime } from "../lib/formatters";
import { EVENT_LABEL, formatMs, formatPercent, humanCode } from "../lib/risk";

const POLL_MS = 15_000;
const LATENCY_LABELS = {
  risk_evaluation_ms: "Risk evaluation (engine)",
  payment_create_ms: "Payment create (service)",
  request_ms: "HTTP request (/api/risk)",
  event_dispatch_ms: "Event dispatch pass",
  simulation_ms: "Fraud Lab run",
};

export default function RiskHealthPage() {
  usePageTitle("Risk System Health");
  const { user } = useAuth();
  const health = useApi("/risk/health");
  const dead = useApi("/risk/events?status=dead&page_size=10");
  const { refetch } = health;

  useEffect(() => {
    const timer = setInterval(() => refetch().catch(() => {}), POLL_MS);
    return () => clearInterval(timer);
  }, [refetch]);

  const data = health.data;
  const status = data?.status;

  async function dispatchNow() {
    try {
      const result = await apiFetch("/risk/events/dispatch", { method: "POST" });
      toast.success(`Processed ${result.processed}, failed ${result.failed}.`);
      refetch().catch(() => {});
      dead.refetch().catch(() => {});
    } catch (error) {
      toast.error(error.message || "Dispatch failed.");
    }
  }

  async function replay(eventId) {
    try {
      await apiFetch(`/risk/events/${eventId}/replay`, { method: "POST" });
      toast.success("Event replayed.");
      refetch().catch(() => {});
      dead.refetch().catch(() => {});
    } catch (error) {
      toast.error(error.message || "Replay failed.");
    }
  }

  return (
    <RiskShell
      title="System Health"
      subtitle="Request counts, error rate, decision counters, latency percentiles and the event queue. Cache-backed counters reset with the process; database figures are exact."
      actions={
        <>
          <button type="button" className="secondary-button" onClick={() => refetch().catch(() => {})}><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
          {user?.is_staff ? <button type="button" className="primary-button" onClick={dispatchNow}>Dispatch events now</button> : null}
        </>
      }
    >
      <PageSection>
        <StateBlock loading={health.loading && !data} error={health.error} onRetry={refetch} skeleton="h-40">
          {data ? (
            <>
              <div className={`mb-4 inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[12px] font-semibold uppercase tracking-[0.12em] ${status === "ok" ? "bg-[color:var(--color-teal-soft)] text-[color:var(--color-teal)]" : "bg-[rgba(201,100,68,0.14)] text-[color:var(--color-urgent)]"}`}>
                <span className={`h-2 w-2 rounded-full ${status === "ok" ? "bg-[color:var(--color-teal)]" : "bg-[color:var(--color-urgent)]"}`} aria-hidden="true" />
                {status === "ok" ? "Healthy" : "Degraded"} · {formatDateTime(data.generated_at)}
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <MetricTile label="Requests" value={data.counters.requests_total} hint="since process start (cache counter)" />
                <MetricTile label="Error rate" value={formatPercent(data.error_rate)} hint={`${data.counters.errors_total} server errors`} tone={data.error_rate > 0.05 ? "bad" : "good"} />
                <MetricTile label="Decisions" value={`${data.counters.decisions_allow_total} / ${data.counters.decisions_review_total} / ${data.counters.decisions_block_total}`} hint="allow / review / block (event-counted)" />
                <MetricTile label="Dead-lettered events" value={data.events.dead} hint={`${data.events.pending} pending · ${data.events.failed} retrying · ${data.events.processed_24h} processed in 24h`} tone={data.events.dead ? "bad" : "good"} />
              </div>
            </>
          ) : null}
        </StateBlock>
      </PageSection>

      {data ? (
        <>
          <PageSection className="mt-6 grid gap-6 lg:grid-cols-2">
            <Panel title="Latency percentiles" description="Bounded reservoir of the last 500 samples per metric, in milliseconds.">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-[13px]">
                  <thead>
                    <tr className="text-[11px] uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
                      <th scope="col" className="pb-2 pr-3 font-semibold">Metric</th>
                      <th scope="col" className="pb-2 pr-3 text-right font-semibold">n</th>
                      <th scope="col" className="pb-2 pr-3 text-right font-semibold">avg</th>
                      <th scope="col" className="pb-2 pr-3 text-right font-semibold">P50</th>
                      <th scope="col" className="pb-2 pr-3 text-right font-semibold">P95</th>
                      <th scope="col" className="pb-2 text-right font-semibold">P99</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-black/5 dark:divide-white/10">
                    {Object.entries(data.latency).map(([name, summary]) => (
                      <tr key={name}>
                        <td className="py-2 pr-3">{LATENCY_LABELS[name] || name}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{summary.count}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatMs(summary.mean_ms)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatMs(summary.p50_ms)}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{formatMs(summary.p95_ms)}</td>
                        <td className="py-2 text-right tabular-nums">{formatMs(summary.p99_ms)}</td>
                      </tr>
                    ))}
                    <tr className="bg-[color:var(--color-surface-2)]">
                      <td className="py-2 pr-3 font-semibold">Risk evaluation, last 24h (database)</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{data.database.evaluation_latency_24h.count}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatMs(data.database.evaluation_latency_24h.mean_ms)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatMs(data.database.evaluation_latency_24h.p50_ms)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{formatMs(data.database.evaluation_latency_24h.p95_ms)}</td>
                      <td className="py-2 text-right tabular-nums">{formatMs(data.database.evaluation_latency_24h.p99_ms)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="Counters">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[13px]">
                {Object.entries(data.counters).map(([name, value]) => (
                  <React.Fragment key={name}>
                    <dt className="text-[color:var(--text-muted)]">{humanCode(name.replace(/_total$/, ""))}</dt>
                    <dd className="text-right font-semibold tabular-nums">{value}</dd>
                  </React.Fragment>
                ))}
              </dl>
            </Panel>
          </PageSection>

          <PageSection className="mt-6 grid gap-6 lg:grid-cols-3">
            <Panel title="Configuration">
              <dl className="space-y-1.5 text-[13px]">
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Model</dt><dd className="font-semibold">{data.model_version}</dd></div>
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Processor</dt><dd className="font-semibold">{data.processor}</dd></div>
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Investigator</dt><dd className="font-semibold">{data.investigator_mode} ({data.investigator_setting})</dd></div>
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Sensitivity</dt><dd className="font-semibold">{data.policy.sensitivity} (review {data.policy.review_threshold}, block {data.policy.block_threshold})</dd></div>
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Transactions 24h</dt><dd className="font-semibold">{data.database.transactions_24h} real · {data.database.simulated_transactions_24h} simulated</dd></div>
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Idempotency keys</dt><dd className="font-semibold">{data.idempotency.active_keys} active · {data.idempotency.in_progress} in progress</dd></div>
                <div className="flex justify-between"><dt className="text-[color:var(--text-muted)]">Oldest unprocessed event</dt><dd className="font-semibold">{data.events.oldest_unprocessed_age_s === null ? "none" : `${data.events.oldest_unprocessed_age_s}s`}</dd></div>
              </dl>
            </Panel>
            <Panel title="Event handlers" description="Registered subscribers per event type. Each is idempotent and retried with backoff before dead-lettering.">
              <ul className="space-y-1.5 text-[13px]">
                {Object.entries(data.events.handlers).map(([type, handlers]) => (
                  <li key={type} className="flex justify-between gap-3"><span>{EVENT_LABEL[type] || type}</span><span className="text-right text-[color:var(--text-muted)]">{handlers.join(", ")}</span></li>
                ))}
              </ul>
            </Panel>
            <Panel title="Dead letters" description="Events whose handlers failed five times. Nothing is dropped silently.">
              <StateBlock loading={dead.loading && !dead.data} error={dead.error} onRetry={dead.refetch} empty={dead.data && dead.data.results.length === 0} emptyText="No dead-lettered events." skeleton="h-20">
                {dead.data ? (
                  <ul className="space-y-2 text-[13px]">
                    {dead.data.results.map((event) => (
                      <li key={event.event_id} className="rounded-[12px] border border-[rgba(201,100,68,0.3)] p-3">
                        <p className="font-semibold">{EVENT_LABEL[event.event_type] || event.event_type} · {event.entity_id}</p>
                        <p className="text-[12px] text-[color:var(--color-urgent)]">{event.last_error}</p>
                        {user?.is_staff ? <button type="button" className="ghost-button mt-2 !py-1" onClick={() => replay(event.event_id)}>Replay</button> : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </StateBlock>
            </Panel>
          </PageSection>
          <p className="mt-4 text-[12px] text-[color:var(--text-muted)]">{data.notes.join(" ")}</p>
        </>
      ) : null}
    </RiskShell>
  );
}
