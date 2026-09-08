import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowRight,
  BadgeInfo,
  Camera,
  ChevronRight,
  Clock3,
  Flag,
  MapPin,
  NotebookPen,
  Share2,
  Shield,
  Sparkles,
  Trash2,
} from "lucide-react";
import React, { useState, useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import Avatar from "../components/Avatar";
import ListingPlaceholder from "../components/ListingPlaceholder";
import ListingUpdatesPanel from "../components/ListingUpdatesPanel";
import RatingStars from "../components/RatingStars";
import SaveButton from "../components/SaveButton";
import StatusPill from "../components/StatusPill";
import UnsavedChangesDialog from "../components/UnsavedChangesDialog";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatCurrencyValue, formatDateTime, formatMoney } from "../lib/formatters";
import type { Listing } from "../types";

function CountdownTimer({ deadline }: { deadline: string }) {
  const [t, setT] = useState({ days: 0, hours: 0, mins: 0 });

  useEffect(() => {
    function tick() {
      const diff = Math.max(0, +new Date(deadline) - +new Date());
      setT({
        days: Math.floor(diff / 86400000),
        hours: Math.floor((diff % 86400000) / 3600000),
        mins: Math.floor((diff % 3600000) / 60000),
      });
    }
    tick();
    const id = setInterval(tick, 60000);
    return () => clearInterval(id);
  }, [deadline]);

  return (
    <div className="flex gap-3 mb-5">
      {[["Days", t.days], ["Hours", t.hours], ["Mins", t.mins]].map(([label, val]) => (
        <div key={label} className="flex-1 bg-[color:var(--color-surface)] dark:bg-[#2c2c2e] rounded-[12px] p-3 text-center shadow-sm border border-black/5 dark:border-white/5">
          <div className="text-[26px] font-bold leading-none tracking-[-0.025em] text-[color:var(--color-tag)]">
            {String(val).padStart(2, "0")}
          </div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mt-1">{label}</div>
        </div>
      ))}
    </div>
  );
}

