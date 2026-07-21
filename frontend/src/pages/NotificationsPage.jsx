import { motion } from "framer-motion";
import { Bell, CheckCheck, Clock3 } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import PageSection from "../components/PageSection";
import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { formatDateTime, formatLabel } from "../lib/formatters";

const listVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
};
const itemVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.32, ease: "easeOut" } },
};

const priorityStyles = {
  high: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  normal: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300",
  low: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

export default function NotificationsPage() {
  usePageTitle("Alerts");
  const {
    data,
    loading,
    error,
    refetch,
    setData,
  } = useApi("/notifications", { initialData: { results: [], unread_count: 0 } });
  const [workingId, setWorkingId] = React.useState("");
  const [markingAll, setMarkingAll] = React.useState(false);

  async function markOne(notificationId, isRead = true) {
    setWorkingId(String(notificationId));
    try {
      const updated = await apiFetch(`/notifications/${notificationId}`, {
        method: "PATCH",
        body: { is_read: isRead },
      });
      setData((current) => {
        const nextResults = current.results.map((item) =>
          item.id === notificationId ? updated : item,
        );
        const unreadCount = nextResults.filter((item) => !item.is_read).length;
        return { ...current, results: nextResults, unread_count: unreadCount };
      });
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setWorkingId("");
    }
  }

  async function markAllRead() {
    setMarkingAll(true);
    try {
      await apiFetch("/notifications/read-all", {
        method: "POST",
        body: {},
      });
      setData((current) => ({
        ...current,
        unread_count: 0,
        results: current.results.map((item) => ({
          ...item,
          is_read: true,
          read_at: item.read_at || new Date().toISOString(),
        })),
      }));
    } catch (requestError) {
      toast.error(requestError.message);
    } finally {
      setMarkingAll(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-6 pb-16">
        <div className="paper-panel p-10 sm:p-14 animate-pulse">
          <div className="h-5 w-28 rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="mt-5 h-11 w-3/4 rounded-2xl bg-[color:var(--color-surface-2)]" />
          <div className="mt-4 h-4 w-full max-w-2xl rounded-full bg-[color:var(--color-surface-2)]" />
          <div className="mt-2 h-4 w-1/2 rounded-full bg-[color:var(--color-surface-2)]" />
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="paper-panel p-6 animate-pulse">
              <div className="h-3 w-20 rounded-full bg-[color:var(--color-surface-2)]" />
              <div className="mt-4 h-10 w-14 rounded-2xl bg-[color:var(--color-surface-2)]" />
            </div>
          ))}
        </div>
        <div className="paper-panel p-8 animate-pulse space-y-4">
          <div className="h-5 w-32 rounded-full bg-[color:var(--color-surface-2)]" />
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="rounded-2xl border border-black/8 dark:border-white/8 p-5 space-y-3">
              <div className="flex justify-between">
                <div className="h-4 w-16 rounded-full bg-[color:var(--color-surface-2)]" />
                <div className="h-4 w-24 rounded-full bg-[color:var(--color-surface-2)]" />
              </div>
              <div className="h-5 w-3/4 rounded-full bg-[color:var(--color-surface-2)]" />
              <div className="h-3 w-full rounded-full bg-[color:var(--color-surface-2)]" />
              <div className="h-3 w-2/3 rounded-full bg-[color:var(--color-surface-2)]" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="paper-panel p-10 text-center">
        <p className="label-title">Alerts</p>
        <h2 className="mt-3 text-[24px] font-bold tracking-[-0.02em]">Notification center failed to load</h2>
        <p className="mt-3 text-[color:var(--text-muted)]">{error}</p>
        <button type="button" onClick={refetch} className="primary-button mt-6">
          Try Again
        </button>
      </div>
    );
  }

  const unread = data.results.filter((item) => !item.is_read);
  const read = data.results.filter((item) => item.is_read);

  return (
    <div className="space-y-8 pb-16">
      <PageSection className="paper-panel p-10 sm:p-14">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="sticker bg-[color:var(--color-tag)] text-white">
              <Bell size={14} />
              Notifications
            </div>
            <h1 className="mt-5 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
              Keep the move-out pipeline moving.
            </h1>
            <p className="mt-4 max-w-3xl text-lg leading-8 text-[color:var(--text-muted)]">
              This inbox tracks what needs attention next: publish-ready drafts, urgent handoffs,
              reservation changes, and time-sensitive move-out tasks.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link to="/dashboard" className="secondary-button">
              Back to Dashboard
            </Link>
            <Link to="/settings/notifications" className="secondary-button">
              Preferences
            </Link>
            <button
              type="button"
              disabled={!data.unread_count || markingAll}
              onClick={markAllRead}
              className="primary-button"
            >
              <CheckCheck size={16} />
              {markingAll ? "Working..." : "Mark all read"}
            </button>
          </div>
        </div>
      </PageSection>

      <PageSection className="grid gap-4 md:grid-cols-3" delay={0.04}>
        <div className="paper-panel p-6">
          <p className="label-title">Unread now</p>
          <p className="mt-3 text-4xl font-bold text-[color:var(--color-tag)]">{data.unread_count}</p>
        </div>
        <div className="paper-panel p-6">
          <p className="label-title">Tracked alerts</p>
          <p className="mt-3 text-4xl font-bold text-[color:var(--color-ink)] dark:text-white">{data.results.length}</p>
        </div>
        <div className="paper-panel p-6">
          <p className="label-title">High priority</p>
          <p className="mt-3 text-4xl font-bold text-red-600 dark:text-red-400">
            {data.results.filter((item) => item.priority === "high" && !item.is_read).length}
          </p>
        </div>
      </PageSection>

      <PageSection className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]" delay={0.08}>
        <section className="paper-panel p-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <p className="label-title">Needs attention</p>
              <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">Unread alerts</h2>
            </div>
            <span className="rounded-full bg-[color:var(--color-tag-soft)] px-3 py-1 text-xs font-bold text-[color:var(--color-tag)]">
              {unread.length}
            </span>
          </div>

          {unread.length ? (
            <motion.div variants={listVariants} initial="hidden" animate="visible" className="space-y-4">
              {unread.map((notification) => (
                <motion.div key={notification.id} variants={itemVariants}>
                  <div className="rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] p-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className={`rounded-full px-3 py-1 text-[0.65rem] font-black uppercase tracking-[0.14em] ${priorityStyles[notification.priority] || priorityStyles.normal}`}>
                        {formatLabel(notification.priority)}
                      </span>
                      <span className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
                        <Clock3 size={14} />
                        {formatDateTime(notification.created_at)}
                      </span>
                    </div>
                    <h3 className="mt-4 text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white">{notification.title}</h3>
                    <p className="mt-2 text-sm leading-6 text-[color:var(--text-muted)]">
                      {notification.body}
                    </p>
                    <div className="mt-5 flex flex-wrap gap-3">
                      {notification.link_path ? (
                        <Link to={notification.link_path} className="primary-button !px-4 !py-2">
                          Open
                        </Link>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => markOne(notification.id, true)}
                        disabled={workingId === String(notification.id)}
                        className="secondary-button !px-4 !py-2"
                      >
                        {workingId === String(notification.id) ? "Saving..." : "Mark read"}
                      </button>
                    </div>
                  </div>
                </motion.div>
              ))}
            </motion.div>
          ) : (
            <div role="status" className="rounded-[2rem] border-2 border-dashed border-slate-200 bg-slate-50/70 p-10 text-center dark:border-slate-800 dark:bg-slate-900/30">
              <img
                src="/images/empty-notifications.png"
                alt=""
                aria-hidden="true"
                loading="lazy"
                className="mx-auto mb-4 h-24 w-24 object-contain dark:rounded-3xl dark:bg-white/90 dark:p-2.5"
              />
              <p className="text-[16px] font-semibold text-[color:var(--color-ink)] dark:text-white">You’re all caught up.</p>
              <p className="mt-2 text-sm text-[color:var(--text-muted)]">
                New publish, reservation, and task alerts will appear here automatically.
              </p>
            </div>
          )}
        </section>

        <section className="paper-panel p-8">
          <div className="mb-6">
            <p className="label-title">Archive</p>
            <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">Recently read</h2>
          </div>
          {read.length ? (
            <motion.div variants={listVariants} initial="hidden" animate="visible" className="space-y-4">
              {read.map((notification) => (
                <motion.div key={notification.id} variants={itemVariants}>
                  <div className="rounded-3xl border border-slate-200 bg-slate-50/80 px-5 py-5 dark:border-slate-800 dark:bg-slate-900/30">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-black text-slate-900 text-[color:var(--color-ink)] dark:text-white">{notification.title}</p>
                      <span className="text-[0.65rem] font-bold uppercase tracking-[0.14em] text-[color:var(--text-muted)]">
                        {formatDateTime(notification.read_at || notification.created_at)}
                      </span>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-[color:var(--text-muted)]">
                      {notification.body}
                    </p>
                  </div>
                </motion.div>
              ))}
            </motion.div>
          ) : (
            <p className="text-sm text-[color:var(--text-muted)]">Read notifications will land here once you clear them.</p>
          )}
        </section>
      </PageSection>
    </div>
  );
}
