import { Bell, RefreshCw, Trash2 } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";

import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function SavedSearchesPage() {
  usePageTitle("Saved Searches");
  const { data, loading, error, refetch, setData } = useApi("/saved-searches");
  const searches = data?.results ?? (Array.isArray(data) ? data : []);

  async function remove(id) {
    setData((prev) => {
      const list = prev?.results ?? (Array.isArray(prev) ? prev : []);
      return list.filter((s) => s.id !== id);
    });
    await apiFetch(`/saved-searches/${id}`, { method: "DELETE" }).catch(() => {});
  }

  function buildBrowseUrl(s) {
    const p = new URLSearchParams();
    if (s.keyword) p.set("search", s.keyword);
    if (s.category) p.set("category", s.category);
    if (s.price_type) p.set("price_type", s.price_type);
    return `/browse?${p.toString()}`;
  }

  return (
    <div className="mx-auto max-w-[680px] px-4 py-10">
      <div className="flex items-center gap-3 mb-8">
        <div className="h-10 w-10 rounded-xl bg-[color:var(--color-surface-2)] flex items-center justify-center">
          <Bell size={18} className="text-[color:var(--color-tag)]" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-[color:var(--color-ink)] dark:text-white">Saved Searches</h1>
          <p className="text-sm text-[color:var(--text-muted)]">You&apos;ll get notified when new matches appear.</p>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 rounded-2xl animate-pulse bg-[color:var(--color-surface)]" />
          ))}
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-16 text-center rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)]">
          <p className="font-semibold text-[color:var(--color-ink)] dark:text-white">Couldn&apos;t load saved searches</p>
          <p className="text-sm text-[color:var(--text-muted)] mt-1">{error}</p>
          <button
            onClick={() => refetch()}
            className="mt-4 flex items-center gap-1.5 px-4 py-2 rounded-full bg-[color:var(--color-tag)] text-white text-sm font-semibold hover:opacity-90"
          >
            <RefreshCw size={14} /> Try again
          </button>
        </div>
      ) : searches.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)]">
          <Bell size={36} className="text-[color:var(--text-muted)] mb-3" />
          <p className="font-semibold text-[color:var(--color-ink)] dark:text-white">No saved searches yet</p>
          <p className="text-sm text-[color:var(--text-muted)] mt-1">Use the bell icon on the Browse page to save a search.</p>
          <Link
            to="/browse"
            className="mt-5 px-5 py-2 bg-[color:var(--color-tag)] text-white rounded-full text-sm font-semibold hover:opacity-90 transition-opacity"
          >
            Browse listings
          </Link>
        </div>
      ) : (
        <ul className="space-y-3">
          {searches.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-4 rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] px-4 py-3.5"
            >
              <Link to={buildBrowseUrl(s)} className="flex-1 min-w-0 group">
                <p className="font-semibold text-[color:var(--color-ink)] dark:text-white group-hover:underline truncate">
                  {s.label || s.keyword || "All listings"}
                </p>
                <p className="text-xs text-[color:var(--text-muted)] mt-0.5">
                  {[s.category, s.price_type].filter(Boolean).join(" · ") || "No filters"}
                </p>
              </Link>
              <button
                onClick={() => remove(s.id)}
                aria-label="Delete saved search"
                className="h-8 w-8 flex items-center justify-center rounded-lg text-[color:var(--text-muted)] hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
              >
                <Trash2 size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