export default function ListingDetailPage() {
  const { listingId } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { data: listing, loading, error, refetch, setData } = useApi<Listing>(`/listings/${listingId}`);
  usePageTitle(listing?.title ?? "Listing");

  const [pickupWindow, setPickupWindow] = useState("");
  const [activeImage, setActiveImage] = useState<string | null>(null);
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reportReason, setReportReason] = useState("safety");
  const [reportDetails, setReportDetails] = useState("");
  const [reportError, setReportError] = useState("");
  const [reporting, setReporting] = useState(false);

  async function handleReserve(event: React.FormEvent) {
    event.preventDefault();
    setFormError("");
    setSubmitting(true);
    setData((cur) => (cur ? { ...cur, can_reserve: false, reservation_count: (cur.reservation_count ?? 0) + 1 } : cur) as Listing);
    try {
      await apiFetch("/reservations", {
        method: "POST",
        body: { listing: Number(listingId), pickup_time_window: pickupWindow },
      });
      toast.success("Reservation requested. The owner can now confirm the handoff.");
      await refetch();
      setPickupWindow("");
    } catch (err) {
      setData((cur) => (cur ? { ...cur, can_reserve: true, reservation_count: (cur.reservation_count ?? 1) - 1 } : cur) as Listing);
      const msg = (err as Error).message;
      setFormError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    setConfirmDelete(false);
    setDeleting(true);
    try {
      await apiFetch(`/listings/${listingId}`, { method: "DELETE" });
      toast.success("Listing deleted.");
      navigate("/my-listings", { replace: true });
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  async function handleReport(event: React.FormEvent) {
    event.preventDefault();
    setReportError("");
    setReporting(true);
    try {
      await apiFetch(`/listings/${listingId}/reports`, {
        method: "POST",
        body: { listing: Number(listingId), reason: reportReason, details: reportDetails },
      });
      setData((cur) => (cur ? { ...cur, has_reported: true, can_report: false } : cur) as Listing);
      setReportDetails("");
      toast.success("Report submitted for review.");
    } catch (err) {
      const msg = (err as Error).message;
      setReportError(msg);
      toast.error(msg);
    } finally {
      setReporting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-5">
        <div className="h-12 w-12 border-4 border-[color:var(--color-tag-soft)] border-t-[color:var(--color-tag)] rounded-full animate-spin" />
        <p className="text-[16px] font-semibold text-[color:var(--color-ink)] dark:text-white">Loading listing…</p>
      </div>
    );
  }

  if (!listing) {
    return (
      <div className="flex flex-col items-center justify-center py-32">
        <div className="p-10 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] text-center max-w-md">
          <BadgeInfo size={48} className="mx-auto text-red-500 mb-5" />
          <h3 className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white">Listing not found</h3>
          <p className="mt-3 text-[color:var(--text-muted)] text-[14px]">{error}</p>
          <button onClick={() => navigate("/browse")} className="mt-8 px-8 py-3 bg-[color:var(--color-tag)] text-white rounded-full font-semibold text-[14px] hover:opacity-90 transition-opacity">
            Return to Marketplace
          </button>
        </div>
      </div>
    );
  }

  const ownerSummary = typeof listing.owner === "object" ? listing.owner : null;
  const isOwner = !!user && ownerSummary?.id === user.id;
  const isFree = listing.price_type === "free";
  const allImages = [
    listing.image_url,
    ...(listing.gallery ?? []).map((img) => img.image_url),
  ].filter((url): url is string => Boolean(url));
  const heroImage = activeImage && allImages.includes(activeImage) ? activeImage : allImages[0] ?? null;

  return (
    <div className="pb-16">
      <UnsavedChangesDialog
        open={confirmDelete}
        kicker="Confirm deletion"
        title="Delete this listing?"
        message="The listing will be removed from the marketplace and any pending reservation requests will be closed. This cannot be undone."
        confirmLabel="Delete listing"
        cancelLabel="Keep listing"
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
      {/* Breadcrumbs */}
      <div className="mx-auto max-w-[1200px] px-4 md:px-8 pt-5 pb-4">
        <nav className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.06em] text-[color:var(--text-muted)]">
          <Link to="/browse" className="hover:text-[color:var(--color-tag)] transition-colors">Marketplace</Link>
          <ChevronRight size={12} />
          {listing.category && (
            <>
              <span className="capitalize">{listing.category.replaceAll("_", " ")}</span>
              <ChevronRight size={12} />
            </>
          )}
          <span className="text-[color:var(--color-ink)] dark:text-white truncate max-w-[200px] normal-case">{listing.title}</span>
        </nav>
      </div>

      {/* Main grid */}
      <div className="mx-auto max-w-[1200px] px-4 md:px-8">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-1 lg:grid-cols-12 gap-10"
        >
          {/* ── Left column: images ── */}
          <div className="lg:col-span-7 space-y-4">
            {/* Hero image */}
            <div className="rounded-[20px] overflow-hidden bg-[color:var(--color-surface-2)] border border-black/10 dark:border-white/10 aspect-[4/5] shadow-sm">
              {heroImage ? (
                <img src={heroImage} alt={listing.title} decoding="async" className="w-full h-full object-cover" />
              ) : (
                <ListingPlaceholder category={listing.category} glyphSize={96} />
              )}
            </div>
            {!heroImage && (
              <p className="text-[12px] text-[color:var(--text-muted)]">
                No photo yet — ask the owner in coordination updates below.
              </p>
            )}

            {/* Thumbnails — only when the listing actually has multiple photos */}
            {allImages.length > 1 && (
              <div className="grid grid-cols-4 gap-3" role="group" aria-label="Listing photos">
                {allImages.map((url, i) => (
                  <button
                    key={url}
                    type="button"
                    onClick={() => setActiveImage(url)}
                    aria-label={`Show photo ${i + 1} of ${allImages.length}`}
                    aria-pressed={heroImage === url}
                    className={`rounded-[12px] overflow-hidden aspect-square border transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-tag)] ${
                      heroImage === url
                        ? "border-[color:var(--color-tag)] ring-2 ring-[color:var(--color-tag)]/30"
                        : "border-black/10 dark:border-white/10 hover:opacity-80"
                    }`}
                  >
                    <img src={url} alt="" loading="lazy" decoding="async" className="w-full h-full object-cover" />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* ── Right column: details ── */}
          <div className="lg:col-span-5 flex flex-col gap-5">
            {/* Title + actions row */}
            <div>
              <div className="flex justify-between items-start gap-4">
                <h1 className="text-[34px] font-bold leading-[1.2] tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white flex-1">
                  {listing.title}
                </h1>
                <div className="flex items-center gap-2 shrink-0 pt-1">
                  <SaveButton listing={listing} onListingChange={setData} className="" />
                  <button
                    onClick={async () => {
                      const url = window.location.href;
                      if (navigator.share) {
                        await navigator.share({ title: listing.title, url }).catch(() => {});
                      } else {
                        await navigator.clipboard.writeText(url).catch(() => {});
                        toast.success("Link copied to clipboard.");
                      }
                    }}
                    className="h-10 w-10 flex items-center justify-center rounded-full border border-black/10 dark:border-white/10 text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface-2)] transition-colors"
                    aria-label="Share"
                  >
                    <Share2 size={16} />
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 mt-3">
                <StatusPill status={listing.status} className="" />
                {listing.is_urgent && <StatusPill status="urgent" className="" />}
                <span className="text-[color:var(--text-muted)] text-[13px]">
                  · Listed {formatDateTime(listing.created_at)}
                </span>
              </div>
            </div>

            {/* Price */}
            <div className="flex items-baseline gap-3">
              <span className={`text-[44px] font-bold tracking-[-0.025em] leading-none ${
                isFree ? "text-[#005620] dark:text-[#7fca86]" : "text-[color:var(--color-tag)]"
              }`}>
                {formatMoney(listing.price_type, listing.price_amount)}
              </span>
              {Number(listing.estimated_retail_value || 0) > 0 && (
                <span className="text-[16px] text-[color:var(--text-muted)] line-through">
                  {formatCurrencyValue(listing.estimated_retail_value)} New
                </span>
              )}
            </div>

            {/* CTA buttons */}
            {isOwner ? (
              <div className="flex flex-col gap-3">
                <Link
                  to={`/listings/${listing.id}/edit`}
                  className="w-full py-4 rounded-full bg-[color:var(--color-tag)] text-white text-[18px] font-semibold text-center hover:opacity-90 active:scale-[0.98] transition-all"
                >
                  Edit Listing
                </Link>
                <Link
                  to="/my-reservations"
                  className="w-full py-4 rounded-full border-2 border-[color:var(--color-tag)] text-[color:var(--color-tag)] text-[18px] font-semibold text-center hover:bg-[color:var(--color-tag-soft)] transition-colors"
                >
                  Review Requests
                </Link>
                <button
                  onClick={() => setConfirmDelete(true)}
                  disabled={deleting}
                  className="flex items-center justify-center gap-2 w-full py-3 rounded-full border border-red-300 dark:border-red-700 text-red-500 text-[14px] font-semibold hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors"
                >
                  <Trash2 size={15} />
                  {deleting ? "Deleting…" : "Delete Listing"}
                </button>
              </div>
            ) : !user ? (
              <div className="flex flex-col gap-3">
                <Link
                  to="/login"
                  state={{ from: `/listings/${listing.id}` }}
                  className="w-full py-4 rounded-full bg-[color:var(--color-tag)] text-white text-[18px] font-semibold text-center hover:opacity-90 active:scale-[0.98] transition-all"
                >
                  Sign In to Reserve
                </Link>
                <p className="text-center text-[13px] text-[color:var(--text-muted)]">
                  Free account · reserve items, message sellers, and coordinate pickups
                </p>
              </div>
            ) : (
              <form onSubmit={handleReserve} className="flex flex-col gap-3">
                <textarea
                  className="w-full bg-[color:var(--color-surface-2)] border border-black/10 dark:border-white/10 rounded-[12px] p-4 text-[15px] min-h-[90px] resize-none outline-none text-[color:var(--color-ink)] dark:text-white placeholder:text-[color:var(--text-muted)] focus:border-[color:var(--color-tag)] focus:ring-2 focus:ring-[color:var(--color-tag)]/10 transition-all"
                  placeholder="Tell the seller your pickup window, e.g. 'Tomorrow 6–7 pm after my exam'"
                  value={pickupWindow}
                  onChange={(e) => setPickupWindow(e.target.value)}
                  disabled={!listing.can_reserve}
                  required
                />
                {formError && (
                  <p className="text-[13px] text-red-500 font-medium">{formError}</p>
                )}
                <button
                  type="submit"
                  disabled={!listing.can_reserve || submitting}
                  className="w-full py-4 rounded-full bg-[color:var(--color-tag)] text-white text-[18px] font-semibold hover:opacity-90 active:scale-[0.98] transition-all shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {submitting ? "Processing…" : listing.can_reserve ? "Reserve This Item" : "Currently Unavailable"}
                </button>
              </form>
            )}

            {/* Description */}
            {listing.description && (
              <p className="text-[15px] leading-relaxed text-[color:var(--text-muted)]">
                {listing.description}
              </p>
            )}

            {/* Source scan badge */}
            {listing.source_scan_name && (
              <div className="inline-flex items-center gap-2 rounded-[12px] border border-[color:var(--color-tag)]/20 bg-[color:var(--color-tag-soft)] px-4 py-2.5 text-[12px] font-semibold uppercase tracking-[0.08em] text-[color:var(--color-tag)] self-start">
                <Camera size={13} />
                From scan: {listing.source_scan_name}
              </div>
            )}

            {/* Seller card */}
            <div className="p-5 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] shadow-sm">
              <div className="flex items-center gap-4">
                <Avatar
                  name={ownerSummary?.display_name}
                  size={56}
                  className="border-2 border-white dark:border-[#2c2c2e] shadow-sm"
                />
                <div className="flex-1 min-w-0">
                  <h3 className="text-[20px] font-semibold leading-none tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">
                    {ownerSummary?.display_name}
                  </h3>
                  <div className="flex items-center gap-2 mt-1.5">
                    <RatingStars rating={listing.owner_trust_summary?.average_rating || 0} className="" />
                    <span className="text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                      {listing.owner_trust_summary?.average_rating || "—"}
                    </span>
                    <span className="text-[13px] text-[color:var(--text-muted)]">
                      ({listing.owner_trust_summary?.total_feedback_received || 0} reviews)
                    </span>
                  </div>
                </div>
                <div className="shrink-0">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[color:var(--color-tag)] bg-[color:var(--color-tag-soft)] px-2.5 py-1 rounded-lg">
                    {listing.owner_trust_summary?.trust_badge || "Member"}
                  </span>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-black/5 dark:border-white/5 grid grid-cols-3 gap-3 text-center">
                {[
                  ["Rescues", listing.owner_trust_summary?.completed_rescues || 0],
                  ["Handoffs", listing.owner_trust_summary?.successful_handoffs || 0],
                  ["Active", listing.owner_trust_summary?.active_listings || 0],
                ].map(([label, val]) => (
                  <div key={label}>
                    <div className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[color:var(--text-muted)]">{label}</div>
                    <div className="text-[16px] font-bold text-[color:var(--color-ink)] dark:text-white mt-0.5">{val}</div>
                  </div>
                ))}
              </div>
              <div className="mt-3">
                <Link to="/trust" className="flex items-center gap-1 text-[12px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity">
                  <Shield size={11} />
                  View Trust Center
                  <ArrowRight size={11} />
                </Link>
              </div>
            </div>

            {/* Pickup & Timeline card */}
            <div className="p-5 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface-2)] dark:bg-[#1c1c1e]">
              {/* Countdown */}
              <div className="flex items-center gap-2 mb-4">
                <Clock3 size={14} className="text-[color:var(--color-tag)]" />
                <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-ink)] dark:text-white">Move-out Deadline</h4>
              </div>
              {listing.available_until && <CountdownTimer deadline={listing.available_until} />}

              {/* Pickup zone */}
              <div className="flex items-center gap-2 mb-3">
                <MapPin size={14} className="text-[color:var(--color-tag)]" />
                <h4 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-ink)] dark:text-white">Pickup Zone</h4>
              </div>
              <div className="rounded-[12px] border border-black/5 dark:border-white/5 bg-[color:var(--color-surface)] px-4 py-3">
                <p className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">{listing.pickup_zone}</p>
                {listing.building ? (
                  <p className="mt-0.5 text-[13px] text-[color:var(--text-muted)]">{listing.building}</p>
                ) : null}
              </div>
            </div>

            {/* Owner scan links */}
            {isOwner && listing.source_scan_session && (
              <div className="grid grid-cols-2 gap-3">
                <Link to={`/publish/${listing.source_scan_session}`} className="flex flex-col items-center p-4 rounded-[16px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] text-center hover:bg-[color:var(--color-surface-2)] transition-colors">
                  <Camera size={18} className="text-[color:var(--color-tag)] mb-1.5" />
                  <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[color:var(--text-muted)]">Publish Queue</span>
                </Link>
                <Link to={`/clearout/${listing.source_scan_session}`} className="flex flex-col items-center p-4 rounded-[16px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] text-center hover:bg-[color:var(--color-surface-2)] transition-colors">
                  <NotebookPen size={18} className="text-[color:var(--color-tag)] mb-1.5" />
                  <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[color:var(--text-muted)]">Clearout Board</span>
                </Link>
              </div>
            )}
          </div>
        </motion.div>

        {/* ── Q&A / Updates + Sidebar ── */}
        <div className="mt-12 pt-10 border-t border-black/10 dark:border-white/10">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-10">

            {/* Updates panel */}
            <div className="lg:col-span-8">
              <h2 className="flex items-center gap-2 text-[28px] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white mb-8">
                <span className="text-[color:var(--color-tag)]">⟳</span>
                Coordination Updates
              </h2>
              <ListingUpdatesPanel listingId={listing.id} canPost={listing.can_post_update} />

              {/* Report (signed-in non-owners only) */}
              {user && !isOwner && (
                <div className="mt-8 p-5 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] dark:bg-[#1c1c1e]">
                  <div className="flex items-center gap-3 mb-3">
                    <Flag size={15} className="text-[color:var(--color-urgent)]" />
                    <h3 className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">Report this listing</h3>
                  </div>
                  <p className="text-[13px] text-[color:var(--text-muted)] mb-4">
                    Use only for safety concerns, misleading info, spam, or handoff issues.
                  </p>
                  {listing.has_reported ? (
                    <p className="text-[13px] font-semibold text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 p-3 rounded-[10px]">
                      You&apos;ve already reported this listing. Contact staff if the issue changes.
                    </p>
                  ) : listing.can_report ? (
                    <form onSubmit={handleReport} className="space-y-3">
                      <select
                        value={reportReason}
                        onChange={(e) => setReportReason(e.target.value)}
                        className="field text-[14px] !rounded-[12px]"
                      >
                        <option value="safety">Safety concern</option>
                        <option value="spam">Spam or scam</option>
                        <option value="inaccurate">Inaccurate listing</option>
                        <option value="no_show">Pickup / no-show issue</option>
                        <option value="inappropriate">Inappropriate content</option>
                        <option value="other">Other</option>
                      </select>
                      <textarea
                        value={reportDetails}
                        onChange={(e) => setReportDetails(e.target.value)}
                        className="field min-h-[90px] text-[14px] !rounded-[12px]"
                        placeholder="Share context for the reviewer."
                      />
                      {reportError && <p className="text-[13px] text-red-500">{reportError}</p>}
                      <button
                        type="submit"
                        disabled={reporting}
                        className="flex items-center gap-2 px-5 py-2.5 rounded-full border border-red-400/30 bg-red-50 dark:bg-red-900/10 text-red-500 text-[13px] font-semibold hover:bg-red-100 dark:hover:bg-red-900/20 transition-colors"
                      >
                        <AlertTriangle size={13} />
                        {reporting ? "Submitting…" : "Submit Report"}
                      </button>
                    </form>
                  ) : null}
                </div>
              )}
            </div>

            {/* Sticky sidebar */}
            <div className="lg:col-span-4">
              <div className="sticky top-24 space-y-4">

                {/* How pickups work — honest trust signals, no invented guarantees */}
                <div className="p-5 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] shadow-sm">
                  <h3 className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-tag)] mb-3">
                    <Shield size={13} />
                    How Pickups Work
                  </h3>
                  <p className="text-[13px] text-[color:var(--text-muted)] leading-relaxed">
                    Reservations lock the item for you while you coordinate a pickup window. Handoffs are confirmed with a PIN, both sides leave feedback afterward, and anything off can be reported for moderator review.
                  </p>
                  <Link
                    to="/trust"
                    className="flex items-center gap-1.5 mt-4 text-[12px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity"
                  >
                    <Shield size={11} />
                    Open Trust Center
                    <ArrowRight size={11} />
                  </Link>
                </div>

                {/* Savings */}
                {Number(listing.estimated_retail_value || 0) > 0 && (
                  <div className="p-5 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] dark:bg-[#1c1c1e]">
                    <div className="flex items-center gap-2 mb-2">
                      <Sparkles size={13} className="text-[color:var(--color-tag)]" />
                      <h3 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-ink)] dark:text-white">Sustainability Impact</h3>
                    </div>
                    <p className="text-[22px] font-bold tracking-[-0.01em] text-[color:var(--color-tag)]">
                      Save {formatCurrencyValue(listing.estimated_student_savings)}
                    </p>
                    <p className="text-[13px] text-[color:var(--text-muted)] mt-0.5">
                      vs. retail {formatCurrencyValue(listing.estimated_retail_value)}
                    </p>
                  </div>
                )}

                {/* Item activity */}
                <div className="p-5 rounded-[20px] border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] dark:bg-[#1c1c1e]">
                  <h3 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-3">Item Activity</h3>
                  <div className="grid grid-cols-3 gap-3">
                    {[["Views", listing.view_count], ["Saves", listing.saved_count], ["Requests", listing.reservation_count]].map(([label, val]) => (
                      <div key={label} className="text-center bg-[color:var(--color-surface-2)] rounded-[12px] p-3">
                        <div className="text-[22px] font-bold text-[color:var(--color-tag)]">{val}</div>
                        <div className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[color:var(--text-muted)]">{label}</div>
                      </div>
                    ))}
                  </div>
                  {listing.time_left_label && (
                    <p className="mt-3 text-[12px] text-[color:var(--text-muted)] font-medium">{listing.time_left_label}</p>
                  )}
                </div>

                {/* Owner quick links */}
                {isOwner && (
                  <div className="p-5 rounded-[20px] border border-[color:var(--color-tag)]/20 bg-[color:var(--color-tag-soft)] dark:bg-[rgba(138,29,69,0.12)]">
                    <h3 className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--color-tag)] mb-3">Owner Tools</h3>
                    <div className="space-y-2">
                      <Link
                        to={`/listings/${listing.id}/edit`}
                        className="flex items-center justify-between w-full px-4 py-2.5 rounded-full bg-[color:var(--color-tag)] text-white text-[13px] font-semibold hover:opacity-90 transition-opacity"
                      >
                        <span>Edit Details</span>
                        <ArrowRight size={13} />
                      </Link>
                      <Link
                        to="/my-reservations"
                        className="flex items-center justify-between w-full px-4 py-2.5 rounded-full bg-white dark:bg-[#2c2c2e] text-[color:var(--color-ink)] dark:text-white text-[13px] font-semibold border border-black/10 dark:border-white/10 hover:opacity-90 transition-opacity"
                      >
                        <span>Review Requests</span>
                        <ArrowRight size={13} />
                      </Link>
                      <Link
                        to={`/requests?category=${listing.category}`}
                        className="flex items-center justify-between w-full px-4 py-2.5 rounded-full bg-white dark:bg-[#2c2c2e] text-[color:var(--color-ink)] dark:text-white text-[13px] font-semibold border border-black/10 dark:border-white/10 hover:opacity-90 transition-opacity"
                      >
                        <span>Browse Need Board</span>
                        <ArrowRight size={13} />
                      </Link>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
