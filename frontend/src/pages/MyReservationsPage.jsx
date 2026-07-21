import { motion } from "framer-motion";
import { CheckCircle2, Clock3, Inbox, MessageCircle, Send } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import SkeletonLoader from "../components/SkeletonLoader";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch, asResults } from "../lib/api";
import { formatDateTime } from "../lib/formatters";

const listVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
};
const itemVariants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.35, ease: "easeOut" } },
};

const ReservationCard = React.memo(function ReservationCard({ reservation, onAction }) {
  return (
    <article className="bulletin-card">
      <p className="label-title">{reservation.relationship === "owner" ? "Incoming request" : "Your claim"}</p>
      <h3 className="mt-3 text-[20px] font-bold tracking-[-0.01em]">{reservation.listing_detail.title}</h3>
      <p className="mt-2 text-sm font-bold uppercase tracking-[0.18em] text-[color:var(--color-tag)]">
        {reservation.status.replaceAll("_", " ")}
      </p>
      <p className="mt-4 text-sm leading-6 text-[color:var(--text-muted)]">{reservation.pickup_time_window}</p>
      <p className="mt-3 text-sm text-[color:var(--text-muted)]">
        Move-out deadline: {formatDateTime(reservation.listing_detail.available_until)}
      </p>
      {reservation.can_leave_feedback ? (
        <p className="mt-3 inline-flex rounded-full bg-amber-100 px-3 py-1 text-[0.65rem] font-black uppercase tracking-[0.14em] text-amber-700">
          Feedback due
        </p>
      ) : null}
      {reservation.unread_messages > 0 ? (
        <Link
          to={`/handoffs/${reservation.id}`}
          className="mt-3 ml-2 inline-flex items-center gap-1.5 rounded-full bg-[color:var(--color-tag)] px-3 py-1 text-[0.65rem] font-black uppercase tracking-[0.14em] text-white"
        >
          <MessageCircle size={11} aria-hidden="true" />
          {reservation.unread_messages} new message{reservation.unread_messages === 1 ? "" : "s"}
        </Link>
      ) : null}
      <p className="mt-3 rounded-[1.3rem] bg-[color:var(--color-teal-soft)] px-4 py-3 text-sm leading-6 text-[color:var(--text-muted)]">
        {reservation.next_step}
      </p>
      <div className="mt-4 flex flex-wrap gap-3">
        <Link to={`/handoffs/${reservation.id}`} className="ghost-button">
          Open Handoff Hub
        </Link>
      </div>
      {reservation.allowed_actions.length ? (
        <div className="mt-5 flex flex-wrap gap-3">
          {reservation.allowed_actions.map((action) => (
            <button key={action} type="button" onClick={() => onAction(reservation.id, action)} className="secondary-button">
              {action.replaceAll("_", " ")}
            </button>
          ))}
        </div>
      ) : null}
    </article>
  );
});

