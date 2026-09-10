import { FlaskConical, Play } from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { MetricTile, Panel, StateBlock } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useApi } from "../hooks/useApi";
import { useDebounce } from "../hooks/useDebounce";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime } from "../lib/formatters";
import { formatMoney, formatMs, formatPercent, humanCode, thresholdsFor } from "../lib/risk";

function ratio(value) {
  return value === null || value === undefined ? "n/a" : formatPercent(value);
}

function SweepChart({ sweep, sensitivity }) {
  const data = sweep.map((point) => ({
    sensitivity: point.sensitivity,
    detection: point.detection_rate === null ? null : Math.round(point.detection_rate * 1000) / 10,
    friction: point.friction_rate === null ? null : Math.round(point.friction_rate * 1000) / 10,
  }));
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
          <XAxis dataKey="sensitivity" tick={{ fontSize: 11 }} label={{ value: "sensitivity", position: "insideBottomRight", offset: -2, fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} unit="%" />
          <Tooltip formatter={(value, name) => [`${value}%`, name === "detection" ? "Fraud detected" : "Legitimate flagged"]} labelFormatter={(value) => `Sensitivity ${value}`} />
          <Legend formatter={(value) => (value === "detection" ? "Fraud detected (recall)" : "Legitimate flagged (friction)")} />
          <ReferenceLine x={sensitivity} stroke="#8a1d45" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="detection" stroke="#1e304a" strokeWidth={2.5} dot={{ r: 3 }} connectNulls />
          <Line type="monotone" dataKey="friction" stroke="#c96444" strokeWidth={2.5} dot={{ r: 3 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function FactorTally({ title, rows }) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <div>
      <p className="label-title">{title}</p>
      {rows.length === 0 ? <p className="mt-2 text-[13px] text-[color:var(--text-muted)]">none</p> : null}
      <ul className="mt-2 space-y-1.5">
        {rows.map((row) => (
          <li key={row.code} className="text-[13px]">
            <div className="flex justify-between"><span>{humanCode(row.code)}</span><span className="tabular-nums text-[color:var(--text-muted)]">{row.count}</span></div>
            <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]">
              <div className="h-full rounded-full bg-[color:var(--color-tag)]" style={{ width: `${(row.count / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RunResults({ run }) {
  const m = run.metrics;
  // Keyed by run id by the caller, so state starts fresh for every run.
  const [preview, setPreview] = useState(run.sensitivity);
  const debounced = useDebounce(preview, 250);
  const rescore = useApi(debounced === run.sensitivity ? null : `/risk/simulations/${run.public_id}/rescore?sensitivity=${debounced}`);
  const live = debounced === run.sensitivity ? m : rescore.data;
  const previewThresholds = thresholdsFor(preview);

  return (
    <>
      <PageSection>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <MetricTile label="Analyzed" value={m.analyzed} hint={`${m.labelled_fraud} labelled fraud · ${m.labelled_legitimate} legitimate`} />
          <MetricTile label="Blocked" value={m.blocked} hint={`${m.fraud_blocked} fraud · ${m.legitimate_blocked} legitimate`} tone="bad" />
          <MetricTile label="Held for review" value={m.reviewed} hint={`${m.fraud_reviewed} fraud · ${m.legitimate_reviewed} legitimate`} tone="warn" />
          <MetricTile label="Allowed" value={m.allowed} hint={`${m.fraud_missed} fraud missed`} tone="good" />
          <MetricTile label="Precision (flagged)" value={ratio(m.precision_flagged)} hint="share of held or blocked rows that were labelled fraud" />
          <MetricTile label="Recall (flagged)" value={ratio(m.recall_flagged)} hint="share of labelled fraud that was held or blocked" />
          <MetricTile label="False positive rate" value={ratio(m.false_positive_rate)} hint="legitimate rows held or blocked" />
          <MetricTile label="Engine latency" value={formatMs(m.latency.p95_ms)} hint={`P95 · avg ${formatMs(m.latency.mean_ms)} · P99 ${formatMs(m.latency.p99_ms)} · whole run ${formatMs(run.duration_ms)}`} />
        </div>
        <p className="mt-3 text-[12px] text-[color:var(--text-muted)]">
          Synthetic data with known labels, scored by the live engine ({m.model_version}) at sensitivity {run.sensitivity} (review {m.thresholds.review}, block {m.thresholds.block}), seed {run.seed}.
          Precision and recall refer to these labels only and say nothing about real traffic. {formatMoney(m.protected_amount)} of labelled fraud was blocked outright, {formatMoney(m.held_amount)} held; {formatMoney(m.friction_amount)} of legitimate spend was slowed down.
        </p>
      </PageSection>

      <PageSection className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Detection versus friction" description="Re-thresholding the same scores at every sensitivity. The dashed line marks this run.">
          <SweepChart sweep={m.sweep} sensitivity={run.sensitivity} />
        </Panel>
        <Panel title="Try another sensitivity" description="Stored scores are compared against new thresholds; no rules re-run.">
          <label htmlFor="rescore" className="field-label">Sensitivity: <strong>{preview}</strong></label>
          <input id="rescore" type="range" min={0} max={100} value={preview} onChange={(event) => setPreview(Number(event.target.value))} className="mt-2 w-full accent-[color:var(--color-tag)]" />
          <p className="mt-2 text-[12px] text-[color:var(--text-muted)]">review {previewThresholds.review} · block {previewThresholds.block}</p>
          <StateBlock loading={rescore.loading && !live} error={rescore.error} skeleton="h-32">
            {live ? (
              <dl className="mt-3 grid grid-cols-2 gap-2 text-[13px]" aria-live="polite">
                <div><dt className="text-[color:var(--text-muted)]">Blocked</dt><dd className="text-[20px] font-black">{live.blocked}</dd></div>
                <div><dt className="text-[color:var(--text-muted)]">Reviewed</dt><dd className="text-[20px] font-black">{live.reviewed}</dd></div>
                <div><dt className="text-[color:var(--text-muted)]">Recall (flagged)</dt><dd className="font-semibold">{ratio(live.recall_flagged)}</dd></div>
                <div><dt className="text-[color:var(--text-muted)]">False positives</dt><dd className="font-semibold">{ratio(live.false_positive_rate)}</dd></div>
              </dl>
            ) : null}
          </StateBlock>
        </Panel>
      </PageSection>

      <PageSection className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Risk bands">
          <ul className="space-y-2 text-[13px]">
            {[["Low", m.risk_bands.low], ["Medium", m.risk_bands.medium], ["High", m.risk_bands.high]].map(([label, count]) => (
              <li key={label} className="flex justify-between"><span>{label}</span><span className="font-semibold tabular-nums">{count}</span></li>
            ))}
          </ul>
        </Panel>
        <Panel title="Top signals">
          <div className="grid gap-5 sm:grid-cols-2">
            <FactorTally title="In labelled fraud" rows={m.top_factors_fraud} />
            <FactorTally title="In legitimate rows" rows={m.top_factors_legitimate} />
          </div>
        </Panel>
        <Panel title="Inspect the rows">
          <p className="text-[13px] text-[color:var(--text-muted)]">Every synthetic transaction is stored with its evaluation, factors and label.</p>
          <Link to={`/risk/transactions?simulation=${run.public_id}&include_simulation=1`} className="primary-button mt-4">
            Open {run.transaction_count} transactions
          </Link>
        </Panel>
      </PageSection>
    </>
  );
}

export default function FraudLabPage() {
  usePageTitle("Fraud Lab");
  const catalogue = useApi("/risk/scenarios");
  const runs = useApi("/risk/simulations?page_size=6");
  const [scenario, setScenario] = useState("account_takeover");
  const [count, setCount] = useState(1000);
  const [sensitivity, setSensitivity] = useState(null);
  const [seed, setSeed] = useState("");
  const [running, setRunning] = useState(false);
  const [current, setCurrent] = useState(null);

  useEffect(() => {
    if (catalogue.data && sensitivity === null) setSensitivity(catalogue.data.current_sensitivity);
  }, [catalogue.data, sensitivity]);

  const selected = useMemo(() => catalogue.data?.scenarios.find((s) => s.key === scenario), [catalogue.data, scenario]);
  const thresholds = thresholdsFor(sensitivity ?? 50);

  async function run(event) {
    event.preventDefault();
    setRunning(true);
    try {
      const body = { scenario, count: Number(count), sensitivity: Number(sensitivity ?? 50) };
      if (seed) body.seed = Number(seed);
      const created = await apiFetch("/risk/simulations", { method: "POST", body, timeout: 60_000 });
      setCurrent(created);
      runs.refetch().catch(() => {});
      toast.success(`${created.metrics.analyzed} transactions scored in ${formatMs(created.duration_ms)}.`);
    } catch (error) {
      toast.error(error.message || "The scenario could not run.");
    } finally {
      setRunning(false);
    }
  }

  async function openRun(publicId) {
    try {
      setCurrent(await apiFetch(`/risk/simulations/${publicId}`));
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      toast.error(error.message || "Could not load the run.");
    }
  }

  return (
    <RiskShell title="Fraud Lab" subtitle="Generate labelled synthetic attacks, score them with the real decision engine, and measure what it caught and what it slowed down.">
      <PageSection>
        <Panel title="Run a scenario" description={selected?.description}>
          <StateBlock loading={catalogue.loading && !catalogue.data} error={catalogue.error} onRetry={catalogue.refetch} skeleton="h-40">
            {catalogue.data ? (
              <form onSubmit={run} className="grid gap-4 md:grid-cols-[1.6fr_1fr]">
                <fieldset>
                  <legend className="field-label">Scenario</legend>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {catalogue.data.scenarios.map((item) => (
                      <label key={item.key} className={`cursor-pointer rounded-[14px] border p-3 text-[13px] transition-colors focus-within:ring-2 focus-within:ring-[color:var(--color-tag)] focus-within:ring-offset-2 ${scenario === item.key ? "border-[color:var(--color-tag)] bg-[color:var(--bg-tag-soft)]" : "border-black/10 hover:bg-[color:var(--color-surface-2)] dark:border-white/10"}`}>
                        <input type="radio" name="scenario" value={item.key} checked={scenario === item.key} onChange={() => setScenario(item.key)} className="sr-only" />
                        <span className="font-semibold text-[color:var(--color-ink)] dark:text-white">{item.title}</span>
                        <span className="block text-[12px] text-[color:var(--text-muted)]">{item.fraud_share ? `${Math.round(item.fraud_share * 100)}% labelled fraud` : "no fraud, measures friction"}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="space-y-3">
                  <div>
                    <label htmlFor="lab-count" className="field-label">Transactions</label>
                    <input id="lab-count" type="number" className="field" min={catalogue.data.min_count} max={catalogue.data.max_count} value={count} onChange={(event) => setCount(event.target.value)} />
                  </div>
                  <div>
                    <label htmlFor="lab-sensitivity" className="field-label">Sensitivity: <strong>{sensitivity ?? 50}</strong> <span className="font-normal text-[color:var(--text-muted)]">(review {thresholds.review}, block {thresholds.block})</span></label>
                    <input id="lab-sensitivity" type="range" min={0} max={100} value={sensitivity ?? 50} onChange={(event) => setSensitivity(Number(event.target.value))} className="mt-1 w-full accent-[color:var(--color-tag)]" />
                  </div>
                  <div>
                    <label htmlFor="lab-seed" className="field-label">Seed (optional, for a reproducible run)</label>
                    <input id="lab-seed" type="number" className="field" min={1} value={seed} onChange={(event) => setSeed(event.target.value)} placeholder="random" />
                  </div>
                  <button type="submit" className="primary-button w-full" disabled={running}>
                    {running ? <FlaskConical size={15} className="animate-pulse" aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
                    {running ? "Scoring…" : "Run scenario"}
                  </button>
                </div>
              </form>
            ) : null}
          </StateBlock>
        </Panel>
      </PageSection>

      <div aria-live="polite">
        {current ? (
          <div className="mt-6">
            <h2 className="mb-3 text-[20px] font-black text-[color:var(--color-ink)] dark:text-white">
              {current.scenario_title} <span className="text-[14px] font-normal text-[color:var(--text-muted)]">{current.public_id} · {formatDateTime(current.created_at)}</span>
            </h2>
            <RunResults key={current.public_id} run={current} />
          </div>
        ) : null}
      </div>

      <PageSection className="mt-6">
        <Panel title="Recent runs" description="The last six runs per account are kept; older ones and their rows are removed.">
          <StateBlock loading={runs.loading && !runs.data} error={runs.error} onRetry={runs.refetch} empty={runs.data && runs.data.results.length === 0} emptyText="No runs yet. Pick a scenario above." skeleton="h-32">
            {runs.data ? (
              <ul className="divide-y divide-black/5 dark:divide-white/10">
                {runs.data.results.map((item) => (
                  <li key={item.public_id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-[13px]">
                    <div>
                      <p className="font-semibold text-[color:var(--color-ink)] dark:text-white">{item.scenario_title} · n={item.transaction_count} · sensitivity {item.sensitivity}</p>
                      <p className="text-[color:var(--text-muted)]">
                        {formatDateTime(item.created_at)} · blocked {item.headline.blocked} · reviewed {item.headline.reviewed} · recall {ratio(item.headline.recall_flagged)} · FPR {ratio(item.headline.false_positive_rate)}
                      </p>
                    </div>
                    <button type="button" className="secondary-button" onClick={() => openRun(item.public_id)}>View</button>
                  </li>
                ))}
              </ul>
            ) : null}
          </StateBlock>
        </Panel>
      </PageSection>
    </RiskShell>
  );
}
