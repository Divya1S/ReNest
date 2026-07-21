import { motion, AnimatePresence } from "framer-motion";
import { Clock3, MapPinned, Warehouse, Search, Map as MapIcon, Navigation, ChevronRight, Settings } from "lucide-react";
import React, { useEffect, useState, useMemo } from "react";
import { Link } from "react-router-dom";

import PageSection from "../components/PageSection";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch, asResults } from "../lib/api";

export default function HubsPage() {
  usePageTitle("Donation Hubs");
  const [hubs, setHubs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const deferredSearch = React.useDeferredValue(search);

  useEffect(() => {
    let active = true;
    apiFetch("/hubs")
      .then((data) => {
        if (active) {
          setHubs(asResults(data));
        }
      })
      .catch((requestError) => {
        if (active) {
          setError(requestError.message);
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
  }, []);

  const filteredHubs = useMemo(() => {
    const normalized = deferredSearch.trim().toLowerCase();
    if (!normalized) {
      return hubs;
    }
    return hubs.filter((hub) =>
      hub.name.toLowerCase().includes(normalized) ||
      hub.campus_name.toLowerCase().includes(normalized) ||
      hub.zone_label.toLowerCase().includes(normalized),
    );
  }, [deferredSearch, hubs]);

  return (
    <div className="space-y-12 pb-20">
      <PageSection className="paper-panel p-8 sm:p-12 relative overflow-hidden">
        <div className="absolute top-0 right-0 p-8 opacity-10 dark:opacity-5">
           <MapPinned size={300} strokeWidth={0.5} />
        </div>
        
        <div className="relative z-10 max-w-3xl">
          <p className="label-title">Donation hubs</p>
          <h1 className="mt-6 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
            Safe, fast, and <span className="text-[color:var(--color-tag)]">campus-friendly.</span>
          </h1>
          <p className="mt-6 text-lg leading-relaxed text-[color:var(--text-muted)]">
            ReNest keeps exchanges local, using pickup zones and campus hubs that work around move-out schedules instead of shipping.
          </p>

          <div className="mt-10 relative group">
            <div className="absolute inset-y-0 left-5 flex items-center pointer-events-none text-[color:var(--text-muted)] group-focus-within:text-[color:var(--color-tag)] transition-colors">
              <Search size={22} />
            </div>
            <input
              type="text"
              aria-label="Search hubs by name, campus, or pickup zone"
              placeholder="Search hubs or pickup zones..."
              className="w-full rounded-[2.5rem] border-2 border-slate-100 bg-white py-6 pl-14 pr-6 text-xl font-medium shadow-sm transition-all focus:border-[color:var(--color-tag)] focus:ring-4 focus:ring-[color:var(--color-tag-soft)] dark:border-slate-800 dark:bg-slate-900 text-[color:var(--color-ink)] dark:text-white"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </PageSection>

      {error ? (
        <PageSection className="flex flex-col items-center justify-center py-20 bg-red-50 rounded-[2.5rem] dark:bg-red-900/10">
           <h3 className="text-[20px] font-bold tracking-[-0.01em] text-red-900 dark:text-red-400">Failed to load hubs</h3>
           <p className="text-red-600/70">{error}</p>
        </PageSection>
      ) : null}

      <PageSection aria-live="polite" aria-atomic="false">
        {loading ? (
          <div className="grid gap-8 lg:grid-cols-2">
            {[1, 2, 3, 4].map(i => (
              <div key={i} aria-hidden="true" className="paper-panel h-64 animate-pulse bg-[color:var(--color-surface-2)] !rounded-[2.5rem]" />
            ))}
          </div>
        ) : filteredHubs.length > 0 ? (
          <motion.div 
            layout
            className="grid gap-8 lg:grid-cols-2"
          >
            <AnimatePresence mode="popLayout">
              {filteredHubs.map((hub) => (
                <motion.article 
                  key={hub.id}
                  layout
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  whileHover={{ y: -8 }}
                  className="paper-panel p-8 group"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-4">
                      <div className="flex h-16 w-16 items-center justify-center rounded-[1.5rem] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)] group-hover:scale-110 transition-transform">
                        <Warehouse size={32} />
                      </div>
                      <div>
                        <p className="label-title !mb-1">{hub.campus_name}</p>
                        <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">{hub.name}</h2>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {hub.is_manager && (
                        <Link
                          to={`/hubs/${hub.id}/manage`}
                          className="flex items-center gap-1.5 rounded-2xl bg-[color:var(--color-tag-soft)] px-3 py-2 text-xs font-semibold text-[color:var(--color-tag)] transition hover:bg-[color:var(--color-tag)] hover:text-white"
                          aria-label={`Manage ${hub.name}`}
                        >
                          <Settings size={14} />
                          Manage
                        </Link>
                      )}
                      <div className="p-3 rounded-2xl bg-slate-50 dark:bg-slate-800 text-[color:var(--text-muted)]">
                        <MapIcon size={24} />
                      </div>
                    </div>
                  </div>

                  <div className="mt-8 flex items-center gap-3">
                    <div className="flex items-center gap-2 rounded-full bg-[color:var(--color-tag)] px-4 py-1.5 text-[0.65rem] font-black uppercase tracking-widest text-white shadow-lg">
                      <MapPinned size={14} />
                      {hub.zone_label}
                    </div>
                    <div className="flex items-center gap-2 rounded-full bg-slate-100 px-4 py-1.5 text-[0.65rem] font-bold uppercase tracking-widest text-[color:var(--text-muted)] dark:bg-slate-800 dark:text-slate-400">
                      <Navigation size={14} />
                      Directions available
                    </div>
                  </div>

                  <p className="mt-6 text-lg leading-relaxed text-[color:var(--text-muted)]">{hub.description}</p>
                  
                  <div className="mt-8 rounded-[2rem] bg-slate-50 p-6 dark:bg-slate-800/50 group-hover:bg-[color:var(--color-tag-soft)] transition-colors">
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col">
                        <p className="inline-flex items-center gap-2 text-xs font-black uppercase tracking-widest text-[color:var(--text-muted)] group-hover:text-[color:var(--color-tag)] transition-colors">
                          <Clock3 size={15} />
                          Pickup instructions
                        </p>
                        <p className="mt-2 text-sm leading-relaxed text-slate-700 dark:text-slate-200">{hub.open_instructions}</p>
                      </div>
                      <div className="h-10 w-10 flex items-center justify-center rounded-full bg-white dark:bg-slate-700 shadow-sm opacity-0 group-hover:opacity-100 transition-opacity">
                         <ChevronRight size={20} className="text-[color:var(--color-tag)]" />
                      </div>
                    </div>
                  </div>
                </motion.article>
              ))}
            </AnimatePresence>
          </motion.div>
        ) : (
          <div className="flex flex-col items-center justify-center py-32 text-center bg-slate-50 rounded-[3rem] dark:bg-slate-900/40">
            <div className="mb-8 flex h-28 w-28 items-center justify-center rounded-full bg-white text-slate-100 shadow-xl dark:bg-slate-800 dark:text-slate-700">
              <Warehouse size={56} />
            </div>
            <h3 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">No hubs found</h3>
            <p className="mt-3 text-[color:var(--text-muted)] max-w-sm text-lg">
              We couldn&apos;t find any donation hubs matching your search.
            </p>
            <button
              type="button"
              onClick={() => setSearch("")}
              className="primary-button mt-10"
            >
              Clear Search
            </button>
          </div>
        )}
      </PageSection>
    </div>
  );
}
