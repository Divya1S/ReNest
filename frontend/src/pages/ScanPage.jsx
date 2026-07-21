import { ArrowRight, Camera, ClipboardCheck, Clock3, Layers3, Target } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import DraftStatusNotice from "../components/DraftStatusNotice";
import PageSection from "../components/PageSection";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePersistentDraftState } from "../hooks/usePersistentDraftState";
import { useUnsavedChangesGuard } from "../hooks/useUnsavedChangesGuard";
import { apiFetch, asResults } from "../lib/api";
import { formatDateTime, formatLabel } from "../lib/formatters";

function localDatePlusDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const initialForm = {
  name: "",
  room_label: "",
  room_type: "dorm_room",
  pickup_zone: "",
  // Pre-filled a week out so the deadline can never silently block submit;
  // the calendar picker makes changing it one tap.
  move_out_deadline: localDatePlusDays(7),
};

export default function ScanPage() {
  usePageTitle("Room Rescue Scan");
  const navigate = useNavigate();
  const [sessions, setSessions] = useState([]);
  const scanDraft = usePersistentDraftState({
    key: "scan-session-create",
    initialValue: initialForm,
  });
  const { state: form, setState: setForm } = scanDraft;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const unsavedGuard = useUnsavedChangesGuard({
    when: scanDraft.isDirty && !saving,
    title: "Leave before creating this scan?",
    message:
      "Your scan setup is still in progress. The form is autosaved locally, but leaving now will interrupt creating the new scan session.",
  });

  async function loadSessions() {
    const data = await apiFetch("/scan-sessions");
    setSessions(asResults(data));
  }

  useEffect(() => {
    let active = true;

    loadSessions()
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
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");

    try {
      const response = await apiFetch("/scan-sessions", {
        method: "POST",
        body: {
          ...form,
          // Date-only picker; the deadline lands at 5pm local that day.
          // (Old autosaved drafts may still carry a datetime-local string —
          // slicing keeps both shapes valid.)
          move_out_deadline: new Date(
            `${form.move_out_deadline.slice(0, 10)}T17:00`,
          ).toISOString(),
        },
      });
      toast.success("Room Rescue Scan created.");
      scanDraft.clearDraft({ reset: true });
      navigate(`/scan/${response.id}`);
    } catch (requestError) {
      setError(requestError.message);
      toast.error(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <UnsavedChangesDialog {...unsavedGuard.dialogProps} />
      <div className="space-y-8">
      <PageSection className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <section className="paper-panel p-8 sm:p-10 relative overflow-hidden visual-scan-hero">
          <div className="sticker bg-[color:var(--color-tag)] text-white">
            <Camera size={14} />
            Room Rescue Scan
          </div>
          <h1 className="mt-5 max-w-4xl text-[34px] font-bold leading-[1.15] tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white sm:text-[42px]">
            Turn one messy move-out room into a structured rescue plan.
          </h1>
          <p className="mt-5 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
            Upload 2 to 4 room photos, tag rescue-worthy items with hotspots, and move them into a clear-out board so the whole room gets handled before deadline day spirals.
          </p>
        </section>

        <section className="soft-panel p-7">
          <p className="label-title">Why this workflow is different</p>
          <h2 className="mt-2 text-[24px] font-bold tracking-[-0.02em]">Move-out chaos is a room-scale problem, not a single-listing problem.</h2>
          <div className="mt-6 space-y-3">
            <div className="flex items-start gap-4 rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] px-4 py-4">
              <div className="rounded-[1rem] bg-[color:var(--color-tag-soft)] p-3 text-[color:var(--color-tag)]">
                <Target size={18} />
              </div>
              <div>
                <p className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">Tag first, decide second</p>
                <p className="mt-1 text-sm leading-6 text-[color:var(--text-muted)]">
                  Keep the full room context visible while deciding what deserves a rescue listing and what belongs in a donation flow.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-4 rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] px-4 py-4">
              <div className="rounded-[1rem] bg-[color:var(--color-teal-soft)] p-3 text-[color:var(--color-teal)]">
                <Layers3 size={18} />
              </div>
              <div>
                <p className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">Bulk-publish the good items</p>
                <p className="mt-1 text-sm leading-6 text-[color:var(--text-muted)]">
                  Drafts become real listings or donation routes without rebuilding the same metadata from scratch for every item.
                </p>
              </div>
            </div>
          </div>
        </section>
      </PageSection>

      <PageSection className="scan-grid" delay={0.04}>
        <section className="soft-panel p-7">
          <p className="label-title">Start a new scan</p>
          <h2 className="mt-1 text-[24px] font-bold tracking-[-0.02em]">Set the room, pickup zone, and deadline first.</h2>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-[color:var(--text-muted)]">
            A good scan session keeps the room label, pickup zone, and final deadline locked in from the start so every draft item can inherit the right rescue context.
          </p>
          <form onSubmit={handleSubmit} className="mt-6 grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <DraftStatusNotice
                lastSavedAt={scanDraft.lastSavedAt}
                hasRestoredDraft={scanDraft.hasRestoredDraft}
                onDiscard={() => scanDraft.clearDraft({ reset: true })}
              />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="scan-name" className="field-label">Scan name</label>
              <input
                id="scan-name"
                className="field"
                placeholder='e.g. "Room 204 final clear-out"'
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                required
              />
            </div>
            <div>
              <label htmlFor="scan-room-label" className="field-label">Room label</label>
              <input
                id="scan-room-label"
                className="field"
                placeholder="e.g. North Hall 204"
                value={form.room_label}
                onChange={(event) => setForm((current) => ({ ...current, room_label: event.target.value }))}
                required
              />
            </div>
            <div>
              <label htmlFor="scan-room-type" className="field-label">Room type</label>
              <select
                id="scan-room-type"
                className="field"
                value={form.room_type}
                onChange={(event) => setForm((current) => ({ ...current, room_type: event.target.value }))}
              >
                <option value="dorm_room">Dorm room</option>
                <option value="suite">Suite</option>
                <option value="apartment">Apartment</option>
                <option value="bathroom">Bathroom</option>
                <option value="storage_corner">Storage corner</option>
              </select>
            </div>
            <div>
              <label htmlFor="scan-pickup-zone" className="field-label">Pickup zone</label>
              <input
                id="scan-pickup-zone"
                className="field"
                placeholder="e.g. North Hall lobby"
                value={form.pickup_zone}
                onChange={(event) => setForm((current) => ({ ...current, pickup_zone: event.target.value }))}
                required
              />
            </div>
            <div>
              <label htmlFor="scan-deadline" className="field-label">Move-out deadline</label>
              <input
                id="scan-deadline"
                className="field"
                type="date"
                min={localDatePlusDays(0)}
                value={form.move_out_deadline.slice(0, 10)}
                onChange={(event) => setForm((current) => ({ ...current, move_out_deadline: event.target.value }))}
                required
              />
            </div>
            {error ? <p role="alert" className="sm:col-span-2 text-sm font-bold text-[color:var(--color-urgent)]">{error}</p> : null}
            <button type="submit" disabled={saving} className="primary-button sm:col-span-2">
              <ArrowRight size={15} />
              {saving ? "Creating..." : "Create Scan Studio"}
            </button>
          </form>
        </section>

        <section className="soft-panel p-6">
          <p className="label-title">How the studio works</p>
          <div className="mt-4 space-y-4">
            <div className="paper-panel p-4">
              <p className="label-title">01</p>
              <p className="mt-2 text-[15px] font-semibold">Upload 2 to 4 room photos.</p>
            </div>
            <div className="paper-panel p-4">
              <p className="label-title">02</p>
              <p className="mt-2 text-[15px] font-semibold">Tag rescue-worthy items with hotspots.</p>
            </div>
            <div className="paper-panel p-4">
              <p className="label-title">03</p>
              <p className="mt-2 text-[15px] font-semibold">Send drafts to the board and publish in bulk.</p>
            </div>
          </div>
        </section>
      </PageSection>

      <PageSection className="space-y-5" delay={0.08}>
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="label-title">Your scan sessions</p>
            <h2 className="text-[24px] font-bold tracking-[-0.02em]">Continue where your clear-out left off.</h2>
          </div>
        </div>

        {loading ? (
          <div role="status" aria-label="Loading scan sessions" className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-ink)] dark:text-white">
            Loading scan sessions…
          </div>
        ) : null}
        {!loading && error ? (
          <div role="alert" className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-urgent)]">{error}</div>
        ) : null}

        {!loading && !error ? (
          sessions.length ? (
            <div className="grid gap-6 lg:grid-cols-2">
              {sessions.map((session) => (
                <article key={session.id} className="bulletin-card">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="scan-chip">{formatLabel(session.status)}</span>
                    <span className="scan-chip">{formatLabel(session.room_type)}</span>
                  </div>
                  <h3 className="mt-4 text-[22px] font-semibold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">{session.name}</h3>
                  <p className="mt-2 text-[color:var(--text-muted)]">{session.room_label}</p>
                  <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <div className="paper-panel p-4">
                      <p className="label-title">Progress</p>
                      <p className="mt-2 text-[24px] font-bold">{session.progress_percent}%</p>
                    </div>
                    <div className="paper-panel p-4">
                      <p className="label-title">Ready</p>
                      <p className="mt-2 text-[24px] font-bold">{session.summary.ready_to_publish_count}</p>
                    </div>
                    <div className="paper-panel p-4">
                      <p className="label-title">Blocked</p>
                      <p className="mt-2 text-[24px] font-bold">{session.summary.missing_info_count}</p>
                    </div>
                    <div className="paper-panel p-4">
                      <p className="label-title">Tasks due</p>
                      <p className="mt-2 text-[24px] font-bold">{session.task_summary?.due_today_tasks || 0}</p>
                    </div>
                  </div>
                  <div className="mt-5 flex items-center gap-2 text-sm text-[color:var(--text-muted)]">
                    <Clock3 size={16} />
                    <span>{formatDateTime(session.move_out_deadline)}</span>
                  </div>
                  <div className="mt-6 flex flex-wrap gap-3">
                    <Link to={`/scan/${session.id}`} className="primary-button">
                      <Camera size={15} />
                      Open Studio
                    </Link>
                    <Link to={`/plan/${session.id}`} className="secondary-button">
                      <ClipboardCheck size={15} />
                      Move-Out Plan
                    </Link>
                    <Link to={`/publish/${session.id}`} className="secondary-button">
                      <ArrowRight size={15} />
                      Publish Queue
                    </Link>
                    <Link to={`/clearout/${session.id}`} className="secondary-button">
                      <Layers3 size={15} />
                      Open Board
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="paper-panel p-10 text-center">
              <h2 className="text-[24px] font-bold tracking-[-0.02em]">No scan sessions yet.</h2>
              <p className="mt-3 text-[color:var(--text-muted)]">Create your first room scan above and start tagging rescue-worthy items.</p>
            </div>
          )
        ) : null}
      </PageSection>
      </div>
    </>
  );
}
