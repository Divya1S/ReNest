import { motion, AnimatePresence } from "framer-motion";
import { X, MapPin, Clock3 } from "lucide-react";
import React, { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import { formatMoney, formatDateTime, formatCurrencyValue } from "../lib/formatters";

import ListingPlaceholder from "./ListingPlaceholder";

export default function ListingPreviewModal({ listing, isOpen, onClose }) {
  const titleId = "preview-modal-title";
  const closeRef = useRef(null);
  const dialogRef = useRef(null);
  const restoreFocusRef = useRef(null);

  // Move focus into the dialog on open and put it back where it was on close.
  // Browsers do not restore focus for us: when the dialog unmounts, focus
  // falls to <body> and a keyboard user loses their place in the grid.
  useEffect(() => {
    if (!isOpen) return undefined;
    restoreFocusRef.current = document.activeElement;
    closeRef.current?.focus();
    return () => {
      const previous = restoreFocusRef.current;
      if (previous && typeof previous.focus === "function" && document.contains(previous)) {
        previous.focus();
      }
    };
  }, [isOpen]);

  // Escape closes; Tab stays inside the dialog (aria-modal alone does not
  // stop the browser tabbing into the obscured page behind it).
  useEffect(() => {
    if (!isOpen) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  if (!listing) return null;

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden="true"
            className="fixed inset-0 z-[60] bg-slate-900/40 backdrop-blur-sm dark:bg-slate-900/60"
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            initial={{ opacity: 0, scale: 0.9, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.9, y: 20 }}
            className="fixed left-1/2 top-1/2 z-[70] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 px-4"
          >
            <div className="paper-panel overflow-hidden !rounded-[2.5rem]">
              <button
                ref={closeRef}
                onClick={onClose}
                aria-label="Close preview"
                className="absolute right-6 top-6 z-10 flex h-10 w-10 items-center justify-center rounded-full bg-white/80 text-slate-900 shadow-lg backdrop-blur-md transition-transform hover:scale-110 dark:bg-slate-800/80 text-[color:var(--color-ink)] dark:text-white"
              >
                <X size={20} aria-hidden="true" />
              </button>

              <div className="grid gap-0 md:grid-cols-2">
                <div className="relative h-64 w-full md:h-auto">
                  {listing.image_url ? (
                    <img src={listing.image_url} alt={listing.title} loading="lazy" decoding="async" className="h-full w-full object-cover" />
                  ) : (
                    <ListingPlaceholder category={listing.category} glyphSize={64} />
                  )}
                  <div className="absolute left-4 top-4">
                    <span className="rounded-full bg-[color:var(--color-tag)] px-4 py-1.5 text-xs font-bold text-white shadow-lg">
                      {formatMoney(listing.price_type, listing.price_amount)}
                    </span>
                  </div>
                </div>

                <div className="flex flex-col p-8 sm:p-10">
                  <p className="label-title">{listing.category?.replaceAll("_", " ")}</p>
                  <h2 id={titleId} className="mt-2 text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">{listing.title}</h2>
                  
                  <p className="mt-4 text-base leading-relaxed text-slate-600 dark:text-slate-400">
                    {listing.description}
                  </p>

                  <div className="mt-8 space-y-4">
                    <div className="flex items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
                      <MapPin size={18} className="text-[color:var(--color-tag)]" />
                      <span className="font-semibold text-slate-900 dark:text-slate-200">{listing.pickup_zone}</span>
                    </div>
                    <div className="flex items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
                      <Clock3 size={18} className="text-[color:var(--color-tag)]" />
                      <span>Available until {formatDateTime(listing.available_until)}</span>
                    </div>
                  </div>

                  {listing.estimated_retail_value > 0 && (
                    <div className="mt-8 rounded-2xl bg-amber-50 p-4 dark:bg-amber-900/20">
                      <p className="text-xs font-bold uppercase tracking-wider text-amber-700 dark:text-amber-400">Student Value</p>
                      <p className="mt-1 text-sm font-medium text-amber-900 dark:text-amber-200">
                        Retail value {formatCurrencyValue(listing.estimated_retail_value)}. 
                        Save {formatCurrencyValue(listing.estimated_student_savings)}!
                      </p>
                    </div>
                  )}

                  <div className="mt-10">
                    <Link
                      to={`/listings/${listing.id}`}
                      onClick={onClose}
                      className="primary-button w-full justify-center"
                    >
                      View Details & Reserve
                    </Link>
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
