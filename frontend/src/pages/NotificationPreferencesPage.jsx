import { Bell, BellOff, Check, Clock, Mail, RefreshCw, Smartphone } from "lucide-react";
import React from "react";
import { useState } from "react";
import { toast } from "sonner";

import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";
import { cn } from "../lib/cn";

const TYPE_LABELS = {
  reservation: "Reservation confirmed",
  handoff_urgent: "Handoff reminders",
  ai_match: "Match alerts",
  system: "Listing updates",
  weekly_digest: "Weekly digest",
  ready_to_publish: "Announcements",
};

const CHANNEL_OPTIONS = [
  { value: "both", label: "Push + Email", icon: Smartphone },
  { value: "push", label: "Push only", icon: Smartphone },
  { value: "email", label: "Email only", icon: Mail },
  { value: "off", label: "Off", icon: BellOff },
];

const DEFAULT_QUIET = { start: "23:00", end: "08:00" };

export default function NotificationPreferencesPage() {
  usePageTitle("Notification Preferences");
  const { data, loading, error, refetch, setData } = useApi("/notifications/preferences");
  const prefs = data?.preferences ?? [];

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  function updatePref(type, field, value) {
    setData((prev) => ({
      ...prev,
      preferences: (prev?.preferences ?? []).map((p) =>
        p.notification_type === type ? { ...p, [field]: value } : p
      ),
    }));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    try {
      await apiFetch("/notifications/preferences", {
        method: "PATCH",
        body: { preferences: prefs },
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (error) {
      // Silently swallowing this left the user unable to tell a failed save
      // from one they never clicked.
      toast.error(error?.message || "Could not save preferences.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-[680px] px-4 py-10">
      <div className="flex items-center gap-3 mb-8">
        <div className="h-10 w-10 rounded-xl bg-[color:var(--color-surface-2)] flex items-center justify-center">
          <Bell size={18} className="text-[color:var(--color-tag)]" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-[color:var(--color-ink)] dark:text-white">
            Notification Preferences
          </h1>
          <p className="text-sm text-[color:var(--text-muted)]">
            Choose how and when ReNest notifies you.
          </p>
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-20 rounded-2xl animate-pulse bg-[color:var(--color-surface)]" />
          ))}
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-16 text-center rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)]">
          <p className="font-semibold text-[color:var(--color-ink)] dark:text-white">Couldn&apos;t load preferences</p>
          <p className="text-sm text-[color:var(--text-muted)] mt-1">{error}</p>
          <button
            onClick={() => refetch()}
            className="mt-4 flex items-center gap-1.5 px-4 py-2 rounded-full bg-[color:var(--color-tag)] text-white text-sm font-semibold hover:opacity-90"
          >
            <RefreshCw size={14} /> Try again
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {prefs.map((pref) => (
            <div
              key={pref.notification_type}
              className="rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] px-4 py-4"
            >
              <div className="flex items-center justify-between gap-4 flex-wrap">
                <p className="font-semibold text-[color:var(--color-ink)] dark:text-white text-sm">
                  {TYPE_LABELS[pref.notification_type] ?? pref.notification_type}
                </p>
                <div
                  role="group"
                  aria-label="Delivery channel"
                  className="flex items-center gap-0.5 bg-[color:var(--color-surface-2)] rounded-xl p-0.5"
                >
                  {CHANNEL_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      onClick={() => updatePref(pref.notification_type, "channel", opt.value)}
                      aria-pressed={pref.channel === opt.value}
                      className={cn(
                        "px-3 py-1.5 rounded-[10px] text-[11px] font-semibold transition-all",
                        pref.channel === opt.value
                          ? "bg-[color:var(--color-surface)] text-[color:var(--color-tag)] shadow-sm"
                          : "text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)]"
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              {pref.channel !== "off" && (
                <div className="mt-3 flex items-center gap-3 flex-wrap">
                  <div className="flex items-center gap-1.5 text-xs text-[color:var(--text-muted)]">
                    <Clock size={12} />
                    <span>Quiet hours</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="time"
                      value={pref.quiet_hours_start ?? DEFAULT_QUIET.start}
                      onChange={(e) =>
                        updatePref(pref.notification_type, "quiet_hours_start", e.target.value || null)
                      }
                      className="text-xs border border-black/10 dark:border-white/10 rounded-lg px-2 py-1 bg-transparent text-[color:var(--color-ink)] dark:text-white focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]"
                    />
                    <span className="text-xs text-[color:var(--text-muted)]">to</span>
                    <input
                      type="time"
                      value={pref.quiet_hours_end ?? DEFAULT_QUIET.end}
                      onChange={(e) =>
                        updatePref(pref.notification_type, "quiet_hours_end", e.target.value || null)
                      }
                      className="text-xs border border-black/10 dark:border-white/10 rounded-lg px-2 py-1 bg-transparent text-[color:var(--color-ink)] dark:text-white focus:outline-none focus:ring-2 focus:ring-[color:var(--color-tag)]"
                    />
                  </div>
                </div>
              )}
            </div>
          ))}

          {prefs.length > 0 && (
            <button
              onClick={save}
              disabled={saving}
              className={cn(
                "w-full mt-4 flex items-center justify-center gap-2 py-3 rounded-2xl font-semibold text-sm transition-all",
                saved
                  ? "bg-green-500 text-white"
                  : "bg-[color:var(--color-tag)] text-white hover:opacity-90"
              )}
            >
              {saved ? <><Check size={16} /> Saved</> : saving ? "Saving…" : "Save preferences"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
