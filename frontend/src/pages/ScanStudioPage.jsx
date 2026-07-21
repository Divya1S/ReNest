import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowRight,
  Bot,
  Camera,
  CheckCircle2,
  CircleAlert,
  ClipboardCheck,
  Keyboard,
  Layers3,
  Save,
  Sparkles,
  Upload,
  Wand2,
  X,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import DraftStatusNotice from "../components/DraftStatusNotice";
import HotspotStage from "../components/HotspotStage";
import PublishReadinessPill from "../components/PublishReadinessPill";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { usePageTitle } from "../hooks/usePageTitle";
import { useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { apiFetch, toLocalDateTimeInput } from "../lib/api";
import { cn } from "../lib/cn";
import { formatDateTime, formatLabel } from "../lib/formatters";

const initialDraftForm = {
  title: "",
  category: "storage",
  condition: "good",
  price_type: "free",
  price_amount: "0.00",
  estimated_retail_value: "0.00",
  preset_key: "",
  triage_status: "review",
  notes: "",
};

function buildDraftFormFromItem(draft) {
  if (!draft) return initialDraftForm;
  return {
    title: draft.title || "",
    category: draft.category || "storage",
    condition: draft.condition || "good",
    price_type: draft.price_type || "free",
    price_amount: String(draft.price_amount ?? "0.00"),
    estimated_retail_value: String(draft.estimated_retail_value ?? "0.00"),
    preset_key: draft.preset_key || "",
    triage_status: draft.triage_status || "review",
    notes: draft.notes || "",
  };
}

function buildSessionMetaFromSession(session) {
  return {
    pickup_zone: session?.pickup_zone || "",
    move_out_deadline: toLocalDateTimeInput(session?.move_out_deadline),
  };
}

const TRIAGE_KEYS = { s: "sell", d: "donate", k: "keep", t: "toss" };
const TRIAGE_ORDER = ["sell", "donate", "keep", "toss"];

function imageBadgeStyle(items) {
  if (!items.length) return "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400";
  const ready = items.filter((i) => ["ready", "published"].includes(i.publish_readiness)).length;
  if (ready === items.length) return "bg-emerald-500 text-white";
  if (ready > 0) return "bg-amber-400 text-amber-900";
  return "bg-rose-400 text-white";
}

export default function ScanStudioPage() {
  usePageTitle("Scan Studio");
  const { sessionId } = useParams();
  const [session, setSession] = useState(null);
  const [presets, setPresets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedImageId, setSelectedImageId] = useState(null);
  const [selectedDraftId, setSelectedDraftId] = useState(null);
  const [draftForm, setDraftForm] = useState(initialDraftForm);
  const [uploading, setUploading] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);
  const [savingSession, setSavingSession] = useState(false);
  const [aiDetecting, setAiDetecting] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [sessionMeta, setSessionMeta] = useState({ pickup_zone: "", move_out_deadline: "" });
  const [pendingDraftSelection, setPendingDraftSelection] = useState(null);
  const [confirmDeleteDraft, setConfirmDeleteDraft] = useState(false);

  const draftFormRef = useRef(draftForm);
  draftFormRef.current = draftForm;

  async function loadSession(nextDraftId = selectedDraftId, nextImageId = selectedImageId) {
    const data = await apiFetch(`/scan-sessions/${sessionId}`);
    setSession(data);
    const resolvedImageId = nextImageId || data.images[0]?.id || null;
    setSelectedImageId(resolvedImageId);
    const resolvedDraftId =
      data.items.find((item) => item.id === nextDraftId)?.id || data.items[0]?.id || null;
    setSelectedDraftId(resolvedDraftId);
    const draft = data.items.find((item) => item.id === resolvedDraftId);
    setDraftForm(buildDraftFormFromItem(draft));
    setSessionMeta(buildSessionMetaFromSession(data));
  }

  useEffect(() => {
    let active = true;
    Promise.all([loadSession(), apiFetch("/publish-presets")])
      .then(([, presetData]) => { if (active) setPresets(presetData); })
      .catch((err) => { if (active) setError(err.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const activeImage = useMemo(
    () => session?.images.find((img) => img.id === selectedImageId) || session?.images[0] || null,
    [session, selectedImageId],
  );
  const activeDraft = useMemo(
    () => session?.items.find((item) => item.id === selectedDraftId) || null,
    [session, selectedDraftId],
  );
  const draftBaseline = useMemo(() => buildDraftFormFromItem(activeDraft), [activeDraft]);
  const sessionMetaBaseline = useMemo(() => buildSessionMetaFromSession(session), [session]);
  const visibleHotspots = useMemo(
    () => session?.items.filter((item) => item.source_image === activeImage?.id) || [],
    [session, activeImage],
  );
  const draftInspectorDirty = activeDraft
    ? JSON.stringify(draftForm) !== JSON.stringify(draftBaseline)
    : false;
  const sessionMetaDirty = JSON.stringify(sessionMeta) !== JSON.stringify(sessionMetaBaseline);
  const unsavedGuard = useUnsavedChangesGuard({
    when: (draftInspectorDirty || sessionMetaDirty) && !savingDraft && !savingSession,
    title: "Leave the scan studio with unsaved changes?",
    message:
      "Your draft inspector or scan settings changes have not been saved yet. Leaving now will discard the in-progress edits on this page.",
  });

  const applyDraftSelection = useCallback((nextDraftId, nextImageId = null) => {
    if (!session) return;
    const nextDraft = session.items.find((item) => item.id === nextDraftId) || null;
    const resolvedImageId = nextImageId ?? nextDraft?.source_image ?? selectedImageId;
    setSelectedDraftId(nextDraft?.id || null);
    setSelectedImageId(resolvedImageId || null);
    setDraftForm(buildDraftFormFromItem(nextDraft));
  }, [selectedImageId, session]);

  const requestDraftSelection = useCallback((nextDraftId, nextImageId = null) => {
    if (!nextDraftId || nextDraftId === selectedDraftId) return;
    if (draftInspectorDirty && !savingDraft) {
      setPendingDraftSelection({ draftId: nextDraftId, imageId: nextImageId });
      return;
    }
    applyDraftSelection(nextDraftId, nextImageId);
  }, [applyDraftSelection, draftInspectorDirty, savingDraft, selectedDraftId]);

  useEffect(() => {
    function handler(e) {
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const key = e.key.toLowerCase();
      if (TRIAGE_KEYS[key] && activeDraft) {
        e.preventDefault();
        setDraftForm((cur) => ({ ...cur, triage_status: TRIAGE_KEYS[key] }));
        toast.info(`Triage → ${TRIAGE_KEYS[key]}`, { duration: 1200 });
        return;
      }
      // "[" / "]" cycle drafts — Tab is left alone so keyboard users can
      // still reach every control on the page.
      if ((key === "[" || key === "]") && session?.items.length > 1) {
        e.preventDefault();
        const items = session.items;
        const idx = items.findIndex((i) => i.id === selectedDraftId);
        const next = key === "["
          ? items[(idx - 1 + items.length) % items.length]
          : items[(idx + 1) % items.length];
        requestDraftSelection(next.id, next.source_image);
        return;
      }
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && activeDraft) {
        e.preventDefault();
        saveDraftNow();
        return;
      }
      if (e.key === "?") {
        e.preventDefault();
        setShowShortcuts((v) => !v);
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDraft, selectedDraftId, session]);

  async function handleUpload(event) {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    const payload = new FormData();
    files.forEach((f) => payload.append("images", f));
    setUploading(true);
    try {
      const response = await apiFetch(`/scan-sessions/${sessionId}/images`, { method: "POST", body: payload });
      setSession(response);
      setSelectedImageId((cur) => cur || response.images[0]?.id || null);
      toast.success("Room photos uploaded.");
    } catch (err) {
      toast.error(err.message);
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function handleCreateHotspot(hotspotBox) {
    if (!activeImage) { toast.error("Upload at least one room photo first."); return; }
    try {
      const response = await apiFetch(`/scan-sessions/${sessionId}/items`, {
        method: "POST",
        body: { source_image: activeImage.id, hotspot_box: hotspotBox },
      });
      await loadSession(response.id, activeImage.id);
    } catch (err) {
      toast.error(err.message);
    }
  }

  async function handleUpdateHotspot(hotspotId, newBox) {
    try {
      await apiFetch(`/scan-items/${hotspotId}`, { method: "PATCH", body: { hotspot_box: newBox } });
      await loadSession(hotspotId, activeImage?.id);
    } catch (err) {
      toast.error(err.message);
    }
  }

  async function saveDraftNow() {
    if (!activeDraft) return;
    const form = draftFormRef.current;
    setSavingDraft(true);
    try {
      await apiFetch(`/scan-items/${activeDraft.id}`, {
        method: "PATCH",
        body: {
          ...form,
          preset_key: form.preset_key || null,
          price_amount: form.price_type === "low_cost" ? form.price_amount : "0.00",
        },
      });
      toast.success("Draft saved.");
      await loadSession(activeDraft.id, activeImage?.id);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSavingDraft(false);
    }
  }

  async function handleSaveDraft(e) {
    e.preventDefault();
    await saveDraftNow();
  }

  async function handleConfirmAndNext() {
    await saveDraftNow();
    const items = session?.items || [];
    const idx = items.findIndex((i) => i.id === selectedDraftId);
    const next = items[idx + 1] || null;
    if (next) requestDraftSelection(next.id, next.source_image);
  }

  async function handleDeleteDraft() {
    if (!activeDraft) return;
    setConfirmDeleteDraft(false);
    try {
      await apiFetch(`/scan-items/${activeDraft.id}`, { method: "DELETE" });
      toast.success("Draft deleted.");
      await loadSession(null, activeImage?.id);
    } catch (err) {
      toast.error(err.message);
    }
  }

  async function pollAiDetectStatus(imageId) {
    // Detection runs in a background worker; poll until it lands (~10-30s).
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2500));
      try {
        const status = await apiFetch(
          `/scan-sessions/${sessionId}/ai-detect/status?image_id=${imageId}`,
        );
        if (status.status !== "processing") return status;
      } catch {
        // transient network blip — keep polling until the deadline
      }
    }
    return { status: "error", detail: "AI detection timed out. Try again." };
  }

  async function handleAiDetect() {
    if (!activeImage) { toast.error("Select a room photo first."); return; }
    setAiDetecting(true);
    const imageId = activeImage.id;
    try {
      let result = await apiFetch(`/scan-sessions/${sessionId}/ai-detect`, {
        method: "POST",
        body: { image_id: imageId },
      });
      if (result.status === "processing") {
        toast.info("AI is scanning the photo — hotspots will appear here shortly.");
        result = await pollAiDetectStatus(imageId);
      }
      if (result.status === "done") {
        setSession(result.session);
        toast.success(`AI detected ${result.created_count} item${result.created_count !== 1 ? "s" : ""} — review each hotspot.`);
      } else {
        toast.error(result.detail || "AI detection failed.");
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setAiDetecting(false);
    }
  }

  function applyPresetToDraft(preset) {
    setDraftForm((cur) => ({
      ...cur,
      title: preset.title,
      category: preset.category,
      condition: preset.default_condition,
      price_type: preset.default_price_type,
      price_amount: preset.default_price_amount,
      estimated_retail_value: preset.default_estimated_retail_value,
      preset_key: preset.key,
      triage_status: cur.triage_status === "review" ? "sell" : cur.triage_status,
      notes: preset.default_notes,
    }));
  }

  function applyAiSuggestion() {
    if (!activeDraft?.suggestion_pack) return;
    const s = activeDraft.suggestion_pack;
    setDraftForm((cur) => ({
      ...cur,
      title: s.title || cur.title,
      category: s.category || cur.category,
      condition: s.default_condition || cur.condition,
      price_type: s.default_price_type || cur.price_type,
      price_amount: s.default_price_amount || cur.price_amount,
      estimated_retail_value: s.default_estimated_retail_value || cur.estimated_retail_value,
      preset_key: s.key || cur.preset_key,
      triage_status: cur.triage_status === "review" ? "sell" : cur.triage_status,
      notes: s.default_notes || cur.notes,
    }));
    toast.success("AI suggestion applied — save to confirm.");
  }

  async function handleSaveSessionMeta(e) {
    e.preventDefault();
    setSavingSession(true);
    try {
      await apiFetch(`/scan-sessions/${sessionId}`, {
        method: "PATCH",
        body: {
          pickup_zone: sessionMeta.pickup_zone,
          move_out_deadline: sessionMeta.move_out_deadline
            ? new Date(sessionMeta.move_out_deadline).toISOString()
            : null,
        },
      });
      toast.success("Scan settings updated.");
      await loadSession(selectedDraftId, activeImage?.id);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSavingSession(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <div className="text-center space-y-3">
          <Camera size={36} className="mx-auto text-[color:var(--color-tag)] animate-pulse" />
          <p className="text-[17px] font-semibold text-[color:var(--color-ink)] dark:text-white">Loading scan studio…</p>
        </div>
      </div>
    );
  }
  if (error || !session) {
    return (
      <div className="flex items-center justify-center py-32">
        <p className="text-[17px] font-semibold text-[color:var(--color-urgent)]">{error || "Scan not found."}</p>
      </div>
    );
  }

  const draftIndex = session.items.findIndex((i) => i.id === selectedDraftId);
  const totalDrafts = session.items.length;

  return (
    <>
      <UnsavedChangesDialog {...unsavedGuard.dialogProps} />
      <UnsavedChangesDialog
        open={Boolean(pendingDraftSelection)}
        title="Switch rescue draft without saving?"
        message="The current draft inspector changes have not been saved yet. Switching to another hotspot will discard those in-progress edits."
        confirmLabel="Switch draft"
        cancelLabel="Keep editing"
        onConfirm={() => {
          if (pendingDraftSelection) applyDraftSelection(pendingDraftSelection.draftId, pendingDraftSelection.imageId);
          setPendingDraftSelection(null);
        }}
        onCancel={() => setPendingDraftSelection(null)}
      />
      <UnsavedChangesDialog
        open={confirmDeleteDraft}
        kicker="Confirm deletion"
        title="Delete this draft item?"
        message="The hotspot and any details you entered for this draft will be removed. This cannot be undone."
        confirmLabel="Delete draft"
        cancelLabel="Keep draft"
        onConfirm={handleDeleteDraft}
        onCancel={() => setConfirmDeleteDraft(false)}
      />

      {/* Keyboard shortcuts overlay */}
      <AnimatePresence>
        {showShortcuts && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm"
            onClick={() => setShowShortcuts(false)}
          >
            <motion.div
              initial={{ scale: 0.92, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.92, opacity: 0 }}
              className="relative w-full max-w-sm bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] rounded-[24px] p-8 shadow-2xl border border-black/8 dark:border-white/8"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                type="button"
                onClick={() => setShowShortcuts(false)}
                className="absolute right-4 top-4 rounded-full p-2 text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface-2)] transition-colors"
              >
                <X size={16} />
              </button>
              <div className="flex items-center gap-3 mb-6">
                <Keyboard size={20} className="text-[color:var(--color-tag)]" />
                <h3 className="text-xl font-bold text-[color:var(--color-ink)] dark:text-white">Keyboard Shortcuts</h3>
              </div>
              <div className="space-y-3 text-sm">
                {[
                  ["S", "Triage → Sell"],
                  ["D", "Triage → Donate"],
                  ["K", "Triage → Keep"],
                  ["T", "Triage → Toss"],
                  ["]", "Next draft"],
                  ["[", "Previous draft"],
                  ["⌘ + Enter", "Save draft"],
                  ["?", "Toggle this panel"],
                ].map(([key, label]) => (
                  <div key={key} className="flex items-center justify-between gap-4">
                    <span className="text-[color:var(--text-muted)]">{label}</span>
                    <kbd className="rounded-lg border border-black/10 dark:border-white/10 bg-[color:var(--color-surface-2)] px-2.5 py-1 font-mono text-xs font-bold text-[color:var(--color-ink)] dark:text-white">
                      {key}
                    </kbd>
                  </div>
                ))}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Page header */}
      <div className="mx-auto max-w-[1400px] px-4 md:px-8 pt-6 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Camera size={15} className="text-[color:var(--color-tag)]" />
              <span className="text-[12px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Scan Studio</span>
            </div>
            <h1 className="text-[26px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white leading-tight">{session.name}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-3 text-[13px] text-[color:var(--text-muted)]">
              <span>{session.summary?.total_items || 0} drafts</span>
              <span>·</span>
              <span>{session.summary?.ready_to_publish_count || 0} ready</span>
              <span>·</span>
              <span className="font-semibold text-[color:var(--color-tag)]">{session.progress_percent || 0}% cleared</span>
            </div>
            <div className="h-4 w-px bg-black/10 dark:bg-white/10" />
            <button
              type="button"
              onClick={() => setShowShortcuts(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-black/10 dark:border-white/10 text-[12px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface)] transition-colors"
            >
              <Keyboard size={13} />
              Shortcuts
            </button>
            <Link to={`/publish/${session.id}`} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[color:var(--color-tag)] text-white text-[12px] font-semibold hover:opacity-90 transition-opacity">
              <Sparkles size={13} />
              Publish Queue
            </Link>
            <Link to={`/clearout/${session.id}`} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-black/10 dark:border-white/10 text-[12px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface)] transition-colors">
              <Layers3 size={13} />
              Clear-Out
            </Link>
          </div>
        </div>
      </div>

      {/* Main layout */}
      <div className="mx-auto max-w-[1400px] px-4 md:px-8 pb-16 flex flex-col lg:flex-row gap-6">

        {/* ── Left: scan canvas ── */}
        <div className="flex-1 min-w-0 space-y-4">

          {/* Image stage */}
          <div className="relative rounded-[24px] overflow-hidden bg-[#111] shadow-[0_8px_40px_rgba(0,0,0,0.18)]" style={{ aspectRatio: "4/3" }}>
            <HotspotStage
              image={activeImage}
              hotspots={visibleHotspots}
              selectedHotspotId={selectedDraftId}
              onSelectHotspot={(id) => requestDraftSelection(id, activeImage?.id)}
              onCreateHotspot={handleCreateHotspot}
              onUpdateHotspot={handleUpdateHotspot}
            />

            {/* AI Detect — glassmorphism top-left */}
            <button
              type="button"
              disabled={!activeImage || aiDetecting}
              onClick={handleAiDetect}
              className="absolute top-4 left-4 z-20 flex items-center gap-2 px-4 py-2.5 rounded-full text-white text-[13px] font-semibold disabled:opacity-50 transition-opacity hover:opacity-80"
              style={{ background: "rgba(255,255,255,0.18)", backdropFilter: "saturate(180%) blur(12px)" }}
            >
              <Bot size={15} />
              {aiDetecting ? "Detecting…" : "AI Detect"}
            </button>

            {/* Bottom-right controls */}
            <div className="absolute bottom-4 right-4 z-20 flex items-center gap-2">
              <label
                className="flex items-center gap-1.5 px-3 py-2 rounded-full text-white text-[12px] font-semibold cursor-pointer disabled:opacity-50 transition-opacity hover:opacity-80"
                style={{ background: "rgba(255,255,255,0.18)", backdropFilter: "saturate(180%) blur(12px)" }}
              >
                <Upload size={13} />
                {uploading ? "Uploading…" : "Add Photo"}
                <input
                  className="hidden"
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={handleUpload}
                  disabled={uploading || session.images.length >= 4}
                />
              </label>
            </div>

            {/* No image placeholder */}
            {!activeImage && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-white/60">
                <Camera size={48} className="opacity-40" />
                <p className="text-[15px] font-medium">Upload a room photo to start scanning</p>
              </div>
            )}
          </div>

          {/* Thumbnail strip */}
          <div className="flex items-center gap-3 overflow-x-auto pb-1" style={{ scrollbarWidth: "none" }}>
            {session.images.map((image) => {
              const imageItems = session.items.filter((i) => i.source_image === image.id);
              const badgeStyle = imageBadgeStyle(imageItems);
              const isActive = image.id === activeImage?.id;
              return (
                <button
                  key={image.id}
                  type="button"
                  onClick={() => setSelectedImageId(image.id)}
                  className={cn(
                    "relative w-24 shrink-0 rounded-[12px] overflow-hidden border-2 transition-all",
                    isActive ? "border-[color:var(--color-tag)] shadow-[0_0_0_2px_rgba(106,0,47,0.2)]" : "border-transparent opacity-70 hover:opacity-100",
                  )}
                  style={{ aspectRatio: "1" }}
                >
                  {image.url ? (
                    <img src={image.url} alt={`View ${image.position}`} className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full bg-[color:var(--color-surface-2)] flex items-center justify-center">
                      <Camera size={20} className="text-[color:var(--text-muted)]" />
                    </div>
                  )}
                  <span className={cn(
                    "absolute top-1.5 right-1.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-black",
                    isActive ? "bg-[color:var(--color-tag)] text-white" : badgeStyle,
                  )}>
                    {imageItems.length}
                  </span>
                </button>
              );
            })}

            {/* Add view */}
            {session.images.length < 4 && (
              <label
                className="w-24 shrink-0 rounded-[12px] border-2 border-dashed border-black/15 dark:border-white/15 flex flex-col items-center justify-center gap-1 cursor-pointer hover:border-[color:var(--color-tag)] transition-colors"
                style={{ aspectRatio: "1" }}
              >
                <Upload size={16} className="text-[color:var(--text-muted)]" />
                <span className="text-[11px] font-medium text-[color:var(--text-muted)]">Add View</span>
                <input
                  type="file"
                  className="hidden"
                  accept="image/*"
                  multiple
                  onChange={handleUpload}
                  disabled={uploading}
                />
              </label>
            )}
          </div>

          {/* Scan settings (compact bar) */}
          <form
            onSubmit={handleSaveSessionMeta}
            className="flex flex-wrap items-center gap-3 p-4 bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] rounded-[16px] border border-black/8 dark:border-white/8"
          >
            <p className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[color:var(--text-muted)] shrink-0">Scan settings</p>
            {sessionMetaDirty && (
              <span className="text-[11px] font-semibold text-[color:var(--color-urgent)]">· unsaved changes</span>
            )}
            <input
              className="field flex-1 min-w-[140px] !py-2 !text-[13px]"
              value={sessionMeta.pickup_zone}
              onChange={(e) => setSessionMeta((cur) => ({ ...cur, pickup_zone: e.target.value }))}
              placeholder="Pickup zone (e.g. Lobby East)"
            />
            <input
              className="field flex-1 min-w-[160px] !py-2 !text-[13px]"
              type="datetime-local"
              value={sessionMeta.move_out_deadline}
              onChange={(e) => setSessionMeta((cur) => ({ ...cur, move_out_deadline: e.target.value }))}
            />
            <button
              type="submit"
              disabled={savingSession || !sessionMetaDirty}
              className="flex items-center gap-1.5 px-4 py-2 rounded-full bg-[color:var(--color-tag)] text-white text-[12px] font-semibold hover:opacity-90 transition-opacity disabled:opacity-40 shrink-0"
            >
              <Save size={13} />
              {savingSession ? "Saving…" : "Save"}
            </button>
            <Link to={`/plan/${session.id}`} className="flex items-center gap-1.5 text-[12px] font-semibold text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] transition-colors">
              <ClipboardCheck size={13} />
              Move-Out Plan
            </Link>
            <Link to="/scan" className="flex items-center gap-1.5 text-[12px] font-semibold text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] transition-colors">
              <ArrowRight size={13} />
              All Scans
            </Link>
          </form>

          {/* Hint text */}
          <p className="text-[12px] text-[color:var(--text-muted)] px-1">
            Drag to draw a hotspot around any item · click to select · corner handles to resize.
            Press <kbd className="rounded border border-black/10 dark:border-white/10 bg-[color:var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[10px]">?</kbd> for shortcuts.
          </p>
        </div>

        {/* ── Right: draft panel ── */}
        <div className="w-full lg:w-[400px] shrink-0 lg:sticky lg:top-24 self-start space-y-4">

          {/* Draft header */}
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-[20px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
                {activeDraft
                  ? `Item Draft${draftIndex >= 0 ? ` #${draftIndex + 1}` : ""}`
                  : "No Draft Selected"}
              </h2>
              {totalDrafts > 0 && (
                <p className="text-[12px] text-[color:var(--text-muted)] mt-0.5">
                  {draftIndex >= 0 ? draftIndex + 1 : "—"} of {totalDrafts} tagged
                </p>
              )}
            </div>
            {activeDraft && (
              <button
                type="button"
                onClick={() => setConfirmDeleteDraft(true)}
                className="text-[12px] font-semibold text-[color:var(--text-muted)] hover:text-red-500 transition-colors"
              >
                Remove
              </button>
            )}
          </div>

          {activeDraft ? (
            <form onSubmit={handleSaveDraft} className="space-y-4">
              {/* Draft card */}
              <div className="bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] rounded-[20px] border border-black/8 dark:border-white/8 p-5 space-y-4">

                {/* Status chips */}
                <div className="flex flex-wrap items-center gap-2">
                  <PublishReadinessPill status={activeDraft.publish_readiness} />
                  <span className="inline-flex items-center rounded-full border border-black/8 dark:border-white/8 bg-[color:var(--color-surface-2)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--text-muted)]">
                    {formatLabel(activeDraft.triage_status)}
                  </span>
                </div>

                {/* Dirty notice */}
                {draftInspectorDirty && (
                  <DraftStatusNotice
                    message="Unsaved changes"
                    detail="Save this draft before leaving the studio."
                  />
                )}

                {/* Missing fields */}
                {activeDraft.missing_fields.length > 0 && (
                  <div className="rounded-[14px] border border-[color:var(--color-urgent)]/20 bg-[rgba(201,100,68,0.07)] p-3.5">
                    <div className="flex items-center gap-2 mb-2">
                      <CircleAlert size={14} className="text-[color:var(--color-urgent)]" />
                      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-urgent)]">Missing before publish</p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {activeDraft.missing_fields.map((field) => (
                        <span key={field} className="rounded-full bg-white/80 dark:bg-black/30 px-2.5 py-1 text-[11px] font-semibold text-[color:var(--color-urgent)]">
                          {formatLabel(field)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* AI suggestion */}
                {activeDraft.suggestion_pack && (
                  <div className="rounded-[14px] border border-[color:var(--color-teal)]/30 bg-[color:var(--color-teal-soft)] p-3.5 dark:bg-[rgba(11,122,114,0.12)]">
                    <div className="flex items-center justify-between gap-3 mb-2.5">
                      <div className="flex items-center gap-2">
                        <Wand2 size={14} className="text-[color:var(--color-teal)] shrink-0" />
                        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-teal)]">AI Suggestion</p>
                      </div>
                      <button
                        type="button"
                        onClick={applyAiSuggestion}
                        className="shrink-0 rounded-full bg-[color:var(--color-teal)] px-3 py-1 text-[11px] font-bold text-white hover:opacity-90 transition-opacity"
                      >
                        Apply All
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
                      <span className="text-[color:var(--text-muted)]">Title</span>
                      <span className="font-semibold truncate text-[color:var(--color-ink)] dark:text-white">{activeDraft.suggestion_pack.title}</span>
                      <span className="text-[color:var(--text-muted)]">Category</span>
                      <span className="font-semibold text-[color:var(--color-ink)] dark:text-white">{formatLabel(activeDraft.suggestion_pack.category)}</span>
                      <span className="text-[color:var(--text-muted)]">Condition</span>
                      <span className="font-semibold text-[color:var(--color-ink)] dark:text-white">{formatLabel(activeDraft.suggestion_pack.default_condition)}</span>
                      <span className="text-[color:var(--text-muted)]">Price</span>
                      <span className="font-semibold text-[color:var(--color-ink)] dark:text-white">
                        {activeDraft.suggestion_pack.default_price_type === "free"
                          ? "Free"
                          : `$${activeDraft.suggestion_pack.default_price_amount}`}
                      </span>
                    </div>
                  </div>
                )}

                {/* Quick presets */}
                {presets.length > 0 && (
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-2">Quick presets</p>
                    <div className="flex flex-wrap gap-1.5">
                      {presets.map((preset) => (
                        <button
                          key={preset.key}
                          type="button"
                          onClick={() => applyPresetToDraft(preset)}
                          className={cn(
                            "rounded-full px-3 py-1 text-[12px] font-semibold border transition-all",
                            draftForm.preset_key === preset.key
                              ? "border-[color:var(--color-tag)] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]"
                              : "border-black/10 dark:border-white/10 text-[color:var(--text-muted)] hover:border-[color:var(--color-tag)] hover:text-[color:var(--color-tag)]",
                          )}
                        >
                          {preset.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Title */}
                <input
                  className="field !text-[15px]"
                  value={draftForm.title}
                  onChange={(e) => setDraftForm((cur) => ({ ...cur, title: e.target.value }))}
                  placeholder="Item title"
                  required
                />

                {/* Category + Condition */}
                <div className="grid grid-cols-2 gap-3">
                  <select
                    className="field !text-[13px]"
                    value={draftForm.category}
                    onChange={(e) => setDraftForm((cur) => ({ ...cur, category: e.target.value }))}
                  >
                    <option value="storage">Storage</option>
                    <option value="lighting">Lighting</option>
                    <option value="supplies">School supplies</option>
                    <option value="comfort">Comfort</option>
                    <option value="toiletries">Toiletries</option>
                    <option value="decor">Decor</option>
                    <option value="other">Other</option>
                  </select>
                  <select
                    className="field !text-[13px]"
                    value={draftForm.condition}
                    onChange={(e) => setDraftForm((cur) => ({ ...cur, condition: e.target.value }))}
                  >
                    <option value="new">Like new</option>
                    <option value="good">Good</option>
                    <option value="fair">Fair</option>
                  </select>
                </div>

                {/* Price */}
                <div className="grid grid-cols-2 gap-3">
                  <select
                    className="field !text-[13px]"
                    value={draftForm.price_type}
                    onChange={(e) => setDraftForm((cur) => ({ ...cur, price_type: e.target.value }))}
                  >
                    <option value="free">Free</option>
                    <option value="low_cost">Low cost</option>
                  </select>
                  <input
                    className="field !text-[13px]"
                    type="number"
                    step="0.01"
                    min="0"
                    value={draftForm.price_amount}
                    onChange={(e) => setDraftForm((cur) => ({ ...cur, price_amount: e.target.value }))}
                    disabled={draftForm.price_type !== "low_cost"}
                    placeholder="Price"
                  />
                </div>

                {/* Retail value */}
                <input
                  className="field !text-[13px]"
                  type="number"
                  step="0.01"
                  min="0"
                  value={draftForm.estimated_retail_value}
                  onChange={(e) => setDraftForm((cur) => ({ ...cur, estimated_retail_value: e.target.value }))}
                  placeholder="Estimated retail value ($)"
                />

                {/* Triage 2×2 grid */}
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-2">Triage Decision</p>
                  <div className="grid grid-cols-2 gap-2">
                    {TRIAGE_ORDER.map((status) => {
                      const hotkey = Object.entries(TRIAGE_KEYS).find(([, v]) => v === status)?.[0];
                      return (
                        <button
                          key={status}
                          type="button"
                          onClick={() => setDraftForm((cur) => ({ ...cur, triage_status: status }))}
                          className={cn(
                            "py-3 rounded-[14px] text-[13px] font-semibold capitalize transition-all border",
                            draftForm.triage_status === status
                              ? "bg-[color:var(--color-tag)] border-[color:var(--color-tag)] text-white shadow-sm"
                              : "bg-[color:var(--color-surface-2)] border-black/8 dark:border-white/8 text-[color:var(--text-muted)] hover:border-[color:var(--color-tag)]/40",
                          )}
                        >
                          {status.charAt(0).toUpperCase() + status.slice(1)}
                          {hotkey && <kbd className="block text-[10px] opacity-50 font-mono font-normal">{hotkey.toUpperCase()}</kbd>}
                        </button>
                      );
                    })}
                  </div>
                  {/* Review + Done extras */}
                  <div className="grid grid-cols-2 gap-2 mt-2">
                    {["review", "done"].map((status) => (
                      <button
                        key={status}
                        type="button"
                        onClick={() => setDraftForm((cur) => ({ ...cur, triage_status: status }))}
                        className={cn(
                          "py-2.5 rounded-[12px] text-[12px] font-semibold capitalize transition-all border",
                          draftForm.triage_status === status
                            ? "bg-[color:var(--color-tag)] border-[color:var(--color-tag)] text-white"
                            : "bg-[color:var(--color-surface-2)] border-black/8 dark:border-white/8 text-[color:var(--text-muted)] hover:border-[color:var(--color-tag)]/40",
                        )}
                      >
                        {status.charAt(0).toUpperCase() + status.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Notes */}
                <textarea
                  className="field min-h-[90px] !text-[13px]"
                  value={draftForm.notes}
                  onChange={(e) => setDraftForm((cur) => ({ ...cur, notes: e.target.value }))}
                  placeholder="Notes for the next student — condition, what's included, etc."
                />

                {/* Linked listing */}
                {activeDraft.linked_listing_detail && (
                  <div className="rounded-[14px] border border-black/8 dark:border-white/8 bg-[color:var(--color-surface-2)] p-3.5">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-1">Published rescue listing</p>
                    <p className="text-[15px] font-bold text-[color:var(--color-ink)] dark:text-white mb-3">{activeDraft.linked_listing_detail.title}</p>
                    <Link to={`/listings/${activeDraft.linked_listing_detail.id}`} className="flex items-center gap-1.5 text-[13px] font-semibold text-[color:var(--color-teal)] hover:opacity-80 transition-opacity">
                      <CheckCircle2 size={14} />
                      Open Listing
                    </Link>
                  </div>
                )}
              </div>

              {/* Confirm & Next — primary CTA */}
              <button
                type="button"
                onClick={handleConfirmAndNext}
                disabled={savingDraft}
                className="w-full py-4 rounded-full bg-[color:var(--color-tag)] text-white text-[15px] font-bold tracking-[-0.01em] hover:opacity-90 transition-opacity disabled:opacity-50 active:scale-[0.98]"
              >
                {savingDraft ? "Saving…" : draftIndex >= 0 && draftIndex < totalDrafts - 1 ? "Confirm & Next Item" : "Confirm & Done"}
              </button>

              {/* Secondary actions */}
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={savingDraft}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-full border border-black/10 dark:border-white/10 text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white hover:bg-[color:var(--color-surface)] transition-colors disabled:opacity-50"
                >
                  <Save size={14} />
                  {savingDraft ? "Saving…" : "Save Draft"}
                </button>
                <Link
                  to={`/publish/${session.id}`}
                  className="flex items-center gap-1.5 py-2.5 px-4 rounded-full border border-black/10 dark:border-white/10 text-[13px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface)] transition-colors"
                >
                  <Sparkles size={14} />
                  Publish
                </Link>
              </div>
            </form>
          ) : (
            <div className="bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] rounded-[20px] border border-dashed border-black/15 dark:border-white/15 p-8 text-center space-y-3">
              <Camera size={32} className="mx-auto text-[color:var(--text-muted)] opacity-50" />
              <p className="text-[14px] font-medium text-[color:var(--text-muted)]">
                Draw a hotspot on the room photo, or click an existing pin to inspect it here.
              </p>
            </div>
          )}

          {/* All drafts list */}
          {session.items.length > 0 && (
            <div className="bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] rounded-[20px] border border-black/8 dark:border-white/8 overflow-hidden">
              <div className="px-5 py-4 border-b border-black/6 dark:border-white/6">
                <p className="text-[12px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">All Drafts</p>
              </div>
              <div className="divide-y divide-black/4 dark:divide-white/4 max-h-[420px] overflow-y-auto">
                {session.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => requestDraftSelection(item.id, item.source_image)}
                    className={cn(
                      "w-full text-left px-5 py-3.5 transition-colors hover:bg-[color:var(--color-surface-2)]",
                      item.id === selectedDraftId && "bg-[color:var(--color-tag-soft)] dark:bg-[rgba(106,0,47,0.12)]",
                    )}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <PublishReadinessPill status={item.publish_readiness} className="!text-[0.55rem] !px-2 !py-1" />
                      <span className="text-[11px] font-semibold text-[color:var(--text-muted)]">{formatLabel(item.triage_status)}</span>
                    </div>
                    <p className={cn(
                      "text-[14px] font-semibold leading-snug",
                      item.id === selectedDraftId ? "text-[color:var(--color-tag)]" : "text-[color:var(--color-ink)] dark:text-white",
                    )}>
                      {item.title}
                    </p>
                    <p className="text-[12px] text-[color:var(--text-muted)] mt-0.5">
                      View {session.images.find((img) => img.id === item.source_image)?.position || 1}
                      {item.linked_listing_detail ? " · published" : ` · ${formatLabel(item.price_type)}`}
                    </p>
                  </button>
                ))}
              </div>
              {session.move_out_deadline && (
                <div className="px-5 py-3 border-t border-black/6 dark:border-white/6">
                  <p className="text-[11px] text-[color:var(--text-muted)]">
                    Move-out deadline: {formatDateTime(session.move_out_deadline)}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
