import { Bookmark, BookmarkCheck } from "lucide-react";
import React, { useState } from "react";
import { toast } from "sonner";

import { useAuth } from "../context/AuthContext";
import { apiFetch } from "../lib/api";
import { cn } from "../lib/cn";

export default function SaveButton({ listing, onListingChange, className }) {
  const { user } = useAuth();
  const [pending, setPending] = useState(false);

  if (!user || listing.owner?.id === user.id) {
    return null;
  }

  async function handleToggle(event) {
    // The browse grid wraps each card in a click handler that opens the preview
    // modal; saving must not also open it.
    event?.stopPropagation?.();
    if (pending) return;
    const wasSaved = listing.is_saved;
    setPending(true);

    // Optimistic update — flip state and adjust count immediately
    onListingChange?.({
      ...listing,
      is_saved: !wasSaved,
      saved_count: listing.saved_count + (wasSaved ? -1 : 1),
    });

    try {
      const response = await apiFetch(`/listings/${listing.id}/save`, {
        method: wasSaved ? "DELETE" : "POST",
      });
      onListingChange?.(response.listing); // confirm with real server data
      toast.success(wasSaved ? "Removed from saved items." : "Saved for later.");
    } catch (error) {
      onListingChange?.(listing); // roll back to original
      toast.error(error.message);
    } finally {
      setPending(false);
    }
  }

  const label = pending
    ? "Updating…"
    : listing.is_saved
      ? "Remove from saved"
      : "Save for later";

  return (
    <button
      type="button"
      onClick={handleToggle}
      disabled={pending}
      aria-label={label}
      aria-pressed={listing.is_saved}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-3.5 py-2 text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-[color:var(--color-night)] shadow-[0_4px_12px_rgba(20,17,24,0.08)] transition duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-tag)] focus-visible:ring-offset-2 disabled:opacity-60",
        listing.is_saved && "border-[color:var(--color-tag)] bg-[color:var(--color-tag)] text-white",
        className,
      )}
    >
      {listing.is_saved ? <BookmarkCheck size={15} aria-hidden="true" /> : <Bookmark size={15} aria-hidden="true" />}
      {pending ? "…" : listing.is_saved ? "Saved" : "Save"}
    </button>
  );
}
