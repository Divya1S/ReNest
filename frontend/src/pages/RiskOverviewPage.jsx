import { Activity, Gauge, ListChecks, ShieldAlert, ShieldCheck, Timer } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { MetricTile, Panel, StateBlock, Toggle, TransactionTable } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatMoney, formatMs, formatPercent, thresholdsFor } from "../lib/risk";

const FEED_POLL_MS = 10_000;
const BEHAVIOURS = [
  ["succeed", "Succeed"],
  ["fail_transient_once", "Transient failure, then succeed"],
  ["fail_transient", "Transient failure every attempt"],
  ["decline", "Processor decline"],
];

function DistributionBars({ distribution }) {
  const total = distribution.low + distribution.medium + distribution.high || 1;
  const rows = [
    ["Low", distribution.low, "bg-[color:var(--color-teal)]", "0 to 29"],
    ["Medium", distribution.medium, "bg-[#d9a441]", "30 to 59"],
    ["High", distribution.high, "bg-[color:var(--color-urgent)]", "60 to 100"],
  ];
  return (
    <ul className="space-y-3">
      {rows.map(([label, count, colour, range]) => (
        <li key={label}>
          <div className="flex items-baseline justify-between text-[13px]">
            <span className="font-semibold text-[color:var(--color-ink)] dark:text-white">
              {label} <span className="font-normal text-[color:var(--text-muted)]">({range})</span>
            </span>
            <span className="tabular-nums text-[color:var(--text-muted)]">
              {count} · {formatPercent(count / total, 0)}
            </span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]">
            <div className={`h-full rounded-full ${colour}`} style={{ width: `${(count / total) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function SensitivityPanel({ policy, canEdit, onSaved }) {
  const [value, setValue] = useState(policy?.sensitivity ?? 50);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (policy) setValue(policy.sensitivity);
  }, [policy]);
  const preview = thresholdsFor(value);

  async function save() {
    setSaving(true);
    try {
      const updated = await apiFetch("/risk/policy", { method: "PUT", body: { sensitivity: value, reason: "Adjusted from the Risk Overview" } });
      toast.success(`Sensitivity saved at ${updated.sensitivity}.`);
      onSaved(updated);
    } catch (error) {
      toast.error(error.message || "Could not save the policy.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel
      title="Risk sensitivity"
      description="One knob moves both thresholds. Higher sensitivity blocks more fraud and adds more friction for honest customers."
    >
      <label htmlFor="sensitivity" className="field-label">
        Sensitivity: <strong>{value}</strong>
      </label>
      <input
        id="sensitivity"
        type="range"
        min={0}
        max={100}
        step={1}
        value={value}
        onChange={(event) => setValue(Number(event.target.value))}
        disabled={!canEdit}
        className="mt-2 w-full accent-[color:var(--color-tag)]"
        aria-describedby="sensitivity-preview"
      />
      <p id="sensitivity-preview" className="mt-3 text-[13px] text-[color:var(--text-muted)]">
        Review at score <strong>{preview.review}</strong>, block at <strong>{preview.block}</strong>.
        {policy ? (
          <>
            {" "}
            Live policy: {policy.sensitivity} (review {policy.review_threshold}, block {policy.block_threshold}).
          </>
        ) : null}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {canEdit ? (
          <button type="button" className="primary-button" onClick={save} disabled={saving || (policy && value === policy.sensitivity)}>
            {saving ? "Saving…" : "Apply to live policy"}
          </button>
        ) : (
          <p className="text-[12px] text-[color:var(--text-muted)]">Only staff can change the live policy. Use the Fraud Lab to explore any sensitivity on synthetic data.</p>
        )}
        <Link to="/risk/lab" className="secondary-button">
          Open Fraud Lab
        </Link>
      </div>
    </Panel>
  );
}

function QuickPaymentForm({ onCreated }) {
  const [form, setForm] = useState({ merchant_id: "mkt_textbooks", amount: "42.50", device_id: "my-laptop", sandbox_behavior: "succeed" });
  const [busy, setBusy] = useState(false);
  const update = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const key = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
      const created = await apiFetch("/risk/payments", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: { ...form, currency: "USD", payment_method: "card", merchant_category: "books" },
      });
      toast.success(`${(created.decision || "pending").toUpperCase()} · score ${created.risk_score ?? "n/a"} · ${created.status}`);
      onCreated(created);
    } catch (error) {
      toast.error(error.message || "Payment failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Create a sandbox payment" description="Runs the real pipeline: idempotency, risk evaluation, events, audit and the sandbox processor. No money moves.">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="qp-merchant" className="field-label">Merchant</label>
          <input id="qp-merchant" className="field" value={form.merchant_id} onChange={update("merchant_id")} required maxLength={80} />
        </div>
        <div>
          <label htmlFor="qp-amount" className="field-label">Amount (USD)</label>
          <input id="qp-amount" className="field" type="number" min="0.01" step="0.01" value={form.amount} onChange={update("amount")} required />
        </div>
        <div>
          <label htmlFor="qp-device" className="field-label">Device id</label>
          <input id="qp-device" className="field" value={form.device_id} onChange={update("device_id")} maxLength={80} />
        </div>
        <div>
          <label htmlFor="qp-behaviour" className="field-label">Processor behaviour</label>
          <select id="qp-behaviour" className="field" value={form.sandbox_behavior} onChange={update("sandbox_behavior")}>
            {BEHAVIOURS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <div className="sm:col-span-2">
          <button type="submit" className="primary-button" disabled={busy}>
            {busy ? "Evaluating…" : "Submit payment"}
          </button>
        </div>
      </form>
    </Panel>
  );
}

const SeriesTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-2xl border border-black/8 bg-[color:var(--color-surface)] p-3 shadow-xl dark:border-white/8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">{label}</p>
      {payload.map((entry) => (
        <p key={entry.dataKey} className="mt-1 text-[13px] font-bold" style={{ color: entry.color }}>
          {entry.dataKey}: {entry.value}
        </p>
      ))}
    </div>
  );
};

export default function RiskOverviewPage() {
  usePageTitle("Risk Overview");
  const { user } = useAuth();
  const [hours, setHours] = useState(24);
  const [includeSim, setIncludeSim] = useState(false);
  const simFlag = includeSim ? 1 : 0;

  const overview = useApi(`/risk/overview?hours=${hours}&include_simulation=${simFlag}`);
  const policy = useApi("/risk/policy");
  const feed = useApi(`/risk/feed?limit=12&include_simulation=${simFlag}`);
  const { refetch: refetchFeed } = feed;

  useEffect(() => {
    const timer = setInterval(() => {
      refetchFeed().catch(() => {});
    }, FEED_POLL_MS);
    return () => clearInterval(timer);
  }, [refetchFeed]);

  const data = overview.data;
  const thresholds = data?.policy ? { review: data.policy.review_threshold, block: data.policy.block_threshold } : null;

  function refreshAll() {
    overview.refetch().catch(() => {});
    refetchFeed().catch(() => {});
  }

  return (
    <RiskShell
      title="Risk Overview"
      subtitle="Every number on this page is counted from stored sandbox transactions. Synthetic Fraud Lab rows are excluded unless you include them."
      actions={
        <>
          <label htmlFor="overview-window" className="sr-only">Time window</label>
          <select id="overview-window" className="field !w-auto" value={hours} onChange={(event) => setHours(Number(event.target.value))}>
            <option value={1}>Last hour</option>
            <option value={24}>Last 24 hours</option>
            <option value={168}>Last 7 days</option>
            <option value={720}>Last 30 days</option>
          </select>
          <Toggle id="overview-sim" checked={includeSim} onChange={setIncludeSim} label="Include simulated" />
        </>
      }
    >
      <PageSection>
        <StateBlock loading={overview.loading && !data} error={overview.error} onRetry={overview.refetch} skeleton="h-40">
          {data ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              <MetricTile label="Transactions" value={data.totals.transactions} hint={`${data.totals.completed} completed, ${data.totals.failed} failed`} icon={Activity} />
              <MetricTile label="Blocked" value={data.totals.blocked} hint={`${formatPercent(data.rates.block_rate)} of traffic · ${formatMoney(data.amounts.blocked)} declined`} icon={ShieldAlert} tone={data.totals.blocked ? "bad" : "default"} />
              <MetricTile label="Review queue (all time)" value={data.totals.review_queue} hint={`${data.totals.reviewed} held in this window · ${formatMoney(data.amounts.held)} on hold`} icon={ListChecks} tone={data.totals.review_queue ? "warn" : "default"} />
              <MetricTile label="Allowed" value={data.totals.allowed} hint={`${formatMoney(data.amounts.completed)} captured`} icon={ShieldCheck} tone="good" />
              <MetricTile label="Avg risk score" value={data.rates.average_risk_score ?? "–"} hint={`sensitivity ${data.policy.sensitivity}`} icon={Gauge} />
              <MetricTile label="Eval latency P95" value={formatMs(data.evaluation_latency.p95_ms)} hint={`P50 ${formatMs(data.evaluation_latency.p50_ms)} · P99 ${formatMs(data.evaluation_latency.p99_ms)} · ${data.evaluation_latency.count} evaluations`} icon={Timer} />
            </div>
          ) : null}
        </StateBlock>
        {data?.includes_simulation && data.simulated_rows ? (
          <p className="mt-3 text-[12px] text-[color:var(--text-muted)]">
            Includes {data.simulated_rows} simulated transactions from the Fraud Lab. They are labelled in every table.
          </p>
        ) : null}
      </PageSection>

      <PageSection className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Decisions over time" description="Allow, review and block counts per bucket." className="lg:col-span-2">
          <StateBlock loading={overview.loading && !data} error={overview.error} empty={data && data.series.length === 0} emptyText="No transactions in this window yet. Create one below or run a Fraud Lab scenario." skeleton="h-64">
            {data ? (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.series} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
                    <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={(value) => value.slice(-5)} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip content={<SeriesTooltip />} />
                    <Bar dataKey="allow" stackId="a" fill="#1e304a" />
                    <Bar dataKey="review" stackId="a" fill="#d9a441" />
                    <Bar dataKey="block" stackId="a" fill="#c96444" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : null}
          </StateBlock>
        </Panel>
        <Panel title="Risk distribution" description="Scores bucketed into low, medium and high.">
          <StateBlock loading={overview.loading && !data} error={overview.error} skeleton="h-40">
            {data ? <DistributionBars distribution={data.distribution} /> : null}
          </StateBlock>
        </Panel>
      </PageSection>

      <PageSection className="mt-6 grid gap-6 lg:grid-cols-2">
        <SensitivityPanel policy={policy.data} canEdit={Boolean(user?.is_staff)} onSaved={(updated) => { policy.setData(updated); refreshAll(); }} />
        <QuickPaymentForm onCreated={refreshAll} />
      </PageSection>

      <PageSection className="mt-6">
        <Panel
          title="Live transaction feed"
          description={`Refreshes every ${FEED_POLL_MS / 1000} seconds.`}
          actions={
            <Link to="/risk/transactions" className="secondary-button">
              Open explorer
            </Link>
          }
        >
          <StateBlock loading={feed.loading && !feed.data} error={feed.error} onRetry={refetchFeed} empty={feed.data && feed.data.results.length === 0} emptyText="No transactions yet." skeleton="h-48">
            {feed.data ? <TransactionTable rows={feed.data.results} thresholds={thresholds} caption="Most recent transactions" /> : null}
          </StateBlock>
        </Panel>
      </PageSection>
    </RiskShell>
  );
}
