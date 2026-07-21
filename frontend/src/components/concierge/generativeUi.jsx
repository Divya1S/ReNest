import { BellPlus, CheckCircle2, MapPin, Package } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";

import { apiFetch } from "../../lib/api";

/**
 * Generative UI registry — the client half of server-driven UI.
 *
 * The concierge backend emits typed blocks ({type, props}) whose props are
 * hydrated from the database at tool-call time; this module maps block types
 * to real ReNest components. Unknown types render nothing, so the backend can
 * ship new blocks before (or without) the frontend catching up.
 *
 * "suggestions" blocks are deliberately absent from the registry: they render
 * as tappable quick-reply chips at the composer, not inside the transcript —
 * see extractSuggestions().
 */

function ListingCards({ listings }) {
  if (!listings?.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {listings.map((listing) => (
        <Link
          key={listing.id}
          to={`/listings/${listing.id}`}
          className="flex items-center gap-3 rounded-xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface)] p-2.5 transition-colors hover:bg-[color:var(--bg-surface-2)]"
        >
          {listing.thumb ? (
            <img
              src={listing.thumb}
              alt=""
              className="h-11 w-11 shrink-0 rounded-lg object-cover"
              loading="lazy"
            />
          ) : (
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[color:var(--bg-surface-2)]">
              <Package size={18} className="text-[color:var(--text-muted)]" aria-hidden="true" />
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-[color:var(--color-night)]">
              {listing.title}
            </span>
            <span className="mt-0.5 flex items-center gap-1 text-xs text-[color:var(--text-muted)]">
              <MapPin size={11} aria-hidden="true" />
              <span className="truncate">{listing.pickup_zone}</span>
            </span>
          </span>
          <span
            className={
              listing.price_type === "free"
                ? "shrink-0 rounded-full bg-[color:var(--color-tag)] px-2.5 py-1 text-xs font-bold text-white"
                : "shrink-0 rounded-full bg-[color:var(--bg-surface-2)] px-2.5 py-1 text-xs font-bold text-[color:var(--color-night)]"
            }
          >
            {listing.price_type === "free" ? "Free" : `$${listing.price_amount}`}
          </span>
        </Link>
      ))}
    </div>
  );
}

function MoveOutProgress({ scan_sessions: sessions }) {
  if (!sessions?.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {sessions.map((session) => (
        <div
          key={session.id}
          className="rounded-xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface)] p-3"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-sm font-semibold text-[color:var(--color-night)]">{session.name}</p>
            <p className="shrink-0 text-xs font-bold text-[color:var(--color-night)]">
              {session.progress_percent}%
            </p>
          </div>
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-[color:var(--bg-surface-2)]"
            role="progressbar"
            aria-valuenow={session.progress_percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${session.name} progress`}
          >
            <div
              className="h-full rounded-full bg-[color:var(--color-teal)]"
              style={{ width: `${Math.min(100, session.progress_percent)}%` }}
            />
          </div>
          <p className="mt-2 flex items-center gap-1 text-xs text-[color:var(--text-muted)]">
            <CheckCircle2 size={12} aria-hidden="true" />
            {session.tasks_done}/{session.tasks_total} tasks done · {session.status}
          </p>
        </div>
      ))}
    </div>
  );
}

/**
 * The concierge's first "write" flow — deliberately user-confirmed. The model
 * only *proposes*; nothing exists until the user taps Create, and the tap goes
 * through the exact same /saved-searches endpoint the settings page uses.
 */
function SavedSearchProposal({ keyword, category, price_type: priceType, label }) {
  const [state, setState] = useState("idle"); // idle | saving | done | error
  const [detail, setDetail] = useState("");

  const create = async () => {
    setState("saving");
    try {
      await apiFetch("/saved-searches", {
        method: "POST",
        body: { keyword, category, price_type: priceType, label },
      });
      setState("done");
    } catch (error) {
      setState("error");
      setDetail(error?.message || "Could not create the alert.");
    }
  };

  const filters = [
    keyword && `“${keyword}”`,
    category && category.charAt(0).toUpperCase() + category.slice(1),
    priceType === "free" && "Free only",
  ].filter(Boolean);

  return (
    <div className="mt-2 rounded-xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface)] p-3">
      <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.12em] text-[color:var(--color-box)]">
        <BellPlus size={13} aria-hidden="true" />
        Alert proposal
      </p>
      <p className="mt-1.5 text-sm text-[color:var(--color-night)]">
        Get notified when new listings match {filters.join(" · ")}.
      </p>
      {state === "done" ? (
        <p className="mt-2 text-sm font-semibold text-[color:var(--color-teal)]">
          Alert on — manage it in{" "}
          <Link to="/saved-searches" className="underline">
            Saved searches
          </Link>
          .
        </p>
      ) : (
        <div className="mt-2.5 flex items-center gap-3">
          <button
            type="button"
            onClick={create}
            disabled={state === "saving"}
            className="primary-button !px-4 !py-1.5 text-sm disabled:opacity-50"
          >
            {state === "saving" ? "Creating…" : "Create alert"}
          </button>
          {state === "error" && (
            <p className="text-xs text-amber-700 dark:text-amber-300">{detail}</p>
          )}
        </div>
      )}
    </div>
  );
}

const BLOCK_REGISTRY = {
  listing_cards: ListingCards,
  move_out_progress: MoveOutProgress,
  saved_search_proposal: SavedSearchProposal,
};

export function GenerativeBlocks({ blocks }) {
  if (!blocks?.length) return null;
  return (
    <>
      {blocks.map((block, index) => {
        const Component = BLOCK_REGISTRY[block.type];
        if (!Component) return null; // forward-compatible: skip unknown blocks
        return <Component key={`${block.type}-${index}`} {...(block.props || {})} />;
      })}
    </>
  );
}

/** Quick-reply chips live at the composer, not in the transcript.
 * Normalised to {label, path}: path chips navigate, label-only chips send. */
export function extractSuggestions(blocks) {
  const block = (blocks || []).find((candidate) => candidate.type === "suggestions");
  return (block?.props?.suggestions || [])
    .map((entry) => (typeof entry === "string" ? { label: entry, path: null } : entry))
    .filter((entry) => entry && entry.label);
}
