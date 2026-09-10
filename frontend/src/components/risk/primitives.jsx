import { AlertCircle, FlaskConical, RefreshCw } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";

import { cn } from "../../lib/cn";
import { formatDateTime } from "../../lib/formatters";
import { DECISION_LABEL, STATUS_LABEL, formatMoney, formatMs, humanCode } from "../../lib/risk";
import SkeletonLoader from "../SkeletonLoader";

const DECISION_TONES = {
  allow: "border-[rgba(30,48,74,0.18)] bg-[color:var(--color-teal-soft)] text-[color:var(--color-teal)] dark:bg-[rgba(154,182,213,0.14)]",
  review: "border-[rgba(217,164,65,0.35)] bg-[rgba(217,164,65,0.16)] text-[#7a5a12] dark:text-[color:var(--color-box)]",
  block: "border-[rgba(201,100,68,0.3)] bg-[rgba(201,100,68,0.14)] text-[color:var(--color-urgent)]",
  pending: "border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] text-[color:var(--text-muted)]",
};

export function DecisionPill({ decision, className }) {
  const key = decision || "pending";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-[0.64rem] font-semibold uppercase tracking-[0.14em]",
        DECISION_TONES[key] || DECISION_TONES.pending,
        className,
      )}
    >
      {DECISION_LABEL[key] || "Pending"}
    </span>
  );
}

export function StatusText({ status }) {
  return <span className="text-[13px] text-[color:var(--text-muted)]">{STATUS_LABEL[status] || status}</span>;
}

export function SimulationTag({ groundTruth }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-dashed border-[color:var(--color-line-strong)] px-2 py-0.5 text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-[color:var(--text-muted)]"
      title="Synthetic Fraud Lab transaction"
    >
      <FlaskConical size={10} aria-hidden="true" />
      Simulated{groundTruth === true ? " · labelled fraud" : groundTruth === false ? " · labelled legit" : ""}
    </span>
  );
}

/** Horizontal 0..100 score with the review and block thresholds marked. */
export function ScoreBar({ score, thresholds, compact = false }) {
  if (score === null || score === undefined) {
    return <span className="text-[12px] text-[color:var(--text-muted)]">not evaluated</span>;
  }
  const value = Math.max(0, Math.min(100, Number(score)));
  const band = value >= 60 ? "bg-[color:var(--color-urgent)]" : value >= 30 ? "bg-[#d9a441]" : "bg-[color:var(--color-teal)]";
  return (
    <div className={cn("w-full", compact ? "min-w-[96px]" : "")}>
      <div
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        aria-label={`Risk score ${value} of 100`}
        className="relative h-2 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]"
      >
        <div className={cn("h-full rounded-full transition-[width]", band)} style={{ width: `${value}%` }} />
        {thresholds ? (
          <>
            <span className="absolute top-0 h-full w-px bg-[#d9a441]" style={{ left: `${thresholds.review}%` }} aria-hidden="true" />
            <span className="absolute top-0 h-full w-px bg-[color:var(--color-urgent)]" style={{ left: `${thresholds.block}%` }} aria-hidden="true" />
          </>
        ) : null}
      </div>
      {!compact ? (
        <div className="mt-1 flex justify-between text-[11px] text-[color:var(--text-muted)]">
          <span>0</span>
          {thresholds ? <span>review {thresholds.review} · block {thresholds.block}</span> : null}
          <span>100</span>
        </div>
      ) : null}
    </div>
  );
}

export function MetricTile({ label, value, hint, tone = "default", icon: Icon }) {
  const tones = {
    default: "",
    good: "border-[rgba(30,48,74,0.18)]",
    warn: "border-[rgba(217,164,65,0.4)]",
    bad: "border-[rgba(201,100,68,0.35)]",
  };
  return (
    <div className={cn("rounded-[20px] border border-black/10 bg-[color:var(--color-surface)] p-5 shadow-sm dark:border-white/10", tones[tone])}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[color:var(--text-muted)]">{label}</p>
        {Icon ? <Icon size={16} className="text-[color:var(--color-tag)]" aria-hidden="true" /> : null}
      </div>
      <p className="mt-2 text-[26px] font-black leading-none text-[color:var(--color-ink)] dark:text-white">{value}</p>
      {hint ? <p className="mt-2 text-[12px] leading-5 text-[color:var(--text-muted)]">{hint}</p> : null}
    </div>
  );
}

