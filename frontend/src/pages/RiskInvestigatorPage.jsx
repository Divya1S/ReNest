import { Search, Sparkles } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { Panel, StateBlock } from "../components/risk/primitives";
import RiskShell from "../components/risk/RiskShell";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime } from "../lib/formatters";
import { formatMs, humanCode } from "../lib/risk";

function Answer({ record }) {
  return (
    <article aria-label="Investigation result" className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-[color:var(--text-muted)]">
        <span className="rounded-full bg-[color:var(--color-surface-2)] px-2.5 py-1 font-semibold uppercase tracking-[0.1em]">intent: {humanCode(record.intent)}</span>
        <span className="rounded-full bg-[color:var(--color-surface-2)] px-2.5 py-1 font-semibold uppercase tracking-[0.1em]">{record.mode === "llm" ? "narrated by model" : "deterministic"}</span>
        <span>{record.model_version} · {formatMs(record.latency_ms)}</span>
      </div>
      <p className="text-[16px] leading-7 text-[color:var(--color-ink)] dark:text-white">{record.answer}</p>
      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <p className="label-title">From the data</p>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[14px]">
            {record.facts.map((fact) => <li key={fact}>{fact}</li>)}
          </ul>
        </div>
        <div>
          <p className="label-title">Possible interpretation</p>
          {record.inferences.length ? (
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[14px] text-[color:var(--text-muted)]">
              {record.inferences.map((item) => <li key={item}>{item}</li>)}
            </ul>
          ) : (
            <p className="mt-2 text-[14px] text-[color:var(--text-muted)]">No inference drawn; the facts speak for themselves.</p>
          )}
        </div>
      </div>
      <div>
        <p className="label-title">Evidence</p>
        {record.evidence.length ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {record.evidence.map((id) => (
              <li key={id}>
                <Link to={`/risk/transactions/${id}`} className="rounded-full border border-[color:var(--color-line)] px-3 py-1 font-mono text-[12px] hover:border-[color:var(--color-tag)]">{id}</Link>
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-[13px] text-[color:var(--text-muted)]">No individual transactions referenced.</p>}
      </div>
      <details className="rounded-[14px] bg-[color:var(--color-surface-2)] p-3 text-[13px]">
        <summary className="cursor-pointer font-semibold">Queries the investigator ran</summary>
        <ul className="mt-2 space-y-1 font-mono text-[12px]">
          {record.queries.map((query) => (
            <li key={query.name}>{query.name}({Object.entries(query.params).map(([key, value]) => `${key}=${value}`).join(", ")})</li>
          ))}
        </ul>
      </details>
    </article>
  );
}

export default function RiskInvestigatorPage() {
  usePageTitle("Risk Investigator");
  const info = useApi("/risk/investigator");
  const history = useApi("/risk/investigations?page_size=8");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState(null);

  async function ask(text) {
    const trimmed = text.trim();
    if (trimmed.length < 3) {
      toast.error("Ask a fuller question.");
      return;
    }
    setBusy(true);
    try {
      const record = await apiFetch("/risk/investigations", { method: "POST", body: { question: trimmed } });
      setCurrent(record);
      setQuestion(trimmed);
      history.refetch().catch(() => {});
    } catch (error) {
      toast.error(error.message || "The investigator could not answer.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RiskShell title="Risk Investigator" subtitle="Ask a question in plain language. It is mapped to an intent, answered with a fixed set of queries against your data, and explained with facts kept separate from interpretation.">
      <PageSection>
        <Panel>
          <form onSubmit={(event) => { event.preventDefault(); ask(question); }} className="flex flex-col gap-3 sm:flex-row">
            <div className="flex-1">
              <label htmlFor="inv-question" className="sr-only">Question</label>
              <input id="inv-question" className="field" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Why did blocked transactions increase today?" maxLength={500} />
            </div>
            <button type="submit" className="primary-button" disabled={busy}>
              <Search size={15} aria-hidden="true" /> {busy ? "Investigating…" : "Investigate"}
            </button>
          </form>
          <StateBlock loading={info.loading && !info.data} error={info.error} skeleton="h-16">
            {info.data ? (
              <div className="mt-4">
                <p className="text-[12px] text-[color:var(--text-muted)]">
                  Mode: <strong>{info.data.mode === "llm" ? "model narration with grounding checks" : "deterministic templates"}</strong> · allowed queries: {info.data.allowed_queries.join(", ")}
                </p>
                <ul className="mt-3 flex flex-wrap gap-2">
                  {info.data.suggested_questions.map((item) => (
                    <li key={item}>
                      <button type="button" className="ghost-button !py-1.5 text-[12.5px]" onClick={() => ask(item)} disabled={busy}>
                        <Sparkles size={12} aria-hidden="true" /> {item}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </StateBlock>
        </Panel>
      </PageSection>

      {current ? (
        <PageSection className="mt-6">
          <Panel title={current.question} description={formatDateTime(current.created_at)}>
            <Answer record={current} />
          </Panel>
        </PageSection>
      ) : null}

      <PageSection className="mt-6">
        <Panel title="Previous investigations">
          <StateBlock loading={history.loading && !history.data} error={history.error} onRetry={history.refetch} empty={history.data && history.data.results.length === 0} emptyText="Nothing asked yet." skeleton="h-24">
            {history.data ? (
              <ul className="divide-y divide-black/5 dark:divide-white/10">
                {history.data.results.map((item) => (
                  <li key={item.public_id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-[13px]">
                    <div>
                      <p className="font-semibold text-[color:var(--color-ink)] dark:text-white">{item.question}</p>
                      <p className="text-[color:var(--text-muted)]">{humanCode(item.intent)} · {item.mode} · {formatDateTime(item.created_at)}</p>
                    </div>
                    <button type="button" className="secondary-button" onClick={() => setCurrent(item)}>Show</button>
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
