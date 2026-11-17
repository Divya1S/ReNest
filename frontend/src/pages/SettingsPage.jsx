import {
  BadgeCheck,
  Bell,
  Download,
  KeyRound,
  Loader2,
  Mail,
  MailCheck,
  School,
  Trash2,
  Trophy,
  User as UserIcon,
} from "lucide-react";
import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { useAuth } from "../context/AuthContext";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

/** One on/off setting: icon, label, explanation and an accessible switch. */
function SettingSwitch({ id, icon: Icon, label, description, checked, disabled, onToggle }) {
  return (
    <div className="flex items-center justify-between gap-4 px-6 py-4">
      <div className="flex items-center gap-3 min-w-0">
        <Icon size={16} className="shrink-0 text-[color:var(--text-muted)]" />
        <div className="min-w-0">
          <p id={id} className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">
            {label}
          </p>
          <p className="text-[12px] text-[color:var(--text-muted)]">{description}</p>
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={id}
        onClick={onToggle}
        disabled={disabled}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-tag)] focus-visible:ring-offset-2 disabled:opacity-60 ${
          checked ? "bg-[color:var(--color-tag)]" : "bg-[color:var(--color-surface-2)]"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
    </div>
  );
}

export default function SettingsPage() {
  usePageTitle("Profile Settings");
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();

  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [campusName, setCampusName] = useState(user?.campus_name ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [leaderboardSaving, setLeaderboardSaving] = useState(false);
  const showOnLeaderboard = user?.show_on_leaderboard ?? true;

  // The leaderboard page tells students they can opt out "in your account
  // settings". This is that control; the backend already filters on the flag.
  async function handleLeaderboardToggle() {
    if (leaderboardSaving) return;
    setLeaderboardSaving(true);
    try {
      await apiFetch("/auth/me", {
        method: "PATCH",
        body: { show_on_leaderboard: !showOnLeaderboard },
      });
      await refreshUser();
      toast.success(
        showOnLeaderboard ? "You're hidden from the leaderboard." : "You're on the leaderboard.",
      );
    } catch (requestError) {
      toast.error(requestError.message || "Could not update your leaderboard setting.");
    } finally {
      setLeaderboardSaving(false);
    }
  }

  const [emailSaving, setEmailSaving] = useState(false);
  const emailNotifications = user?.email_notifications ?? true;

  // The unsubscribe page says emails can be switched back on "in your account
  // settings". This is that control.
  async function handleEmailToggle() {
    if (emailSaving) return;
    setEmailSaving(true);
    try {
      await apiFetch("/auth/me", {
        method: "PATCH",
        body: { email_notifications: !emailNotifications },
      });
      await refreshUser();
      toast.success(emailNotifications ? "Emails are off." : "Emails are back on.");
    } catch (requestError) {
      toast.error(requestError.message || "Could not update your email setting.");
    } finally {
      setEmailSaving(false);
    }
  }

  const [exporting, setExporting] = useState(false);

  async function handleExport() {
    if (exporting) return;
    setExporting(true);
    try {
      const archive = await apiFetch("/auth/data-export");
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(archive, null, 2)], { type: "application/json" }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = "renest-data.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      toast.error(requestError.message || "Could not prepare your data.");
    } finally {
      setExporting(false);
    }
  }

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  async function handleDelete(event) {
    event.preventDefault();
    if (deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await apiFetch("/auth/account", { method: "DELETE", body: { password: deletePassword } });
      toast.success("Your account has been deleted.");
      await refreshUser().catch(() => null);
      navigate("/", { replace: true });
    } catch (requestError) {
      setDeleteError(requestError.message || "Could not delete your account.");
      setDeleting(false);
    }
  }

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

      {/* Preference toggles */}
      <div className="mt-6 bg-[color:var(--color-surface)] rounded-[20px] border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] overflow-hidden">
        <div className="px-6 py-5 border-b border-black/5 dark:border-white/5">
          <h2 className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white tracking-[-0.01em]">
            Visibility and email
          </h2>
        </div>
        <div className="divide-y divide-black/5 dark:divide-white/5">
          <SettingSwitch
            id="leaderboard-toggle-label"
            icon={Trophy}
            label="Show me on the campus leaderboard"
            description="Your display name and rescue count appear in campus rankings."
            checked={showOnLeaderboard}
            disabled={leaderboardSaving}
            onToggle={handleLeaderboardToggle}
          />
          <SettingSwitch
            id="email-toggle-label"
            icon={MailCheck}
            label="Email me about my listings and pickups"
            description="Confirmations, reminders and digests. Sign-in and security emails are always sent."
            checked={emailNotifications}
            disabled={emailSaving}
            onToggle={handleEmailToggle}
          />
        </div>
      </div>

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

      {/* Your data: the two controls the privacy page points here for */}
      <div className="mt-6 bg-[color:var(--color-surface)] rounded-[20px] border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] overflow-hidden">
        <div className="px-6 py-5 border-b border-black/5 dark:border-white/5">
          <h2 className="text-[18px] font-semibold text-[color:var(--color-ink)] dark:text-white tracking-[-0.01em]">
            Your data
          </h2>
        </div>
        <div className="divide-y divide-black/5 dark:divide-white/5">
          <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            className="flex w-full items-center gap-3 px-6 py-4 text-left transition-colors hover:bg-[color:var(--color-surface-2)] disabled:opacity-60"
          >
            {exporting ? (
              <Loader2 size={16} className="shrink-0 animate-spin text-[color:var(--text-muted)]" />
            ) : (
              <Download size={16} className="shrink-0 text-[color:var(--text-muted)]" />
            )}
            <div>
              <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                Download my data
              </p>
              <p className="text-[12px] text-[color:var(--text-muted)]">
                Your profile, listings, reservations and messages as one JSON file
              </p>
            </div>
          </button>

          {confirmingDelete ? (
            <form onSubmit={handleDelete} className="space-y-3 px-6 py-4">
              <p className="text-[14px] font-semibold text-[color:var(--color-ink)] dark:text-white">
                Delete your account?
              </p>
              <p className="text-[12px] text-[color:var(--text-muted)]">
                This cancels your open reservations and removes your listings, saved items and messages. It cannot be undone.
              </p>
              <label
                htmlFor="settings-delete-password"
                className="block text-[13px] font-semibold text-[color:var(--color-ink)] dark:text-white"
              >
                Enter your password to confirm
              </label>
              <input
                id="settings-delete-password"
                type="password"
                autoComplete="current-password"
                className="field"
                value={deletePassword}
                onChange={(event) => setDeletePassword(event.target.value)}
                required
              />
              {deleteError && (
                <p role="alert" className="text-[13px] font-semibold text-red-500">
                  {deleteError}
                </p>
              )}
              <div className="flex flex-wrap gap-3">
                <button
                  type="submit"
                  disabled={deleting || !deletePassword}
                  className="flex items-center justify-center gap-2 rounded-full bg-red-600 px-5 py-2.5 text-[13px] font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {deleting && <Loader2 size={14} className="animate-spin" />}
                  {deleting ? "Deleting…" : "Delete my account"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setConfirmingDelete(false);
                    setDeletePassword("");
                    setDeleteError("");
                  }}
                  className="rounded-full border border-black/10 px-5 py-2.5 text-[13px] font-semibold text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-surface-2)] dark:border-white/10 dark:text-white"
                >
                  Keep my account
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              className="flex w-full items-center gap-3 px-6 py-4 text-left transition-colors hover:bg-[color:var(--color-surface-2)]"
            >
              <Trash2 size={16} className="shrink-0 text-red-500" />
              <div>
                <p className="text-[14px] font-semibold text-red-600 dark:text-red-400">Delete account</p>
                <p className="text-[12px] text-[color:var(--text-muted)]">
                  Permanently remove your account and everything in it
                </p>
              </div>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
