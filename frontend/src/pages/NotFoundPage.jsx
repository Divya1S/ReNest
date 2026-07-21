import { ArrowRight, SearchX } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";

import { usePageTitle } from "../hooks/usePageTitle";

export default function NotFoundPage() {
  usePageTitle("Not Found");
  return (
    <section className="mx-auto max-w-3xl paper-panel p-8 sm:p-10 text-center">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-3xl bg-[color:var(--color-paper)] text-[color:var(--color-tag)]">
        <SearchX size={30} />
      </div>
      <p className="label-title mt-6">Page not found</p>
      <h1 className="mt-3 text-[34px] font-bold tracking-[-0.025em]">This pickup slip points to nowhere.</h1>
      <p className="mt-4 text-lg text-[color:var(--text-muted)]">The page you&apos;re looking for doesn&apos;t exist or has been moved.</p>
      <div className="mt-8 flex justify-center gap-4">
        <Link to="/" className="secondary-button">
          Home
        </Link>
        <Link to="/browse" className="primary-button">
          <ArrowRight size={15} />
          Browse Listings
        </Link>
      </div>
    </section>
  );
}
