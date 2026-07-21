import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  SlidersHorizontal,
  LayoutGrid,
  List,
  Map,
  Bell,
  Check,
  Sparkles,
  X,
} from "lucide-react";
import React, { lazy, Suspense, useEffect, useRef, useState, useTransition, useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";

import AnnouncementBanner from "../components/AnnouncementBanner";
import ListingCard from "../components/ListingCard";
import ListingPreviewModal from "../components/ListingPreviewModal";
import TrendingStrip from "../components/TrendingStrip";
import { useAuth } from "../context/AuthContext";
import { useDebounce } from "../hooks/useDebounce";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { cn } from "../lib/cn";
import type { Listing, NlChip, NlParseResult, PaginatedListings } from "../types";

const MapView = lazy(() => import("../components/MapView"));

const CATEGORIES = [
  { label: "All Items", value: "" },
  { label: "Storage", value: "storage" },
  { label: "Lighting", value: "lighting" },
  { label: "Toiletries", value: "toiletries" },
  { label: "Comfort", value: "comfort" },
  { label: "Supplies", value: "supplies" },
];

const PRICE_OPTIONS = [
  { label: "All", value: "" },
  { label: "Free", value: "free" },
  { label: "Low Cost", value: "low_cost" },
];

export default function BrowsePage() {
  usePageTitle("Browse Listings");
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [isPending, startTransition] = useTransition();

  const [searchInput, setSearchInput] = useState(() => searchParams.get("search") ?? "");
  const [category, setCategory] = useState(() => searchParams.get("category") ?? "");
  const [priceType, setPriceType] = useState(() => searchParams.get("price_type") ?? "");
  const [building, setBuilding] = useState(() => searchParams.get("building") ?? "");
  const [sort, setSort] = useState(() => searchParams.get("sort") ?? "deadline");

  const debouncedSearch = useDebounce(searchInput, 350);

  const [listings, setListings] = useState<Listing[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [retryKey, setRetryKey] = useState(0);
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [viewMode, setViewMode] = useState<"grid" | "list" | "map">("grid");
  const [selectedListing, setSelectedListing] = useState<Listing | null>(null);
  const [nlChips, setNlChips] = useState<NlChip[]>([]);
  const [nlParsing, setNlParsing] = useState(false);
  const [savedSearch, setSavedSearch] = useState<"saving" | "saved" | "error" | null>(null);
  const parseControllerRef = useRef<AbortController | null>(null);
  const sentinelRef = useRef(null);

  useEffect(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("search", debouncedSearch);
    if (category) params.set("category", category);
    if (priceType) params.set("price_type", priceType);
    if (building) params.set("building", building);
    if (sort && sort !== "deadline") params.set("sort", sort);
    setSearchParams(params, { replace: true });
  }, [debouncedSearch, category, priceType, building, sort, setSearchParams]);

  // Auto-select relevance sort when the user starts typing a search query
  useEffect(() => {
    if (debouncedSearch && sort === "deadline") setSort("relevance");
    if (!debouncedSearch && sort === "relevance") setSort("deadline");
  }, [debouncedSearch]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, category, priceType, building, sort]);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      if (page === 1) {
        setLoading(true);
        setError("");
      } else {
        setLoadingMore(true);
      }
      try {
        const params = new URLSearchParams();
        if (debouncedSearch) params.set("search", debouncedSearch);
        if (category) params.set("category", category);
        if (priceType) params.set("price_type", priceType);
        if (building) params.set("building", building);
        if (sort) params.set("sort", sort);
        params.set("page", String(page));
        const query = `?${params.toString()}`;
        const data = await apiFetch<PaginatedListings>(`/listings${query}`, { signal: controller.signal });
        if (page === 1) {
          setListings(data.results);
        } else {
          setListings((current) => [...current, ...data.results]);
        }
        setTotalCount(data.count);
        setHasMore(!!data.next);
      } catch (requestError) {
        const re = requestError as Error;
        if (re.name !== "AbortError") setError(re.message);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    }

    load();
    return () => controller.abort();
  }, [debouncedSearch, category, priceType, building, sort, page, retryKey]);

  // Infinite scroll — advance page when the sentinel enters the viewport
  useEffect(() => {
    if (!sentinelRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasMore && !loadingMore && !loading) {
          setPage((p) => p + 1);
        }
      },
      { rootMargin: "200px" }
    );
    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [hasMore, loadingMore, loading]);

  const replaceListing = useCallback((updatedListing: Listing) => {
    setListings((current) =>
      current.map((l) => (l.id === updatedListing.id ? updatedListing : l)),
    );
    setSelectedListing((current) =>
      current?.id === updatedListing.id ? updatedListing : current,
    );
  }, []);

  const parseSearchQuery = useCallback(async (query: string) => {
    if (!query.trim()) return;
    if (parseControllerRef.current) parseControllerRef.current.abort();
    parseControllerRef.current = new AbortController();
    setNlParsing(true);
    try {
      const data = await apiFetch<NlParseResult>("/listings/parse-search", {
        method: "POST",
        body: { query },
        signal: parseControllerRef.current.signal,
      });
      if (data.search !== null) setSearchInput(data.search ?? "");
      startTransition(() => {
        if (data.category) setCategory(data.category);
        if (data.price_type) setPriceType(data.price_type);
      });
      setNlChips(data.chips ?? []);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        // silently ignore — user can still use the raw query
      }
    } finally {
      setNlParsing(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const removeNlChip = useCallback((field: string) => {
    setNlChips((prev) => prev.filter((c) => c.field !== field));
    startTransition(() => {
      if (field === "category") setCategory("");
      if (field === "price_type") setPriceType("");
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const resetFilters = useCallback(() => {
    setSearchInput("");
    setNlChips([]);
    startTransition(() => {
      setCategory("");
      setPriceType("");
      setBuilding("");
      setSort("deadline");
    });
  }, []);

  const isFiltered = searchInput || category || priceType || building;
  const showPending = isPending && !loading;

  const saveCurrentSearch = useCallback(async () => {
    if (!isFiltered) return;
    setSavedSearch("saving");
    try {
      await apiFetch("/saved-searches", {
        method: "POST",
        body: {
          label: searchInput || category || "My search",
          category,
          keyword: searchInput,
          price_type: priceType,
        },
      });
      setSavedSearch("saved");
      setTimeout(() => setSavedSearch(null), 3000);
    } catch {
      setSavedSearch("error");
      setTimeout(() => setSavedSearch(null), 2000);
    }
  }, [isFiltered, searchInput, category, priceType]);

  return (
    <div className="pb-16">
      {/* Sticky search + filter bar */}
      <div
        className="sticky top-[var(--nav-h,4.5rem)] z-30 border-b border-black/8 dark:border-white/8"
        style={{
          background: "color-mix(in srgb, var(--bg-paper) 92%, transparent)",
          backdropFilter: "saturate(180%) blur(20px)",
        }}
      >
        <div className="mx-auto max-w-[1200px] px-4 md:px-8 py-4 space-y-3">
          {/* Search bar */}
          <div className="relative group">
            <div className="absolute inset-y-0 left-4 flex items-center pointer-events-none text-[color:var(--text-muted)] group-focus-within:text-[color:var(--color-tag)] transition-colors">
              <Search size={18} />
            </div>
            <input
              type="search"
              placeholder='Try "free lamp near north dorms" or "storage bins under $10"'
              aria-label="Search listings"
              className="w-full rounded-[12px] border border-black/10 dark:border-white/10 py-3 pl-11 pr-32 text-[15px] text-[color:var(--color-ink)] dark:text-white placeholder:text-[color:var(--text-muted)] transition-all focus:border-[color:var(--color-tag)] focus:ring-2 focus:ring-[color:var(--color-tag)]/10 outline-none"
              style={{ background: "var(--color-surface-2,#fff0f2)" }}
              value={searchInput}
              onChange={(e) => { setSearchInput(e.target.value); setNlChips([]); }}
              onKeyDown={(e) => e.key === "Enter" && parseSearchQuery(searchInput)}
            />
            <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
              <AnimatePresence>
                {searchInput && (
                  <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    transition={{ duration: 0.12 }}
                    onClick={() => { setSearchInput(""); setNlChips([]); }}
                    aria-label="Clear search"
                    className="p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]"
                  >
                    <X size={15} />
                  </motion.button>
                )}
              </AnimatePresence>
              <button
                onClick={() => parseSearchQuery(searchInput)}
                disabled={!searchInput.trim() || nlParsing}
                aria-label="Parse with AI"
                title="Parse with AI"
                className="flex items-center gap-1.5 rounded-[8px] bg-[color:var(--color-tag)] px-3 py-1.5 text-[11px] font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Sparkles size={12} className={nlParsing ? "animate-spin" : ""} />
                {nlParsing ? "Parsing…" : "AI"}
              </button>
            </div>
          </div>

          {/* AI intent chips */}
          <AnimatePresence>
            {nlChips.length > 0 && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.18 }}
                className="flex flex-wrap gap-2 overflow-hidden"
              >
                <span className="text-[11px] font-semibold uppercase tracking-wide text-[color:var(--text-muted)] flex items-center">
                  <Sparkles size={10} className="mr-1" /> AI parsed:
                </span>
                {nlChips.map((chip) => (
                  <button
                    key={chip.field}
                    onClick={() => removeNlChip(chip.field)}
                    className="flex items-center gap-1 rounded-full bg-[color:var(--color-tag)]/10 border border-[color:var(--color-tag)]/20 px-3 py-1 text-[12px] font-medium text-[color:var(--color-tag)] transition-all hover:bg-[color:var(--color-tag)]/20"
                  >
                    {chip.label}
                    <X size={11} className="ml-0.5" />
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>

          {/* Building filter + Category chips row */}
          <div className="flex items-center gap-3 overflow-x-auto pb-0.5" style={{ scrollbarWidth: "none" }}>
            <div className="relative shrink-0">
              <input
                type="text"
                placeholder="Building / dorm…"
                aria-label="Filter by building"
                value={building}
                onChange={(e) => startTransition(() => setBuilding(e.target.value))}
                className="rounded-full border border-black/10 dark:border-white/10 bg-[color:var(--color-surface)] py-2 pl-3 pr-7 text-[13px] text-[color:var(--color-ink)] dark:text-white placeholder:text-[color:var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]/10 focus:border-[color:var(--color-tag)] transition-all w-36"
              />
              {building && (
                <button
                  onClick={() => startTransition(() => setBuilding(""))}
                  aria-label="Clear building filter"
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]"
                >
                  <X size={12} />
                </button>
              )}
            </div>
            <div className="h-4 w-px bg-black/10 dark:bg-white/10 shrink-0" />

          {/* Category chips */}
          <div
            role="group"
            aria-label="Filter by category"
            className="flex items-center gap-2"
          >
            {CATEGORIES.map((cat) => (
              <button
                key={cat.value}
                onClick={() => { setNlChips((prev) => prev.filter((c) => c.field !== "category")); startTransition(() => setCategory(cat.value)); }}
                aria-pressed={category === cat.value}
                className={cn(
                  "whitespace-nowrap rounded-full px-4 py-2 text-[13px] font-medium transition-all shrink-0 min-h-[2.75rem] flex items-center",
                  category === cat.value
                    ? "bg-[color:var(--color-tag)] text-white shadow-sm"
                    : "bg-[color:var(--color-surface)] border border-black/10 dark:border-white/10 text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface-2)]",
                )}
              >
                {cat.label}
              </button>
            ))}
          </div>
          </div>
        </div>
      </div>

      {/* Authenticated-only strips — skipped for guests so no 401s fire */}
      {user && <AnnouncementBanner />}
      {user && <TrendingStrip />}

      {/* Main content */}
      <div className="mx-auto max-w-[1200px] px-4 md:px-8 pt-6">
        {/* Results header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
          <div>
            <h1
              className={cn(
                "text-[28px] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white transition-opacity",
                showPending && "opacity-50",
              )}
            >
              {loading ? "Loading…" : `${totalCount} Result${totalCount !== 1 ? "s" : ""}`}
            </h1>
            {/* Screen-reader announcement kept separate from the heading */}
            <div role="status" aria-live="polite" className="sr-only">
              {loading ? "Loading results" : `${totalCount} result${totalCount !== 1 ? "s" : ""} found`}
            </div>
            <p className="text-[13px] text-[color:var(--text-muted)] mt-0.5">
              Items available for pickup near your campus
            </p>
          </div>

          <div className="flex items-center gap-2.5 shrink-0 flex-wrap">
            {/* Price filter */}
            <div
              role="group"
              aria-label="Filter by price"
              className="flex items-center gap-0.5 bg-[color:var(--color-surface)] border border-black/10 dark:border-white/10 rounded-[10px] p-0.5"
            >
              {PRICE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => { setNlChips((prev) => prev.filter((c) => c.field !== "price_type")); startTransition(() => setPriceType(opt.value)); }}
                  aria-pressed={priceType === opt.value}
                  className={cn(
                    "px-3 py-2.5 rounded-[8px] text-[11px] font-semibold tracking-[0.02em] uppercase transition-all min-h-[2.75rem] flex items-center",
                    priceType === opt.value
                      ? "bg-[color:var(--color-ink)] text-white dark:bg-white dark:text-[color:var(--color-ink)]"
                      : "text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface-2)]",
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <div className="h-5 w-px bg-black/10 dark:bg-white/10" />

            {/* Sort */}
            <div className="flex items-center gap-1.5 bg-[color:var(--color-surface)] border border-black/10 dark:border-white/10 rounded-[10px] px-3 py-2.5 min-h-[2.75rem]">
              <SlidersHorizontal size={13} className="text-[color:var(--text-muted)]" />
              <select
                aria-label="Sort results"
                className="bg-transparent text-[11px] font-semibold uppercase tracking-[0.02em] text-[color:var(--color-ink)] dark:text-white focus:outline-none cursor-pointer min-h-[1.5rem]"
                value={sort}
                onChange={(e) => startTransition(() => setSort(e.target.value))}
              >
                {debouncedSearch && <option value="relevance">Relevance</option>}
                <option value="deadline">Deadline</option>
                <option value="newest">Newest</option>
                <option value="price_low">Price ↑</option>
                <option value="price_high">Price ↓</option>
              </select>
            </div>

            <div className="h-5 w-px bg-black/10 dark:bg-white/10" />

            {/* View toggle */}
            <div
              role="group"
              aria-label="Toggle view mode"
              className="flex items-center bg-[color:var(--color-surface)] border border-black/10 dark:border-white/10 rounded-[10px] p-0.5"
            >
              <button
                onClick={() => setViewMode("grid")}
                aria-pressed={viewMode === "grid"}
                aria-label="Grid view"
                className={cn(
                  "flex h-11 w-11 items-center justify-center rounded-[8px] transition-all",
                  viewMode === "grid"
                    ? "bg-[color:var(--color-surface-2)] text-[color:var(--color-tag)]"
                    : "text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]",
                )}
              >
                <LayoutGrid size={16} />
              </button>
              <button
                onClick={() => setViewMode("list")}
                aria-pressed={viewMode === "list"}
                aria-label="List view"
                className={cn(
                  "flex h-11 w-11 items-center justify-center rounded-[8px] transition-all",
                  viewMode === "list"
                    ? "bg-[color:var(--color-surface-2)] text-[color:var(--color-tag)]"
                    : "text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]",
                )}
              >
                <List size={16} />
              </button>
              <button
                onClick={() => setViewMode("map")}
                aria-pressed={viewMode === "map"}
                aria-label="Map view"
                className={cn(
                  "flex h-11 w-11 items-center justify-center rounded-[8px] transition-all",
                  viewMode === "map"
                    ? "bg-[color:var(--color-surface-2)] text-[color:var(--color-tag)]"
                    : "text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]",
                )}
              >
                <Map size={16} />
              </button>
            </div>

            {user && <div className="h-5 w-px bg-black/10 dark:bg-white/10" />}

            {/* Save search — requires an account */}
            {user && (
            <button
              onClick={saveCurrentSearch}
              aria-label={savedSearch === "saved" ? "Search saved" : "Save this search"}
              title={savedSearch === "saved" ? "Search saved" : "Save this search"}
              disabled={savedSearch === "saving" || savedSearch === "saved"}
              className={cn(
                "flex h-11 w-11 items-center justify-center rounded-[10px] border transition-all",
                savedSearch === "saved"
                  ? "border-[color:var(--color-tag)] text-[color:var(--color-tag)] bg-[color:var(--color-surface)]"
                  : savedSearch === "error"
                    ? "border-red-400 text-red-500 bg-[color:var(--color-surface)]"
                    : "border-black/10 dark:border-white/10 text-[color:var(--text-muted)] bg-[color:var(--color-surface)] hover:text-[color:var(--color-ink)]",
              )}
            >
              {savedSearch === "saved" ? <Check size={16} /> : <Bell size={16} />}
            </button>
            )}
          </div>
        </div>

        {/* Guest prompt — browsing is open; acting needs an account */}
        {!user && (
          <div className="mb-6 flex flex-col sm:flex-row sm:items-center gap-3 rounded-[16px] border border-[color:var(--color-tag)]/20 bg-[color:var(--bg-tag-soft)] px-5 py-4">
            <p className="flex-1 text-[14px] text-[color:var(--color-ink)] dark:text-white">
              You&apos;re browsing as a guest. Create a free account to reserve items, save favorites, and post your own rescues.
            </p>
            <Link
              to="/register"
              className="shrink-0 rounded-full bg-[color:var(--color-tag)] px-5 py-2 text-center text-[13px] font-semibold text-white hover:opacity-90 transition-opacity"
            >
              Join Free
            </Link>
          </div>
        )}

        {/* Results area */}
        {viewMode === "map" ? (
          <Suspense fallback={<div className="h-[500px] flex items-center justify-center"><div className="h-6 w-6 animate-spin rounded-full border-2 border-[color:var(--color-tag)] border-t-transparent" /></div>}>
            <MapView className="h-[500px] mb-10" />
          </Suspense>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-20 bg-red-50 dark:bg-red-900/10 rounded-[20px]">
            <X size={40} className="text-red-500 mb-3" />
            <h3 className="text-xl font-bold text-red-900 dark:text-red-400">Failed to load listings</h3>
            <p className="text-red-600/70 text-sm mt-1">{error}</p>
            <button
              onClick={() => setRetryKey((k) => k + 1)}
              className="mt-6 px-6 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-full text-[13px] font-semibold transition-colors"
            >
              Try Again
            </button>
          </div>
        ) : loading ? (
          <div className={`grid gap-5 ${viewMode === "grid" ? "sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4" : "grid-cols-1"}`}>
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="rounded-[20px] overflow-hidden border border-black/10 dark:border-white/10 animate-pulse bg-[color:var(--color-surface)]">
                <div className="aspect-square bg-[color:var(--color-surface-2)]" />
                <div className="p-5 space-y-3">
                  <div className="flex justify-between gap-2">
                    <div className="h-5 w-3/5 rounded-full bg-[color:var(--color-surface-2)]" />
                    <div className="h-5 w-1/5 rounded-full bg-[color:var(--color-surface-2)]" />
                  </div>
                  <div className="h-3.5 w-2/5 rounded-full bg-[color:var(--color-surface-2)]" />
                  <div className="h-9 w-full rounded-full bg-[color:var(--color-surface-2)]" />
                </div>
              </div>
            ))}
          </div>
        ) : listings.length > 0 ? (
          <motion.div
            layout
            className={`grid gap-5 ${viewMode === "grid" ? "sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4" : "grid-cols-1"}`}
          >
            <AnimatePresence mode="popLayout">
              {listings.map((listing) => (
                <motion.div
                  key={listing.id}
                  layout
                  initial={{ opacity: 0, scale: 0.97 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.97 }}
                  transition={{ duration: 0.25 }}
                  onClick={() => setSelectedListing(listing)}
                  className="cursor-pointer h-full"
                >
                  <ListingCard
                    listing={listing}
                    onListingChange={replaceListing}
                    onQuickView={() => setSelectedListing(listing)}
                    compact={viewMode === "list"}
                    className=""
                  />
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        ) : (
          <div className="flex flex-col items-center justify-center py-28 text-center bg-[color:var(--color-surface)] dark:bg-[#1c1c1e] rounded-[20px] border border-black/8 dark:border-white/8">
            <img
              src="/images/empty-browse-results.png"
              alt=""
              aria-hidden="true"
              loading="lazy"
              className="mb-5 h-28 w-28 object-contain dark:rounded-3xl dark:bg-white/90 dark:p-3"
            />
            <h3 className="text-2xl font-bold text-[color:var(--color-ink)] dark:text-white">No matches found</h3>
            <p className="mt-2 text-[color:var(--text-muted)] max-w-xs text-[15px]">
              Try adjusting your filters or search terms.
            </p>
            {isFiltered && (
              <button
                onClick={resetFilters}
                className="mt-6 px-6 py-2.5 bg-[color:var(--color-tag)] text-white rounded-full text-[13px] font-semibold hover:opacity-90 transition-opacity"
              >
                Reset Filters
              </button>
            )}
          </div>
        )}

        {/* Infinite scroll sentinel */}
        <div ref={sentinelRef} className="h-1" />
        {loadingMore && (
          <div className="mt-6 flex justify-center">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-[color:var(--color-tag)] border-t-transparent" />
          </div>
        )}

        {/* Bottom CTA */}
        {!loading && !error && (
          <div className="mt-12 text-center">
            <Link
              to="/requests"
              className="inline-block px-8 py-3 border border-black/10 dark:border-white/10 rounded-full text-[13px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface)] transition-colors"
            >
              Don&apos;t see what you need? Post a rescue request
            </Link>
          </div>
        )}
      </div>

      <ListingPreviewModal
        listing={selectedListing}
        isOpen={!!selectedListing}
        onClose={() => setSelectedListing(null)}
      />
    </div>
  );
}
