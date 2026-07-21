import { AlertTriangle, CheckCircle2, Flag, Shield, Sparkles, Star } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";

import ListingCard from "../components/ListingCard";
import PageSection from "../components/PageSection";
import RatingStars from "../components/RatingStars";
import SkeletonLoader from "../components/SkeletonLoader";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { formatDateTime, formatLabel } from "../lib/formatters";

function MetricCard({ label, value, tone = "text-[color:var(--color-tag)]" }) {
  return (
    <div className="paper-panel p-5">
      <p className="label-title">{label}</p>
      <p className={`mt-3 text-4xl font-bold ${tone}`}>{value}</p>
    </div>
  );
}

function formatMemberSince(value) {
  if (!value) return "Unknown";
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    year: "numeric",
  });
}

export default function TrustPage() {
  usePageTitle("Reputation");
  const { data, loading, error, refetch } = useApi("/trust/me");

  if (loading) {
    return (
      <div className="space-y-8">
        <SkeletonLoader className="h-56 !rounded-[2.5rem]" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          {[1, 2, 3, 4, 5].map((item) => (
            <SkeletonLoader key={item} className="h-32 !rounded-[1.8rem]" />
          ))}
        </div>
        <SkeletonLoader className="h-96 !rounded-[2rem]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="paper-panel p-10 text-center">
        <p className="label-title">Trust Center</p>
        <h2 className="mt-3 text-[24px] font-bold tracking-[-0.02em]">Trust data failed to load</h2>
        <p className="mt-4 text-[color:var(--text-muted)]">{error}</p>
        <button type="button" onClick={refetch} className="primary-button mt-6">
          Try Again
        </button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-8 pb-16">
      <PageSection className="paper-panel p-8 sm:p-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="sticker bg-[color:var(--color-tag)] text-white">
              <Shield size={14} />
              Reputation
            </div>
            <h1 className="mt-5 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
              Build confidence into every rescue and handoff.
            </h1>
            <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
              This is your safety and reliability snapshot: completed rescues, handoff history,
              active flags, and the issues you have already reported.
            </p>
          </div>
          <div className="rounded-[2rem] border border-[color:var(--color-line)] bg-white/80 px-6 py-5 shadow-sm dark:bg-slate-900/40">
            <p className="label-title">Member since</p>
            <p className="mt-2 text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">
              {formatMemberSince(data.summary.member_since)}
            </p>
            <p className="mt-1 text-sm text-[color:var(--text-muted)]">
              {data.summary.campus_name || "Campus not set"}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <p className="inline-flex rounded-full bg-[color:var(--color-tag-soft)] px-3 py-1 text-[0.65rem] font-black uppercase tracking-[0.14em] text-[color:var(--color-tag)]">
                {data.summary.trust_badge}
              </p>
              {data.summary.milestone && (
                <p className="inline-flex rounded-full bg-amber-100 dark:bg-amber-900/30 px-3 py-1 text-[0.65rem] font-black uppercase tracking-[0.14em] text-amber-700 dark:text-amber-400">
                  ★ {data.summary.milestone.replace(/_/g, " ")}
                </p>
              )}
            </div>
            {data.summary.referral_count > 0 && (
              <p className="mt-2 text-[11px] text-[color:var(--text-muted)]">
                Invited {data.summary.referral_count} student{data.summary.referral_count !== 1 ? "s" : ""}
              </p>
            )}
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-2 xl:grid-cols-6" delay={0.04}>
        <MetricCard label="Completed rescues" value={data.summary.completed_rescues} />
        <MetricCard label="Successful handoffs" value={data.summary.successful_handoffs} />
        <MetricCard label="Claims completed" value={data.summary.claims_completed} />
        <MetricCard label="Active listings" value={data.summary.active_listings} />
        <MetricCard
          label="Average rating"
          value={data.summary.average_rating ? `${data.summary.average_rating}/5` : "New"}
          tone="text-amber-500"
        />
        <MetricCard
          label="Open reports"
          value={data.summary.open_reports}
          tone={data.summary.open_reports ? "text-red-600" : "text-emerald-600"}
        />
      </PageSection>

      <PageSection className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]" delay={0.08}>
        <section className="paper-panel p-8">
          <div className="mb-8 flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]">
              <Sparkles size={24} />
            </div>
            <div>
              <p className="label-title">Active rescue profile</p>
              <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">Listings people see right now</h2>
            </div>
          </div>

          {data.pending_feedback?.length ? (
            <div className="mb-8 rounded-[2rem] border border-amber-200 bg-amber-50/70 p-6 dark:border-amber-900/30 dark:bg-amber-900/15">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="label-title text-amber-700">Feedback due</p>
                  <h3 className="text-[18px] font-semibold text-amber-900 dark:text-amber-100">
                    {data.summary.pending_feedback} completed handoff{data.summary.pending_feedback === 1 ? "" : "s"} still waiting on your review.
                  </h3>
                </div>
              </div>
              <div className="mt-4 space-y-3">
                {data.pending_feedback.map((entry) => (
                  <div key={entry.reservation_id} className="paper-panel p-4 !rounded-2xl border-amber-200/80 bg-white/80 dark:border-amber-900/30 dark:bg-[color:var(--color-surface-2)]">
                    <p className="text-base font-black text-[color:var(--color-ink)] dark:text-white">{entry.listing_title}</p>
                    <p className="mt-1 text-sm text-[color:var(--text-muted)]">Handoff with {entry.counterparty_name}</p>
                    <Link to={`/handoffs/${entry.reservation_id}`} className="ghost-button mt-4 w-full">
                      Leave feedback
                    </Link>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {data.active_rescues.length ? (
            <div className="grid gap-6 xl:grid-cols-2">
              {data.active_rescues.map((listing) => (
                <ListingCard key={listing.id} listing={listing} />
              ))}
            </div>
          ) : (
            <div className="rounded-[2rem] border-2 border-dashed border-slate-200 bg-slate-50/70 p-10 text-center dark:border-slate-800 dark:bg-slate-900/30">
              <p className="text-[16px] font-semibold text-[color:var(--color-ink)] dark:text-white">No active listings yet.</p>
              <p className="mt-2 text-sm text-[color:var(--text-muted)]">
                When you post items, they will appear here with the trust signals buyers see.
              </p>
              <Link to="/listings/new" className="primary-button mt-6">
                Create listing
              </Link>
            </div>
          )}
        </section>

        <div className="space-y-6">
          <section className="paper-panel p-8">
            <div className="mb-6 flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-500 dark:bg-amber-900/20 dark:text-amber-300">
                <Star size={24} />
              </div>
              <div>
                <p className="label-title">Feedback received</p>
                <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">What people say after the pickup</h2>
              </div>
            </div>
            {data.received_feedback?.length ? (
              <div className="space-y-4">
                {data.received_feedback.map((entry) => (
                  <div key={entry.id} className="paper-panel p-4 !rounded-2xl dark:border-white/10">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-base font-black text-[color:var(--color-ink)] dark:text-white">{entry.reviewer.display_name}</p>
                        <p className="mt-1 text-sm text-[color:var(--text-muted)]">{entry.listing_title}</p>
                      </div>
                      <div className="text-right">
                        <RatingStars rating={entry.rating} />
                        <p className="mt-2 text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
                          {formatDateTime(entry.created_at)}
                        </p>
                      </div>
                    </div>
                    {entry.tags?.length ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {entry.tags.map((tag) => (
                          <span key={tag} className="scan-chip">
                            {formatLabel(tag)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {entry.note ? (
                      <p className="mt-3 text-sm leading-6 text-[color:var(--text-muted)]">{entry.note}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-[color:var(--text-muted)]">No pickup feedback yet. Complete a handoff to start building your reputation.</p>
            )}
          </section>

          <section className="paper-panel p-8">
            <div className="mb-6 flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-400">
                <Flag size={24} />
              </div>
              <div>
                <p className="label-title">Reports you filed</p>
                <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">Community issues you flagged</h2>
              </div>
            </div>
            <div className="space-y-4">
              {data.reports_filed.length ? data.reports_filed.map((report) => (
                <div key={report.id} className="paper-panel p-4 !rounded-2xl dark:border-white/10">
                  <div className="flex items-center justify-between gap-3">
                    <span className="rounded-full bg-amber-100 px-3 py-1 text-[0.65rem] font-bold uppercase tracking-[0.14em] text-amber-700 dark:bg-amber-900/20 dark:text-amber-300">
                      {formatLabel(report.reason)}
                    </span>
                    <span className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
                      {formatLabel(report.status)}
                    </span>
                  </div>
                  <p className="mt-3 text-base font-black text-[color:var(--color-ink)] dark:text-white">{report.listing_title}</p>
                  {report.details ? (
                    <p className="mt-2 text-sm leading-6 text-[color:var(--text-muted)]">{report.details}</p>
                  ) : null}
                  <p className="mt-3 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
                    {formatDateTime(report.created_at)}
                  </p>
                </div>
              )) : (
                <p className="text-sm text-[color:var(--text-muted)]">You have not reported any listings.</p>
              )}
            </div>
          </section>

          <section className="paper-panel p-8">
            <div className="mb-6 flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400">
                <AlertTriangle size={24} />
              </div>
              <div>
                <p className="label-title">Reports on your listings</p>
                <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">Anything reviewers may need</h2>
              </div>
            </div>
            <div className="space-y-4">
              {data.reports_on_my_listings.length ? data.reports_on_my_listings.map((report) => (
                <div key={report.id} className="paper-panel p-4 !rounded-2xl dark:border-white/10">
                  <div className="flex items-center justify-between gap-3">
                    <span className="rounded-full bg-red-100 px-3 py-1 text-[0.65rem] font-bold uppercase tracking-[0.14em] text-red-700 dark:bg-red-900/20 dark:text-red-300">
                      {formatLabel(report.reason)}
                    </span>
                    <span className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
                      {formatLabel(report.status)}
                    </span>
                  </div>
                  <p className="mt-3 text-base font-black text-[color:var(--color-ink)] dark:text-white">{report.listing_title}</p>
                  <p className="mt-2 text-sm text-[color:var(--text-muted)]">Reported by {report.reporter.display_name}</p>
                </div>
              )) : (
                <div className="rounded-[2rem] border border-emerald-200 bg-emerald-50/70 p-6 text-sm text-emerald-800 dark:border-emerald-900/30 dark:bg-emerald-900/20 dark:text-emerald-200">
                  <div className="inline-flex items-center gap-2 font-black">
                    <CheckCircle2 size={16} />
                    No open trust issues on your listings right now.
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>
      </PageSection>
    </div>
  );
}
