import { motion } from "framer-motion";
import { ArrowRight, KeyRound, Mail, ShieldCheck } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";

import PageSection from "../components/PageSection";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

export default function PasswordResetPage() {
  usePageTitle("Reset Password");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await apiFetch("/auth/password-reset", { method: "POST", body: { email } });
      setSent(true);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <PageSection className="auth-grid min-h-[80vh] items-center gap-12">
      <motion.aside
        initial={{ opacity: 0, x: -30 }}
        animate={{ opacity: 1, x: 0 }}
        className="auth-aside auth-aside--login !rounded-[3rem] p-10 sm:p-16"
      >
        <p className="label-title !text-emerald-300">Account recovery</p>
        <h1 className="mt-4 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] sm:text-[42px] text-white">
          Get back to your rescue pipeline.
        </h1>
        <p className="mt-8 max-w-xl text-lg leading-relaxed text-white/80">
          Enter the email address you registered with and we&apos;ll send you a
          time-limited link to set a new password. The link expires in 1 hour.
        </p>

        <div className="mt-12 space-y-8">
          <div className="auth-point group">
            <div className="flex items-center gap-4 transition-transform group-hover:translate-x-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/10 backdrop-blur-md">
                <ShieldCheck size={24} className="text-emerald-300" />
              </div>
              <p className="text-[18px] font-semibold text-white">Secure one-time link</p>
            </div>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              The reset link is tied to your current password hash. Once used, it can&apos;t be replayed.
            </p>
          </div>
        </div>
      </motion.aside>

      <motion.section
        initial={{ opacity: 0, x: 30 }}
        animate={{ opacity: 1, x: 0 }}
        className="paper-panel mx-auto w-full max-w-xl !rounded-[3rem] p-10 sm:p-16"
      >
        <div className="sticker bg-[color:var(--color-tag)] text-white font-bold">
          <KeyRound size={14} />
          Password Reset
        </div>

        {sent ? (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-8"
          >
            <h2 className="text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              Check your inbox.
            </h2>
            <p className="mt-4 text-lg leading-8 text-[color:var(--text-muted)]">
              If <strong>{email}</strong> is registered, a reset link is on its way. It
              expires in 1 hour.
            </p>
            <p className="mt-3 text-sm text-[color:var(--text-muted)]">
              Didn&apos;t get it? Check your spam folder, or{" "}
              <button
                type="button"
                onClick={() => setSent(false)}
                className="font-bold text-[color:var(--color-tag)] underline-offset-2 hover:underline"
              >
                try a different email
              </button>
              .
            </p>
            <Link to="/login" className="primary-button mt-8 w-full !py-5 !text-lg">
              Back to Sign In
              <ArrowRight size={20} />
            </Link>
          </motion.div>
        ) : (
          <>
            <h2 className="mt-6 text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
              Forgot your password?
            </h2>
            <p className="mt-3 text-[color:var(--text-muted)]">
              We&apos;ll email you a secure link to reset it.
            </p>

            <form onSubmit={handleSubmit} className="mt-10 space-y-6">
              <div className="floating-label-group">
                <Mail
                  className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]"
                  size={20}
                />
                <input
                  type="email"
                  className="field pl-14"
                  placeholder=" "
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  id="email"
                />
                <label htmlFor="email">Email Address</label>
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
                {submitting ? "Sending..." : "Send Reset Link"}
                <ArrowRight size={20} />
              </button>
            </form>

            <div className="mt-10 pt-10 border-t border-slate-100 dark:border-slate-800">
              <Link to="/login" className="text-sm font-bold text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] dark:hover:text-white transition-colors">
                ← Back to Sign In
              </Link>
            </div>
          </>
        )}
      </motion.section>
    </PageSection>
  );
}
