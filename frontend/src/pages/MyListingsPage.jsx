import { AlertCircle, ArrowRight, BarChart2, Box, Clock3, Eye, FileText, Gift, Heart, RefreshCw, RotateCcw, Sparkles, X } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import ImpactCard from "../components/ImpactCard";
import ListingCard from "../components/ListingCard";
import PageSection from "../components/PageSection";
import SkeletonLoader from "../components/SkeletonLoader";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

function ListingCardSkeleton() {
  return (
    <div className="paper-panel overflow-hidden animate-pulse">
      <div className="h-56 w-full bg-[color:var(--color-surface-2)]" />
      <div className="space-y-4 p-6">
        <div className="flex justify-between">
          <div className="h-3 w-24 rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="h-3 w-16 rounded-full bg-[color:var(--color-surface-2)]" />
        </div>
        <div className="h-6 w-3/4 rounded-full bg-[color:var(--color-surface-2)]" />
        <div className="h-3.5 w-full rounded-full bg-[color:var(--color-surface-2)]" />
        <div className="h-3.5 w-2/3 rounded-full bg-[color:var(--color-surface-2)]" />
        <div className="flex items-center justify-between border-t border-black/8 dark:border-white/8 pt-4">
          <div className="h-8 w-28 rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="h-10 w-10 rounded-2xl bg-[color:var(--color-surface-2)]" />
        </div>
      </div>
    </div>
  );
}

