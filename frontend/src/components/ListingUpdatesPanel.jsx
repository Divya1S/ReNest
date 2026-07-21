import { MessageSquareMore, SendHorizonal } from "lucide-react";
import React, { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiFetch, asResults } from "../lib/api";
import { formatDateTime } from "../lib/formatters";

import PageSection from "./PageSection";

export default function ListingUpdatesPanel({ listingId, canPost }) {
  const [updates, setUpdates] = useState([]);
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  async function loadUpdates() {
    const data = await apiFetch(`/listings/${listingId}/updates`);
    setUpdates(asResults(data));
  }

  useEffect(() => {
    let active = true;

    loadUpdates()
      .catch((error) => {
        if (active) {
          toast.error(error.message);
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listingId]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!body.trim()) {
      return;
    }

    setSubmitting(true);
    try {
      await apiFetch(`/listings/${listingId}/updates`, {
        method: "POST",
        body: { body },
      });
      setBody("");
      await loadUpdates();
      toast.success("Update posted.");
    } catch (error) {
      toast.error(error.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageSection className="soft-panel p-6" delay={0.08}>
      <div className="flex items-center gap-3">
        <div className="rounded-2xl bg-[color:var(--color-tag-soft)] p-3 text-[color:var(--color-tag)]">
          <MessageSquareMore size={20} />
        </div>
        <div>
          <p className="label-title">Coordination thread</p>
          <h3 className="mt-1 text-[20px] font-bold tracking-[-0.01em]">Pickup updates and handoff notes</h3>
        </div>
      </div>

      {canPost ? (
        <form onSubmit={handleSubmit} className="mt-5 space-y-3">
          <textarea
            className="field min-h-28"
            placeholder="Share where you will meet, timing updates, or pickup details."
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
          <div className="flex justify-end">
            <button type="submit" disabled={submitting} className="primary-button">
              <SendHorizonal size={15} />
              <span>{submitting ? "Posting..." : "Post Update"}</span>
            </button>
          </div>
        </form>
      ) : (
        <div className="mt-5 rounded-3xl border border-dashed border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-5 py-4 text-sm leading-6 text-[color:var(--text-muted)]">
          Reserve the listing first, or own it, to post coordination notes here.
        </div>
      )}

      <div className="mt-6 space-y-3">
        {loading ? <div className="soft-panel p-4 text-sm font-semibold text-[color:var(--text-muted)]">Loading updates...</div> : null}
        {!loading && updates.length === 0 ? (
          <div className="soft-panel p-4 text-sm leading-6 text-[color:var(--text-muted)]">
            No updates yet. The first note usually confirms the pickup window or meetup spot.
          </div>
        ) : null}
        {updates.map((update) => (
          <div key={update.id} className="rounded-3xl border border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] px-5 py-4 shadow-sm dark:shadow-none">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-black text-[color:var(--color-ink)]">{update.author.display_name}</p>
              <p className="text-xs font-bold uppercase tracking-[0.16em] text-[color:var(--text-muted)]">
                {formatDateTime(update.created_at)}
              </p>
            </div>
            <p className="mt-3 text-sm leading-6 text-[color:var(--text-muted)]">{update.body}</p>
          </div>
        ))}
      </div>
    </PageSection>
  );
}
