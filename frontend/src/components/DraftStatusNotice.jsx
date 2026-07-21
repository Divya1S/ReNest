import { RotateCcw, Save } from "lucide-react";
import React from "react";

function formatAutosaveTime(value) {
  if (!value) {
    return "";
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "";
  }
}

export default function DraftStatusNotice({
  lastSavedAt,
  hasRestoredDraft = false,
  onDiscard,
  message = "Draft autosaved locally",
  detail,
}) {
  if (!lastSavedAt && !hasRestoredDraft) {
    return null;
  }

  const savedLabel = formatAutosaveTime(lastSavedAt);

  return (
    <div className="notice-panel flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="inline-flex items-center gap-2 text-sm font-black text-[color:var(--color-night)] text-[color:var(--color-ink)] dark:text-white">
          <Save size={15} />
          {hasRestoredDraft ? "Recovered your saved draft" : message}
        </p>
        {detail || savedLabel ? (
          <p className="mt-1 text-xs font-semibold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
            {detail || `Last autosaved at ${savedLabel}`}
          </p>
        ) : null}
      </div>
      {onDiscard ? (
        <button type="button" onClick={onDiscard} className="secondary-button !px-4 !py-2">
          <RotateCcw size={14} />
          Discard local draft
        </button>
      ) : null}
    </div>
  );
}
