import { motion, AnimatePresence } from "framer-motion";
import { Calendar, CheckCircle2, Loader2, MapPin, Package, Plus, RefreshCw, Truck } from "lucide-react";
import React from "react";
import { useState } from "react";

import { useApi } from "../hooks/useApi";
import { apiFetch, asResults } from "../lib/api";
import { cn } from "../lib/cn";

const STATUS_LABELS = {
  scheduled: { label: "Scheduled", color: "text-[var(--color-warning)]" },
  in_progress: { label: "In Progress", color: "text-[var(--color-primary)]" },
  completed: { label: "Completed", color: "text-[var(--color-success)]" },
  cancelled: { label: "Cancelled", color: "text-[var(--color-text-muted)]" },
};

function EventCard({ event, onSelect, selected }) {
  const meta = STATUS_LABELS[event.status] ?? STATUS_LABELS.scheduled;
  return (
    <motion.button
      layout
      onClick={() => onSelect(event)}
      className={cn(
        "w-full text-left rounded-xl border p-4 transition-colors",
        selected
          ? "border-[var(--color-primary)] bg-[var(--color-primary-subtle)]"
          : "border-[var(--color-border)] bg-[var(--color-surface-raised)] hover:border-[var(--color-primary-muted)]"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-medium text-[var(--color-text)] text-sm">{event.title}</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            {new Date(event.scheduled_at).toLocaleString([], {
              month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
            })}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          <span className={cn("text-xs font-medium", meta.color)}>{meta.label}</span>
          <span className="text-xs text-[var(--color-text-muted)]">{event.holds_count} item{event.holds_count !== 1 ? "s" : ""}</span>
        </div>
      </div>
    </motion.button>
  );
}

