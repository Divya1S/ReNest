import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import PageSection from "../components/PageSection";
import { Panel, StateBlock, Toggle, TransactionTable } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useApi } from "../hooks/useApi";
import { useDebounce } from "../hooks/useDebounce";
import { usePageTitle } from "../hooks/usePageTitle";

const PAGE_SIZE = 24;

export default function RiskTransactionsPage() {
  usePageTitle("Transaction Explorer");
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");
  const debounced = useDebounce(query, 250);
  const urlQuery = params.get("q") || "";

  // The URL is the source of truth for the request; typing is debounced into
  // it with replace so the history does not grow by one entry per keystroke,
  // and Back/Forward bring the input back in line with the URL.
  useEffect(() => {
    if (debounced.trim() === urlQuery) return;
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (debounced.trim()) next.set("q", debounced.trim());
      else next.delete("q");
      next.delete("page");
      return next;
    }, { replace: true });
  }, [debounced, urlQuery, setParams]);
  useEffect(() => {
    setQuery((current) => (current.trim() === urlQuery ? current : urlQuery));
  }, [urlQuery]);

  const decision = params.get("decision") || "";
  const status = params.get("status") || "";
  const band = params.get("band") || "";
  const simulation = params.get("simulation") || "";
  const includeSim = params.get("include_simulation") === "1" || Boolean(simulation);
  const page = Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1);

  const search = new URLSearchParams();
  search.set("page", String(page));
  search.set("page_size", String(PAGE_SIZE));
  if (decision) search.set("decision", decision);
  if (status) search.set("status", status);
  if (band) search.set("band", band);
  if (simulation) search.set("simulation", simulation);
  if (includeSim) search.set("include_simulation", "1");
  if (urlQuery) search.set("q", urlQuery);

  const list = useApi(`/risk/payments?${search.toString()}`);
  const policy = useApi("/risk/policy");
  const thresholds = policy.data ? { review: policy.data.review_threshold, block: policy.data.block_threshold } : null;

  function setFilter(key, value) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    setParams(next);
  }

  const total = list.data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <RiskShell title="Transaction Explorer" subtitle="Search and filter every sandbox payment, then open one to see its factors, explanation, event timeline and audit trail.">
      <PageSection>
        <Panel>
          <div className="grid gap-3 md:grid-cols-5">
            <div className="md:col-span-2">
              <label htmlFor="tx-search" className="field-label">Search</label>
              <input id="tx-search" className="field" placeholder="transaction id, merchant, device or request id" value={query} onChange={(event) => setQuery(event.target.value)} />
            </div>
            <div>
              <label htmlFor="tx-decision" className="field-label">Decision</label>
              <select id="tx-decision" className="field" value={decision} onChange={(event) => setFilter("decision", event.target.value)}>
                <option value="">Any</option>
                <option value="allow">Allow</option>
                <option value="review">Review</option>
                <option value="block">Block</option>
              </select>
            </div>
            <div>
              <label htmlFor="tx-status" className="field-label">Status</label>
              <select id="tx-status" className="field" value={status} onChange={(event) => setFilter("status", event.target.value)}>
                <option value="">Any</option>
                <option value="completed">Completed</option>
                <option value="review">Under review</option>
                <option value="blocked">Blocked</option>
                <option value="failed">Failed</option>
                <option value="approved">Approved</option>
                <option value="pending">Pending</option>
              </select>
            </div>
            <div>
              <label htmlFor="tx-band" className="field-label">Risk band</label>
              <select id="tx-band" className="field" value={band} onChange={(event) => setFilter("band", event.target.value)}>
                <option value="">Any</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-4">
            <Toggle id="tx-sim" checked={includeSim} onChange={(checked) => setFilter("include_simulation", checked ? "1" : "")} label="Include simulated transactions" />
            {simulation ? (
              <button type="button" className="ghost-button" onClick={() => setFilter("simulation", "")}>
                Clear run filter ({simulation.slice(0, 12)}…)
              </button>
            ) : null}
            <span className="text-[13px] text-[color:var(--text-muted)]" aria-live="polite">
              {list.data ? `${total} matching` : ""}
            </span>
          </div>
        </Panel>
      </PageSection>

      <PageSection className="mt-6">
        <Panel>
          <StateBlock loading={list.loading && !list.data} error={list.error} onRetry={list.refetch} empty={list.data && list.data.results.length === 0} emptyText="No transactions match these filters." skeleton="h-64">
            {list.data ? <TransactionTable rows={list.data.results} thresholds={thresholds} caption="Matching transactions" /> : null}
          </StateBlock>
          {pages > 1 ? (
            <nav aria-label="Pagination" className="mt-4 flex items-center justify-between text-[13px]">
              <button type="button" className="secondary-button" disabled={page <= 1} onClick={() => setParams((prev) => { const next = new URLSearchParams(prev); next.set("page", String(page - 1)); return next; })}>
                Previous
              </button>
              <span className="text-[color:var(--text-muted)]">Page {page} of {pages}</span>
              <button type="button" className="secondary-button" disabled={page >= pages} onClick={() => setParams((prev) => { const next = new URLSearchParams(prev); next.set("page", String(page + 1)); return next; })}>
                Next
              </button>
            </nav>
          ) : null}
        </Panel>
      </PageSection>
    </RiskShell>
  );
}
