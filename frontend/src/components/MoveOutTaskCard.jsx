import {
  ClipboardCheck,
  Clock3,
  HandHeart,
  Home,
  MoreHorizontal,
  PackageCheck,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import React from "react";

import { cn } from "../lib/cn";
import { formatDateTime, formatLabel } from "../lib/formatters";

const categoryIcons = {
  setup: ShieldCheck,
  publish: PackageCheck,
  donation: HandHeart,
  pickup: Clock3,
  room_reset: Home,
  admin: ClipboardCheck,
};

const dueBucketClasses = {
  overdue: "bg-[rgba(234,124,91,0.14)] text-[color:var(--color-urgent)] border-[rgba(234,124,91,0.22)]",
  due_today: "bg-[rgba(217,164,65,0.18)] text-[color:var(--color-night)] border-[rgba(217,164,65,0.24)]",
  due_soon: "bg-[rgba(138,29,69,0.1)] text-[color:var(--color-tag)] border-[rgba(138,29,69,0.16)]",
  upcoming: "bg-[color:var(--color-surface-2)] text-[color:var(--text-muted)] border-[color:var(--color-line)]",
  unscheduled: "bg-[color:var(--color-surface-2)] text-[color:var(--text-muted)] border-[color:var(--color-line)]",
  done: "bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)] border-[rgba(138,29,69,0.14)]",
};

function dueBucketLabel(bucket) {
  if (bucket === "due_today") return "Due today";
  if (bucket === "due_soon") return "Due soon";
  if (bucket === "unscheduled") return "No due time";
  return formatLabel(bucket);
}

function MoveOutTaskCard({
  task,
  compact = false,
  onStatusChange,
  onDelete,
  disabled = false,
  showSessionName = false,
}) {
  const Icon = categoryIcons[task.category] || MoreHorizontal;
  const wrapperClassName = compact ? "paper-panel p-4" : "bulletin-card p-5";

  return (
    <article className={wrapperClassName}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="scan-chip">
              <Icon size={14} />
              {formatLabel(task.category)}
            </span>
            <span
              className={cn(
                "inline-flex items-center rounded-full border px-3 py-1 text-[0.64rem] font-semibold uppercase tracking-[0.14em]",
                dueBucketClasses[task.due_bucket] || dueBucketClasses.upcoming,
              )}
            >
              {dueBucketLabel(task.due_bucket)}
            </span>
            {task.is_system ? <span className="scan-chip">System task</span> : null}
          </div>
          <h3 className={cn("mt-3 font-black text-[color:var(--color-ink)]", compact ? "text-xl" : "text-2xl")}>
            {task.title}
          </h3>
          {showSessionName && task.scan_session_name ? (
            <p className="mt-2 text-sm font-semibold uppercase tracking-[0.12em] text-[color:var(--text-muted)]">
              {task.scan_session_name}
            </p>
          ) : null}
          {task.details ? <p className="mt-2 text-sm leading-6 text-[color:var(--text-muted)]">{task.details}</p> : null}
          <div className="mt-3 flex flex-wrap gap-3 text-sm text-[color:var(--text-muted)]">
            <span>Status {formatLabel(task.status)}</span>
            <span>{task.due_at ? formatDateTime(task.due_at) : "No due time"}</span>
          </div>
        </div>

        <div className="flex shrink-0 flex-col gap-3 sm:w-[13rem]">
          {onStatusChange ? (
            <select
              className="field min-h-[3rem] text-sm"
              value={task.status}
              disabled={disabled}
              aria-label={`Status for ${task.title}`}
              onChange={(event) => onStatusChange(task, event.target.value)}
            >
              <option value="todo">To do</option>
              <option value="in_progress">In progress</option>
              <option value="done">Done</option>
            </select>
          ) : (
            <div className="field flex min-h-[3rem] items-center justify-center text-sm font-bold text-[color:var(--text-muted)]">
              {formatLabel(task.status)}
            </div>
          )}
          {!task.is_system && onDelete ? (
            <button
              type="button"
              disabled={disabled}
              onClick={() => onDelete(task)}
              className="secondary-button"
            >
              <Trash2 size={15} />
              Remove
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}

export default React.memo(MoveOutTaskCard);
