import { Bot, Plus } from "lucide-react";
import React, { useEffect, useState } from "react";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { DecisionPill, Panel, StateBlock } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime } from "../lib/formatters";
import { formatMoney } from "../lib/risk";

const EMPTY_POLICY = {
  name: "Dorm restock agent",
  daily_limit: "150",
  transaction_limit: "60",
  requires_approval_above: "40",
  allowed_categories: "supplies, food, books",
  blocked_merchants: "mkt_tickets",
};

const OUTCOME_TONES = {
  pass: "text-[color:var(--color-teal)]",
  review: "text-[#7a5a12] dark:text-[color:var(--color-box)]",
  block: "text-[color:var(--color-urgent)]",
};

function csv(value) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function PolicyForm({ onCreated }) {
  const [form, setForm] = useState(EMPTY_POLICY);
  const [busy, setBusy] = useState(false);
  const update = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await apiFetch("/risk/agents", {
        method: "POST",
        body: { ...form, allowed_categories: csv(form.allowed_categories), blocked_merchants: csv(form.blocked_merchants) },
      });
      toast.success(`Agent "${created.name}" created.`);
      onCreated(created);
    } catch (error) {
      toast.error(error.message || "Could not create the agent.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <label htmlFor="ag-name" className="field-label">Agent name</label>
        <input id="ag-name" className="field" value={form.name} onChange={update("name")} required maxLength={80} />
      </div>
      <div>
        <label htmlFor="ag-daily" className="field-label">Daily limit (USD)</label>
        <input id="ag-daily" type="number" min="0.01" step="0.01" className="field" value={form.daily_limit} onChange={update("daily_limit")} required />
      </div>
      <div>
        <label htmlFor="ag-txn" className="field-label">Per-transaction limit (USD)</label>
        <input id="ag-txn" type="number" min="0.01" step="0.01" className="field" value={form.transaction_limit} onChange={update("transaction_limit")} required />
      </div>
      <div>
        <label htmlFor="ag-approval" className="field-label">Requires approval above (USD)</label>
        <input id="ag-approval" type="number" min="0" step="0.01" className="field" value={form.requires_approval_above} onChange={update("requires_approval_above")} required />
      </div>
      <div>
        <label htmlFor="ag-categories" className="field-label">Allowed categories (comma separated, blank allows all)</label>
        <input id="ag-categories" className="field" value={form.allowed_categories} onChange={update("allowed_categories")} />
      </div>
      <div className="sm:col-span-2">
        <label htmlFor="ag-blocked" className="field-label">Blocked merchants (comma separated)</label>
        <input id="ag-blocked" className="field" value={form.blocked_merchants} onChange={update("blocked_merchants")} />
      </div>
      <div className="sm:col-span-2">
        <button type="submit" className="primary-button" disabled={busy}><Plus size={15} aria-hidden="true" /> {busy ? "Creating…" : "Create agent"}</button>
      </div>
    </form>
  );
}

