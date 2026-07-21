import { AlertCircle, BookmarkCheck, Search } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";

import ListingCard from "../components/ListingCard";
import PageSection from "../components/PageSection";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";

function SavedCardSkeleton() {
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
      </div>
    </div>
  );
}

export default function SavedPage() {
  usePageTitle("Saved Items");
  const { data, loading, error, refetch, setData } = useApi("/saved-listings", {
    initialData: { results: [], count: 0 },
  });
  const savedListings = data?.results ?? [];

  function handleListingChange(updatedListing) {
    setData((current) => {
      const nextResults = updatedListing.is_saved
        ? current.results.map((item) =>
            item.listing.id === updatedListing.id ? { ...item, listing: updatedListing } : item,
          )
        : current.results.filter((item) => item.listing.id !== updatedListing.id);
      return { ...current, results: nextResults, count: nextResults.length };
    });
  }

  if (loading) {
    return (
      <div className="space-y-8">
        <div className="paper-panel p-10 sm:p-14 animate-pulse">
          <div className="h-5 w-28 rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="mt-5 h-10 w-3/4 rounded-2xl bg-[color:var(--color-surface-2)]" />
          <div className="mt-4 h-4 w-2/3 rounded-full bg-[color:var(--color-surface-2)]" />
        </div>
        <div className="grid gap-6 lg:grid-cols-2">
          {Array.from({ length: 4 }, (_, i) => <SavedCardSkeleton key={i} />)}
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
        <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Could not load saved items</h2>
        <p className="mt-4 text-[color:var(--text-muted)]">{error}</p>
        <button onClick={refetch} className="primary-button mt-8">Try Again</button>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageSection className="paper-panel p-10 sm:p-14">
        <div className="sticker bg-[color:var(--color-tag)] text-white">
          <BookmarkCheck size={14} />
          Saved watchlist
        </div>
        <h1 className="mt-5 text-[34px] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
          Keep your favorite move-out finds in one place.
        </h1>
        <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
          Save listings from Browse, then come back here when you&apos;re ready to reserve them.
        </p>
      </PageSection>

      {savedListings.length ? (
        <PageSection className="grid gap-6 lg:grid-cols-2" delay={0.06}>
          {savedListings.map((item) => (
            <ListingCard
              key={item.id}
              listing={item.listing}
              onListingChange={handleListingChange}
            />
          ))}
        </PageSection>
      ) : (
        <div className="paper-panel p-12 flex flex-col items-center text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[color:var(--color-surface-2)] mb-5">
            <Search size={28} className="text-[color:var(--text-muted)]" />
          </div>
          <h2 className="text-[24px] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Nothing saved yet.</h2>
          <p className="mt-3 max-w-sm text-[color:var(--text-muted)]">
            Bookmark listings from Browse and they&apos;ll appear here so you can claim them when you&apos;re ready.
          </p>
          <Link to="/browse" className="primary-button mt-8">
            <Search size={15} />
            Browse Available Listings
          </Link>
        </div>
      )}
    </div>
  );
}
