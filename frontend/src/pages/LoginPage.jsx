import { motion } from "framer-motion";
import { ArrowRight, KeyRound, Layers3, Sparkles, Tag, Mail, Lock } from "lucide-react";
import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import PageSection from "../components/PageSection";
import { useAuth } from "../context/AuthContext";
import { usePageTitle } from "../hooks/usePageTitle";

export default function LoginPage() {
  usePageTitle("Sign In");
  const { login, authNotice, clearAuthNotice } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");

    try {
      await login(form);
      clearAuthNotice();
      navigate(location.state?.from || "/dashboard", { replace: true });
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
        <p className="label-title !text-emerald-300">Room rescue workflow</p>
        <h1 className="mt-4 text-[34px] font-bold leading-[1.15] tracking-[-0.025em] sm:text-[42px] text-white">Log in to keep good dorm gear in circulation.</h1>
        <p className="mt-8 max-w-xl text-lg leading-relaxed text-white/80">
          ReNest helps outgoing students turn a rushed move-out room into a structured rescue pipeline with room scans, clear-out boards, and faster handoffs.
        </p>

        <div className="mt-12 space-y-8">
          <div className="auth-point group">
            <div className="flex items-center gap-4 transition-transform group-hover:translate-x-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/10 backdrop-blur-md">
                <Layers3 size={24} className="text-emerald-300" />
              </div>
              <p className="text-[17px] font-semibold text-white">Scan first, post second</p>
            </div>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              Visual triage makes it easier to decide what should be listed, donated, kept, or tossed before the deadline hits.
            </p>
          </div>
          
          <div className="auth-point group">
            <div className="flex items-center gap-4 transition-transform group-hover:translate-x-2">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/10 backdrop-blur-md">
                <Tag size={24} className="text-amber-300" />
              </div>
              <p className="text-[17px] font-semibold text-white">Coordinate pickups</p>
            </div>
            <p className="mt-4 text-base leading-relaxed text-white/70">
              Message buyers, confirm pickup times, and track handoffs through a single streamlined flow — no back-and-forth needed.
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
          Welcome back
        </div>
        <h2 className="mt-6 text-[28px] font-bold leading-snug tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">Pick up where your last rescue left off.</h2>
        
        <form onSubmit={handleSubmit} className="mt-12 space-y-6">
          {(location.state?.reason || authNotice) && (
            <motion.p
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-bold text-amber-800 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-200"
            >
              {location.state?.reason || authNotice}
            </motion.p>
          )}

          <div className="floating-label-group">
            <Mail className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="email"
              className="field pl-14"
              placeholder=" "
              autoComplete="email"
              value={form.email}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
              required
              id="email"
            />
            <label htmlFor="email">Email Address</label>
          </div>

          <div className="floating-label-group">
            <Lock className="absolute left-5 top-1/2 -translate-y-1/2 transition-colors text-[color:var(--text-muted)]" size={20} />
            <input
              type="password"
              className="field pl-14"
              placeholder=" "
              autoComplete="current-password"
              value={form.password}
              onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
              required
              id="password"
            />
            <label htmlFor="password">Password</label>
          </div>

          <div className="flex justify-end -mt-2">
            <Link
              to="/password-reset"
              className="text-sm font-bold text-[color:var(--text-muted)] hover:text-[color:var(--color-ink)] dark:hover:text-white transition-colors"
            >
              Forgot password?
            </Link>
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

          <button type="submit" disabled={submitting} className="primary-button w-full !py-5 !text-lg">
            {submitting ? "Logging In..." : "Sign In to Dashboard"}
            <ArrowRight size={20} />
          </button>
        </form>

        <div className="mt-10 pt-10 border-t border-slate-100 dark:border-slate-800">
          <p className="text-sm font-bold text-[color:var(--text-muted)]">Need an account?</p>
          <div className="mt-4 flex items-center justify-between gap-4">
             <p className="text-sm text-[color:var(--text-muted)] leading-relaxed">
              Join your campus community to start rescuing and reusing essentials.
            </p>
            <Link to="/register" className="secondary-button whitespace-nowrap !px-6">
              <Sparkles size={16} />
              Join Now
            </Link>
          </div>
        </div>
      </motion.section>
    </PageSection>
  );
}