function BulkUpdateModal({ activeCount, onClose, onSuccess }) {
  const [pickupZone, setPickupZone] = useState("");
  const [availableUntil, setAvailableUntil] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const canSubmit = pickupZone.trim() || availableUntil;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setError(null);

    const body = {};
    if (pickupZone.trim()) body.pickup_zone = pickupZone.trim();
    if (availableUntil) body.available_until = new Date(availableUntil).toISOString();

    try {
      const res = await fetch("/api/listings/bulk-update", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        credentials: "include",
        body: body,
      });
      const data = await res.json();
      if (!res.ok) {
        const msg = data.detail || data.pickup_zone || data.available_until || "Update failed.";
        setError(typeof msg === "string" ? msg : JSON.stringify(msg));
        return;
      }
      onSuccess(data);
    } catch {
      setError("Network error — please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="paper-panel w-full max-w-md p-8">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <p className="label-title">Bulk edit</p>
            <h2 className="mt-1 text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">
              Update all {activeCount} active listing{activeCount !== 1 ? "s" : ""}
            </h2>
            <p className="mt-1 text-sm text-[color:var(--text-muted)]">
              Fill in what you want to change — leave blank to keep as-is.
            </p>
          </div>
          <button
            onClick={onClose}
            className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[color:var(--text-muted)] transition hover:bg-[color:var(--color-surface-2)]"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="mb-1.5 block text-sm font-semibold text-[color:var(--color-ink)] dark:text-white">
              Pickup zone
            </label>
            <input
              type="text"
              value={pickupZone}
              onChange={(e) => setPickupZone(e.target.value)}
              placeholder="e.g. Building A lobby, Room 214"
              className="w-full rounded-2xl border border-black/12 dark:border-white/12 bg-[color:var(--color-surface)] px-4 py-3 text-sm text-[color:var(--color-ink)] dark:text-white placeholder:text-[color:var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)]"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-semibold text-[color:var(--color-ink)] dark:text-white">
              Move-out deadline
            </label>
            <input
              type="datetime-local"
              value={availableUntil}
              onChange={(e) => setAvailableUntil(e.target.value)}
              min={new Date(Date.now() + 60_000).toISOString().slice(0, 16)}
              className="w-full rounded-2xl border border-black/12 dark:border-white/12 bg-[color:var(--color-surface)] px-4 py-3 text-sm text-[color:var(--color-ink)] dark:text-white focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)]"
            />
          </div>

          {error && (
            <p className="rounded-2xl bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-600 dark:text-red-400">
              {error}
            </p>
          )}

          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-2xl border border-black/12 dark:border-white/12 px-4 py-3 text-sm font-semibold text-[color:var(--color-ink)] dark:text-white transition hover:bg-[color:var(--color-surface-2)]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit || submitting}
              className="primary-button flex-1 justify-center disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Updating…" : "Apply to all"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Sparkline({ data }) {
  if (!data || data.length === 0) return null;
  const values = data.map((d) => d.views);
  const max = Math.max(...values, 1);
  const width = 120;
  const height = 32;
  const step = width / Math.max(values.length - 1, 1);
  const points = values
    .map((v, i) => `${i * step},${height - (v / max) * height}`)
    .join(" ");
  return (
    <svg width={width} height={height} className="overflow-visible" aria-hidden="true">
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        points={points}
        className="text-[color:var(--color-tag)]"
      />
      {values.map((v, i) => (
        <circle
          key={i}
          cx={i * step}
          cy={height - (v / max) * height}
          r="2"
          className="fill-[color:var(--color-tag)]"
        />
      ))}
    </svg>
  );
}

function ListingAnalyticsPanel({ listingId }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    if (data) { setOpen((o) => !o); return; }
    setOpen(true);
    setLoading(true);
    try {
      const result = await apiFetch(`/listings/${listingId}/analytics`);
      setData(result);
    } catch {
      toast.error("Could not load analytics.");
      setOpen(false);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <button
        onClick={load}
        className="flex items-center gap-1.5 text-[12px] font-medium text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] dark:hover:text-white transition-colors px-1"
      >
        <BarChart2 size={13} />
        {open ? "Hide stats" : "Show stats"}
      </button>

      {open && (
        <div className="mt-2 rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] px-5 py-4">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-[color:var(--text-muted)] animate-pulse">
              <BarChart2 size={14} /> Loading analytics…
            </div>
          ) : data ? (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <div className="flex items-center justify-center gap-1 text-[color:var(--text-muted)] text-[11px] font-medium uppercase tracking-wide mb-1">
                    <Eye size={11} /> Views
                  </div>
                  <p className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white">{data.total_views}</p>
                </div>
                <div>
                  <div className="flex items-center justify-center gap-1 text-[color:var(--text-muted)] text-[11px] font-medium uppercase tracking-wide mb-1">
                    <Heart size={11} /> Saves
                  </div>
                  <p className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white">{data.total_saves}</p>
                </div>
                <div>
                  <div className="flex items-center justify-center gap-1 text-[color:var(--text-muted)] text-[11px] font-medium uppercase tracking-wide mb-1">
                    <Sparkles size={11} /> Conv.
                  </div>
                  <p className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                    {data.total_views > 0 ? `${(data.conversion_rate * 100).toFixed(1)}%` : "—"}
                  </p>
                </div>
              </div>

              {data.views_by_day.length > 1 && (
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-[color:var(--text-muted)] mb-2">
                    Views over time ({data.days_active}d active)
                  </p>
                  <Sparkline data={data.views_by_day} />
                </div>
              )}

              {data.total_views === 0 && (
                <p className="text-sm text-[color:var(--text-muted)]">No views yet — share the link to get started.</p>
              )}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function getCsrfToken() {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith("csrftoken="))
    ?.split("=")[1] ?? "";
}

export default function MyListingsPage() {
  usePageTitle("My Listings");
  const { data, loading, error, refetch } = useApi("/listings?mine=1", {
    initialData: { results: [], count: 0 },
  });
  const [showBulkModal, setShowBulkModal] = useState(false);
  const [listings, setListings] = useState(null);
  const [repostingId, setRepostingId] = useState(null);
  const [donatingId, setDonatingId] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();

  // Handle ?repost=<id>&token=<token> deep-link from expiry notification
  useEffect(() => {
    const repostId = searchParams.get("repost");
    const repostToken = searchParams.get("token");
    if (!repostId || !repostToken) return;
    setSearchParams({}, { replace: true });
    handleRepost(Number(repostId), repostToken);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleRepost(listingId, token) {
    setRepostingId(listingId);
    try {
      const data = await apiFetch(`/listings/${listingId}/repost`, {
        method: "POST",
        body: { token },
      });
      setListings((prev) => {
        const base = prev ?? [];
        return base.map((l) => (l.id === listingId ? data.listing : l));
      });
      toast.success(`Relisted for ${data.extended_days} more days.`);
    } catch (err) {
      toast.error(err.message || "Could not relist — try editing the listing directly.");
    } finally {
      setRepostingId(null);
    }
  }

  async function handleDonate(listingId) {
    setDonatingId(listingId);
    try {
      await apiFetch(`/listings/${listingId}/donate`, { method: "POST", body: {} });
      setListings((prev) => {
        const base = prev ?? [];
        return base.map((l) => (l.id === listingId ? { ...l, status: "donated" } : l));
      });
      toast.success("Item marked as donated. Receipt emailed to you.");
    } catch (err) {
      toast.error(err.message || "Could not mark as donated.");
    } finally {
      setDonatingId(null);
    }
  }

  const displayListings = listings ?? (data?.results ?? []);
  const activeCount = displayListings.filter((l) => ["available", "reserved"].includes(l.status)).length;
  const urgentCount = displayListings.filter((l) => l.is_urgent).length;
  const rescuedCount = displayListings.filter((l) => l.status === "picked_up").length;

  function handleBulkSuccess(responseData) {
    setListings(responseData.listings);
    setShowBulkModal(false);
  }

  if (loading) {
    return (
      <div className="space-y-8">
        <div className="paper-panel p-10 sm:p-14 animate-pulse">
          <div className="h-3.5 w-20 rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="mt-4 h-10 w-2/3 rounded-2xl bg-[color:var(--color-surface-2)]" />
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => <SkeletonLoader key={i} className="h-32 !rounded-[1.9rem]" />)}
        </div>
        <div className="grid gap-6 lg:grid-cols-2">
          {Array.from({ length: 4 }, (_, i) => <ListingCardSkeleton key={i} />)}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-red-100 text-red-500 dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle size={40} />
        </div>
        <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Could not load your listings</h2>
        <p className="mt-4 text-[color:var(--text-muted)]">{error}</p>
        <button onClick={refetch} className="primary-button mt-8">Try Again</button>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {showBulkModal && (
        <BulkUpdateModal
          activeCount={activeCount}
          onClose={() => setShowBulkModal(false)}
          onSuccess={handleBulkSuccess}
        />
      )}

      <PageSection className="paper-panel p-10 sm:p-14">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="label-title">My listings</p>
            <h1 className="mt-3 text-[34px] font-bold tracking-[-0.025em]">Track what you already rescued from the dumpster path.</h1>
          </div>
          <div className="flex flex-wrap gap-3">
            {activeCount > 0 && (
              <button
                onClick={() => setShowBulkModal(true)}
                className="secondary-button"
              >
                <RefreshCw size={15} />
                Bulk Edit Active
              </button>
            )}
            <Link to="/listings/new" className="primary-button">
              <ArrowRight size={15} />
              Add Another Listing
            </Link>
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-3" delay={0.04}>
        <ImpactCard label="Active listings" value={activeCount} tone="box" icon={Box} />
        <ImpactCard label="Urgent deadlines" value={urgentCount} tone="blue" icon={Clock3} />
        <ImpactCard label="Picked up" value={rescuedCount} tone="teal" icon={Sparkles} />
      </PageSection>

      {displayListings.length ? (
        <PageSection className="grid gap-6 lg:grid-cols-2" delay={0.08}>
          {displayListings.map((listing) => (
            <div key={listing.id} className="flex flex-col gap-2">
              <ListingCard listing={listing} />
              <ListingAnalyticsPanel listingId={listing.id} />
              {listing.status === "expired" && listing.repost_token && (
                <button
                  onClick={() => handleRepost(listing.id, listing.repost_token)}
                  disabled={repostingId === listing.id}
                  className="flex items-center justify-center gap-2 rounded-2xl border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] px-4 py-3 text-sm font-semibold text-[color:var(--color-ink)] dark:text-white transition hover:bg-[color:var(--color-surface-2)] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <RotateCcw size={15} />
                  {repostingId === listing.id ? "Relisting…" : "Repost for 7 more days"}
                </button>
              )}
              {["available", "expired"].includes(listing.status) && (
                <button
                  onClick={() => handleDonate(listing.id)}
                  disabled={donatingId === listing.id}
                  className="flex items-center justify-center gap-2 rounded-2xl border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] px-4 py-3 text-sm font-semibold text-[color:var(--color-ink)] dark:text-white transition hover:bg-[color:var(--color-surface-2)] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Gift size={15} />
                  {donatingId === listing.id ? "Donating…" : "Donate to hub"}
                </button>
              )}
              {listing.status === "donated" && listing.donation_receipt_url && (
                <a
                  href={listing.donation_receipt_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 rounded-2xl border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 px-4 py-3 text-sm font-semibold text-emerald-700 dark:text-emerald-400 transition hover:bg-emerald-100 dark:hover:bg-emerald-900/30"
                >
                  <FileText size={15} />
                  Download donation receipt
                </a>
              )}
            </div>
          ))}
        </PageSection>
      ) : (
        <div className="paper-panel p-12 flex flex-col items-center text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[color:var(--color-surface-2)] mb-5">
            <Box size={28} className="text-[color:var(--text-muted)]" />
          </div>
          <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">No listings yet.</h2>
          <p className="mt-3 max-w-sm text-[color:var(--text-muted)]">
            Start with a lamp, storage bins, or unopened supplies you know you won&apos;t carry home.
          </p>
          <Link to="/listings/new" className="primary-button mt-8">
            <ArrowRight size={15} />
            Post Your First Listing
          </Link>
        </div>
      )}
    </div>
  );
}