function AttemptForm({ agent, onVerdict }) {
  const [form, setForm] = useState({ amount: "18.50", category: "supplies", merchant_id: "mkt_supplies", description: "Printer paper" });
  const [busy, setBusy] = useState(false);
  const update = (key) => (event) => setForm((prev) => ({ ...prev, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const verdict = await apiFetch(`/risk/agents/${agent.public_id}/transactions`, { method: "POST", body: form });
      onVerdict(verdict);
    } catch (error) {
      toast.error(error.message || "The attempt failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <div>
        <label htmlFor="at-amount" className="field-label">Amount ({agent.currency})</label>
        <input id="at-amount" type="number" step="0.01" className="field" value={form.amount} onChange={update("amount")} required />
      </div>
      <div>
        <label htmlFor="at-category" className="field-label">Category</label>
        <input id="at-category" className="field" value={form.category} onChange={update("category")} required maxLength={40} />
      </div>
      <div>
        <label htmlFor="at-merchant" className="field-label">Merchant</label>
        <input id="at-merchant" className="field" value={form.merchant_id} onChange={update("merchant_id")} required maxLength={80} />
      </div>
      <div>
        <label htmlFor="at-description" className="field-label">Description</label>
        <input id="at-description" className="field" value={form.description} onChange={update("description")} maxLength={200} />
      </div>
      <div className="sm:col-span-2">
        <button type="submit" className="primary-button" disabled={busy}><Bot size={15} aria-hidden="true" /> {busy ? "Evaluating…" : "Attempt purchase"}</button>
      </div>
    </form>
  );
}

function BudgetBar({ agent }) {
  const spent = Number(agent.spent_today);
  const limit = Number(agent.daily_limit) || 1;
  const pct = Math.min(100, (spent / limit) * 100);
  return (
    <div>
      <div className="flex justify-between text-[13px]">
        <span className="text-[color:var(--text-muted)]">Spent today</span>
        <span className="font-semibold tabular-nums">{formatMoney(spent, agent.currency)} of {formatMoney(agent.daily_limit, agent.currency)}</span>
      </div>
      <div role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={spent} aria-label="Daily budget used" className="mt-1 h-2.5 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]">
        <div className={`h-full rounded-full ${pct >= 100 ? "bg-[color:var(--color-urgent)]" : pct >= 75 ? "bg-[#d9a441]" : "bg-[color:var(--color-teal)]"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function AgentSandboxPage() {
  usePageTitle("Agent Payment Sandbox");
  const agents = useApi("/risk/agents?page_size=50");
  const [selectedId, setSelectedId] = useState(null);
  const [verdict, setVerdict] = useState(null);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    if (!selectedId && agents.data?.results?.length) setSelectedId(agents.data.results[0].public_id);
  }, [agents.data, selectedId]);

  const agent = agents.data?.results.find((item) => item.public_id === selectedId) || null;
  const attempts = useApi(agent ? `/risk/agents/${agent.public_id}/transactions?page_size=10` : null);

  function handleVerdict(result) {
    setVerdict(result);
    toast[result.decision === "allow" ? "success" : result.decision === "review" ? "message" : "error"](
      `${result.decision.toUpperCase()} · ${formatMoney(result.remaining_today, agent.currency)} left today`,
    );
    agents.refetch().catch(() => {});
    attempts.refetch().catch(() => {});
  }

  return (
    <RiskShell
      title="Agent Payment Sandbox"
      subtitle="Give an autonomous agent a spending policy, then watch every purchase attempt pass through limits, allow-lists, approval thresholds and the risk engine."
      actions={<button type="button" className="secondary-button" onClick={() => setShowForm((value) => !value)}>{showForm ? "Hide form" : "New agent"}</button>}
    >
      {showForm || (agents.data && agents.data.results.length === 0) ? (
        <PageSection>
          <Panel title="Create an agent policy" description="Limits are enforced server-side on every attempt. Approved and held purchases count toward the daily budget; blocked ones never do.">
            <PolicyForm
              onCreated={(created) => {
                setShowForm(false);
                agents.setData((prev) => ({
                  ...(prev || { count: 0, next: null, previous: null }),
                  count: (prev?.count || 0) + 1,
                  results: [created, ...(prev?.results || [])],
                }));
                setSelectedId(created.public_id);
                agents.refetch().catch(() => {});
              }}
            />
          </Panel>
        </PageSection>
      ) : null}

      <PageSection className="mt-6 grid gap-6 lg:grid-cols-3">
        <Panel title="Agents">
          <StateBlock loading={agents.loading && !agents.data} error={agents.error} onRetry={agents.refetch} empty={agents.data && agents.data.results.length === 0} emptyText="No agents yet. Create one above." skeleton="h-32">
            {agents.data ? (
              <ul className="space-y-2" aria-label="Agents">
                {agents.data.results.map((item) => (
                  <li key={item.public_id}>
                    <button
                      type="button"
                      aria-pressed={item.public_id === selectedId}
                      onClick={() => { setSelectedId(item.public_id); setVerdict(null); }}
                      className={`w-full rounded-[14px] border p-3 text-left text-[13px] transition-colors ${item.public_id === selectedId ? "border-[color:var(--color-tag)] bg-[color:var(--bg-tag-soft)]" : "border-black/10 hover:bg-[color:var(--color-surface-2)] dark:border-white/10"}`}
                    >
                      <span className="font-semibold text-[color:var(--color-ink)] dark:text-white">{item.name}</span>
                      <span className="block text-[12px] text-[color:var(--text-muted)]">{formatMoney(item.daily_limit, item.currency)}/day · {formatMoney(item.transaction_limit, item.currency)}/txn{item.active ? "" : " · inactive"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </StateBlock>
        </Panel>

        <div className="space-y-6 lg:col-span-2">
          {agent ? (
            <>
              <Panel title={agent.name} description={`Approval needed above ${formatMoney(agent.requires_approval_above, agent.currency)} · categories: ${agent.allowed_categories.length ? agent.allowed_categories.join(", ") : "any"} · blocked merchants: ${agent.blocked_merchants.length ? agent.blocked_merchants.join(", ") : "none"}`}>
                <BudgetBar agent={agent} />
                <div className="mt-5">
                  <AttemptForm agent={agent} onVerdict={handleVerdict} />
                </div>
              </Panel>
              <div aria-live="polite">
              {verdict ? (
                <Panel title="Verdict" as="div">
                  <div className="flex flex-wrap items-center gap-3">
                    <DecisionPill decision={verdict.decision} />
                    <span className="text-[13px] text-[color:var(--text-muted)]">
                      {verdict.risk_score !== null ? `risk score ${verdict.risk_score} · ` : ""}
                      {formatMoney(verdict.spent_today, agent.currency)} spent today · {formatMoney(verdict.remaining_today, agent.currency)} remaining
                    </span>
                  </div>
                  <ol className="mt-4 space-y-2 text-[13.5px]">
                    {verdict.reasons.map((reason) => (
                      <li key={reason.code} className="flex gap-3">
                        <span className={`w-14 shrink-0 text-[11px] font-bold uppercase tracking-[0.1em] ${OUTCOME_TONES[reason.outcome]}`}>{reason.outcome}</span>
                        <span>{reason.text}</span>
                      </li>
                    ))}
                  </ol>
                </Panel>
              ) : null}
              </div>
              <Panel title="Recent attempts">
                <StateBlock loading={attempts.loading && !attempts.data} error={attempts.error} onRetry={attempts.refetch} empty={attempts.data && attempts.data.results.length === 0} emptyText="No attempts yet." skeleton="h-24">
                  {attempts.data ? (
                    <ul className="divide-y divide-black/5 dark:divide-white/10 text-[13px]">
                      {attempts.data.results.map((item) => (
                        <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                          <div>
                            <span className="font-semibold">{formatMoney(item.amount, item.currency)}</span> · {item.category} · {item.merchant_id}
                            {item.description ? <span className="text-[color:var(--text-muted)]"> · {item.description}</span> : null}
                            <p className="text-[12px] text-[color:var(--text-muted)]">{formatDateTime(item.created_at)}{item.counts_toward_spend ? " · counted" : " · not counted"}</p>
                          </div>
                          <DecisionPill decision={item.decision} />
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </StateBlock>
              </Panel>
            </>
          ) : null}
        </div>
      </PageSection>
    </RiskShell>
  );
}