export function Panel({ title, description, actions, children, className, as: Tag = "section" }) {
  return (
    <Tag className={cn("rounded-[20px] border border-black/10 bg-[color:var(--color-surface)] p-5 shadow-sm dark:border-white/10 md:p-6", className)}>
      {title || actions ? (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title ? <h2 className="text-[17px] font-bold text-[color:var(--color-ink)] dark:text-white">{title}</h2> : null}
            {description ? <p className="mt-1 text-[13px] leading-5 text-[color:var(--text-muted)]">{description}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </Tag>
  );
}

export function StateBlock({ loading, error, onRetry, empty, emptyText = "Nothing here yet.", skeleton = "h-32", children }) {
  if (loading) return <SkeletonLoader className={cn("!rounded-[16px]", skeleton)} />;
  if (error) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 rounded-[16px] border border-[rgba(201,100,68,0.3)] bg-[rgba(201,100,68,0.06)] p-4">
        <p className="flex items-center gap-2 text-[14px] text-[color:var(--color-urgent)]">
          <AlertCircle size={16} aria-hidden="true" /> {error}
        </p>
        {onRetry ? (
          <button type="button" onClick={() => onRetry()} className="secondary-button">
            <RefreshCw size={14} aria-hidden="true" /> Retry
          </button>
        ) : null}
      </div>
    );
  }
  if (empty) return <p className="rounded-[16px] bg-[color:var(--color-surface-2)] p-4 text-[14px] text-[color:var(--text-muted)]">{emptyText}</p>;
  return children;
}

export function FactorList({ factors, emptyText = "No risk signals fired." }) {
  if (!factors?.length) return <p className="text-[14px] text-[color:var(--text-muted)]">{emptyText}</p>;
  return (
    <ul className="divide-y divide-black/5 dark:divide-white/10">
      {factors.map((factor) => {
        const positive = factor.points > 0;
        return (
          <li key={`${factor.code}-${factor.points}`} className="flex items-start gap-3 py-2.5">
            <span
              className={cn(
                "w-12 shrink-0 rounded-full px-2 py-0.5 text-center text-[12px] font-bold tabular-nums",
                positive ? "bg-[rgba(201,100,68,0.14)] text-[color:var(--color-urgent)]" : "bg-[color:var(--color-teal-soft)] text-[color:var(--color-teal)]",
              )}
            >
              {positive ? "+" : ""}
              {factor.points}
            </span>
            <div className="min-w-0">
              <p className="text-[14px] text-[color:var(--color-ink)] dark:text-white">{factor.label}</p>
              <p className="text-[12px] text-[color:var(--text-muted)]">{factor.code}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function ReasonChips({ reasons }) {
  if (!reasons?.length) return <span className="text-[12px] text-[color:var(--text-muted)]">no signals</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {reasons.slice(0, 3).map((code) => (
        <span key={code} className="rounded-full bg-[color:var(--color-surface-2)] px-2 py-0.5 text-[11px] text-[color:var(--text-muted)]">
          {humanCode(code)}
        </span>
      ))}
    </span>
  );
}

/** Table used by the live feed and the explorer. */
export function TransactionTable({ rows, thresholds, caption }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-[13px]">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="text-[11px] uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
            <th scope="col" className="pb-2 pr-3 font-semibold">Transaction</th>
            <th scope="col" className="pb-2 pr-3 font-semibold">Amount</th>
            <th scope="col" className="pb-2 pr-3 font-semibold">Score</th>
            <th scope="col" className="pb-2 pr-3 font-semibold">Decision</th>
            <th scope="col" className="pb-2 pr-3 font-semibold">Signals</th>
            <th scope="col" className="pb-2 font-semibold">When</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-black/5 dark:divide-white/10">
          {rows.map((row) => (
            <tr key={row.public_id} className="align-top">
              <td className="py-2.5 pr-3">
                <Link to={`/risk/transactions/${row.public_id}`} className="font-semibold text-[color:var(--color-tag)] hover:underline">
                  {row.public_id.slice(0, 14)}…
                </Link>
                <p className="text-[12px] text-[color:var(--text-muted)]">
                  {row.merchant_id} · {row.payment_method}
                  {row.is_simulation ? (
                    <>
                      {" "}
                      <SimulationTag groundTruth={row.ground_truth_fraud} />
                    </>
                  ) : null}
                </p>
              </td>
              <td className="py-2.5 pr-3 font-semibold tabular-nums text-[color:var(--color-ink)] dark:text-white">
                {formatMoney(row.amount, row.currency)}
              </td>
              <td className="py-2.5 pr-3">
                <div className="flex items-center gap-2">
                  <span className="w-7 text-right font-bold tabular-nums">{row.risk_score ?? "–"}</span>
                  <ScoreBar score={row.risk_score} thresholds={thresholds} compact />
                </div>
              </td>
              <td className="py-2.5 pr-3">
                <DecisionPill decision={row.decision} />
                <p className="mt-1">
                  <StatusText status={row.status} />
                </p>
              </td>
              <td className="py-2.5 pr-3">
                <ReasonChips reasons={row.reasons} />
              </td>
              <td className="py-2.5 whitespace-nowrap text-[color:var(--text-muted)]">
                {formatDateTime(row.created_at)}
                {row.evaluation_ms ? <p className="text-[11px]">eval {formatMs(row.evaluation_ms)}</p> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Toggle({ id, checked, onChange, label }) {
  return (
    <label htmlFor={id} className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-[color:var(--text-muted)]">
      <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 accent-[color:var(--color-tag)]" />
      {label}
    </label>
  );
}
