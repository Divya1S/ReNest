import { motion } from "framer-motion";
import {
  ArrowRight,
  Bookmark,
  Bell,
  Camera,
  ChartBar,
  CheckCheck,
  ClipboardCheck,
  LifeBuoy,
  PackagePlus,
  Star,
  Zap,
  LayoutDashboard,
  AlertCircle,
  MessageSquare,
  Download,
  X,
} from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";

import Avatar from "../components/Avatar";
import ListingPlaceholder from "../components/ListingPlaceholder";
import MoveOutTaskCard from "../components/MoveOutTaskCard";
import ProgressRing from "../components/ProgressRing";
import SkeletonLoader from "../components/SkeletonLoader";
import { useAuth } from "../context/AuthContext";
import { useApi } from "../hooks/useApi";
import { useInstallPrompt } from "../hooks/useInstallPrompt";
import { usePageTitle } from "../hooks/usePageTitle";
import { usePushNotifications } from "../hooks/usePushNotifications";
import { cn } from "../lib/cn";
import { formatCurrencyValue, formatDateTime, formatLabel } from "../lib/formatters";
import type { DashboardData } from "../types";

type LucideIcon = React.ComponentType<{ size?: number; className?: string }>;

const barColors = ["#8a1d45", "#1e304a", "#d9a441", "#c96444", "#b57ca5", "#5d7898"];

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; name?: string }>; label?: string }) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[color:var(--color-surface)] rounded-[16px] p-4 shadow-xl border border-black/8 dark:border-white/8">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">{label ?? payload[0].name}</p>
        <p className="mt-1 text-[17px] font-bold text-[color:var(--color-tag)]">
          {payload[0].value} {payload[0].value === 1 ? "Item" : "Items"}
        </p>
      </div>
    );
  }
  return null;
};

function StatCard({ icon: Icon, label, value, sublabel, iconBg, iconColor }: { icon: LucideIcon; label: string; value: React.ReactNode; sublabel?: string; iconBg: string; iconColor: string }) {
  return (
    <div className="bg-[color:var(--color-surface)] rounded-[20px] p-6 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10">
      <div className="flex justify-between items-start mb-3">
        <div className={cn("p-2 rounded-[10px]", iconBg)}>
          <Icon size={20} className={iconColor} />
        </div>
        {sublabel && (
          <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">{sublabel}</span>
        )}
      </div>
      <div className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em] leading-none mb-1">{value}</div>
      <div className="text-[14px] text-[color:var(--text-muted)]">{label}</div>
    </div>
  );
}

function SectionCard({ title, action, children, className }: { title?: string; action?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("bg-[color:var(--color-surface)] rounded-[20px] shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10 overflow-hidden", className)}>
      {(title || action) && (
        <div className="flex items-center justify-between px-6 py-5 border-b border-black/5 dark:border-white/5">
          {title && <h3 className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white tracking-[-0.01em]">{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </div>
  );
}

function PickupStatusPill({ status }: { status: string }) {
  const isConfirmed = status === "confirmed" || status === "reserved";
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[12px] font-medium",
      isConfirmed
        ? "bg-[color:var(--color-free-soft)] text-[color:var(--color-free)]"
        : "bg-[color:var(--color-box-soft)] text-[color:var(--color-box)]",
    )}>
      <span className={cn("w-1.5 h-1.5 rounded-full", isConfirmed ? "bg-[color:var(--color-free)]" : "bg-[color:var(--color-box)]")} />
      {isConfirmed ? "Confirmed" : formatLabel(status)}
    </span>
  );
}

