import { BadgeCheck, Bell, KeyRound, Loader2, Mail, School, User as UserIcon } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { useAuth } from "../context/AuthContext";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function SettingsPage() {
  usePageTitle("Profile Settings");
  const { user, refreshUser } = useAuth();

  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [campusName, setCampusName] = useState(user?.campus_name ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const isDirty =
    displayName !== (user?.display_name ?? "") || campusName !== (user?.campus_name ?? "");

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await apiFetch("/auth/me", {
        method: "PATCH",
        body: { display_name: displayName, campus_name: campusName },
      });
      await refreshUser();
      toast.success("Profile updated.");
    } catch (requestError) {
      setError(requestError.message);
      toast.error(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 md:px-8 pb-20">
      <section className="pt-8 pb-8">
        <h1 className="text-[34px] font-bold text-[color:var(--color-ink)] dark:text-white tracking-[-0.025em]">
          Profile settings
        </h1>
        <p className="mt-1 text-[16px] text-[color:var(--text-muted)]">
          Your name and campus are shown to other students on your listings.
        </p>
      </section>

      {/* Profile form */}
      <form
        onSubmit={handleSubmit}
        className="bg-[color:var(--color-surface)] rounded-[20px] border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] p-6 space-y-5"
      >
        <div>
          <label
            htmlFor="settings-display-name"
            className="flex items-center gap-1.5 text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white mb-1.5"
          >
            <UserIcon size={13} className="text-[color:var(--color-tag)]" />
            Display name
          </label>
          <input
            id="settings-display-name"
            className="field"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={120}
            required
          />
        </div>

        <div>
          <label
            htmlFor="settings-campus"
            className="flex items-center gap-1.5 text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white mb-1.5"
          >
            <School size={13} className="text-[color:var(--color-tag)]" />
            Campus
          </label>
          <input
            id="settings-campus"
            className="field"
            value={campusName}
            onChange={(event) => setCampusName(event.target.value)}
            maxLength={120}
            placeholder="e.g. University of Southern California"
          />
          <p className="mt-1.5 text-[12px] text-[color:var(--text-muted)]">
            Listings are scoped to your campus, so keep this accurate.
          </p>
        </div>

        {error && (
          <p role="alert" className="text-[13px] font-semibold text-red-500">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={saving || !isDirty}
          className="flex items-center justify-center gap-2 rounded-full bg-[color:var(--color-tag)] px-6 py-2.5 text-[13px] font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving && <Loader2 size={14} className="animate-spin" />}
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </form>

      {/* Account details */}
      <div className="mt-6 bg-[color:var(--color-surface)] rounded-[20px] border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] overflow-hidden">
        <div className="px-6 py-5 border-b border-black/5 dark:border-white/5">
          <h2 className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white tracking-[-0.01em]">
            Account
          </h2>
        </div>
        <div className="divide-y divide-black/5 dark:divide-white/5">
          <div className="flex items-center justify-between gap-4 px-6 py-4">
            <div className="flex items-center gap-3 min-w-0">
              <Mail size={16} className="shrink-0 text-[color:var(--text-muted)]" />
              <div className="min-w-0">
                <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white truncate">
                  {user?.email}
                </p>
                <p className="text-[12px] text-[color:var(--text-muted)]">Email address</p>
              </div>
            </div>
            {user?.email_verified ? (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-[color:var(--bg-free-soft)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--color-free)]">
                <BadgeCheck size={12} />
                Verified
              </span>
            ) : (
              <Link
                to="/verify-email"
                className="shrink-0 text-[12px] font-semibold text-[color:var(--color-tag)] hover:opacity-70 transition-opacity"
              >
                Verify Now
              </Link>
            )}
          </div>

          <Link
            to="/password-reset"
            className="flex items-center gap-3 px-6 py-4 transition-colors hover:bg-[color:var(--color-surface-2)]"
          >
            <KeyRound size={16} className="shrink-0 text-[color:var(--text-muted)]" />
            <div>
              <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                Change password
              </p>
              <p className="text-[12px] text-[color:var(--text-muted)]">
                We&apos;ll email you a secure reset link
              </p>
            </div>
          </Link>

          <Link
            to="/settings/notifications"
            className="flex items-center gap-3 px-6 py-4 transition-colors hover:bg-[color:var(--color-surface-2)]"
          >
            <Bell size={16} className="shrink-0 text-[color:var(--text-muted)]" />
            <div>
              <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                Notification preferences
              </p>
              <p className="text-[12px] text-[color:var(--text-muted)]">
                Email, push, and quiet hours
              </p>
            </div>
          </Link>
        </div>
      </div>
    </div>
  );
}
