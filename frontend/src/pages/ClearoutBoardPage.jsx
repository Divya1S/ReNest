import { ArrowRight, CheckCheck, CheckSquare, ClipboardCheck, HeartHandshake, Layers3, ScanSearch, ShoppingBag, Sparkles, Trash2 } from "lucide-react";
import React, { useEffect, useState } from "react";
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
import { apiFetch, asResults } from "../lib/api";
import { formatCurrencyValue, formatDateTime, formatLabel, formatMoney } from "../lib/formatters";

const actionOptions = [
  { value: "create_free_listings", label: "Create free listings", icon: ShoppingBag },
  { value: "create_low_cost_listings", label: "Create low-cost listings", icon: ShoppingBag },
  { value: "send_to_donation_hub", label: "Route to donation hub", icon: HeartHandshake },
  { value: "mark_keep", label: "Mark keep", icon: CheckSquare },
  { value: "mark_toss", label: "Mark toss", icon: Trash2 },
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

export default function ClearoutBoardPage() {
  usePageTitle("Clear-Out Board");
  const { sessionId } = useParams();
  const [board, setBoard] = useState(null);
  const [hubs, setHubs] = useState([]);
  const [presets, setPresets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedIds, setSelectedIds] = useState([]);
  const [action, setAction] = useState("create_free_listings");
  const [donationHub, setDonationHub] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showBatchEditor, setShowBatchEditor] = useState(false);
  const batchDraft = usePersistentDraftState({
    key: `clearout-batch:${sessionId}`,
    initialValue: initialBatchForm,
  });
  const { state: batchForm, setState: setBatchForm } = batchDraft;
  const [savingBatch, setSavingBatch] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const unsavedGuard = useUnsavedChangesGuard({
    when: showBatchEditor && batchDraft.isDirty && !savingBatch,
    title: "Leave before applying these clear-out edits?",
    message:
      "Your clear-out batch edits have not been applied yet. Leaving now will preserve the local draft, but the board itself will remain unchanged.",
  });

  async function loadBoard() {
    const [boardData, hubsData, presetData] = await Promise.all([
      apiFetch(`/scan-sessions/${sessionId}/board`),
      apiFetch("/hubs"),
      apiFetch("/publish-presets"),
    ]);
    setBoard(boardData);
    setHubs(asResults(hubsData));
    setPresets(presetData);
  }

  useEffect(() => {
    let active = true;
    loadBoard()
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

  const selectedCount = selectedIds.length;
  const actionNeedsHub = action === "send_to_donation_hub";


  async function handleStatusChange(itemId, nextStatus) {
    try {
      await apiFetch(`/scan-items/${itemId}`, {
        method: "PATCH",
        body: { triage_status: nextStatus },
      });
      await loadBoard();
      toast.success("Board updated.");
    } catch (requestError) {
      toast.error(requestError.message);
    }
  }

  async function handleBatchUpdate() {
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
      await apiFetch(`/scan-sessions/${sessionId}/batch-update`, {
        method: "PATCH",
        body: {
          item_ids: selectedIds,
          changes,
        },
      });
      await loadBoard();
      batchDraft.clearDraft({ reset: true });
      setShowBatchEditor(false);
      toast.success("Selected drafts updated.");
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setSavingBatch(false);
    }
  }

  async function handlePublishSelected(ids = selectedIds) {
    if (!ids.length) {
      toast.error("Select at least one publish-ready draft first.");
      return;
    }

    setPublishing(true);
    try {
      await apiFetch(`/scan-sessions/${sessionId}/publish-selected`, {
        method: "POST",
        body: {
          item_ids: ids,
        },
      });
      await loadBoard();
      setSelectedIds((current) => current.filter((value) => !ids.includes(value)));
      toast.success("Selected drafts were published.");
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

  async function handleBulkAction(nextAction = action) {
    if (!selectedIds.length) {
      toast.error("Select at least one draft first.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await apiFetch(`/scan-sessions/${sessionId}/bulk-convert`, {
        method: "POST",
        body: {
          item_ids: selectedIds,
          action: nextAction,
          donation_hub: nextAction === "send_to_donation_hub" ? Number(donationHub) : undefined,
        },
      });
      setBoard(response);
      setSelectedIds([]);
      toast.success("Clear-out board updated.");
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  function toggleSelected(itemId) {
    setSelectedIds((current) =>
      current.includes(itemId) ? current.filter((value) => value !== itemId) : [...current, itemId],
    );
  }

  if (loading) {
    return <div className="paper-panel p-10 text-center text-lg font-bold">Loading clear-out board...</div>;
  }

  if (error || !board) {
    return <div className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-urgent)]">{error || "Board not found."}</div>;
  }

  return (
    <>
      <UnsavedChangesDialog {...unsavedGuard.dialogProps} />
      <div className="space-y-8">
      <PageSection className="paper-panel p-8 sm:p-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="sticker bg-[color:var(--color-teal)] text-white">
              <Layers3 size={14} />
              Clear-Out Board
            </div>
            <h1 className="mt-5 text-[34px] font-bold leading-[1.15] tracking-[-0.025em]">{board.session.name}</h1>
            <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
              This board shows what still needs a decision, what is ready to publish, and what has already been routed to keep, toss, donation, or live rescue listings.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link to={`/plan/${board.session.id}`} className="secondary-button">
              <ClipboardCheck size={15} />
              Move-Out Plan
            </Link>
            <Link to={`/scan/${board.session.id}`} className="secondary-button">
              <ScanSearch size={15} />
              Back to Studio
            </Link>
            <Link to={`/publish/${board.session.id}`} className="secondary-button">
              <Sparkles size={15} />
              Publish Queue
            </Link>
            <Link to="/browse" className="primary-button">
              <ArrowRight size={15} />
              Browse Live Listings
            </Link>
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-2 xl:grid-cols-5" delay={0.04}>
        <div className="paper-panel p-5">
          <p className="label-title">Total drafts</p>
          <p className="mt-3 text-4xl font-bold">{board.summary.total_items}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Ready to publish</p>
          <p className="mt-3 text-4xl font-bold">{board.summary.ready_to_publish_count}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Missing info</p>
          <p className="mt-3 text-4xl font-bold">{board.summary.missing_info_count}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Donation-ready</p>
          <p className="mt-3 text-4xl font-bold">{board.summary.donation_route_count}</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Published from scan</p>
          <p className="mt-3 text-4xl font-bold">{board.summary.published_count}</p>
        </div>
      </PageSection>

      <PageSection className="soft-panel p-7" delay={0.08}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="label-title">Bulk actions</p>
            <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Move faster when the room is almost packed.</h2>
            <p className="mt-3 text-[color:var(--text-muted)]">
              {selectedCount} item{selectedCount === 1 ? "" : "s"} selected • move-out deadline{" "}
              {board.session.move_out_deadline ? formatDateTime(board.session.move_out_deadline) : "not set"}
            </p>
            <p className="mt-2 text-sm text-[color:var(--text-muted)]">
              Room cleared {board.summary.room_cleared_percent}% • savings unlocked{" "}
              {formatCurrencyValue(board.summary.estimated_student_savings)}
            </p>
          </div>
          <div className="grid gap-3 lg:grid-cols-[1fr_1fr_auto_auto]">
            <select aria-label="Bulk action" className="field" value={action} onChange={(event) => setAction(event.target.value)}>
              {actionOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {actionNeedsHub ? (
              <select aria-label="Donation hub" className="field" value={donationHub} onChange={(event) => setDonationHub(event.target.value)}>
                <option value="">Select donation hub</option>
                {hubs.map((hub) => (
                  <option key={hub.id} value={hub.id}>
                    {hub.name}
                  </option>
                ))}
              </select>
            ) : (
              <div className="field flex items-center text-sm font-bold text-[color:var(--text-muted)]">
                Select an action to publish or close out the drafts.
              </div>
            )}
            <button
              type="button"
              onClick={() => setShowBatchEditor((current) => !current)}
              className="secondary-button"
            >
              {showBatchEditor ? "Hide Batch Edit" : "Batch Edit"}
            </button>
            <button
              type="button"
              onClick={() => handleBulkAction()}
              disabled={submitting || (actionNeedsHub && !donationHub)}
              className="primary-button"
            >
              <ArrowRight size={15} />
              {submitting ? "Working..." : "Run Action"}
            </button>
          </div>
        </div>
        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => handlePublishSelected()}
            disabled={!selectedIds.length || publishing}
            className="primary-button"
          >
            <CheckCheck size={15} />
            {publishing ? "Publishing..." : "Publish Selected"}
          </button>
          <Link to={`/publish/${board.session.id}`} className="secondary-button">
            <Sparkles size={15} />
            Open Full Publish Queue
          </Link>
        </div>
      </PageSection>

      {showBatchEditor ? (
        <PageSection delay={0.1}>
          <div className="space-y-4">
            <DraftStatusNotice
              lastSavedAt={batchDraft.lastSavedAt}
              hasRestoredDraft={batchDraft.hasRestoredDraft}
              detail="Batch-edit choices are saved locally for this clear-out board."
              onDiscard={() => batchDraft.clearDraft({ reset: true })}
            />
            <BatchEditPanel
              selectedCount={selectedIds.length}
              presets={presets}
              form={batchForm}
              setForm={setBatchForm}
              saving={savingBatch}
              onApply={handleBatchUpdate}
              onClose={() => setShowBatchEditor(false)}
            />
          </div>
        </PageSection>
      ) : null}

      <PageSection className="grid gap-6 xl:grid-cols-3 2xl:grid-cols-6" delay={0.12}>
        {board.columns.map((column) => (
          <section key={column.key} className="board-column">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="label-title">{column.label}</p>
                <h2 className="mt-1 text-[20px] font-bold tracking-[-0.01em]">{column.items.length} items</h2>
              </div>
              <span className="scan-chip">{column.key}</span>
            </div>
            <div className="mt-5 space-y-4">
              {column.items.length ? (
                column.items.map((item) => (
                  <article key={item.id} className="paper-panel p-4">
                    <div className="flex items-start justify-between gap-3">
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
                          </div>
                          <h3 className="mt-3 text-[16px] font-semibold">{item.title}</h3>
                        </div>
                      </label>
                      <span className="scan-chip">{formatMoney(item.price_type, item.price_amount)}</span>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-[color:var(--text-muted)]">{item.notes || "No notes yet."}</p>
                    {item.missing_fields.length ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {item.missing_fields.map((field) => (
                          <span key={field} className="scan-chip !bg-[rgba(255,252,248,0.84)] !text-[color:var(--color-urgent)]">
                            {formatLabel(field)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {item.donation_hub_detail ? (
                      <p className="mt-3 text-xs font-bold uppercase tracking-[0.16em] text-[color:var(--color-tag)]">
                        Routed to {item.donation_hub_detail.name}
                      </p>
                    ) : null}
                    {item.linked_listing_detail ? (
                      <Link to={`/listings/${item.linked_listing_detail.id}`} className="ghost-button mt-4">
                        Open published listing
                      </Link>
                    ) : null}
                    <div className="mt-4">
                      <select
                        aria-label={`Triage status for ${item.title}`}
                        className="field"
                        value={item.triage_status}
                        onChange={(event) => handleStatusChange(item.id, event.target.value)}
                      >
                        <option value="review">Review</option>
                        <option value="sell">Sell / Post</option>
                        <option value="donate">Donate</option>
                        <option value="keep">Keep</option>
                        <option value="toss">Toss</option>
                        <option value="done">Done</option>
                      </select>
                    </div>
                  </article>
                ))
              ) : (
                <div className="empty-dashed-panel">
                  No items here right now.
                </div>
              )}
            </div>
          </section>
        ))}
      </PageSection>

      <PageSection className="soft-panel p-7" delay={0.16}>
        <p className="label-title">Published from this scan</p>
        <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Rescue listings already flowing into the marketplace</h2>
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          {board.published_listings.length ? (
            board.published_listings.map((listing) => (
              <article key={listing.id} className="paper-panel p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="label-title">{formatLabel(listing.category)}</p>
                    <h3 className="mt-2 text-[20px] font-bold tracking-[-0.01em]">{listing.title}</h3>
                    <p className="mt-3 text-sm text-[color:var(--text-muted)]">{listing.pickup_zone}</p>
                  </div>
                  <span className="scan-chip">{formatMoney(listing.price_type, listing.price_amount)}</span>
                </div>
                <p className="mt-4 text-sm leading-6 text-[color:var(--text-muted)]">{listing.description}</p>
                <div className="mt-5 flex flex-wrap gap-2">
                  <span className="scan-chip">{formatDateTime(listing.available_until)}</span>
                  <span className="scan-chip">Retail {formatCurrencyValue(listing.estimated_retail_value)}</span>
                </div>
                <Link to={`/listings/${listing.id}`} className="primary-button mt-5">
                  Open Listing
                </Link>
              </article>
            ))
          ) : (
            <div className="empty-dashed-panel">
              Nothing has been published from this board yet. Select a few `sell` items and bulk-create listings.
            </div>
          )}
        </div>
      </PageSection>

      {selectedCount ? (
        <div className="action-tray">
          <p className="px-2 text-xs font-bold uppercase tracking-[0.16em] text-[color:var(--text-muted)]">
            {selectedCount} draft{selectedCount === 1 ? "" : "s"} selected
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setShowBatchEditor(true)} className="secondary-button px-3 py-2.5">
              Batch Edit
            </button>
            <button
              type="button"
              onClick={() => handlePublishSelected()}
              disabled={publishing}
              className="primary-button px-3 py-2.5"
            >
              Publish
            </button>
            <button
              type="button"
              onClick={() => handleBulkAction("mark_keep")}
              className="secondary-button px-3 py-2.5"
            >
              Keep
            </button>
            <button
              type="button"
              onClick={() => handleBulkAction("mark_toss")}
              className="secondary-button px-3 py-2.5"
            >
              Toss
            </button>
          </div>
        </div>
      ) : null}
      </div>
    </>
  );
}
