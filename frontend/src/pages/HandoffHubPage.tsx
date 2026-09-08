import {
  AlertCircle,
  Calendar,
  Check,
  CheckCheck,
  Clock3,
  Copy,
  KeyRound,
  MapPin,
  MessageCircle,
  Navigation,
  Plus,
  Send,
  Star,
} from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import RatingStars from "../components/RatingStars";
import SkeletonLoader from "../components/SkeletonLoader";
import StatusPill from "../components/StatusPill";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime, formatLabel } from "../lib/formatters";
import type { ChatMessage, ReservationDetail } from "../types";

const FEEDBACK_TAGS = [
  "responsive",
  "on_time",
  "easy_pickup",
  "friendly",
  "as_described",
  "great_value",
  "clear_communication",
];

function localDateTimeMin(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const ACTION_LABELS: Record<string, string> = {
  confirmed: "Approve request",
  completed: "Mark handed off",
  cancelled: "Cancel reservation",
};

function ReservationChat({ reservationId }: { reservationId: string | undefined }) {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;

    async function loadMessages(initial = false) {
      try {
        const data = await apiFetch(`/reservations/${reservationId}/messages`);
        if (!active) return;
        const typed = data as { results?: ChatMessage[] } | ChatMessage[];
        const next = Array.isArray(typed) ? typed : (typed.results ?? []);
        // Only replace state when something changed so the scroll effect
        // doesn't fire on every poll tick.
        setMessages((prev) =>
          prev !== null && prev.length === next.length && prev.at(-1)?.id === next.at(-1)?.id
            ? prev
            : next,
        );
      } catch {
        if (active && initial) setMessages([]);
      }
    }

    loadMessages(true);
    // Pickup coordination is time-sensitive — poll while the tab is visible
    const timer = setInterval(() => {
      if (!document.hidden) loadMessages();
    }, 15_000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [reservationId]);

  useEffect(() => {
    // block:"nearest" scrolls only the chat pane — never the page itself,
    // which would yank the viewport down to the chat on load.
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  async function sendMessage(e: React.FormEvent) {
    e.preventDefault();
    const text = body.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const msg = await apiFetch<ChatMessage>(`/reservations/${reservationId}/messages`, {
        method: "POST",
        body: { body: text },
      });
      setMessages((prev) => [...(prev ?? []), msg]);
      setBody("");
    } catch {
      toast.error("Couldn't send message. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="soft-panel flex flex-col gap-0 overflow-hidden p-0">
      <div className="flex items-center gap-2 border-b border-[color:var(--color-line)] px-5 py-4">
        <MessageCircle size={16} className="text-[color:var(--color-tag)]" />
        <p className="text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white">Pickup chat</p>
        <span className="ml-auto text-[11px] text-[color:var(--text-muted)]">
          Only visible to you and the other party
        </span>
      </div>

      <div className="flex h-[26rem] flex-col gap-3 overflow-y-auto px-5 py-4">
        {messages === null ? (
          <p className="text-[13px] text-[color:var(--text-muted)]">Loading…</p>
        ) : messages.length === 0 ? (
          <p className="text-[13px] text-[color:var(--text-muted)]">
            No messages yet. Say hello to coordinate pickup.
          </p>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex flex-col gap-0.5 ${msg.is_mine ? "items-end" : "items-start"}`}
            >
              <div
                className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
                  msg.is_mine
                    ? "bg-[color:var(--color-tag)] text-white"
                    : "bg-[color:var(--color-surface)] text-[color:var(--color-ink)] dark:text-white border border-[color:var(--color-line)]"
                }`}
              >
                {msg.body}
              </div>
              <span className="text-[10px] text-[color:var(--text-muted)] px-1">
                {msg.is_mine ? "You" : msg.sender_name}
              </span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={sendMessage}
        className="flex items-center gap-2 border-t border-[color:var(--color-line)] px-4 py-3"
      >
        <input
          type="text"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Message…"
          maxLength={1000}
          className="flex-1 rounded-full border border-[color:var(--color-line)] bg-[color:var(--color-surface)] px-4 py-2 text-[13px] outline-none focus:border-[color:var(--color-tag)] transition-colors"
        />
        <button
          type="submit"
          disabled={!body.trim() || sending}
          aria-label="Send message"
          className="flex h-8 w-8 items-center justify-center rounded-full bg-[color:var(--color-tag)] text-white disabled:opacity-40 transition-opacity"
        >
          <Send size={14} />
        </button>
      </form>
    </section>
  );
}

/** Tiny three-step progress caption: Approve → Schedule → Hand off. */
function StageSteps({ current }: { current: 1 | 2 | 3 }) {
  const steps = ["Approve", "Schedule", "Hand off"];
  return (
    <ol className="flex items-center gap-2" aria-label="Handoff progress">
      {steps.map((label, i) => {
        const n = (i + 1) as 1 | 2 | 3;
        const state = n < current ? "done" : n === current ? "active" : "todo";
        return (
          <li key={label} className="flex items-center gap-2">
            {i > 0 && <span className="h-px w-5 bg-[color:var(--color-line-strong)]" aria-hidden="true" />}
            <span
              className={`flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] ${
                state === "active"
                  ? "text-[color:var(--color-tag)]"
                  : state === "done"
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-[color:var(--text-muted)]"
              }`}
            >
              {state === "done" ? (
                <Check size={12} aria-hidden="true" />
              ) : (
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    state === "active" ? "bg-[color:var(--color-tag)]" : "bg-[color:var(--color-line-strong)]"
                  }`}
                  aria-hidden="true"
                />
              )}
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export default function HandoffHubPage() {
  usePageTitle("Handoff Hub");
  const { reservationId } = useParams();
  const { data: serverReservation, loading, error, refetch } = useApi<ReservationDetail>(`/reservations/${reservationId}`);
  const [optimisticReservation, setOptimisticReservation] = React.useState<ReservationDetail | null>(null);
  const reservation = optimisticReservation ?? serverReservation;
  const [savingStatus, setSavingStatus] = React.useState("");
  // Cancelling is irreversible: it releases the listing, re-opens any matched
  // rescue request and (for an owner backing out after confirming) records a
  // trust strike. Confirm before doing it.
  const [pendingCancel, setPendingCancel] = React.useState(false);
  const [pinInput, setPinInput] = React.useState("");
  const [verifyingPin, setVerifyingPin] = React.useState(false);
  const [sendingEnRoute, setSendingEnRoute] = React.useState(false);
  const [proposingSlots, setProposingSlots] = React.useState(false);
  const [slotInputs, setSlotInputs] = React.useState([""]);
  const [feedbackRating, setFeedbackRating] = React.useState(5);
  const [feedbackTags, setFeedbackTags] = React.useState(["responsive"]);
  const [feedbackNote, setFeedbackNote] = React.useState("");
  const [submittingFeedback, setSubmittingFeedback] = React.useState(false);

  // Clear optimistic state once server data catches up
  React.useEffect(() => {
    if (serverReservation && optimisticReservation &&
        serverReservation.status === optimisticReservation.status) {
      setOptimisticReservation(null);
    }
  }, [serverReservation, optimisticReservation]);

  async function handleAction(nextStatus: string) {
    if (!reservation) return;
    const previous = reservation;
    setOptimisticReservation({ ...reservation, status: nextStatus as ReservationDetail["status"] });
    setSavingStatus(nextStatus);
    try {
      const updated = await apiFetch<ReservationDetail>(`/reservations/${reservation.id}`, {
        method: "PATCH",
        body: { status: nextStatus },
      });
      setOptimisticReservation(updated);
      await refetch();
      toast.success(`Handoff ${nextStatus.replaceAll("_", " ")}.`);
    } catch (requestError) {
      setOptimisticReservation(previous);
      toast.error((requestError as Error).message);
    } finally {
      setSavingStatus("");
    }
  }

  // Runs on submit AND automatically the moment the 6th digit lands, so a
  // pasted PIN verifies with zero extra clicks.
  async function verifyPin(value?: string) {
    const pin = (value ?? pinInput).trim();
    if (pin.length !== 6 || !reservation || verifyingPin) return;
    setVerifyingPin(true);
    try {
      await apiFetch(`/reservations/${reservation.id}/verify-pin`, { method: "POST", body: { pin } });
      setPinInput("");
      await refetch();
      toast.success("PIN verified — handoff complete!");
    } catch (err) {
      toast.error((err as Error).message || "Incorrect PIN.");
    } finally {
      setVerifyingPin(false);
    }
  }

  async function handleEnRoute() {
    if (!reservation) return;
    setSendingEnRoute(true);
    try {
      await apiFetch(`/reservations/${reservation.id}/en-route`, { method: "POST" });
      toast.success("Owner notified — they know you're on the way.");
    } catch (err) {
      toast.error((err as Error).message || "Could not send notification.");
    } finally {
      setSendingEnRoute(false);
    }
  }

  async function handleProposeSlots(e: React.FormEvent) {
    e.preventDefault();
    const filled = slotInputs.filter(Boolean);
    if (!filled.length || !reservation) return;
    setProposingSlots(true);
    try {
      // datetime-local yields a naive "2026-09-10T14:00"; the server would
      // interpret that in its own timezone, shifting the slot for everyone
      // outside it. Send an explicit UTC instant instead.
      await apiFetch(`/reservations/${reservation.id}/slots`, {
        method: "PATCH",
        body: { slots: filled.map((value) => new Date(value).toISOString()) },
      });
      await refetch();
      toast.success("Pickup times sent.");
    } catch (err) {
      toast.error((err as Error).message || "Failed to send times.");
    } finally {
      setProposingSlots(false);
    }
  }

  async function handleConfirmSlot(slot: string) {
    if (!reservation) return;
    try {
      await apiFetch(`/reservations/${reservation.id}/slots`, { method: "PATCH", body: { confirm_slot: slot } });
      await refetch();
      toast.success("Pickup time confirmed!");
    } catch (err) {
      toast.error((err as Error).message || "Failed to confirm time.");
    }
  }

  async function copyPin() {
    if (!reservation?.handoff_pin_display) return;
    try {
      await navigator.clipboard.writeText(reservation.handoff_pin_display);
      toast.success("PIN copied.");
    } catch {
      toast.error("Could not copy the PIN.");
    }
  }

  function toggleTag(tag: string) {
    setFeedbackTags((current) => {
      if (current.includes(tag)) {
        return current.filter((item) => item !== tag);
      }
      if (current.length >= 3) {
        return current;
      }
      return [...current, tag];
    });
  }

  async function handleFeedbackSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!reservation) return;
    setSubmittingFeedback(true);
    try {
      await apiFetch(`/reservations/${reservation.id}/feedback`, {
        method: "POST",
        body: {
          rating: feedbackRating,
          tags: feedbackTags,
          note: feedbackNote,
        },
      });
      setFeedbackNote("");
      await refetch();
      toast.success("Feedback saved.");
    } catch (requestError) {
      toast.error((requestError as Error).message);
    } finally {
      setSubmittingFeedback(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <SkeletonLoader className="paper-panel h-32" />
        <div className="grid gap-6 lg:grid-cols-[1fr_0.85fr]">
          <SkeletonLoader className="h-[30rem] !rounded-[2rem]" />
          <SkeletonLoader className="h-72 !rounded-[2rem]" />
        </div>
      </div>
    );
  }

  if (error || !reservation) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-red-100 text-red-500 dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle size={40} />
        </div>
        <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Handoff not found</h2>
        <p className="mt-4 text-[color:var(--text-muted)]">{error || "This reservation may have been cancelled or does not exist."}</p>
        <button onClick={() => window.history.back()} className="primary-button mt-8">Go Back</button>
      </div>
    );
  }

  const isOwner = reservation.relationship === "owner";
  const slots = reservation.pickup_slots ?? [];
  const stage: 1 | 2 | 3 | null =
    reservation.status === "requested" ? 1
    : reservation.status === "confirmed" ? (reservation.confirmed_slot ? 3 : 2)
    : null;
  const canCancel = reservation.allowed_actions.includes("cancelled");

  return (
    <div className="space-y-6">
      <UnsavedChangesDialog
        open={pendingCancel}
        kicker="Cancel reservation"
        title="Cancel this pickup?"
        message={
          isOwner
            ? "The item goes back on the board, the other person is notified, and cancelling a handoff you already confirmed counts against your reliability score."
            : "The item goes back on the board and the owner is notified. You can request it again if it is still available."
        }
        confirmLabel="Cancel reservation"
        cancelLabel="Keep it"
        onCancel={() => setPendingCancel(false)}
        onConfirm={() => {
          setPendingCancel(false);
          handleAction("cancelled");
        }}
      />

      {/* Compact header: what, with whom, where, by when. */}
      <PageSection className="paper-panel p-6 sm:p-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-[26px] font-bold leading-tight tracking-[-0.02em]">
                {reservation.listing_detail.title}
              </h1>
              <StatusPill status={reservation.status} className="" />
            </div>
            <p className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-[color:var(--text-muted)]">
              <span>{isOwner ? "Pickup by" : "From"} <strong className="font-semibold text-[color:var(--color-ink)] dark:text-white">{reservation.counterparty_name}</strong></span>
              <span className="inline-flex items-center gap-1.5"><MapPin size={13} aria-hidden="true" />{reservation.pickup_zone}</span>
              <span className="inline-flex items-center gap-1.5"><Clock3 size={13} aria-hidden="true" />Deadline {formatDateTime(reservation.move_out_deadline)}</span>
            </p>
          </div>
          <Link to={`/listings/${reservation.listing_detail.id}`} className="secondary-button shrink-0">
            View listing
          </Link>
        </div>
      </PageSection>

      <PageSection className="grid items-start gap-6 lg:grid-cols-[1fr_0.85fr]" delay={0.04}>
        {/* Chat is the heart of coordination — it leads. */}
        <ReservationChat reservationId={reservationId} />

        <div className="space-y-6">
          {/* ONE pickup card: exactly the next action for this role at this stage. */}
          <section className="soft-panel p-6">
            <div className="flex items-center justify-between gap-3">
              <p className="label-title">Pickup</p>
              {stage && <StageSteps current={stage} />}
            </div>

            {/* Stage: waiting for approval */}
            {reservation.status === "requested" && (
              isOwner ? (
                <div className="mt-4">
                  <p className="text-sm leading-6 text-[color:var(--text-muted)]">
                    {reservation.counterparty_name} wants this item. Approve to start coordinating pickup.
                  </p>
                  <div className="mt-4 flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => handleAction("confirmed")}
                      disabled={savingStatus !== ""}
                      className="primary-button"
                    >
                      <CheckCheck size={15} />
                      {savingStatus === "confirmed" ? "Approving…" : "Approve request"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setPendingCancel(true)}
                      disabled={savingStatus !== ""}
                      className="text-sm font-semibold text-[color:var(--text-muted)] hover:text-[color:var(--color-urgent)] transition-colors"
                    >
                      Decline
                    </button>
                  </div>
                </div>
              ) : (
                <p className="mt-4 text-sm leading-6 text-[color:var(--text-muted)]">
                  Waiting for {reservation.counterparty_name} to approve your request. You&apos;ll be notified the moment they do.
                </p>
              )
            )}

            {/* Stage: schedule the pickup */}
            {reservation.status === "confirmed" && !reservation.confirmed_slot && (
              <div className="mt-4">
                {isOwner ? (
                  slots.length === 0 ? (
                    <form onSubmit={handleProposeSlots}>
                      <p className="text-sm leading-6 text-[color:var(--text-muted)]">
                        Offer a pickup time — {reservation.counterparty_name} confirms with one tap.
                      </p>
                      <div className="mt-3 space-y-2">
                        {slotInputs.map((val, i) => (
                          <input
                            key={i}
                            type="datetime-local"
                            aria-label={`Pickup time option ${i + 1}`}
                            min={localDateTimeMin()}
                            value={val}
                            onChange={(e) => setSlotInputs((prev) => { const n = [...prev]; n[i] = e.target.value; return n; })}
                            className="field !min-h-0 !py-2.5 text-[14px]"
                          />
                        ))}
                      </div>
                      <div className="mt-3 flex items-center gap-3">
                        <button type="submit" disabled={proposingSlots || !slotInputs.some(Boolean)} className="primary-button">
                          {proposingSlots ? "Sending…" : "Send"}
                        </button>
                        {slotInputs.length < 3 && (
                          <button
                            type="button"
                            onClick={() => setSlotInputs((prev) => [...prev, ""])}
                            className="inline-flex items-center gap-1 text-sm font-semibold text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] transition-colors"
                          >
                            <Plus size={14} aria-hidden="true" />
                            Another option
                          </button>
                        )}
                      </div>
                    </form>
                  ) : (
                    <p className="text-sm leading-6 text-[color:var(--text-muted)]">
                      Times sent — waiting for {reservation.counterparty_name} to pick one.
                    </p>
                  )
                ) : slots.length > 0 ? (
                  <div>
                    <p className="text-sm leading-6 text-[color:var(--text-muted)]">Pick a time that works for you:</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {slots.map((slot) => (
                        <button
                          key={slot}
                          type="button"
                          onClick={() => handleConfirmSlot(slot)}
                          className="rounded-full border border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-4 py-2 text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white hover:border-[color:var(--color-tag)] hover:text-[color:var(--color-tag)] transition-colors"
                        >
                          {new Date(slot).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="text-sm leading-6 text-[color:var(--text-muted)]">
                    {reservation.counterparty_name} is choosing pickup times — or just agree on one in the chat.
                  </p>
                )}
              </div>
            )}

            {/* Stage: scheduled — show the agreed time */}
            {reservation.status === "confirmed" && reservation.confirmed_slot && (
              <div className="mt-4 flex items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-900/20 px-4 py-3">
                <CheckCheck size={16} className="shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                <p className="text-[14px] font-semibold text-emerald-900 dark:text-emerald-200">
                  {new Date(reservation.confirmed_slot).toLocaleString([], { weekday: "long", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                </p>
                <a
                  href={`/api/reservations/${reservation.id}/calendar.ics`}
                  download
                  className="ml-auto inline-flex shrink-0 items-center gap-1.5 text-[12px] font-semibold text-emerald-700 dark:text-emerald-400 hover:underline"
                >
                  <Calendar size={13} aria-hidden="true" />
                  Add to calendar
                </a>
              </div>
            )}

            {/* At pickup: PIN — claimant shows it, owner enters it. */}
            {reservation.status === "confirmed" && (
              <div className="mt-5 border-t border-[color:var(--color-line)] pt-4">
                <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[color:var(--text-muted)]">
                  <KeyRound size={12} aria-hidden="true" />
                  At pickup
                </p>
                {isOwner ? (
                  <div className="mt-2.5">
                    <p className="text-sm leading-6 text-[color:var(--text-muted)]">
                      Enter the 6-digit PIN from {reservation.counterparty_name}&apos;s screen — it completes the handoff instantly.
                    </p>
                    <form onSubmit={(e) => { e.preventDefault(); verifyPin(); }} className="mt-3 flex items-center gap-3">
                      <input
                        type="text"
                        inputMode="numeric"
                        maxLength={6}
                        aria-label="Pickup PIN"
                        value={pinInput}
                        onChange={(e) => {
                          const next = e.target.value.replace(/\D/g, "").slice(0, 6);
                          setPinInput(next);
                          if (next.length === 6) verifyPin(next);
                        }}
                        placeholder="······"
                        className="w-36 rounded-[10px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-3 py-2.5 text-center font-mono text-[20px] font-semibold tracking-[0.35em] text-[color:var(--color-ink)] dark:text-white placeholder:tracking-[0.35em] focus:outline-none focus:border-[color:var(--color-tag)] focus:ring-2 focus:ring-[color:var(--color-tag)]/10"
                      />
                      {verifyingPin && <span className="text-sm text-[color:var(--text-muted)]">Checking…</span>}
                    </form>
                  </div>
                ) : (
                  <div className="mt-2.5">
                    {reservation.handoff_pin_display ? (
                      <div className="flex flex-wrap items-center gap-4">
                        <p className="font-mono text-[34px] font-black tracking-[0.28em] text-[color:var(--color-tag)]">
                          {reservation.handoff_pin_display}
                        </p>
                        <button
                          type="button"
                          onClick={copyPin}
                          aria-label="Copy PIN"
                          className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-line-strong)] px-3 py-1.5 text-[12px] font-semibold text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] hover:border-[color:var(--color-tag)] transition-colors"
                        >
                          <Copy size={12} aria-hidden="true" />
                          Copy
                        </button>
                      </div>
                    ) : (
                      <p className="text-sm text-[color:var(--text-muted)]">Your PIN will appear here.</p>
                    )}
                    <p className="mt-1.5 text-[12px] leading-5 text-[color:var(--text-muted)]">
                      Show this to {reservation.counterparty_name} at pickup — they enter it to complete the handoff.
                    </p>
                    <button
                      type="button"
                      onClick={handleEnRoute}
                      disabled={sendingEnRoute}
                      className="mt-3 inline-flex items-center gap-2 rounded-full bg-[color:var(--color-teal)] px-4 py-2 text-[13px] font-semibold text-white hover:opacity-90 disabled:opacity-60 transition-opacity"
                    >
                      <Navigation size={13} aria-hidden="true" />
                      {sendingEnRoute ? "Notifying…" : "I'm on my way"}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Stage: done / stalled / cancelled */}
            {reservation.status === "completed" && (
              <p className="mt-4 flex items-center gap-2 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
                <CheckCheck size={16} aria-hidden="true" />
                Handoff complete. Nice rescue.
              </p>
            )}
            {reservation.status === "cancelled" && (
              <p className="mt-4 text-sm text-[color:var(--text-muted)]">This reservation was cancelled.</p>
            )}
            {reservation.status === "expired_unresolved" && (
              <div className="mt-4">
                <p className="text-sm leading-6 text-[color:var(--color-urgent)]">
                  This handoff passed 48 hours without completion. Mark it complete or cancel to unblock the listing.
                </p>
                <div className="mt-3 flex flex-wrap gap-3">
                  {reservation.allowed_actions.map((action) => (
                    <button
                      key={action}
                      type="button"
                      onClick={() => (action === "cancelled" ? setPendingCancel(true) : handleAction(action))}
                      disabled={savingStatus === action}
                      className="primary-button"
                    >
                      {savingStatus === action ? "Working…" : ACTION_LABELS[action] ?? formatLabel(action)}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Quiet escape hatch — never a primary button. */}
            {canCancel && reservation.status === "confirmed" && (
              <button
                type="button"
                onClick={() => setPendingCancel(true)}
                disabled={savingStatus !== ""}
                className="mt-4 text-[12px] font-semibold text-[color:var(--text-muted)] hover:text-[color:var(--color-urgent)] transition-colors"
              >
                Cancel this reservation
              </button>
            )}
          </section>

          {/* Feedback — only exists once the handoff is done. */}
          {reservation.status === "completed" && (
            <section className="soft-panel p-6">
              <div className="flex items-center justify-between gap-3">
                <p className="label-title">Feedback</p>
                <div className="flex items-center gap-2">
                  <RatingStars rating={reservation.feedback_summary?.average_rating || 0} size={14} className="" />
                  <span className="text-[12px] font-semibold text-[color:var(--text-muted)]">
                    {reservation.feedback_summary?.count
                      ? `${reservation.feedback_summary.average_rating} / 5`
                      : "None yet"}
                  </span>
                </div>
              </div>

              {reservation.can_leave_feedback ? (
                <form onSubmit={handleFeedbackSubmit} className="mt-4 space-y-4">
                  <div className="flex flex-wrap gap-2">
                    {[1, 2, 3, 4, 5].map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-label={`${value} star${value !== 1 ? "s" : ""}`}
                        aria-pressed={feedbackRating === value}
                        onClick={() => setFeedbackRating(value)}
                        className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-bold transition ${
                          feedbackRating === value
                            ? "border-amber-300 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400"
                            : "border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] text-[color:var(--text-muted)] hover:border-amber-300 hover:text-amber-700 dark:hover:text-amber-400"
                        }`}
                      >
                        <Star size={14} aria-hidden="true" className={feedbackRating >= value ? "fill-amber-400 text-amber-400" : ""} />
                        {value}
                      </button>
                    ))}
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {FEEDBACK_TAGS.map((tag) => {
                      const active = feedbackTags.includes(tag);
                      return (
                        <button
                          key={tag}
                          type="button"
                          aria-pressed={active}
                          onClick={() => toggleTag(tag)}
                          className={`rounded-full border px-3 py-1.5 text-[11px] font-bold uppercase tracking-[0.08em] transition ${
                            active
                              ? "border-[color:var(--color-tag)] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]"
                              : "border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] text-[color:var(--text-muted)] hover:border-[color:var(--color-tag)] hover:text-[color:var(--color-tag)]"
                          }`}
                        >
                          {formatLabel(tag)}
                        </button>
                      );
                    })}
                  </div>

                  <textarea
                    value={feedbackNote}
                    onChange={(event) => setFeedbackNote(event.target.value)}
                    className="field min-h-[90px]"
                    aria-label="Feedback note"
                    placeholder="A short note about how the pickup went (optional)."
                  />

                  <button type="submit" disabled={submittingFeedback} className="primary-button w-full">
                    {submittingFeedback ? "Saving…" : "Save feedback"}
                  </button>
                </form>
              ) : reservation.my_feedback ? (
                <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-900/20 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2.5">
                      <RatingStars rating={reservation.my_feedback.rating} size={15} className="" />
                      <p className="text-[14px] font-semibold text-emerald-900 dark:text-emerald-200">{reservation.my_feedback.rating} / 5</p>
                    </div>
                    <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-emerald-700 dark:text-emerald-400">Saved</span>
                  </div>
                  {reservation.my_feedback.note ? (
                    <p className="mt-2 text-sm leading-6 text-emerald-900/80 dark:text-emerald-200/80">{reservation.my_feedback.note}</p>
                  ) : null}
                </div>
              ) : null}

              {reservation.feedback_entries?.length ? (
                <div className="mt-4 space-y-3">
                  {reservation.feedback_entries.map((entry) => (
                    <div key={entry.id} className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-surface)] px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-sm font-bold">{entry.reviewer.display_name}</p>
                        <RatingStars rating={entry.rating} className="" />
                      </div>
                      {entry.note ? (
                        <p className="mt-2 text-sm leading-6 text-[color:var(--text-muted)]">{entry.note}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          )}
        </div>
      </PageSection>
    </div>
  );
}