export default function MyReservationsPage() {
  usePageTitle("My Reservations");
  const { data, loading, error, refetch, setData } = useApi("/reservations", { initialData: [] });
  const reservations = asResults(data);

  const reservationsRef = React.useRef(reservations);
  reservationsRef.current = reservations;

  const handleAction = React.useCallback(async function handleAction(reservationId, status) {
    const snapshot = reservationsRef.current;
    setData((current) => {
      // `current` may be the raw paginated envelope or a bare array.
      const patch = (r) => (r.id === reservationId ? { ...r, status, allowed_actions: [] } : r);
      return Array.isArray(current)
        ? current.map(patch)
        : { ...current, results: (current?.results ?? []).map(patch) };
    });
    try {
      await apiFetch(`/reservations/${reservationId}`, {
        method: "PATCH",
        body: { status },
      });
      await refetch();
      toast.success(`Reservation ${status.replaceAll("_", " ")}.`);
    } catch (requestError) {
      setData(snapshot);
      toast.error(requestError.message);
    }
  }, [refetch, setData]);

  const incoming = reservations.filter((reservation) => reservation.relationship === "owner");
  const outgoing = reservations.filter((reservation) => reservation.relationship === "claimant");
  const confirmedCount = reservations.filter((reservation) => reservation.status === "confirmed").length;

  if (loading) {
    return (
      <div className="space-y-8">
        <SkeletonLoader className="paper-panel h-40" />
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="paper-panel p-5 space-y-3">
              <SkeletonLoader className="h-3 w-20 !rounded-full" />
              <SkeletonLoader className="h-10 w-16 !rounded-full" />
              <SkeletonLoader className="h-3 w-40 !rounded-full" />
            </div>
          ))}
        </div>
        <div className="grid gap-8 lg:grid-cols-2">
          {Array.from({ length: 2 }, (_, col) => (
            <div key={col} className="space-y-4">
              <SkeletonLoader className="h-8 w-48 !rounded-full" />
              {Array.from({ length: 2 }, (_, i) => (
                <div key={i} className="bulletin-card space-y-3">
                  <SkeletonLoader className="h-3 w-28 !rounded-full" />
                  <SkeletonLoader className="h-6 w-3/4 !rounded-full" />
                  <SkeletonLoader className="h-3 w-20 !rounded-full" />
                  <SkeletonLoader className="h-3 w-full !rounded-full" />
                  <SkeletonLoader className="h-3 w-2/3 !rounded-full" />
                  <SkeletonLoader className="h-16 w-full !rounded-[1.3rem]" />
                  <SkeletonLoader className="h-10 w-36 !rounded-full" />
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageSection className="paper-panel p-8 sm:p-10">
        <p className="label-title">Reservations</p>
        <h1 className="mt-3 text-[34px] font-bold tracking-[-0.025em]">Coordinate handoff without losing the move-out deadline.</h1>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-3" delay={0.04}>
        <div className="paper-panel p-5">
          <p className="label-title">Incoming</p>
          <p className="metric-number mt-3">{incoming.length}</p>
          <p className="mt-4 inline-flex items-center gap-2 text-sm text-[color:var(--text-muted)]"><Inbox size={16} /> Requests on your listings</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Outgoing</p>
          <p className="metric-number mt-3">{outgoing.length}</p>
          <p className="mt-4 inline-flex items-center gap-2 text-sm text-[color:var(--text-muted)]"><Send size={16} /> Claims you have made</p>
        </div>
        <div className="paper-panel p-5">
          <p className="label-title">Confirmed</p>
          <p className="metric-number mt-3">{confirmedCount}</p>
          <p className="mt-4 inline-flex items-center gap-2 text-sm text-[color:var(--text-muted)]"><CheckCircle2 size={16} /> Handshakes ready for pickup</p>
        </div>
      </PageSection>

      {error ? <div className="paper-panel p-10 text-center text-lg font-bold text-[color:var(--color-urgent)]">{error}</div> : null}

      <PageSection className="grid gap-8 lg:grid-cols-2" delay={0.08}>
        <div className="space-y-4">
          <h2 className="inline-flex items-center gap-2 text-[24px] font-bold tracking-[-0.02em]">
            <Clock3 size={22} />
            Requests on your items
          </h2>
          {incoming.length ? (
            <motion.div variants={listVariants} initial="hidden" animate="visible" className="space-y-4">
              {incoming.map((reservation) => (
                <motion.div key={reservation.id} variants={itemVariants}>
                  <ReservationCard reservation={reservation} onAction={handleAction} />
                </motion.div>
              ))}
            </motion.div>
          ) : (
            <div className="paper-panel p-8 text-[color:var(--text-muted)]">No one has requested your listings yet.</div>
          )}
        </div>

        <div className="space-y-4">
          <h2 className="inline-flex items-center gap-2 text-[24px] font-bold tracking-[-0.02em]">
            <Send size={22} />
            Items you reserved
          </h2>
          {outgoing.length ? (
            <motion.div variants={listVariants} initial="hidden" animate="visible" className="space-y-4">
              {outgoing.map((reservation) => (
                <motion.div key={reservation.id} variants={itemVariants}>
                  <ReservationCard reservation={reservation} onAction={handleAction} />
                </motion.div>
              ))}
            </motion.div>
          ) : (
            <div className="paper-panel p-8 text-[color:var(--text-muted)]">You have not reserved anything yet.</div>
          )}
        </div>
      </PageSection>
    </div>
  );
}
