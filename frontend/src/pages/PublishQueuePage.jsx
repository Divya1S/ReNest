import {
  ArrowRight,
  CheckCheck,
  CircleAlert,
  ClipboardCheck,
  HeartHandshake,
  Layers3,
  ScanSearch,
  Sparkles,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import BatchEditPanel from "../components/BatchEditPanel";
import DraftStatusNotice from "../components/DraftStatusNotice";
import PageSection from "../components/PageSection";
import PublishReadinessPill from "../components/PublishReadinessPill";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePersistentDraftState } from "../hooks/usePersistentDraftState";
import { useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { apiFetch } from "../lib/api";
import { formatCurrencyValue, formatDateTime, formatLabel, formatMoney } from "../lib/formatters";

const filterOptions = [
  { value: "all", label: "All candidates" },
  { value: "ready", label: "Ready" },
  { value: "needs_info", label: "Needs info" },
  { value: "donation_route", label: "Donation routes" },
  { value: "published", label: "Published" },
];

const initialBatchForm = {
  preset_key: "",
  category: "",
  condition: "",
  price_type: "",
  price_amount: "",
  estimated_retail_value: "",
  triage_status: "",
  pickup_zone: "",
  move_out_deadline: "",
};

function buildBatchChanges(form) {
  const changes = {};
  Object.entries(form).forEach(([key, value]) => {
    if (value === "" || value === null || value === undefined) {
      return;
    }
    changes[key] =
      key === "move_out_deadline" ? new Date(value).toISOString() : value;
  });
  return changes;
}

export default function PublishQueuePage() {
  usePageTitle("Publish Queue");
  const { sessionId } = useParams();
  const [queue, setQueue] = useState(null);
  const [presets, setPresets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedIds, setSelectedIds] = useState([]);
  const [filter, setFilter] = useState("all");
  const [showBatchEditor, setShowBatchEditor] = useState(false);
  const batchDraft = usePersistentDraftState({
    key: `publish-batch:${sessionId}`,
    initialValue: initialBatchForm,
  });
  const { state: batchForm, setState: setBatchForm } = batchDraft;
  const [savingBatch, setSavingBatch] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const unsavedGuard = useUnsavedChangesGuard({
    when: showBatchEditor && batchDraft.isDirty && !savingBatch,
    title: "Leave before applying these batch edits?",
    message:
      "Your publish-queue batch edits have not been applied yet. Leaving now will keep the local draft, but the server-side drafts will stay unchanged.",
  });

  async function loadQueue() {
    const [queueData, presetData] = await Promise.all([
      apiFetch(`/scan-sessions/${sessionId}/publish-queue`),
      apiFetch("/publish-presets"),
    ]);
    setQueue(queueData);
    setPresets(presetData);
  }

  useEffect(() => {
    let active = true;
    loadQueue()
      .catch((requestError) => {
        if (active) {
          setError(requestError.message);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const visibleItems = useMemo(() => {
    const items = queue?.items || [];
    if (filter === "all") {
      return items;
    }
    return items.filter((item) => item.publish_readiness === filter);
  }, [filter, queue]);

  function toggleSelected(itemId) {
    setSelectedIds((current) =>
      current.includes(itemId) ? current.filter((value) => value !== itemId) : [...current, itemId],
    );
  }

  async function applyPreset(itemId, presetKey) {
    try {
      const response = await apiFetch(`/scan-sessions/${sessionId}/batch-update`, {
        method: "PATCH",
        body: {
          item_ids: [itemId],
          changes: {
            preset_key: presetKey,
            triage_status: "sell",
          },
        },
      });
      setQueue(response);
      toast.success("Quick preset applied.");
    } catch (requestError) {
      toast.error(requestError.message);
    }
  }

  async function handleApplyBatch() {
    const changes = buildBatchChanges(batchForm);
    if (!selectedIds.length) {
      toast.error("Select at least one draft first.");
      return;
    }
    if (!Object.keys(changes).length) {
      toast.error("Choose at least one field to update.");
      return;
    }

    setSavingBatch(true);
    try {
      const response = await apiFetch(`/scan-sessions/${sessionId}/batch-update`, {
        method: "PATCH",
        body: {
          item_ids: selectedIds,
          changes,
        },
      });
      setQueue(response);
      batchDraft.clearDraft({ reset: true });
      setShowBatchEditor(false);
      toast.success("Selected drafts updated.");
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setSavingBatch(false);
    }
  }

  async function handlePublish(ids = selectedIds) {
    if (!ids.length) {
      toast.error("Select at least one publish-ready draft.");
      return;
    }

    setPublishing(true);
    try {
      const response = await apiFetch(`/scan-sessions/${sessionId}/publish-selected`, {
        method: "POST",
        body: {
          item_ids: ids,
        },
      });
      setQueue(response.queue);
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)));
      toast.success(`${response.published_count} draft${response.published_count === 1 ? "" : "s"} published.`);
    } catch (requestError) {
      // The endpoint is all-or-nothing and names the drafts that are not ready.
      // Surfacing only "Only publish-ready drafts can go live." left the user
      // hunting for which ones.
      const blocked = requestError?.data?.blocked_items;
      if (Array.isArray(blocked) && blocked.length) {
        const names = blocked.map((item) => item.title || `Draft ${item.id}`).join(", ");
        toast.error(`Not ready yet: ${names}. Add the missing details first.`);
        const blockedIds = blocked.map((item) => item.id);
        setSelectedIds((current) => current.filter((value) => !blockedIds.includes(value)));
      } else {
        toast.error(requestError.message);
      }
    } finally {
      setPublishing(false);
    }
  }

  if (loading) {
    return <div className="paper-panel p-10 text-center text-lg font-bold">Loading publish queue...</div>;
  }

  if (error || !queue) {
    return (
      <div className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-urgent)]">
        {error || "Publish queue not found."}
      </div>
    );
  }

  return (
    <>
      <UnsavedChangesDialog {...unsavedGuard.dialogProps} />
      <div className="space-y-8 pb-28 sm:pb-8">
      <PageSection className="paper-panel p-8 sm:p-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="sticker bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]">
              <Sparkles size={14} />
              Smart Publish Flow
            </div>
            <h1 className="mt-5 text-[34px] font-bold leading-[1.15] tracking-[-0.025em]">{queue.session.name}</h1>
            <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
              Focus only on the drafts that matter for rescue publishing. Fix missing information, apply dorm-item presets, and send ready items live in one pass.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link to={`/plan/${queue.session.id}`} className="secondary-button">
              <ClipboardCheck size={15} />
              Move-Out Plan
            </Link>
            <Link to={`/clearout/${queue.session.id}`} className="secondary-button">
              <Layers3 size={15} />
              Open Board
            </Link>
            <Link to={`/scan/${queue.session.id}`} className="secondary-button">
              <ScanSearch size={15} />
              Back to Studio
            </Link>
            <button
              type="button"
              onClick={() => handlePublish(visibleItems.filter((item) => item.publish_readiness === "ready").map((item) => item.id))}
              disabled={publishing}
              className="primary-button"
            >
              <CheckCheck size={15} />
              {publishing ? "Publishing..." : "Publish Ready Items"}
            </button>
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-2 xl:grid-cols-4" delay={0.04}>
        <div className="paper-panel p-5">
          <p className="label-title">Ready to publish</p>
          <p className="mt-3 text-4xl font-bold">{queue.summary.ready_to_publish_count}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Missing info</p>
          <p className="mt-3 text-4xl font-bold">{queue.summary.missing_info_count}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Donation routes</p>
          <p className="mt-3 text-4xl font-bold">{queue.summary.donation_route_count}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Published from scan</p>
          <p className="mt-3 text-4xl font-bold">{queue.summary.published_count}</p>
        </div>
      </PageSection>

      <PageSection className="soft-panel p-6" delay={0.08}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="label-title">Queue filters</p>
            <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Sort the publish work by readiness.</h2>
            <p className="mt-3 text-sm leading-6 text-[color:var(--text-muted)]">
              Move-out deadline {queue.session.move_out_deadline ? formatDateTime(queue.session.move_out_deadline) : "not set"} • pickup zone{" "}
              {queue.session.pickup_zone || "not set"}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {filterOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setFilter(option.value)}
                className={`scan-chip transition ${filter === option.value ? "bg-[color:var(--color-tag)] text-white shadow-[0_14px_28px_rgba(138,29,69,0.24)]" : ""}`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => setShowBatchEditor((current) => !current)}
            className="secondary-button"
          >
            <CircleAlert size={15} />
            {showBatchEditor ? "Hide Batch Edit" : "Batch Edit Selected"}
          </button>
          <button
            type="button"
            onClick={() => handlePublish()}
            disabled={!selectedIds.length || publishing}
            className="primary-button"
          >
            <CheckCheck size={15} />
            Publish Selected
          </button>
        </div>
      </PageSection>

      {showBatchEditor ? (
        <PageSection delay={0.1}>
          <div className="space-y-4">
            <DraftStatusNotice
              lastSavedAt={batchDraft.lastSavedAt}
              hasRestoredDraft={batchDraft.hasRestoredDraft}
              detail="Batch-edit choices are saved locally for this publish queue."
              onDiscard={() => batchDraft.clearDraft({ reset: true })}
            />
            <BatchEditPanel
              selectedCount={selectedIds.length}
              presets={presets}
              form={batchForm}
              setForm={setBatchForm}
              saving={savingBatch}
              onApply={handleApplyBatch}
              onClose={() => setShowBatchEditor(false)}
            />
          </div>
        </PageSection>
      ) : null}

      <PageSection className="grid gap-5 xl:grid-cols-2" delay={0.12}>
        {visibleItems.length ? (
          visibleItems.map((item) => (
            <article key={item.id} className="bulletin-card">
              <div className="flex flex-col gap-5 lg:flex-row">
                <div className="relative w-full overflow-hidden rounded-[1.6rem] border border-[color:var(--color-line)] bg-[color:var(--color-paper)] lg:max-w-[15rem]">
                  {item.source_image_detail?.image_url ? (
                    <img
                      src={item.source_image_detail.image_url}
                      alt={item.title}
                      loading="lazy"
                      decoding="async"
                      className="h-48 w-full object-cover lg:h-full"
                    />
                  ) : (
                    <div className="flex h-48 items-center justify-center text-sm font-bold uppercase tracking-[0.18em] text-[color:var(--text-muted)]">
                      No photo
                    </div>
                  )}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <label className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(item.id)}
                        onChange={() => toggleSelected(item.id)}
                        className="mt-1"
                      />
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <PublishReadinessPill status={item.publish_readiness} />
                          <span className="scan-chip">{formatLabel(item.category)}</span>
                          <span className="scan-chip">{formatMoney(item.price_type, item.price_amount)}</span>
                        </div>
                        <h2 className="mt-4 text-[24px] font-bold tracking-[-0.02em]">{item.title}</h2>
                        <p className="mt-3 text-sm leading-6 text-[color:var(--text-muted)]">
                          {item.notes || "No draft notes yet. Apply a preset or add a short handoff description."}
                        </p>
                      </div>
                    </label>
                    <div className="text-left sm:text-right">
                      <p className="label-title">Student savings</p>
                      <p className="mt-2 text-[20px] font-bold tracking-[-0.01em]">
                        {formatCurrencyValue(Number(item.estimated_retail_value || 0) - Number(item.price_amount || 0))}
                      </p>
                    </div>
                  </div>

                  <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <div className="paper-panel p-4">
                      <p className="label-title">Retail value</p>
                      <p className="mt-2 text-[18px] font-semibold">{formatCurrencyValue(item.estimated_retail_value)}</p>
                    </div>
                    <div className="paper-panel p-4">
                      <p className="label-title">Recommended action</p>
                      <p className="mt-2 text-[18px] font-semibold">{formatLabel(item.recommended_action)}</p>
                    </div>
                    <div className="paper-panel p-4">
                      <p className="label-title">Room view</p>
                      <p className="mt-2 text-[18px] font-semibold">
                        {item.source_image_detail?.position ? `View ${item.source_image_detail.position}` : "No source"}
                      </p>
                    </div>
                    <div className="paper-panel p-4">
                      <p className="label-title">Deadline</p>
                      <p className="mt-2 text-[18px] font-semibold">
                        {queue.session.move_out_deadline ? formatDateTime(queue.session.move_out_deadline) : "Missing"}
                      </p>
                    </div>
                  </div>

                  {item.missing_fields.length ? (
                    <div className="notice-panel notice-panel--urgent mt-5">
                      <p className="label-title text-[color:var(--color-urgent)]">Still missing</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {item.missing_fields.map((field) => (
                          <span key={field} className="scan-chip !bg-[rgba(255,252,248,0.84)] !text-[color:var(--color-urgent)]">
                            {formatLabel(field)}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-5">
                    <p className="label-title">Quick presets</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {presets.slice(0, 4).map((preset) => (
                        <button
                          key={preset.key}
                          type="button"
                          onClick={() => applyPreset(item.id, preset.key)}
                          className="ghost-button"
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="mt-5 flex flex-wrap gap-3">
                    {item.publish_readiness === "ready" ? (
                      <button
                        type="button"
                        onClick={() => handlePublish([item.id])}
                        disabled={publishing}
                        className="primary-button"
                      >
                        <CheckCheck size={15} />
                        Publish this draft
                      </button>
                    ) : null}
                    {item.linked_listing_detail ? (
                      <Link to={`/listings/${item.linked_listing_detail.id}`} className="secondary-button">
                        <ArrowRight size={15} />
                        Open listing
                      </Link>
                    ) : null}
                    {item.publish_readiness === "donation_route" ? (
                      <div className="ghost-button">
                        <HeartHandshake size={15} />
                        Donation route complete
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            </article>
          ))
        ) : (
          <div className="paper-panel p-10 text-center text-[color:var(--text-muted)] xl:col-span-2">
            No drafts match this readiness filter yet.
          </div>
        )}
      </PageSection>
      </div>
    </>
  );
}
