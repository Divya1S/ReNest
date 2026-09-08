import { motion, AnimatePresence } from "framer-motion";
import { Clock3, MapPin, ArrowRight, Camera, Eye, MoreHorizontal, X, Check, Loader2 } from "lucide-react";
import React, { memo, useRef, useEffect } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../context/AuthContext";
import { useIntentPrefetch } from "../hooks/useIntentPrefetch";
import { apiFetch, toLocalDateTimeInput } from "../lib/api";
import { cn } from "../lib/cn";
import { formatCurrencyValue, formatMoney } from "../lib/formatters";

import ListingPlaceholder from "./ListingPlaceholder";
import SaveButton from "./SaveButton";

function categoryLabel(value) {
  return value?.replaceAll("_", " ") || "Uncategorized";
}

function QuickEditPopover({ listing, onClose, onSaved }) {
  const ref = useRef(null);
  const [pickupZone, setPickupZone] = React.useState(listing.pickup_zone || "");
  // The API renders datetimes with the server's offset; slicing the string
  // would drop it and re-read the wall time in the browser's zone.
  const initialAvailableUntil = toLocalDateTimeInput(listing.available_until);
  const [availableUntil, setAvailableUntil] = React.useState(initialAvailableUntil);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  useEffect(() => {
    function handleOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [onClose]);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const body = {};
      if (pickupZone !== listing.pickup_zone) body.pickup_zone = pickupZone;
      // Compare against the same local-input normalisation used for the initial
      // value. The old string-slice comparison could never match, so every save
      // re-sent the deadline, reinterpreted in the browser's timezone, which
      // shifted the date for anyone outside the server's zone.
      if (availableUntil && availableUntil !== initialAvailableUntil) {
        body.available_until = new Date(availableUntil).toISOString();
      }
      if (Object.keys(body).length === 0) { onClose(); return; }
      const updated = await apiFetch(`/listings/${listing.id}`, { method: "PATCH", body });
      onSaved?.(updated);
      onClose();
    } catch (err) {
      setError(err.message || "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      ref={ref}
      className="absolute bottom-14 right-2 z-50 w-72 rounded-[16px] border border-[color:var(--color-line)] bg-[color:var(--color-surface)] shadow-xl p-4"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white">Quick edit</span>
        <button onClick={onClose} className="rounded-full p-1 hover:bg-[color:var(--color-surface-2)] transition-colors">
          <X size={14} />
        </button>
      </div>
      <form onSubmit={handleSave} className="space-y-3">
        <div>
          <label className="block text-[11px] font-semibold text-[color:var(--text-muted)] mb-1">Pickup zone</label>
          <input
            value={pickupZone}
            onChange={(e) => setPickupZone(e.target.value)}
            className="w-full rounded-[10px] border border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] px-3 py-2 text-[13px] text-[color:var(--color-ink)] dark:text-white focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]"
            placeholder="e.g. Birnkrant 3rd floor lobby"
          />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-[color:var(--text-muted)] mb-1">Available until</label>
          <input
            type="datetime-local"
            value={availableUntil}
            onChange={(e) => setAvailableUntil(e.target.value)}
            className="w-full rounded-[10px] border border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] px-3 py-2 text-[13px] text-[color:var(--color-ink)] dark:text-white focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]"
          />
        </div>
        {error && <p className="text-[12px] text-red-500">{error}</p>}
        <button
          type="submit"
          disabled={saving}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-[color:var(--color-tag)] py-2 text-[13px] font-semibold text-white hover:opacity-90 disabled:opacity-60 transition-opacity"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          {saving ? "Saving…" : "Save changes"}
        </button>
      </form>
    </div>
  );
}

function ListingCard({ listing, preview = false, onListingChange, onQuickView, className, compact = false }) {
  const { user } = useAuth();
  const isOwner = user && listing.owner?.id === user.id;
  const [imgLoaded, setImgLoaded] = React.useState(false);
  const [quickEditOpen, setQuickEditOpen] = React.useState(false);
  const detailPrefetch = useIntentPrefetch({
    route: isOwner ? "listingForm" : "listingDetail",
    // Route chunk only. GET /listings/:id is not a pure read: it records a
    // ListingViewEvent and an interaction event, so prefetching the data on
    // hover, focus or touchstart inflated every listing's view count.
    data: null,
    enabled: !preview,
  });

  if (compact) {
    return (
      <motion.article
        initial={{ opacity: 0, x: 10 }}
        animate={{ opacity: 1, x: 0 }}
        whileHover={{ x: 4 }}
        {...detailPrefetch}
        className={cn(
          "flex items-center gap-4 p-4 bg-[color:var(--color-surface)] rounded-[16px] border border-black/8 dark:border-white/8 shadow-[0px_2px_8px_rgba(0,0,0,0.04)] group cursor-pointer",
          className,
        )}
      >
        <div className="h-20 w-20 shrink-0 overflow-hidden rounded-[12px] border border-black/8 dark:border-white/8 bg-[color:var(--color-surface-2)]">
          {listing.image_url ? (
            <img
              src={listing.thumb_url || listing.image_url}
              alt=""
              loading="lazy"
              decoding="async"
              onLoad={() => setImgLoaded(true)}
              className={cn("h-full w-full object-cover transition-all duration-500 group-hover:scale-105", imgLoaded ? "opacity-100 blur-0" : "opacity-0 blur-sm")}
            />
          ) : (
            <ListingPlaceholder category={listing.category} glyphSize={28} />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-0.5">{categoryLabel(listing.category)}</p>
          <h2 className="text-[15px] font-semibold truncate text-[color:var(--color-ink)] dark:text-white group-hover:text-[color:var(--color-tag)] transition-colors">{listing.title}</h2>
          <div className="mt-1.5 flex items-center gap-3">
            <span className="text-[13px] font-semibold text-[color:var(--color-tag)]">{formatMoney(listing.price_type, listing.price_amount)}</span>
            <div className="flex items-center gap-1 text-[12px] text-[color:var(--text-muted)]">
              <MapPin size={11} />
              <span className="truncate">{listing.pickup_zone}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-2 shrink-0">
          {!preview && <SaveButton listing={listing} onListingChange={onListingChange} />}
          <Link
            to={`/listings/${listing.id}`}
            aria-label={`View ${listing.title}`}
            onClick={(event) => event.stopPropagation()}
            className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] transition-colors"
          >
            <ArrowRight size={18} aria-hidden="true" />
          </Link>
        </div>
      </motion.article>
    );
  }

  const isFree = listing.price_type === "free";

  return (
    <motion.article
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      whileHover={{ y: -4 }}
      {...detailPrefetch}
      className={cn(
        "bg-[color:var(--color-surface)] rounded-[20px] overflow-hidden border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] group cursor-pointer flex flex-col h-full",
        className,
      )}
    >
      {/* Image */}
      <div className="relative aspect-square overflow-hidden">
        {listing.image_url ? (
          <img
            src={listing.thumb_url || listing.image_url}
            alt={listing.title}
            loading="lazy"
            decoding="async"
            onLoad={() => setImgLoaded(true)}
            className={cn("h-full w-full object-cover transition-all duration-500 group-hover:scale-105", imgLoaded ? "opacity-100 blur-0" : "opacity-0 blur-sm")}
          />
        ) : (
          <ListingPlaceholder category={listing.category} glyphSize={48} />
        )}

        {/* Urgent badge — top right */}
        {listing.is_urgent && (
          <div className="absolute top-3 right-3 z-10 flex items-center gap-1 bg-[color:var(--color-tag)] text-white px-3 py-1.5 rounded-full text-[12px] font-medium shadow-sm">
            <Clock3 size={12} />
            {listing.time_left_label || "Urgent"}
          </div>
        )}

        {/* Free badge — top left */}
        {isFree && (
          <div className="absolute top-3 left-3 z-10 bg-[color:var(--color-free)] text-white px-3 py-1.5 rounded-full text-[12px] font-medium shadow-sm">
            Free
          </div>
        )}

        {/* Scan origin badge */}
        {listing.source_scan_session && (
          <div className="absolute bottom-3 left-3 z-10 flex items-center gap-1 bg-white/90 dark:bg-black/60 backdrop-blur-sm text-[color:var(--color-ink)] dark:text-white px-2.5 py-1 rounded-full text-[11px] font-semibold shadow-sm">
            <Camera size={11} />
            Scan
          </div>
        )}

        {/* Save button — top left (or below free badge) */}
        {!preview && (
          <div className={cn("absolute right-3 z-10", listing.is_urgent ? "top-12" : "top-3")}>
            <SaveButton listing={listing} onListingChange={onListingChange} />
          </div>
        )}

        {/* Quick view — keyboard-reachable path to the preview modal */}
        {onQuickView && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onQuickView(); }}
            aria-label={`Quick view ${listing.title}`}
            className="absolute bottom-3 right-3 z-10 flex h-9 w-9 items-center justify-center rounded-full bg-white/90 text-[color:var(--text-ink,#23191b)] shadow-sm backdrop-blur-sm transition-transform hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-tag)] dark:bg-black/60 dark:text-white"
          >
            <Eye size={15} aria-hidden="true" />
          </button>
        )}
      </div>

      {/* Card body */}
      <div className="p-5 flex flex-col flex-1">
        <div className="flex items-start justify-between gap-2 mb-1.5">
          <h2 className="text-[20px] font-semibold leading-snug tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white group-hover:text-[color:var(--color-tag)] transition-colors line-clamp-2 flex-1">
            {listing.title}
          </h2>
          <span className={cn(
            "text-[20px] font-semibold tracking-[-0.01em] whitespace-nowrap shrink-0",
            isFree ? "text-[color:var(--color-free)]" : "text-[color:var(--color-tag)]",
          )}>
            {formatMoney(listing.price_type, listing.price_amount)}
          </span>
        </div>

        <div className="flex items-center gap-1.5 text-[color:var(--text-muted)] mb-4">
          <MapPin size={13} className="shrink-0" />
          <span className="text-[13px] truncate">{listing.pickup_zone}</span>
        </div>

        {Number(listing.estimated_retail_value || 0) > 0 && (
          <p className="text-[11px] font-semibold text-[color:var(--text-muted)] mb-3">
            Retail {formatCurrencyValue(listing.estimated_retail_value)} · Save {formatCurrencyValue(listing.estimated_student_savings)}
          </p>
        )}

        {listing.view_count > 1 && (
          <p className="text-[11px] text-[color:var(--text-muted)] mb-3">
            {listing.view_count} people viewed this
          </p>
        )}

        <div className="mt-auto relative">
          <div className="flex items-center gap-2">
            <Link
              to={preview ? "/register" : isOwner ? `/listings/${listing.id}/edit` : `/listings/${listing.id}`}
              onClick={(e) => e.stopPropagation()}
              className="flex-1 text-center bg-[color:var(--color-teal)] text-white py-2.5 rounded-full text-[12px] font-semibold tracking-[0.02em] hover:opacity-90 transition-opacity active:scale-[0.98]"
            >
              {isOwner ? "Edit Listing" : preview ? "Unlock" : "View Item"}
            </Link>
            {isOwner && !preview && (
              <button
                onClick={(e) => { e.stopPropagation(); e.preventDefault(); setQuickEditOpen((o) => !o); }}
                aria-label="Quick edit"
                className="shrink-0 flex h-9 w-9 items-center justify-center rounded-full border border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] text-[color:var(--text-muted)] hover:text-[color:var(--color-tag)] hover:border-[color:var(--color-tag)] transition-colors"
              >
                <MoreHorizontal size={16} />
              </button>
            )}
          </div>
          <AnimatePresence>
            {quickEditOpen && (
              <motion.div
                initial={{ opacity: 0, scale: 0.95, y: 4 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: 4 }}
                transition={{ duration: 0.15 }}
              >
                <QuickEditPopover
                  listing={listing}
                  onClose={() => setQuickEditOpen(false)}
                  onSaved={(updated) => onListingChange?.(updated)}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.article>
  );
}

export default memo(ListingCard, (prev, next) =>
  prev.listing === next.listing &&
  prev.compact === next.compact &&
  prev.preview === next.preview &&
  prev.className === next.className,
);
