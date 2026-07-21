import {
  AlertCircle,
  BarChart2,
  Building2,
  MapPin,
  RefreshCw,
  TrendingUp,
  Users,
} from "lucide-react";
import React, { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import PageSection from "../components/PageSection";
import SkeletonLoader from "../components/SkeletonLoader";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { cn } from "../lib/cn";
import { formatCurrencyValue, formatLabel } from "../lib/formatters";

const CATEGORY_COLORS = ["#8a1d45", "#1e304a", "#d9a441", "#c96444", "#b57ca5", "#5d7898", "#7a9e5b"];

function StatCard({ icon: Icon, label, value, sublabel, accent }) {
  return (
    <div className="bg-[color:var(--color-surface)] rounded-[20px] p-6 border border-black/10 dark:border-white/10 shadow-sm flex items-start gap-4">
      <div className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-full", accent ?? "bg-[color:var(--color-surface-2)]")}>
        <Icon size={20} className="text-[color:var(--color-tag)]" />
      </div>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">{label}</p>
        <p className="mt-1 text-[26px] font-black leading-none text-[color:var(--color-ink)] dark:text-white">{value}</p>
        {sublabel && <p className="mt-1 text-[12px] text-[color:var(--text-muted)]">{sublabel}</p>}
      </div>
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-[color:var(--color-surface)] rounded-2xl p-3 shadow-xl border border-black/8 dark:border-white/8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">{label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="mt-1 text-[14px] font-bold" style={{ color: p.color }}>
          {formatLabel(p.dataKey)}: {p.value}
        </p>
      ))}
    </div>
  );
};

