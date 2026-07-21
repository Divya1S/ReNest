import { motion } from "framer-motion";
import { ArrowRight, Building2, Leaf, PackageCheck, Users } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

const CATEGORY_LABELS = {
  storage: "Storage",
  lighting: "Lighting",
  supplies: "School Supplies",
  comfort: "Comfort",
  toiletries: "Toiletries",
  decor: "Decor",
  other: "Other",
};

function StatCard({ icon: Icon, value, label, color = "text-[color:var(--color-teal)]" }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex flex-col gap-2 rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-6"
    >
      <Icon className={`h-6 w-6 ${color}`} />
      <p className={`text-3xl font-bold tracking-tight ${color}`}>{value}</p>
      <p className="text-sm text-[color:var(--color-muted)]">{label}</p>
    </motion.div>
  );
}

export default function CampusLandingPage() {
  const { slug } = useParams();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  usePageTitle(stats ? `${stats.name} — ReNest` : "Campus — ReNest");

  useEffect(() => {
    apiFetch(`/auth/campus/${slug}/stats`)
      .then((data) => setStats(data))
      .catch((err) => {
        if (err?.status === 404) setNotFound(true);
      })
      .finally(() => setLoading(false));
    // Phase 27 — inject white-label theme CSS variables
    apiFetch(`/campus/${slug}/theme`).then(({ theme }) => {
      if (!theme) return;
      const root = document.documentElement;
      if (theme.primary_color) root.style.setProperty("--color-primary", theme.primary_color);
      if (theme.font_family) root.style.setProperty("--font-family", theme.font_family);
    }).catch(() => {});
  }, [slug]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-[color:var(--color-muted)]">Loading…</p>
      </div>
    );
  }

  if (notFound || !stats) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
        <Building2 className="h-12 w-12 text-[color:var(--color-muted)]" />
        <h1 className="text-2xl font-bold">Campus not found</h1>
        <p className="max-w-sm text-[color:var(--color-muted)]">
          This campus isn&apos;t on ReNest yet.{" "}
          <Link to="/campus/onboard" className="text-[color:var(--color-teal)] underline">
            Request yours →
          </Link>
        </p>
      </div>
    );
  }

  const moveOutWindow =
    stats.move_out_start && stats.move_out_end
      ? `${new Date(stats.move_out_start).toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${new Date(stats.move_out_end).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`
      : null;

  return (
    <div className="mx-auto max-w-3xl px-6 py-16 space-y-12">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[color:var(--color-teal)]/10 px-3 py-1 text-xs font-medium text-[color:var(--color-teal)]">
            <Leaf className="h-3.5 w-3.5" />
            ReNest Campus
          </span>
          {stats.subscription_tier !== "free" && (
            <span className="inline-flex items-center rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800 dark:bg-amber-900/30 dark:text-amber-400">
              Partner
            </span>
          )}
        </div>
        <h1 className="text-4xl font-bold tracking-tight">{stats.name}</h1>
        {moveOutWindow && (
          <p className="text-[color:var(--color-muted)]">
            Move-out window: <span className="font-medium text-[color:var(--color-fg)]">{moveOutWindow}</span>
          </p>
        )}
      </motion.div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard icon={PackageCheck} value={stats.listing_count.toLocaleString()} label="listings posted" />
        <StatCard icon={PackageCheck} value={stats.claimed_count.toLocaleString()} label="items claimed" color="text-emerald-600 dark:text-emerald-400" />
        <StatCard icon={Users} value={stats.member_count.toLocaleString()} label="students active" color="text-violet-600 dark:text-violet-400" />
        <StatCard
          icon={Leaf}
          value={`$${Number(stats.total_value_rescued).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          label="value rescued"
          color="text-amber-600 dark:text-amber-400"
        />
      </div>

      {/* Top categories */}
      {stats.top_categories?.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[color:var(--color-muted)]">
            Top categories
          </h2>
          <div className="flex flex-wrap gap-2">
            {stats.top_categories.map((c) => (
              <span
                key={c.category}
                className="rounded-full border border-[color:var(--color-border)] px-4 py-1.5 text-sm font-medium"
              >
                {CATEGORY_LABELS[c.category] ?? c.category}{" "}
                <span className="text-[color:var(--color-muted)]">· {c.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* CTA */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-8 text-center space-y-4"
      >
        <h2 className="text-xl font-bold">Join your campus on ReNest</h2>
        <p className="text-[color:var(--color-muted)] max-w-md mx-auto">
          Create an account with your {stats.name} email to start rescuing and donating items during move-out.
        </p>
        <Link
          to="/register"
          className="inline-flex items-center gap-2 rounded-xl bg-[color:var(--color-teal)] px-6 py-3 font-semibold text-white hover:opacity-90 transition-opacity"
        >
          Get started <ArrowRight className="h-4 w-4" />
        </Link>
      </motion.div>

      <p className="text-center text-xs text-[color:var(--color-muted)]">
        Not your campus?{" "}
        <Link to="/campus/onboard" className="underline hover:text-[color:var(--color-teal)]">
          Request yours
        </Link>
      </p>
    </div>
  );
}