function EmptyState({ icon: Icon, title, description, action, imageSrc }: { icon: LucideIcon; title: string; description: string; action?: React.ReactNode; imageSrc?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center px-6">
      {imageSrc ? (
        <img
          src={imageSrc}
          alt=""
          aria-hidden="true"
          loading="lazy"
          className="mb-4 h-28 w-28 object-contain dark:rounded-3xl dark:bg-white/90 dark:p-3"
        />
      ) : (
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-[color:var(--color-surface-2)]">
        <Icon size={28} className="text-[color:var(--text-muted)] opacity-60" />
      </div>
      )}
      <h3 className="text-[16px] font-semibold text-[color:var(--color-ink)] dark:text-white">{title}</h3>
      <p className="mt-1.5 max-w-xs text-[13px] leading-relaxed text-[color:var(--text-muted)]">{description}</p>
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

const ONBOARDING_STEPS = [
  { label: "Complete your profile", description: "Add your display name and campus", path: "/settings" },
  { label: "Post your first listing", description: "Turn a dorm item into a rescue", path: "/listings/new" },
  { label: "Make your first claim", description: "Reserve something another student is giving away", path: "/browse" },
];

function OnboardingChecklist({ step }: { step: number }) {
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-6 rounded-[18px] border border-[color:var(--color-line)] bg-[color:var(--color-surface)] p-5 shadow-sm"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ClipboardCheck size={18} className="text-[color:var(--color-tag)]" />
          <span className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">Getting started</span>
          <span className="ml-1 rounded-full bg-[color:var(--color-tag)] px-2 py-0.5 text-[11px] font-semibold text-white">
            {step}/3
          </span>
        </div>
        <button
          onClick={() => setCollapsed(true)}
          className="rounded-full p-1 hover:bg-[color:var(--color-surface-2)] transition-colors"
          aria-label="Dismiss checklist"
        >
          <X size={14} />
        </button>
      </div>
      <div className="space-y-2">
        {ONBOARDING_STEPS.map((s, i) => {
          const done = i < step;
          const rowContent = (
            <>
              <div className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 ${
                done ? "border-[color:var(--color-tag)] bg-[color:var(--color-tag)]" : "border-[color:var(--color-line)]"
              }`}>
                {done && <CheckCheck size={11} className="text-white" />}
              </div>
              <div>
                <p className={`text-[13px] font-semibold ${done ? "line-through text-[color:var(--text-muted)]" : "text-[color:var(--color-ink)] dark:text-white"}`}>
                  {s.label}
                </p>
                {!done && <p className="text-[11px] text-[color:var(--text-muted)]">{s.description}</p>}
              </div>
            </>
          );
          return done ? (
            <div key={i} className="flex items-center gap-3 rounded-[12px] px-3 py-2.5 opacity-50">
              {rowContent}
            </div>
          ) : (
            <Link
              key={i}
              to={s.path}
              className="flex items-center gap-3 rounded-[12px] px-3 py-2.5 transition-colors hover:bg-[color:var(--color-surface-2)]"
            >
              {rowContent}
            </Link>
          );
        })}
      </div>
    </motion.div>
  );
}

export default function DashboardPage() {
  usePageTitle("Dashboard");
  const { user } = useAuth();
  const { data, loading, error, refetch } = useApi<DashboardData>("/dashboard");

  const firstName = user?.display_name?.split(" ")[0] || "there";
  const { canInstall, install, dismiss } = useInstallPrompt();
  const {
    available: pushAvailable,
    permission,
    subscribed,
    loading: pushLoading,
    subscribe: subscribePush,
  } = usePushNotifications(!!user);

  if (loading) {
    return (
      <div className="mx-auto max-w-[1200px] px-4 md:px-8 py-8 space-y-8">
        <div className="h-20 rounded-[20px] overflow-hidden">
          <SkeletonLoader className="h-full w-full" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[1, 2, 3].map((i) => <SkeletonLoader key={i} className="h-32 !rounded-[20px]" />)}
        </div>
        <SkeletonLoader className="h-72 !rounded-[20px]" />
        <SkeletonLoader className="h-64 !rounded-[20px]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-red-100 text-red-500 dark:bg-red-900/20 dark:text-red-400">
          <AlertCircle size={40} />
        </div>
        <h2 className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em]">Dashboard failed to load</h2>
        <p className="mt-3 text-[color:var(--text-muted)]">{error}</p>
        <button
          onClick={() => refetch()}
          className="mt-8 px-6 py-3 bg-[color:var(--color-tag)] text-white rounded-full text-[14px] font-semibold hover:opacity-90 transition-opacity"
        >
          Try Again
        </button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="pb-20">
      <div className="mx-auto max-w-[1200px] px-4 md:px-8">

        {/* PWA install prompt */}
        {canInstall && (
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            className="mt-6 flex items-center gap-4 rounded-[18px] bg-[color:var(--color-tag)] px-5 py-4 text-white shadow-lg"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white/20">
              <Download size={18} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[14px] font-semibold leading-snug">Add ReNest to your home screen</p>
              <p className="text-[12px] text-white/75 mt-0.5">Works offline · faster than the browser</p>
            </div>
            <button
              onClick={install}
              className="shrink-0 rounded-full bg-white px-4 py-1.5 text-[13px] font-semibold text-[color:var(--color-tag)] hover:bg-white/90 transition-colors"
            >
              Install
            </button>
            <button
              onClick={dismiss}
              aria-label="Dismiss install prompt"
              className="shrink-0 rounded-full p-1.5 hover:bg-white/15 transition-colors"
            >
              <X size={16} />
            </button>
          </motion.div>
        )}

        {/* Onboarding checklist — shown until step 3 (complete) */}
        {user && (user.onboarding_step ?? 0) < 3 && (
          <OnboardingChecklist step={user.onboarding_step ?? 0} />
        )}

        {/* Web Push opt-in: only when the browser supports it AND the server
            has VAPID keys, otherwise the button can never succeed. */}
        {pushAvailable && permission === "default" && !subscribed && !canInstall && (
          <motion.div
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-4 flex items-center gap-4 rounded-[18px] bg-[color:var(--color-surface)] border border-[color:var(--color-line)] px-5 py-4 shadow-sm"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[color:var(--color-surface-2)]">
              <Bell size={18} className="text-[color:var(--color-tag)]" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white leading-snug">
                Get push notifications
              </p>
              <p className="text-[12px] text-[color:var(--text-muted)] mt-0.5">
                Know instantly when a reservation is confirmed or pickup is due
              </p>
            </div>
            <button
              onClick={async () => {
                const result = await subscribePush();
                if (result.ok) {
                  toast.success("Push notifications enabled.");
                } else if (result.reason === "denied") {
                  toast.error("Notifications are blocked. Enable them in your browser settings.");
                } else {
                  toast.error(result.message || "Could not enable push notifications.");
                }
              }}
              disabled={pushLoading}
              className="shrink-0 rounded-full bg-[color:var(--color-tag)] px-4 py-1.5 text-[13px] font-semibold text-white hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {pushLoading ? "…" : "Enable"}
            </button>
          </motion.div>
        )}

        {/* Welcome header */}
        <section className="pt-8 pb-8">
          <h1 className="text-[34px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em] leading-snug">
            Hi, {firstName}!
          </h1>
          <p className="mt-1 text-[18px] text-[color:var(--text-muted)]">
            {data.stats.incoming_pickup_actions > 0
              ? `You have ${data.stats.incoming_pickup_actions} pending pickup${data.stats.incoming_pickup_actions !== 1 ? "s" : ""} right now.`
              : data.stats.tasks_due_today > 0
              ? `You have ${data.stats.tasks_due_today} task${data.stats.tasks_due_today !== 1 ? "s" : ""} due today.`
              : "Welcome back to your move-out dashboard."}
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <Link
              to="/scan"
              className="flex items-center gap-1.5 px-5 py-2.5 bg-[color:var(--color-tag)] text-white rounded-full text-[13px] font-semibold hover:opacity-90 transition-opacity"
            >
              <Camera size={15} />
              Start Room Scan
            </Link>
            <Link
              to="/listings/new"
              className="flex items-center gap-1.5 px-5 py-2.5 border border-black/10 dark:border-white/10 rounded-full text-[13px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface)] transition-colors"
            >
              <PackagePlus size={15} />
              Post Item
            </Link>
          </div>
        </section>

        {/* Stats grid */}
        <section className="pb-10">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <StatCard
              icon={CheckCheck}
              label="Ready to Publish"
              value={data.stats.ready_to_publish_now}
              sublabel="DRAFTS"
              iconBg="bg-[color:var(--color-tag-soft)]"
              iconColor="text-[color:var(--color-tag)]"
            />
            <StatCard
              icon={Zap}
              label="Pending Pickups"
              value={data.stats.incoming_pickup_actions}
              sublabel="ACTIVE"
              iconBg="bg-[color:var(--color-teal-soft)]"
              iconColor="text-[color:var(--color-teal)]"
            />
            <StatCard
              icon={Bookmark}
              label="Savings Unlocked"
              value={formatCurrencyValue(data.stats.scan_savings_total)}
              sublabel="TOTAL"
              iconBg="bg-[color:var(--color-box-soft)]"
              iconColor="text-[color:var(--color-box)]"
            />
          </div>
        </section>

        {/* Ready to publish — horizontal scroll */}
        <section className="pb-10">
          <div className="flex justify-between items-center mb-5">
            <h2 className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em]">Ready to publish</h2>
            {data.action_queues.ready_to_publish_now.length > 0 && (
              <Link
                to="/scan"
                className="flex items-center gap-1 text-[color:var(--color-tag)] font-semibold text-[13px] hover:opacity-70 transition-opacity"
              >
                View Drafts <ArrowRight size={14} />
              </Link>
            )}
          </div>
          {data.action_queues.ready_to_publish_now.length > 0 ? (
            <div className="flex gap-5 overflow-x-auto pb-2" style={{ scrollbarWidth: "none" }}>
              {data.action_queues.ready_to_publish_now.map((item) => (
                <div
                  key={item.id}
                  className="min-w-[280px] md:min-w-[320px] flex-shrink-0 bg-[color:var(--color-surface)] rounded-[20px] shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10 overflow-hidden"
                >
                  <div className="h-40 bg-[color:var(--color-surface-2)] relative">
                    {item.image_url ? (
                      <img src={item.image_url} alt={item.title} loading="lazy" decoding="async" className="h-full w-full object-cover" />
                    ) : (
                      <ListingPlaceholder category={item.category} glyphSize={30} draft />
                    )}
                    <div className="absolute top-3 right-3 px-2.5 py-1 bg-white/90 dark:bg-black/70 backdrop-blur rounded-full text-[12px] font-medium text-[color:var(--text-muted)]">
                      Draft
                    </div>
                  </div>
                  <div className="p-5">
                    <h3 className="text-[22px] font-semibold text-[color:var(--color-ink)] dark:text-white tracking-[-0.01em] mb-1 line-clamp-1">
                      {item.title}
                    </h3>
                    <p className="text-[14px] text-[color:var(--text-muted)] mb-5 line-clamp-2">
                      {item.notes || formatLabel(item.category)}
                    </p>
                    <Link
                      to={`/publish/${item.scan_session}`}
                      className="block w-full text-center py-3 bg-[color:var(--color-tag)] text-white rounded-full text-[13px] font-semibold hover:opacity-90 transition-opacity active:scale-[0.98]"
                    >
                      Complete Listing
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <SectionCard>
              <EmptyState
                icon={CheckCheck}
                title="Nothing to publish yet"
                description="When you tag items in your room scans, they'll show up here for one-click publishing."
                action={
                  <Link to="/scan" className="px-5 py-2.5 bg-[color:var(--color-tag)] text-white rounded-full text-[13px] font-semibold hover:opacity-90 transition-opacity">
                    Start Room Scan
                  </Link>
                }
              />
            </SectionCard>
          )}
        </section>

        {/* Pickup coordination table */}
        <section className="pb-10">
          <h2 className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em] mb-5">Pickup coordination</h2>
          {data.action_queues.incoming_pickup_actions.length > 0 ? (
            <div className="bg-[color:var(--color-surface)] rounded-[20px] shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[540px] text-left border-collapse">
                  <thead>
                    <tr className="bg-[color:var(--color-surface-2)] border-b border-black/5 dark:border-white/5">
                      <th className="px-6 py-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Item</th>
                      <th className="px-6 py-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Buyer</th>
                      <th className="px-6 py-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Location</th>
                      <th className="px-6 py-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Status</th>
                      <th className="px-6 py-3 text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-black/5 dark:divide-white/5">
                    {data.action_queues.incoming_pickup_actions.map((action) => (
                      <tr key={action.reservation_id} className="hover:bg-[color:var(--color-surface-2)]/50 transition-colors">
                        <td className="px-6 py-4 text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">{action.listing_title}</td>
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            <Avatar name={action.claimant_name} size={28} />
                            <span className="text-[14px] text-[color:var(--color-ink)] dark:text-white">{action.claimant_name}</span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-[14px] text-[color:var(--text-muted)]">{action.pickup_zone || "TBD"}</td>
                        <td className="px-6 py-4">
                          <PickupStatusPill status={action.status} />
                        </td>
                        <td className="px-6 py-4 text-right">
                          <Link
                            to={`/handoffs/${action.reservation_id}`}
                            className="inline-flex items-center justify-center w-8 h-8 rounded-full hover:bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)] transition-colors"
                            aria-label="Manage handoff"
                          >
                            <MessageSquare size={16} />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <SectionCard>
              <EmptyState
                icon={Zap}
                title="No active pickups"
                description="Coordination requests from other students will appear here when they reserve your items."
              />
            </SectionCard>
          )}
        </section>

        {/* ── Secondary sections ── */}
        <div className="grid gap-6 xl:grid-cols-2 pb-10">

          {/* Unread alerts */}
          <SectionCard
            title="Unread alerts"
            action={
              <Link to="/notifications" className="text-[13px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity">
                Open Inbox
              </Link>
            }
          >
            <div className="divide-y divide-black/5 dark:divide-white/5">
              {data.notification_preview.length ? (
                data.notification_preview.map((notification) => (
                  <div key={notification.id} className="px-6 py-4">
                    <div className="flex items-center justify-between gap-3 mb-1.5">
                      <span className="inline-flex items-center rounded-full bg-[color:var(--color-tag-soft)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--color-tag)]">
                        {formatLabel(notification.priority)}
                      </span>
                      <span className="text-[11px] text-[color:var(--text-muted)]">{formatDateTime(notification.created_at)}</span>
                    </div>
                    <h4 className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">{notification.title}</h4>
                    <p className="mt-0.5 text-[13px] text-[color:var(--text-muted)] line-clamp-2">{notification.body}</p>
                    {notification.link_path && (
                      <Link to={notification.link_path} className="mt-2 text-[12px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity flex items-center gap-1">
                        Open Alert <ArrowRight size={12} />
                      </Link>
                    )}
                  </div>
                ))
              ) : (
                <EmptyState icon={Bell} title="All caught up" description="No unread alerts right now." />
              )}
            </div>
          </SectionCard>

          {/* Critical tasks */}
          <SectionCard
            title="Critical move-out tasks"
            action={
              <span className="text-[13px] font-semibold text-[color:var(--color-box)]">
                {data.stats.tasks_due_today} due today
              </span>
            }
          >
            <div className="p-4 space-y-3">
              {data.action_queues.move_out_tasks.length ? (
                data.action_queues.move_out_tasks.map((task) => (
                  <MoveOutTaskCard key={task.id} task={task} compact showSessionName onStatusChange={undefined} onDelete={undefined} />
                ))
              ) : (
                <EmptyState icon={ClipboardCheck} title="No urgent tasks today" description="Nice work — you're all caught up!" />
              )}
            </div>
          </SectionCard>

          {/* Feedback waiting */}
          <SectionCard title="Feedback waiting on you">
            <div className="divide-y divide-black/5 dark:divide-white/5">
              {data.action_queues.handoffs_waiting_for_feedback?.length ? (
                data.action_queues.handoffs_waiting_for_feedback.map((entry) => (
                  <div key={entry.reservation_id} className="px-6 py-4">
                    <p className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">{entry.listing_title}</p>
                    <p className="mt-0.5 text-[13px] text-[color:var(--text-muted)]">
                      Leave a quick review for {entry.counterparty_name}
                    </p>
                    <Link
                      to={`/handoffs/${entry.reservation_id}`}
                      className="mt-3 inline-flex items-center gap-1.5 text-[13px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity"
                    >
                      Open Handoff Review <ArrowRight size={13} />
                    </Link>
                  </div>
                ))
              ) : (
                <EmptyState icon={Star} title="No feedback due" description="Completed pickups that need your review will show up here." />
              )}
            </div>
          </SectionCard>

          {/* Match center */}
          <SectionCard
            title="Requests &amp; listings lining up"
            action={
              <Link to="/requests" className="text-[13px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity">
                Open Board
              </Link>
            }
          >
            <div className="divide-y divide-black/5 dark:divide-white/5">
              {data.action_queues.fulfill_opportunities?.length ? (
                data.action_queues.fulfill_opportunities.map((entry) => (
                  <div key={entry.listing.id} className="px-6 py-4">
                    <div className="flex items-center justify-between gap-3 mb-1.5">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[color:var(--text-muted)]">{formatLabel(entry.listing.category)}</span>
                      <span className="text-[11px] text-[color:var(--text-muted)]">{entry.match_count} needs</span>
                    </div>
                    <p className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">{entry.listing.title}</p>
                    <Link
                      to={`/listings/${entry.listing.id}`}
                      className="mt-2 inline-flex items-center gap-1 text-[13px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity"
                    >
                      Open Listing <ArrowRight size={12} />
                    </Link>
                  </div>
                ))
              ) : data.action_queues.request_recommendations?.length ? (
                data.action_queues.request_recommendations.map((entry) => (
                  <div key={entry.request.id} className="px-6 py-4">
                    <div className="flex items-center justify-between gap-3 mb-1.5">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[color:var(--text-muted)]">{formatLabel(entry.request.category)}</span>
                      <span className="text-[11px] text-[color:var(--text-muted)]">{entry.match_count} matches</span>
                    </div>
                    <p className="text-[15px] font-semibold text-[color:var(--color-ink)] dark:text-white">{entry.request.title}</p>
                    <Link
                      to="/requests"
                      className="mt-2 inline-flex items-center gap-1 text-[13px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity"
                    >
                      Open Request Board <ArrowRight size={12} />
                    </Link>
                  </div>
                ))
              ) : (
                <EmptyState icon={LifeBuoy} title="No match signals yet" description="When requests and listings align, the best opportunities will surface here." />
              )}
            </div>
          </SectionCard>
        </div>

        {/* Charts */}
        <section className="pb-10">
          <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
            <div className="bg-[color:var(--color-surface)] rounded-[20px] shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10 p-6">
              <div className="flex items-center gap-3 mb-6">
                <div className="p-2 bg-[color:var(--color-tag-soft)] rounded-[10px]">
                  <ChartBar size={18} className="text-[color:var(--color-tag)]" />
                </div>
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)]">Insights</p>
                  <h3 className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white">Rescue breakdown</h3>
                </div>
              </div>
              <div className="h-64 w-full">
                {data.category_breakdown.length ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.category_breakdown}>
                      <CartesianGrid strokeDasharray="3 3" stroke="currentColor" strokeOpacity={0.07} vertical={false} />
                      <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 10, fontWeight: 600, fill: "var(--text-muted)" }} />
                      <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fontWeight: 600, fill: "var(--text-muted)" }} />
                      <Tooltip content={<CustomTooltip />} cursor={{ fill: "rgba(138,29,69,0.04)" }} />
                      <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                        {data.category_breakdown.map((entry, index) => (
                          <Cell key={entry.name} fill={barColors[index % barColors.length]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState icon={ChartBar} title="No data yet" description="Start rescuing items to see your category insights here." />
                )}
              </div>
            </div>

            <div className="bg-[color:var(--color-surface)] rounded-[20px] shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10 p-6">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-1">Pipeline</p>
              <h3 className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white mb-6">Status distribution</h3>
              <div className="h-64 w-full relative">
                {data.status_breakdown.length ? (
                  <>
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={data.status_breakdown} dataKey="value" nameKey="name" innerRadius={60} outerRadius={95} paddingAngle={8} stroke="none">
                          {data.status_breakdown.map((entry, index) => (
                            <Cell key={entry.name} fill={barColors[index % barColors.length]} />
                          ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                      <div className="text-center">
                        <p className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white leading-none">
                          {data.status_breakdown.reduce((acc, curr) => acc + curr.value, 0)}
                        </p>
                        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mt-1">Total Items</p>
                      </div>
                    </div>
                  </>
                ) : (
                  <EmptyState icon={LayoutDashboard} title="Empty pipeline" description="Track your move-out decisions from first scan to final pickup." />
                )}
              </div>
            </div>
          </div>
        </section>

        {/* Room rescue scans */}
        <section className="pb-10">
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-[28px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em]">Room rescue scans</h2>
            <Link
              to="/scan"
              className="flex items-center gap-1 text-[color:var(--color-tag)] font-semibold text-[13px] hover:opacity-70 transition-opacity"
            >
              New Scan <ArrowRight size={14} />
            </Link>
          </div>
          {data.scan_overview.length ? (
            <div className="grid gap-5 xl:grid-cols-2">
              {data.scan_overview.map((session) => (
                <motion.div
                  key={session.id}
                  whileHover={{ y: -3 }}
                  className="bg-[color:var(--color-surface)] rounded-[20px] shadow-[0px_6px_20px_rgba(0,0,0,0.04)] border border-black/10 dark:border-white/10 p-6"
                >
                  <div className="flex items-start justify-between gap-4 mb-5">
                    <div>
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="inline-flex items-center rounded-full bg-[color:var(--color-tag-soft)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--color-tag)]">
                          {formatLabel(session.status)}
                        </span>
                        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[color:var(--text-muted)]">{formatLabel(session.room_type)}</span>
                      </div>
                      <h3 className="text-[20px] font-semibold text-[color:var(--color-ink)] dark:text-white leading-snug">{session.name}</h3>
                      <p className="text-[13px] text-[color:var(--text-muted)] mt-0.5">{session.room_label}</p>
                    </div>
                    <div className="relative h-16 w-16 shrink-0">
                      <ProgressRing progress={session.progress_percent} radius={32} stroke={3} />
                      <div className="absolute inset-0 flex items-center justify-center text-[11px] font-bold text-[color:var(--color-ink)] dark:text-white">
                        {session.progress_percent}%
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 mb-5">
                    <div className="bg-[color:var(--color-surface-2)] rounded-[12px] p-3.5">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-1">Ready</p>
                      <p className="text-[20px] font-bold text-[color:var(--color-tag)]">{session.summary.ready_to_publish_count}</p>
                    </div>
                    <div className="bg-[color:var(--color-surface-2)] rounded-[12px] p-3.5">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[color:var(--text-muted)] mb-1">Blocked</p>
                      <p className="text-[20px] font-bold text-[color:var(--color-urgent)]">{session.summary.missing_info_count}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Link
                      to={`/scan/${session.id}`}
                      className="px-4 py-2 bg-[color:var(--color-tag)] text-white rounded-full text-[12px] font-semibold hover:opacity-90 transition-opacity"
                    >
                      Open Studio
                    </Link>
                    <Link
                      to={`/clearout/${session.id}`}
                      className="px-4 py-2 border border-black/10 dark:border-white/10 rounded-full text-[12px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface-2)] transition-colors"
                    >
                      Board View
                    </Link>
                    <Link
                      to={`/plan/${session.id}`}
                      className="px-4 py-2 border border-black/10 dark:border-white/10 rounded-full text-[12px] font-semibold text-[color:var(--text-muted)] hover:bg-[color:var(--color-surface-2)] transition-colors"
                    >
                      Plan
                    </Link>
                  </div>
                </motion.div>
              ))}
            </div>
          ) : (
            <SectionCard>
              <EmptyState
                icon={Camera}
                imageSrc="/images/empty-scans-dashboard.png"
                title="No room scans yet"
                description="The easiest way to move out. Photograph your room and tag everything in minutes."
                action={
                  <Link to="/scan" className="px-5 py-2.5 bg-[color:var(--color-tag)] text-white rounded-full text-[13px] font-semibold hover:opacity-90 transition-opacity">
                    Start First Scan
                  </Link>
                }
              />
            </SectionCard>
          )}
        </section>
      </div>
    </div>
  );
}