function RouteStop({ stop, index }) {
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div className="w-7 h-7 rounded-full bg-[var(--color-primary)] flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
          {index + 1}
        </div>
        <div className="w-px flex-1 bg-[var(--color-border)] mt-1" />
      </div>
      <div className="pb-4 flex-1 min-w-0">
        <p className="font-medium text-sm text-[var(--color-text)] flex items-center gap-1.5">
          <MapPin size={13} className="text-[var(--color-primary)]" />
          {stop.building}
        </p>
        <ul className="mt-1.5 space-y-1">
          {stop.items.map((item) => (
            <li key={item.hold_id} className="text-xs text-[var(--color-text-muted)] flex items-center gap-1.5">
              <Package size={11} />
              <span className="truncate">{item.listing_title}</span>
              {item.pickup_zone && (
                <span className="flex-shrink-0 text-[var(--color-text-subtle)]">— {item.pickup_zone}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function NewEventModal({ hubId, onCreated, onClose }) {
  const [title, setTitle] = useState("Collection Run");
  const [scheduledAt, setScheduledAt] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (!scheduledAt) { setError("Pick a date/time."); return; }
    setSaving(true);
    try {
      const data = await apiFetch("/logistics/events", {
        method: "POST",
        body: { hub_id: hubId, title, scheduled_at: new Date(scheduledAt).toISOString() },
      });
      onCreated(data);
    } catch {
      setError("Failed to create event.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="bg-[var(--color-surface)] rounded-2xl p-6 w-full max-w-sm shadow-xl"
      >
        <h3 className="font-semibold text-[var(--color-text)] mb-4">Schedule Collection Run</h3>
        <form onSubmit={submit} className="space-y-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Run title"
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
          />
          <input
            type="datetime-local"
            value={scheduledAt}
            onChange={(e) => setScheduledAt(e.target.value)}
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-primary)]"
          />
          {error && <p className="text-xs text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose} className="flex-1 rounded-lg border border-[var(--color-border)] py-2 text-sm text-[var(--color-text-muted)]">
              Cancel
            </button>
            <button type="submit" disabled={saving} className="flex-1 rounded-lg bg-[var(--color-primary)] py-2 text-sm text-white disabled:opacity-50">
              {saving ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
}

export default function HubDispatchPage() {
  // Dependent fetch: first resolve which hub this manager owns, then load its events.
  const { data: hubsData } = useApi("/hubs/mine");
  const hubId = asResults(hubsData)[0]?.id ?? null;

  const eventsPath = hubId ? `/logistics/events?hub=${hubId}` : null;
  const {
    data: eventsData,
    loading: loadingEvents,
    error: eventsError,
    refetch: refetchEvents,
    setData: setEventsData,
  } = useApi(eventsPath);
  const events = eventsData ?? [];

  const [selected, setSelected] = useState(null);
  const [route, setRoute] = useState(null);
  const [loadingRoute, setLoadingRoute] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [completing, setCompleting] = useState(false);

  async function selectEvent(event) {
    setSelected(event);
    setRoute(null);
    setLoadingRoute(true);
    try {
      const data = await apiFetch(`/logistics/events/${event.id}/route`);
      setRoute(data);
    } catch {
      setRoute(null);
    } finally {
      setLoadingRoute(false);
    }
  }

  async function completeEvent() {
    if (!selected) return;
    setCompleting(true);
    try {
      const updated = await apiFetch(`/logistics/events/${selected.id}`, {
        method: "PATCH",
        body: { status: "completed" },
      });
      setEventsData((prev) => (prev ?? []).map((e) => (e.id === updated.id ? updated : e)));
      setSelected(updated);
    } catch {
      // ignore
    } finally {
      setCompleting(false);
    }
  }

  function onCreated(event) {
    setEventsData((prev) => [event, ...(prev ?? [])]);
    setShowModal(false);
    selectEvent(event);
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-[var(--color-text)] flex items-center gap-2">
            <Truck size={20} className="text-[var(--color-primary)]" />
            Hub Dispatch
          </h1>
          <p className="text-sm text-[var(--color-text-muted)] mt-0.5">Schedule and run collection events</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          disabled={!hubId}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-2 text-sm text-white disabled:opacity-40"
        >
          <Plus size={15} />
          New Run
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Event list */}
        <div className="space-y-2">
          {loadingEvents ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-20 rounded-xl animate-pulse bg-[var(--color-surface-raised)]" />
              ))}
            </div>
          ) : eventsError ? (
            <div className="rounded-xl border border-dashed border-[var(--color-border)] p-8 text-center">
              <p className="text-sm text-[var(--color-text-muted)] mb-3">{eventsError}</p>
              <button
                onClick={() => refetchEvents()}
                className="inline-flex items-center gap-1.5 text-xs text-[var(--color-primary)] hover:opacity-80"
              >
                <RefreshCw size={13} /> Retry
              </button>
            </div>
          ) : events.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--color-border)] p-8 text-center text-sm text-[var(--color-text-muted)]">
              <Calendar size={28} className="mx-auto mb-2 opacity-40" />
              No collection runs yet
            </div>
          ) : (
            events.map((event) => (
              <EventCard
                key={event.id}
                event={event}
                selected={selected?.id === event.id}
                onSelect={selectEvent}
              />
            ))
          )}
        </div>

        {/* Route panel */}
        <div>
          {!selected ? (
            <div className="rounded-xl border border-dashed border-[var(--color-border)] p-8 text-center text-sm text-[var(--color-text-muted)]">
              Select a run to view its route
            </div>
          ) : (
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-raised)] p-5">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <p className="font-semibold text-[var(--color-text)]">{selected.title}</p>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    {route ? `${route.stop_count} stop${route.stop_count !== 1 ? "s" : ""}` : "—"}
                  </p>
                </div>
                {selected.status !== "completed" && (
                  <button
                    onClick={completeEvent}
                    disabled={completing}
                    className="flex items-center gap-1.5 rounded-lg bg-[var(--color-success)] px-3 py-1.5 text-xs text-white disabled:opacity-50"
                  >
                    {completing ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
                    Complete
                  </button>
                )}
              </div>

              {loadingRoute ? (
                <div className="flex justify-center py-6">
                  <Loader2 size={20} className="animate-spin text-[var(--color-text-muted)]" />
                </div>
              ) : route?.stops?.length ? (
                <div className="mt-2">
                  {route.stops.map((stop, i) => (
                    <RouteStop key={stop.building} stop={stop} index={i} />
                  ))}
                </div>
              ) : (
                <p className="text-sm text-[var(--color-text-muted)] text-center py-4">No active holds assigned.</p>
              )}
            </div>
          )}
        </div>
      </div>

      <AnimatePresence>
        {showModal && hubId && (
          <NewEventModal hubId={hubId} onCreated={onCreated} onClose={() => setShowModal(false)} />
        )}
      </AnimatePresence>
    </div>
  );
}
