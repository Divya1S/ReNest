import { motion } from "framer-motion";
import { ArrowRight, KeyRound, Lock } from "lucide-react";
import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import PageSection from "../components/PageSection";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function PasswordResetConfirmPage() {
  usePageTitle("Set New Password");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const uid = searchParams.get("uid") ?? "";
  const token = searchParams.get("token") ?? "";

  const [form, setForm] = useState({ new_password: "", confirm_password: "" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const linkMissing = !uid || !token;

  function handleChange(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (form.new_password !== form.confirm_password) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await apiFetch("/auth/password-reset/confirm", {
        method: "POST",
        body: { uid, token, ...form },
      });
      navigate("/login", {
        state: { reason: "Password updated. Sign in with your new password." },
        replace: true,
      });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageSection className="flex min-h-[80vh] items-center justify-center py-16">
      <motion.section
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="paper-panel mx-auto w-full max-w-xl !rounded-[3rem] p-10 sm:p-16"
      >
        <div className="sticker bg-[color:var(--color-tag)] text-white font-bold">
          <KeyRound size={14} />
          New Password
        </div>

        {linkMissing ? (
          <div className="mt-8">
            <h2 className="text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              Invalid reset link.
            </h2>
            <p className="mt-4 text-[color:var(--text-muted)]">
              This link is missing required parameters. Please request a new one.
            </p>
            <Link to="/password-reset" className="primary-button mt-8 inline-flex">
              Request a new link
              <ArrowRight size={18} />
            </Link>
          </div>
        ) : (
          <>
            <h2 className="mt-6 text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              Set a new password.
            </h2>
            <p className="mt-3 text-[color:var(--text-muted)]">
              Choose something secure — at least 8 characters.
            </p>

            <form onSubmit={handleSubmit} className="mt-10 space-y-6">
              <div className="floating-label-group">
                <Lock
                  className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]"
                  size={20}
                />
                <input
                  type="password"
                  name="new_password"
                  className="field pl-14"
                  placeholder=" "
                  autoComplete="new-password"
                  value={form.new_password}
                  onChange={handleChange}
                  required
                  id="new_password"
                />
                <label htmlFor="new_password">New Password</label>
              </div>

              <div className="floating-label-group">
                <Lock
                  className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]"
                  size={20}
                />
                <input
                  type="password"
                  name="confirm_password"
                  className="field pl-14"
                  placeholder=" "
                  autoComplete="new-password"
                  value={form.confirm_password}
                  onChange={handleChange}
                  required
                  id="confirm_password"
                />
                <label htmlFor="confirm_password">Confirm New Password</label>
              </div>

              {error && (
                <motion.p
                  role="alert"
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="text-sm font-bold text-red-500 bg-red-50 p-3 rounded-xl dark:bg-red-900/20"
                >
                  {error}
                </motion.p>
              )}

              <button
                type="submit"
                disabled={submitting}
                className="primary-button w-full !py-5 !text-lg"
              >
                {submitting ? "Saving..." : "Set New Password"}
                <ArrowRight size={20} />
              </button>
            </form>

            <div className="mt-10 pt-10 border-t border-slate-100 dark:border-slate-800">
              <Link
                to="/login"
                className="text-sm font-bold text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] dark:hover:text-white transition-colors"
              >
                ← Back to Sign In
              </Link>
            </div>
          </>
        )}
      </motion.section>
    </PageSection>
  );
}