export default function CampusAnalyticsPage() {
  usePageTitle("Campus Analytics");
  const { user } = useAuth();
  const [weeks, setWeeks] = useState(12);
  const { data, loading, error, refetch } = useApi(`/campus-analytics?weeks=${weeks}`);

  const canAccess = user?.is_staff || user?.is_campus_manager;

  if (!canAccess) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4 text-center px-6">
        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-red-100 dark:bg-red-900/20">
          <AlertCircle size={36} className="text-red-500" />
        </div>
        <h2 className="text-[28px] font-bold tracking-tight text-[color:var(--color-ink)] dark:text-white">
          Access restricted
        </h2>
        <p className="max-w-xs text-[color:var(--text-muted)]">
          This dashboard is only available to campus partners and staff.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-[1200px] px-4 md:px-8 py-8 space-y-6">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => <SkeletonLoader key={i} className="h-28 !rounded-[20px]" />)}
        </div>
        <SkeletonLoader className="h-72 !rounded-[20px]" />
        <div className="grid md:grid-cols-2 gap-6">
          <SkeletonLoader className="h-64 !rounded-[20px]" />
          <SkeletonLoader className="h-64 !rounded-[20px]" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4">
        <AlertCircle size={40} className="text-red-500" />
        <p className="text-[color:var(--text-muted)]">{error}</p>
        <button onClick={refetch} className="primary-button">
          <RefreshCw size={15} /> Retry
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { summary, weekly_chart, category_breakdown, top_pickup_zones, hub_utilization } = data;

  // Format weekly chart data: shorten week label to "Mon DD"
  const chartData = weekly_chart.map((row) => ({
    ...row,
    label: new Date(row.week).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  }));

  return (
    <div className="mx-auto max-w-[1200px] px-4 md:px-8 pb-20">
      {/* Header */}
      <PageSection className="pt-8 pb-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="sticker bg-[color:var(--color-tag)] text-white mb-3">
              <BarChart2 size={14} />
              Campus Analytics
            </div>
            <h1 className="text-[34px] font-black tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white leading-tight">
              {data.campus === "all" ? "All Campuses" : data.campus}
            </h1>
            <p className="mt-2 text-[color:var(--text-muted)]">
              Last {data.period_weeks} weeks · partner sustainability dashboard
            </p>
          </div>
          <div className="flex items-center gap-3">
            <label className="text-[13px] text-[color:var(--text-muted)] font-medium">Period</label>
            <select
              value={weeks}
              onChange={(e) => setWeeks(Number(e.target.value))}
              className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-surface)] px-3 py-2 text-[13px] text-[color:var(--color-ink)] dark:text-white outline-none"
            >
              {[4, 8, 12, 26, 52].map((w) => (
                <option key={w} value={w}>{w} weeks</option>
              ))}
            </select>
          </div>
        </div>
      </PageSection>

      {/* Summary stats */}
      <PageSection className="grid grid-cols-2 lg:grid-cols-4 gap-4" delay={0.04}>
        <StatCard
          icon={TrendingUp}
          label="Items posted"
          value={summary.total_posted.toLocaleString()}
          sublabel={`${summary.rescue_rate_pct}% rescue rate`}
        />
        <StatCard
          icon={BarChart2}
          label="Items rescued"
          value={summary.total_rescued.toLocaleString()}
          sublabel={`$${formatCurrencyValue(summary.total_retail_rescued)} retail value`}
        />
        <StatCard
          icon={Users}
          label="Active donors"
          value={summary.total_posters.toLocaleString()}
          sublabel={`${summary.retention_pct}% post more than once`}
        />
        <StatCard
          icon={Users}
          label="Returning donors"
          value={summary.repeat_posters.toLocaleString()}
          sublabel={`Cohort retention`}
        />
      </PageSection>

      {/* Weekly volume chart */}
      <PageSection className="bg-[color:var(--color-surface)] rounded-[20px] p-6 border border-black/10 dark:border-white/10 mt-6" delay={0.06}>
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-4">
          Weekly listing volume
        </p>
        {chartData.length === 0 ? (
          <p className="text-sm text-[color:var(--text-muted)] py-8 text-center">No data for this period.</p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData} barGap={4}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={32} />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: 12, paddingTop: 12 }} formatter={formatLabel} />
              <Bar dataKey="posted" fill="#1e304a" radius={[6, 6, 0, 0]} />
              <Bar dataKey="rescued" fill="#8a1d45" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </PageSection>

      <div className="mt-6 grid md:grid-cols-2 gap-6">
        {/* Category breakdown */}
        <PageSection className="bg-[color:var(--color-surface)] rounded-[20px] p-6 border border-black/10 dark:border-white/10" delay={0.08}>
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-4">
            Category breakdown
          </p>
          {category_breakdown.length === 0 ? (
            <p className="text-sm text-[color:var(--text-muted)] py-8 text-center">No data.</p>
          ) : (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width={160} height={160}>
                <PieChart>
                  <Pie
                    data={category_breakdown}
                    dataKey="count"
                    nameKey="category"
                    cx="50%"
                    cy="50%"
                    innerRadius={44}
                    outerRadius={72}
                  >
                    {category_breakdown.map((_, i) => (
                      <Cell key={i} fill={CATEGORY_COLORS[i % CATEGORY_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(v, name) => [v, formatLabel(name)]} />
                </PieChart>
              </ResponsiveContainer>
              <ul className="flex-1 space-y-1.5 text-[13px]">
                {category_breakdown.map((row, i) => (
                  <li key={row.category} className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
                        style={{ background: CATEGORY_COLORS[i % CATEGORY_COLORS.length] }}
                      />
                      <span className="text-[color:var(--color-ink)] dark:text-white">{formatLabel(row.category)}</span>
                    </span>
                    <span className="font-semibold text-[color:var(--text-muted)]">{row.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </PageSection>

        {/* Top pickup zones */}
        <PageSection className="bg-[color:var(--color-surface)] rounded-[20px] p-6 border border-black/10 dark:border-white/10" delay={0.1}>
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-4">
            Top pickup zones
          </p>
          {top_pickup_zones.length === 0 ? (
            <p className="text-sm text-[color:var(--text-muted)] py-8 text-center">No data.</p>
          ) : (
            <ul className="space-y-2">
              {top_pickup_zones.map((zone, i) => {
                const max = top_pickup_zones[0].count;
                const pct = Math.round((zone.count / max) * 100);
                return (
                  <li key={i} className="flex items-center gap-3">
                    <MapPin size={13} className="shrink-0 text-[color:var(--color-tag)]" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="truncate text-[12px] font-medium text-[color:var(--color-ink)] dark:text-white">
                          {zone.pickup_zone}
                        </span>
                        <span className="ml-2 text-[11px] font-semibold text-[color:var(--text-muted)]">{zone.count}</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-[color:var(--color-surface-2)] overflow-hidden">
                        <div
                          className="h-full rounded-full bg-[color:var(--color-tag)]"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </PageSection>
      </div>

      {/* Hub utilization */}
      {hub_utilization.length > 0 && (
        <PageSection className="bg-[color:var(--color-surface)] rounded-[20px] p-6 border border-black/10 dark:border-white/10 mt-6" delay={0.12}>
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-4">
            Hub utilization
          </p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {hub_utilization.map((hub) => (
              <div
                key={hub.hub_id}
                className="rounded-2xl border border-[color:var(--color-line)] p-4 space-y-2"
              >
                <div className="flex items-start gap-2">
                  <Building2 size={15} className="mt-0.5 shrink-0 text-[color:var(--color-tag)]" />
                  <div className="min-w-0">
                    <p className="font-semibold text-[13px] text-[color:var(--color-ink)] dark:text-white leading-snug">
                      {hub.name}
                    </p>
                    <p className="text-[11px] text-[color:var(--text-muted)]">{hub.zone}</p>
                  </div>
                </div>
                <div className="flex items-center justify-between text-[12px]">
                  <span className="text-[color:var(--text-muted)]">
                    {hub.donated_items} items donated
                    {hub.capacity ? ` / ${hub.capacity} capacity` : ""}
                  </span>
                  {hub.utilization_pct != null && (
                    <span className={cn(
                      "font-semibold",
                      hub.utilization_pct >= 80 ? "text-red-500" :
                      hub.utilization_pct >= 50 ? "text-amber-500" : "text-emerald-600"
                    )}>
                      {hub.utilization_pct}%
                    </span>
                  )}
                </div>
                {hub.utilization_pct != null && (
                  <div className="h-1.5 rounded-full bg-[color:var(--color-surface-2)] overflow-hidden">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        hub.utilization_pct >= 80 ? "bg-red-500" :
                        hub.utilization_pct >= 50 ? "bg-amber-400" : "bg-emerald-500"
                      )}
                      style={{ width: `${Math.min(hub.utilization_pct, 100)}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        </PageSection>
      )}
    </div>
  );
}
